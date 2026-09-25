# sql-explainer

Explain a SQL query in plain English. No dependencies, no API keys, no LLM.

## Problem

BI developers inherit long SQL queries — nested CTEs, layered joins, filters
buried three subqueries deep — and have to answer "what does this actually do?"
before they can fix, optimize, or rebuild it. Reading 200 lines of SQL to
summarize it for a stakeholder is slow, error-prone work.

## Solution

A stdlib-only CLI that parses a `SELECT` query and prints:

- a one-paragraph plain-English summary,
- every table (with aliases and join types),
- columns selected, filters translated to words ("order_total is greater than 10000"),
- GROUP BY / HAVING / ORDER BY / LIMIT,
- CTEs and subqueries it found.

## Quickstart

```bash
python explainer.py sample_queries.sql
```

Or pass a query directly:

```bash
python explainer.py --query "SELECT region, SUM(revenue) FROM sales GROUP BY region"
```

## Example output

```
Defines 1 CTE: monthly_sales.
Retrieves distinct rows with aggregations (count) from monthly_sales, dim.region_targets
using LEFT JOIN, filtered to 2 conditions, grouped by m.region, m.sales_month, m.revenue,
ordered by m.revenue DESC, limited to 50 rows.

Details
-------
Tables:
  - monthly_sales (aliased as m) [FROM]
  - dim.region_targets (aliased as t) [LEFT JOIN]
Columns selected: 4
  - m.region
  - m.sales_month
  - m.revenue
  - COUNT(*) AS months_reported
Filters:
  - m.revenue is greater than 10000
  - t.target_year equals 2024
Grouped by: m.region, m.sales_month, m.revenue
Ordered by: m.revenue DESC
Row limit: 50
```

## How it works

Regex-based, top-level-aware parsing: splits clauses while respecting
parentheses, so commas inside function calls and subqueries don't break
column or condition extraction. Handles CTEs (`WITH x AS (...)`), all common
join types, `DISTINCT`, aggregations, `HAVING`, and nested subqueries.

## Limitations

- Built for `SELECT` analysis; `INSERT`/`UPDATE`/`DELETE` get a one-line note.
- Very exotic syntax (window frames, PIVOT) is reported generically, not deeply parsed.
- It's a reader's aid, not a validator — it won't tell you the query is wrong.

## License

MIT — Pawan Patel, 2026.
