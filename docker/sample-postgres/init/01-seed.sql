-- Sample source database for PostgresConnector and gold-benchmark work.
CREATE SCHEMA sales;

CREATE TABLE sales.customers (
  customer_id  integer PRIMARY KEY,
  name         text NOT NULL,
  region       text NOT NULL,
  signup_date  date NOT NULL
);

CREATE TABLE sales.orders (
  order_id     integer PRIMARY KEY,
  customer_id  integer NOT NULL REFERENCES sales.customers (customer_id),
  order_date   date NOT NULL,
  amount       numeric(12, 2) NOT NULL
);

INSERT INTO sales.customers
SELECT g,
       'Customer ' || g,
       (ARRAY['West', 'East', 'Central', 'South'])[1 + g % 4],
       DATE '2024-01-01' + (g % 365)
FROM generate_series(1, 1000) AS g;

INSERT INTO sales.orders
SELECT g,
       1 + (g * 7) % 1000,
       DATE '2024-01-01' + (g % 540),
       round((10 + (g * 37) % 990)::numeric, 2)
FROM generate_series(1, 50000) AS g;

-- Least-privilege, read-only role the connector should use.
CREATE ROLE analyst_ro LOGIN PASSWORD 'analyst_ro_local_dev';
ALTER ROLE analyst_ro SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE sample TO analyst_ro;
GRANT USAGE ON SCHEMA sales TO analyst_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA sales TO analyst_ro;
