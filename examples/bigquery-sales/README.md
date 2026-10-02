# Sample BigQuery data

Test runs of agents with a BigQuery connection query these tables in DuckDB instead of BigQuery. One JSON file per
table: `bigquery/<dataset>/<table>.json`. Made up; nothing here is real.

- `sales_processed.monthly_trend`, `revenue_by_region`, `revenue_by_product`: shaped like the real tables of the same
  names, and like them they reconcile: $543,673.80 over 731 orders in all three (the sample year has one weak month, August, for monthly-revenue-watch to catch)
- `sales_processed.orders`: 120 orders
- `credit.customer_limits`

`sales-load-check.agent.yaml` is the ETL example (see the main README): it runs when a Pub/Sub message says
`sales_processed` finished loading, checks the load, and reports what moved. `replay-load-check.yaml` scripts its two
model steps for test runs on this sample.

`sample-data/gcs/sales-landing/orders/` holds two days of landing files for Cloud Storage steps: a CSV for
2026-09-30, and JSON lines for 2026-10-01 with one order missing its amount and one order id twice, for checks to catch.
