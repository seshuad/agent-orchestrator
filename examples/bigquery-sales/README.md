# Sample BigQuery data

Test runs of agents with a BigQuery connection query these tables in DuckDB instead of BigQuery. One JSON file per
table: `bigquery/<dataset>/<table>.json`. Made up; nothing here is real.

- `sales_processed.monthly_trend`, `revenue_by_region`, `revenue_by_product`: shaped like the real tables of the same
  names (the sample year has one weak month, August, for monthly-revenue-watch to catch)
- `sales_processed.orders`: 120 orders
- `credit.customer_limits`
