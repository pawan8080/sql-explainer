#!/usr/bin/env python3
"""
sql-explainer: explain a SQL query in plain English.

Reads a .sql file (or a query string via --query) and prints:
  - what the query does, in plain English
  - tables and aliases used
  - columns selected, filters, grouping, ordering, limits
  - CTEs and subqueries

No dependencies, no API keys, no LLM. Pure stdlib parsing aimed at the
SELECT queries BI developers actually inherit and have to understand.

Usage:
    python explainer.py query.sql
    python explainer.py --query "SELECT ..."
"""

import argparse
import re
import sys

AGG_FUNCS = ("COUNT", "SUM", "AVG", "MIN", "MAX")


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", _strip_comments(sql)).strip().rstrip(";")


def _split_top_level(text: str, delimiter: str) -> list:
    """Split on delimiter, ignoring anything inside parentheses."""
    parts, depth, current = [], 0, []
    i, n = 0, len(text)
    d = delimiter.upper()
    while i < n:
        ch = text[i]
        if ch == "(":
            depth += 1
            current.append(ch)
            i += 1
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
            i += 1
        elif depth == 0 and text[i : i + len(d)].upper() == d:
            if len(d) == 1 and not d.isalnum():
                # punctuation delimiter (e.g. comma): split unconditionally
                parts.append("".join(current).strip())
                current = []
                i += len(d)
                continue
            before = text[i - 1] if i > 0 else " "
            after = text[i + len(d)] if i + len(d) < n else " "
            if not before.isalnum() and before != "_" and not after.isalnum() and after != "_":
                parts.append("".join(current).strip())
                current = []
                i += len(d)
                continue
            current.append(ch)
            i += 1
        else:
            current.append(ch)
            i += 1
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def extract_ctes(sql: str) -> tuple:
    """Return (dict of cte_name -> body, remaining sql)."""
    ctes = {}
    m = re.match(r"(?i)^\s*WITH\s+", sql)
    if not m:
        return ctes, sql
    rest = sql[m.end():]
    # peel off name AS (...) blocks
    while True:
        m = re.match(r"\s*(\w+)\s+AS\s*\(", rest, re.IGNORECASE)
        if not m:
            break
        name = m.group(1)
        depth, i = 0, m.end() - 1
        for j in range(m.end() - 1, len(rest)):
            if rest[j] == "(":
                depth += 1
            elif rest[j] == ")":
                depth -= 1
                if depth == 0:
                    i = j
                    break
        ctes[name] = rest[m.end():i].strip()
        rest = rest[i + 1 :]
        m2 = re.match(r"\s*,", rest)
        if m2:
            rest = rest[m2.end():]
        else:
            break
    return ctes, rest.strip()


def extract_clause(sql: str, keyword: str, stop_words: tuple) -> str:
    pattern = re.compile(r"(?i)\b" + keyword + r"\b(.*?)(?=" + "|".join(r"\b" + w + r"\b" for w in stop_words) + r"|$)", re.DOTALL)
    m = pattern.search(sql)
    return m.group(1).strip() if m else ""


def parse_tables(from_text: str, join_texts: list) -> list:
    tables = []

    def add_table(chunk: str, how: str):
        chunk = chunk.strip()
        m = re.match(r"(?i)^([\w.]+)(?:\s+(?:AS\s+)?(\w+))?", chunk)
        if m:
            tables.append({"table": m.group(1), "alias": m.group(2) or "", "via": how})

    if from_text:
        for part in _split_top_level(from_text, ","):
            if re.match(r"(?i)^\s*\(", part.strip()):
                continue  # subquery in FROM; covered separately
            add_table(part, "FROM")
    for jt in join_texts:
        m = re.match(r"(?i)^\s*(\w[\w\s]*?JOIN)\s+(.*)$", jt.strip(), re.DOTALL)
        if m:
            join_type, rest = m.group(1).strip(), m.group(2)
            on_split = _split_top_level(rest, "ON")
            add_table(on_split[0], join_type)
    return tables


def parse_joins(sql: str) -> list:
    out = []
    for m in re.finditer(r"(?i)\b((?:LEFT|RIGHT|FULL|INNER|CROSS)?\s*(?:OUTER\s+)?JOIN)\b(.*?)(?=\b(?:LEFT|RIGHT|FULL|INNER|CROSS)?\s*(?:OUTER\s+)?JOIN\b|\bWHERE\b|\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b|$)", sql, re.DOTALL):
        out.append(m.group(1).strip() + " " + m.group(2).strip())
    return out


def describe_condition(cond: str) -> str:
    cond = cond.strip()
    m = re.match(r"(?i)^([\w.]+)\s*(=|<>|!=|>=|<=|>|<|LIKE|IN|IS\s+NULL|IS\s+NOT\s+NULL)\s*(.*)$", cond, re.DOTALL)
    if not m:
        return cond
    col, op, val = m.group(1), m.group(2).upper(), m.group(3).strip()
    op_words = {
        "=": "equals", "<>": "is not", "!=": "is not",
        ">": "is greater than", "<": "is less than",
        ">=": "is at least", "<=": "is at most",
        "LIKE": "matches the pattern", "IN": "is one of",
        "IS NULL": "has no value", "IS NOT NULL": "has a value",
    }
    if op.startswith("IS"):
        return f"{col} {op_words.get(op, op)}"
    return f"{col} {op_words.get(op, op)} {val}"


def explain(sql: str) -> str:
    sql = _normalize(sql)
    if not sql:
        return "Empty query."
    lines = []
    ctes, main = extract_ctes(sql)

    if ctes:
        lines.append(f"Defines {len(ctes)} CTE{'s' if len(ctes) > 1 else ''}: {', '.join(ctes.keys())}.")

    upper = main.upper()
    distinct = bool(re.search(r"(?i)^\s*SELECT\s+DISTINCT\b", main))
    stops = ("FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING", "LIMIT")
    select_text = extract_clause(main, "SELECT", stops)
    select_text = re.sub(r"(?i)^\s*DISTINCT\s+", "", select_text)
    cols = _split_top_level(select_text, ",")
    n_cols = len(cols)

    aggs = sorted({f for f in AGG_FUNCS if re.search(r"\b" + f + r"\s*\(", select_text, re.IGNORECASE)})
    joins = parse_joins(main)
    join_texts = [re.sub(r"(?i)^\s*(\w[\w\s]*?JOIN)\s+", "", j) for j in joins]
    from_text = extract_clause(main, "FROM", ("WHERE", "GROUP BY", "ORDER BY", "HAVING", "LIMIT") + tuple())
    # re-extract FROM stopping before JOINs too
    from_text = re.split(r"(?i)\b(?:LEFT|RIGHT|FULL|INNER|CROSS)?\s*(?:OUTER\s+)?JOIN\b", from_text)[0].strip()
    tables = parse_tables(from_text, joins)
    where_text = extract_clause(main, "WHERE", ("GROUP BY", "ORDER BY", "HAVING", "LIMIT"))
    group_text = extract_clause(main, "GROUP BY", ("ORDER BY", "HAVING", "LIMIT"))
    having_text = extract_clause(main, "HAVING", ("ORDER BY", "LIMIT"))
    order_text = extract_clause(main, "ORDER BY", ("LIMIT",))
    limit_m = re.search(r"(?i)\bLIMIT\s+(\d+)", main)

    # --- plain-English summary ---
    if re.match(r"(?i)^\s*SELECT", main):
        what = "distinct rows" if distinct else "rows"
        summary = f"Retrieves {what}"
        if aggs:
            summary += f" with aggregations ({', '.join(a.lower() for a in aggs)})"
        if tables:
            tnames = [t["table"] for t in tables]
            summary += f" from {', '.join(tnames)}"
        if joins:
            jtypes = sorted({re.match(r'(?i)^\s*((?:LEFT|RIGHT|FULL|INNER|CROSS)?\s*(?:OUTER\s+)?JOIN)', j).group(1).strip().upper() for j in joins})
            summary += f" using {', '.join(jtypes)}"
        if where_text:
            conds = _split_top_level(where_text, "AND")
            summary += f", filtered to {len(conds)} condition{'s' if len(conds) > 1 else ''}"
        if group_text:
            summary += f", grouped by {group_text}"
        if order_text:
            summary += f", ordered by {order_text}"
        if limit_m:
            summary += f", limited to {limit_m.group(1)} rows"
        lines.append(summary + ".")
    elif upper.startswith("INSERT"):
        lines.append("Inserts data into a table.")
    elif upper.startswith("UPDATE"):
        lines.append("Updates existing rows in a table.")
    elif upper.startswith("DELETE"):
        lines.append("Deletes rows from a table.")
    else:
        lines.append("Statement type not recognized as SELECT/INSERT/UPDATE/DELETE.")

    # --- details ---
    lines.append("")
    lines.append("Details")
    lines.append("-------")
    if tables:
        lines.append("Tables:")
        for t in tables:
            alias = f" (aliased as {t['alias']})" if t["alias"] else ""
            lines.append(f"  - {t['table']}{alias} [{t['via']}]")
    lines.append(f"Columns selected: {n_cols}")
    for c in cols[:10]:
        lines.append(f"  - {c.strip()}")
    if n_cols > 10:
        lines.append(f"  ... and {n_cols - 10} more")
    if where_text:
        lines.append("Filters:")
        for cond in _split_top_level(where_text, "AND"):
            for sub in _split_top_level(cond, "OR"):
                lines.append(f"  - {describe_condition(sub)}")
    if group_text:
        lines.append(f"Grouped by: {group_text}")
    if having_text:
        lines.append(f"Post-aggregation filter (HAVING): {having_text}")
    if order_text:
        lines.append(f"Ordered by: {order_text}")
    if limit_m:
        lines.append(f"Row limit: {limit_m.group(1)}")

    subq = len(re.findall(r"\(\s*SELECT", main, re.IGNORECASE))
    if subq:
        lines.append(f"Contains {subq} subquer{'ies' if subq > 1 else 'y'} (nested SELECT inside parentheses).")

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Explain a SQL query in plain English.")
    ap.add_argument("file", nargs="?", help="Path to a .sql file")
    ap.add_argument("--query", help="SQL query string (alternative to file)")
    args = ap.parse_args()

    if args.query:
        sql = args.query
    elif args.file:
        with open(args.file) as fh:
            sql = fh.read()
    else:
        ap.print_help()
        sys.exit(1)

    print(explain(sql))


if __name__ == "__main__":
    main()
