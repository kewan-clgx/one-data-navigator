-- Curated live-demo dataset (docs/config/config_demo_tenant.md, P9): a snapshot of Google's fictional "theLook"
-- e-commerce store (bigquery-public-data.thelook_ecommerce) with table and column descriptions EasyQuery reads.
-- Person-like fields (names, emails, street addresses, coordinates) are left out. Re-run to refresh the snapshot:
--   bq --project_id=datahop-514819 query --use_legacy_sql=false < deploy/demo/easyquery_demo.sql

CREATE OR REPLACE TABLE `datahop-514819.easyquery_demo.orders` (
  order_id INT64 OPTIONS (description = 'Order identifier.'),
  customer_id INT64 OPTIONS (description = 'The customer who placed the order (customers.customer_id).'),
  status STRING OPTIONS (description = 'Processing, Shipped, Complete, Cancelled or Returned.'),
  created_at TIMESTAMP OPTIONS (description = 'When the order was placed (UTC).'),
  shipped_at TIMESTAMP OPTIONS (description = 'When it shipped; empty if not shipped yet.'),
  delivered_at TIMESTAMP OPTIONS (description = 'When it was delivered; empty if not delivered.'),
  returned_at TIMESTAMP OPTIONS (description = 'When it was returned; empty unless returned.'),
  num_of_item INT64 OPTIONS (description = 'Number of items in the order.')
)
OPTIONS (description = 'Demo store orders: one row per customer order. Revenue is in order_items.sale_price.')
AS SELECT order_id, user_id, status, created_at, shipped_at, delivered_at, returned_at, num_of_item
FROM `bigquery-public-data.thelook_ecommerce.orders`;

CREATE OR REPLACE TABLE `datahop-514819.easyquery_demo.order_items` (
  order_item_id INT64 OPTIONS (description = 'Line item identifier.'),
  order_id INT64 OPTIONS (description = 'The order this item belongs to (orders.order_id).'),
  customer_id INT64 OPTIONS (description = 'The customer (customers.customer_id).'),
  product_id INT64 OPTIONS (description = 'The product sold (products.product_id).'),
  status STRING OPTIONS (description = 'Processing, Shipped, Complete, Cancelled or Returned (per item).'),
  created_at TIMESTAMP OPTIONS (description = 'When the item was ordered (UTC).'),
  shipped_at TIMESTAMP OPTIONS (description = 'When it shipped.'),
  delivered_at TIMESTAMP OPTIONS (description = 'When it was delivered.'),
  returned_at TIMESTAMP OPTIONS (description = 'When it was returned; empty unless returned.'),
  sale_price FLOAT64 OPTIONS (description = 'Price the customer paid for this item, in USD (revenue).')
)
OPTIONS (description = 'Demo store order lines: one row per item sold. Sum sale_price for revenue; exclude Cancelled and Returned for net sales.')
AS SELECT id, order_id, user_id, product_id, status, created_at, shipped_at, delivered_at, returned_at, sale_price
FROM `bigquery-public-data.thelook_ecommerce.order_items`;

CREATE OR REPLACE TABLE `datahop-514819.easyquery_demo.products` (
  product_id INT64 OPTIONS (description = 'Product identifier.'),
  name STRING OPTIONS (description = 'Product name.'),
  brand STRING OPTIONS (description = 'Brand.'),
  category STRING OPTIONS (description = 'Category, e.g. Jeans, Tops & Tees, Outerwear & Coats.'),
  department STRING OPTIONS (description = 'Men or Women.'),
  retail_price FLOAT64 OPTIONS (description = 'List price in USD.'),
  cost FLOAT64 OPTIONS (description = 'What the store paid for the product, in USD (margin = sale_price - cost).'),
  sku STRING OPTIONS (description = 'Stock keeping unit.'),
  distribution_center_id INT64 OPTIONS (description = 'Distribution center that stocks it (distribution_centers.distribution_center_id).')
)
OPTIONS (description = 'Demo store product catalog.')
AS SELECT id, name, brand, category, department, retail_price, cost, sku, distribution_center_id
FROM `bigquery-public-data.thelook_ecommerce.products`;

CREATE OR REPLACE TABLE `datahop-514819.easyquery_demo.customers` (
  customer_id INT64 OPTIONS (description = 'Customer identifier.'),
  age INT64 OPTIONS (description = 'Age in years.'),
  gender STRING OPTIONS (description = 'M or F.'),
  city STRING OPTIONS (description = 'City.'),
  state STRING OPTIONS (description = 'State or region.'),
  postal_code STRING OPTIONS (description = 'Postal code.'),
  country STRING OPTIONS (description = 'Country.'),
  traffic_source STRING OPTIONS (description = 'How the customer found the store: Search, Organic, Facebook, Email or Display.'),
  created_at TIMESTAMP OPTIONS (description = 'When the customer signed up (UTC).')
)
OPTIONS (description = 'Demo store customers (fictional; no names, emails or addresses).')
AS SELECT id, age, gender, city, state, postal_code, country, traffic_source, created_at
FROM `bigquery-public-data.thelook_ecommerce.users`;

CREATE OR REPLACE TABLE `datahop-514819.easyquery_demo.distribution_centers` (
  distribution_center_id INT64 OPTIONS (description = 'Distribution center identifier.'),
  name STRING OPTIONS (description = 'City and state of the distribution center.'),
  latitude FLOAT64 OPTIONS (description = 'Latitude.'),
  longitude FLOAT64 OPTIONS (description = 'Longitude.')
)
OPTIONS (description = 'Demo store warehouses that stock products.')
AS SELECT id, name, latitude, longitude
FROM `bigquery-public-data.thelook_ecommerce.distribution_centers`;
