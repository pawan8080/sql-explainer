-- Monthly revenue by region: the kind of query BI developers inherit
WITH monthly_sales AS (
    SELECT
        region,
        DATE_TRUNC('month', order_date) AS sales_month,
        SUM(order_total) AS revenue
    FROM sales.orders
    WHERE order_date >= '2024-01-01'
      AND order_status <> 'cancelled'
    GROUP BY region, DATE_TRUNC('month', order_date)
)
SELECT DISTINCT
    m.region,
    m.sales_month,
    m.revenue,
    COUNT(*) AS months_reported
FROM monthly_sales m
LEFT JOIN dim.region_targets t
    ON t.region = m.region
WHERE m.revenue > 10000
  AND t.target_year = 2024
GROUP BY m.region, m.sales_month, m.revenue
ORDER BY m.revenue DESC
LIMIT 50;
