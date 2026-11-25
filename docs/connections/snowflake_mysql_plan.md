# Snowflake and MySQL connections: research and implementation plan

Status: Snowflake implementation in progress; MySQL remains proposed. Snowflake live-account verification is pending.

## Goal and scope

Add **Snowflake** and **MySQL** data connections alongside the existing BigQuery and PostgreSQL connections. Users should be able to configure and test a connection, browse permitted tables and views, inspect columns, preview and query data, and use the same connection in chat and the workspace.

Here, "dataset" means an externally hosted collection of tables exposed through a connection, not an imported copy. Snowflake supports multiple databases per connection; one MySQL database per connection remains a proposal. Both personal and organization-shared profiles are in scope. BigQuery and PostgreSQL behavior and existing profiles must remain unchanged.

The choices below are recommendations, not completed functionality. Confirm the deployment and authentication choices in [Open decisions](#open-decisions) before implementation.

## Current repository baseline

- The [connector SPI](../../backend/src/odn/platform/connectors/base.py) provides connection tests, catalog and column discovery, schema inspection, cost estimates, paged Arrow results and cancellation. [BigQuery](../../backend/src/odn/platform/connectors/bigquery.py) and [PostgreSQL](../../backend/src/odn/platform/connectors/postgres.py) already implement it.
- [Connection profiles](../../backend/src/odn/platform/connections/service.py) support personal and tenant-shared credentials, encrypted secret storage, allowlists, and an unsaved test flow. Platform connections are separately managed, visible across tenants and read-only to tenant users; do not make a new global connection automatically. [Tenant transactions](../../backend/src/odn/db/__init__.py) must include the user ID for personal profiles.
- [SQL policy](../../backend/src/odn/platform/policy/sql_policy.py) uses sqlglot and filters both queries and catalog paths. It currently recognizes only BigQuery, PostgreSQL and DuckDB, and compares identifiers case-insensitively. That comparison is unsafe for the new engines' case-sensitive identifiers.
- [Query service](../../backend/src/odn/platform/query/service.py) performs policy validation, estimation, result caps and auditing. Its error handling is Google-specific; the [search index](../../backend/src/odn/platform/catalog/search_index.py) likewise catches Google-specific errors. New driver failures need explicit, sanitized handling.
- The [Explorer](../../frontend/app/components/Explorer.tsx) currently branches between PostgreSQL and a BigQuery default for preview SQL. [Settings](../../frontend/app/components/Settings.tsx) currently treats anything other than PostgreSQL as BigQuery. Both require explicit dialect handling.
- The [connections table](../../backend/src/odn/db/migrations/0001_core.sql) restricts `kind` to BigQuery and PostgreSQL. Adding either kind requires a forward migration, not editing an applied migration. The existing catalog search expects root containers and their direct table children.

## Provider research

### Snowflake

- The [Python connector](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector) provides DB-API connections and cursors. [Connection settings](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect) include account identifier, user, warehouse, database, schema and optional role. Account identifiers omit the `snowflakecomputing.com` suffix. Enterprise authentication policies can require MFA.
- Each database has its own [INFORMATION_SCHEMA](https://docs.snowflake.com/en/sql-reference/info-schema). A session needs a running [warehouse](https://docs.snowflake.com/en/sql-reference/sql/use-warehouse) for queries. Warehouse usage can incur credits even for discovery or tests.
- [Unquoted identifiers resolve to uppercase; quoted identifiers preserve case](https://docs.snowflake.com/en/sql-reference/identifiers-syntax). For example, `orders` and `"orders"` may name different objects. Dots can occur inside quoted identifiers; the existing dot-separated allowlist cannot represent those safely.
- Snowflake supports [key-pair authentication and key rotation](https://docs.snowflake.com/en/user-guide/key-pair-auth). A dedicated service identity and restricted role are the recommended initial model; do not assume interactive user SSO works in a server-side connection.
- Cost is warehouse-credit based, not equivalent to BigQuery bytes billed. Snowflake documents [warehouse access, timeouts, auto-suspend and resource monitors](https://docs.snowflake.com/en/user-guide/cost-controlling-controls) as controls. `max_bytes_billed` and BigQuery dry-run estimates must not be presented as Snowflake credit limits.

### MySQL

- MySQL exposes [INFORMATION_SCHEMA](https://docs.oracle.com/cd/E17952_01/mysql-8.4-en/information-schema.html) metadata for databases, tables, views and columns, subject to the source account's privileges. Database and schema are synonyms in MySQL; model them as the same namespace.
- The Python driver must support the deployed server's authentication plugin, verified TLS, bounded timeouts and unbuffered fetching. Evaluate [MySQL Connector/Python](https://docs.oracle.com/cd/E17952_01/connector-python-en/connector-python-connectargs.html) against project dependency and license requirements; its [buffered cursor](https://docs.oracle.com/cd/E17952_01/connector-python-en/connector-python-api-mysqlcursorbuffered.html) loads the entire result set and is unsuitable for large results.
- Identifier case sensitivity depends on server and `lower_case_table_names` configuration; on many Unix servers `Orders` and `orders` are distinct ([MySQL identifier rules](https://docs.oracle.com/cd/E17952_01/mysql-8.4-en/identifier-case-sensitivity.html)). Never use unconditional case-insensitive allowlist matching.
- A [read-only transaction](https://docs.oracle.com/cd/E17952_01/mysql-8.4-en/commit.html) adds protection but still permits changes to temporary tables. Use an account with only required read privileges as the primary safeguard, and reject unsafe SQL and functions. TLS must validate the server identity; encryption without verification is not sufficient.
- [`KILL QUERY`](https://docs.oracle.com/cd/E17952_01/mysql-8.4-en/kill.html) is asynchronous and permissions restrict which sessions may be killed. Cancellation must target only the active query owned by this application, not an arbitrary or stale connection ID.

## Proposed connection contracts

| | Snowflake | MySQL |
|---|---|---|
| Profile kind and SQL dialect | `snowflake` | `mysql` |
| Non-secret configuration | Account identifier, user, warehouse, database, default schema, role; bounded timeout | Host, port (default 3306), database, user, TLS verification settings; bounded timeout |
| Credential, write-only | Service-user private key and optional passphrase, or password | Database password; certificate/key material only if explicitly supported |
| Root catalog / table path | Schemas from accessible databases `(database, schema)` / `(database, schema, table)` | Selected database `(database,)` / `(database, table)` |
| SQL default namespace | `(database, default_schema)` | `(database,)` |
| Allowlist | `database.schema.table`, with `*` per whole part | `database.table`, with `*` per whole part |
| Cost estimate | Unavailable initially unless a validated provider-specific estimate is added; no fabricated bytes or credit estimate | Optional `EXPLAIN` row estimate only if safe and supported; otherwise unavailable |
| Source-side control | Restricted role, warehouse controls and statement timeout | SELECT-only account, read-only transaction and server/client timeouts |

Snowflake lists schemas from multiple databases at the root as `DATABASE.SCHEMA` and their tables below, retaining full three-part table paths and the existing Explorer tree shape. One MySQL database per connection likewise makes the existing two-level catalog shape usable. Both should include permitted views. The source user's permissions remain authoritative; an allowlist is an additional application restriction, not a substitute for source grants. See [Snowflake setup and limitations](snowflake.md).

### Identifier and policy requirements

Before enabling either kind, extend the policy and catalog matching together. Preserve SQLglot's quoted/unquoted identifier information and apply the provider's actual resolution rules; reject ambiguous references rather than broaden access. For MySQL, conservatively use exact-case matching for allowlist names initially and validate behavior against supported `lower_case_table_names` modes; stricter matches are preferable to unauthorized matches. For Snowflake, resolve unquoted names to uppercase and preserve quoted names. Test same-spelling/different-case objects end to end.

The current dotted pattern format cannot distinguish identifier components containing dots or a literal `*` from separators/wildcards. For the first release, explicitly reject unsupported ambiguous identifiers at profile validation, catalog exposure and query policy; document the restriction. A structured path format and migration can be designed later if such names are required. Quote and escape each identifier component separately when generating preview or metadata SQL; bind metadata values where supported.

Reject multiple statements, writes, DDL, `CALL`, locking reads, external access, and provider-specific side-effecting functions. Assess UDFs, views and other indirect access explicitly: AST analysis cannot establish their behavior or all underlying objects. Source-level grants and restrictions are essential, and untrusted UDF/external access must not be assumed safe merely because a statement starts with `SELECT`.

### Credentials and tenancy

- Reuse tenant/user RLS and the existing per-connection authenticated encryption and write-only secret flow; do not put passwords, private keys or passphrases in public config, logs, traces, errors or responses. Preserve existing secrets on edit only for the same provider; reject cross-kind edits unless an explicit safe replacement design is implemented.
- Prefer a Snowflake service identity using key-pair auth and a narrowly scoped role. Password authentication is also supported when the account policy permits it; interactive MFA, OAuth and SSO are not supported. Do not silently fall back to another identity.
- Require verified TLS for MySQL by default. Choose a safe method for custom CA provisioning and server-name verification, including when a tunnel connects locally. SSH tunneling can reuse existing fingerprint verification only after validating hostname/TLS behavior; it is not assumed to be in the first release.
- Decide permitted network destinations for user-supplied MySQL hosts, including private databases, metadata endpoints, DNS rebinding and egress controls. Enforce the approved policy on every connection and tunnel hop; do not equate unrestricted private-network access with a safe default.

## Implementation phases

1. **Agree on deployment assumptions.** Confirm supported Snowflake auth, MySQL versions/networking and one-database scope. Select maintained drivers compatible with Python 3.12 and existing Arrow dependencies; perform dependency/license review. Add drivers to the API extra and update `backend/uv.lock` with `uv lock` when manifests change.
2. **Foundation and authorization.** Add a forward migration to extend the connection-kind constraint while retaining existing rows and RLS. Add typed profile validation, secrets and explicit connector dispatch; reject unknown kinds. Extend dialect parsing and allowlist semantics as above, including catalog checks. Keep existing BigQuery/Postgres matching behavior unless a separately reviewed migration is needed. Add least-privilege setup guidance for both providers.
3. **Snowflake connector.** Implement tests, root schema/table/view listing, single-table schema and bulk column discovery scoped to the configured database. Stream bounded Arrow-compatible results, preserve types and nulls, set statement timeout and an appropriate non-sensitive query tag. Map errors to safe actionable diagnostics. Establish active-query lifecycle and cancellation semantics before advertising cancellation; otherwise explicitly report it unsupported.
4. **MySQL connector.** Implement the same SPI using parameterized metadata queries, a source read-only account, a read-only transaction and nonbuffered fetches. Bound both execution and result retrieval, close resources on failure or early truncation, and handle cancellation safely. Do not copy PostgreSQL's full `fetchall()` behavior for query results. Verify driver/server timeout and cancellation support instead of claiming a universal guarantee.
5. **Application integration.** Add personal and authorized shared create/edit/test forms, field-specific help and clear auth/TLS errors. Integrate catalog filtering, search indexing, schema/dictionary, previews, query/audit and agent dialect instructions. Add explicit provider error handling for metadata and query failures; mark unavailable estimates honestly. Keep a configured warehouse or connection from being silently changed by user SQL.
6. **Validation and rollout.** Run focused policy, profile, connector, API and frontend tests plus real-provider integration checks below. Deploy migration before creating new profiles; enable each provider only after its acceptance checks pass. Roll back by disabling creation/use of the new kind while preserving records; never drop credentials or rewrite existing profiles as a rollback shortcut.

## Acceptance and test matrix

- **Isolation and secrets:** personal connections invisible to other users and tenants; shared management requires the existing role; global profiles remain operator-managed. Test saved/unsaved flows, secret replacement and omitted-secret edits, cross-kind rejection, and no secret in API output or errors.
- **Policy:** for each engine test fully and partially qualified names, quoted identifiers, same-name case variants, wildcards, denied tables, CTEs/joins, system catalogs, multiple statements, writes, locking and side-effecting constructs. Denied objects must not appear through catalog, schema, dictionary or search, or execute through chat/Explorer.
- **Discovery:** list permitted databases/schemas, tables, views and columns, including empty containers and permission-denied/partial metadata. Verify identifier escaping and unsupported-name rejection; do not leak unallowlisted columns or metadata.
- **Execution:** empty results, nulls, decimals, timestamps, Unicode and large values; multi-batch result streaming, row and byte caps, timeouts, early termination, resource cleanup and sanitized failure/audit records. Verify preview SQL uses correct per-part quoting and no BigQuery-only sampling for either engine.
- **Snowflake integration:** use a dedicated test account/database/schema, warehouse and read-only role. Test connection and key rotation, role and warehouse errors, quoted/unquoted case distinctions, permissions, timeout and query/cancellation lifecycle. Confirm the warehouse/credit impact of metadata and tests; no BigQuery byte-billed promise.
- **MySQL integration:** use a disposable supported MySQL server with a SELECT-only test account and a verified TLS certificate. Test wrong credentials, wrong CA/hostname, denied writes including indirect cases, database case behavior, large unbuffered results, timeout and safe cancellation. Test tunneling separately if included.
- **Regression:** existing BigQuery and PostgreSQL profiles, policy, catalog, queries, UI and RLS tests continue passing. Apply the migration to a database with existing profiles and verify they remain usable.

Mocked tests alone cannot establish provider IAM, TLS, driver streaming, server-side timeouts or billing behavior. Record any unavailable real-provider checks as release blockers or explicit limitations, not as passed tests.

## Open decisions

1. Snowflake decisions: multiple databases, service-user key-pair or password authentication, implement Snowflake first. A real test account will be configured later.
2. Is one database per MySQL connection sufficient? Which MySQL versions and hosting environments must be supported, and are private networking, SSH tunnels, custom CA or mutual TLS required at launch?
3. What network egress policy should govern user-configured database hosts? Who approves destinations?

## References

Provider documentation linked above is the research baseline; verify driver compatibility and deployment-specific policies during implementation. Existing BigQuery setup is documented in [BigQuery public data](../config/bigquery-public-data-setting.md); it is not a specification for Snowflake billing or MySQL security.
