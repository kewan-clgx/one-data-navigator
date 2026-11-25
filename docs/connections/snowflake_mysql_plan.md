# Snowflake and MySQL connections: research and implementation plan

Status (2026-11-25): Snowflake is implemented, deployed, and user-verified end to end on production. MySQL is deployed in release `4f18039` and verified against disposable MySQL 8.0 and 8.4 servers; a customer-hosted MySQL test remains pending. The same release adds [Oracle](oracle.md), live-tested on Database Free but not yet on Oracle 19c. See [Snowflake verification](snowflake.md#delivery-and-verification-status) and [MySQL verification](mysql.md#delivery-and-verification-status) for exact checks and limitations.

## Goal and scope

Add **Snowflake** and **MySQL** data connections alongside the existing BigQuery and PostgreSQL connections. Users should be able to configure and test a connection, browse permitted tables and views, inspect columns, preview and query data, and use the same connection in chat and the workspace.

Here, "dataset" means an externally hosted collection of tables exposed through a connection, not an imported copy. Snowflake and MySQL support multiple databases per connection, with a default namespace for unqualified SQL. Both personal and organization-shared profiles are in scope. BigQuery and PostgreSQL behavior and existing profiles must remain unchanged.

Delivered behavior is documented in [Snowflake connections](snowflake.md) and [MySQL connections](mysql.md). Confirmed deployment/authentication choices are recorded below; deployment-specific acceptance remains necessary.

## Current repository baseline

- The [connector SPI](../../backend/src/odn/platform/connectors/base.py) provides connection tests, catalog and column discovery, schema inspection, cost estimates, paged Arrow results and cancellation. [BigQuery](../../backend/src/odn/platform/connectors/bigquery.py), [PostgreSQL](../../backend/src/odn/platform/connectors/postgres.py), [Snowflake](../../backend/src/odn/platform/connectors/snowflake.py), and [MySQL](../../backend/src/odn/platform/connectors/mysql.py) implement it; Snowflake/MySQL explicitly report cancellation unsupported and provide no cost estimate.
- [Connection profiles](../../backend/src/odn/platform/connections/service.py) support personal and tenant-shared credentials, encrypted secret storage, allowlists, and an unsaved test flow. Platform connections are separately managed, visible across tenants and read-only to tenant users; do not make a new global connection automatically. [Tenant transactions](../../backend/src/odn/db/__init__.py) must include the user ID for personal profiles.
- [SQL policy](../../backend/src/odn/platform/policy/sql_policy.py) uses sqlglot and filters queries and catalog paths for BigQuery, PostgreSQL, Snowflake, MySQL and DuckDB. Snowflake resolves unquoted names to uppercase and preserves quoted case; MySQL preserves exact catalog spelling. Existing engine matching is unchanged.
- [Query service](../../backend/src/odn/platform/query/service.py) performs policy validation, estimation, result caps and auditing. It and the [search index](../../backend/src/odn/platform/catalog/search_index.py) handle sanitized `ConnectorError` failures as well as Google errors; MySQL translates its driver failures into this shared contract.
- [Explorer](../../frontend/app/components/Explorer.tsx) and [Settings](../../frontend/app/components/Settings.tsx) handle Snowflake and MySQL explicitly, including per-component quoted SQL and connection forms.
- The [Snowflake migration](../../backend/src/odn/db/migrations/0029_snowflake_connections.sql), [MySQL migration](../../backend/src/odn/db/migrations/0030_mysql_connections.sql) and [Oracle migration](../../backend/src/odn/db/migrations/0031_oracle_connections.sql) are applied in production. Forward migrations extend kinds without editing applied migrations. The catalog tree remains root containers and direct table children.

## Snowflake completed milestone

- Implemented the connector, encrypted credential/profile flows, multi-database catalog paths, case-aware SQL policy, query integration, and personal/shared settings UI.
- Added password and key-pair authentication plus local browser TOML paste/upload, including credential retention on same-method edits and required replacement on authentication-method changes.
- Deployed the Snowflake driver, schema migration and application integration to production.
- User confirmed all connection checks passed and then confirmed end-to-end operation after the browse/query verification prompt.
- Added setup, least-privilege, billing, TLS and role-grant troubleshooting guidance. The improved `390186` diagnostic is deployed in release `4f18039`.

The remaining live edge-case checks and unsupported capabilities are listed in [Snowflake delivery and verification](snowflake.md#delivery-and-verification-status). This milestone does not claim that every acceptance case below has been exercised live.

## Provider research

### Snowflake

- The [Python connector](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector) provides DB-API connections and cursors. [Connection settings](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect) include account identifier, user, warehouse, database, schema and optional role. Account identifiers omit the `snowflakecomputing.com` suffix. Enterprise authentication policies can require MFA.
- Each database has its own [INFORMATION_SCHEMA](https://docs.snowflake.com/en/sql-reference/info-schema). A session needs a running [warehouse](https://docs.snowflake.com/en/sql-reference/sql/use-warehouse) for queries. Warehouse usage can incur credits even for discovery or tests.
- [Unquoted identifiers resolve to uppercase; quoted identifiers preserve case](https://docs.snowflake.com/en/sql-reference/identifiers-syntax). For example, `orders` and `"orders"` may name different objects. Dots can occur inside quoted identifiers; the existing dot-separated allowlist cannot represent those safely.
- Snowflake supports [key-pair authentication and key rotation](https://docs.snowflake.com/en/user-guide/key-pair-auth). A dedicated service identity and restricted role are the recommended initial model; do not assume interactive user SSO works in a server-side connection.
- Cost is warehouse-credit based, not equivalent to BigQuery bytes billed. Snowflake documents [warehouse access, timeouts, auto-suspend and resource monitors](https://docs.snowflake.com/en/user-guide/cost-controlling-controls) as controls. `max_bytes_billed` and BigQuery dry-run estimates must not be presented as Snowflake credit limits.

### MySQL

- MySQL exposes [INFORMATION_SCHEMA](https://docs.oracle.com/cd/E17952_01/mysql-8.4-en/information-schema.html) metadata for databases, tables, views and columns, subject to the source account's privileges. Database and schema are synonyms in MySQL; model them as the same namespace.
- The selected driver is MIT-licensed PyMySQL with its RSA extra, explicit verified SSL context and unbuffered cursor. See [driver research and execution design](mysql.md#driver-and-execution-research). Buffered fetching is not used for user query results.
- Identifier case sensitivity depends on server and `lower_case_table_names` configuration; on many Unix servers `Orders` and `orders` are distinct ([MySQL identifier rules](https://docs.oracle.com/cd/E17952_01/mysql-8.4-en/identifier-case-sensitivity.html)). Never use unconditional case-insensitive allowlist matching.
- A [read-only transaction](https://docs.oracle.com/cd/E17952_01/mysql-8.4-en/commit.html) adds protection but still permits changes to temporary tables. Use an account with only required read privileges as the primary safeguard, and reject unsafe SQL and functions. TLS must validate the server identity; encryption without verification is not sufficient.
- [`KILL QUERY`](https://docs.oracle.com/cd/E17952_01/mysql-8.4-en/kill.html) is asynchronous and permissions restrict which sessions may be killed. Cancellation must target only the active query owned by this application, not an arbitrary or stale connection ID.

## Implemented connection contracts

| | Snowflake | MySQL |
|---|---|---|
| Profile kind and SQL dialect | `snowflake` | `mysql` |
| Non-secret configuration | Account identifier, user, warehouse, database, default schema, role; bounded timeout | Host, port (default 3306), database, user, TLS verification settings; bounded timeout |
| Credential, write-only | Service-user private key and optional passphrase, or password | Database password; certificate/key material only if explicitly supported |
| Root catalog / table path | Schemas from accessible databases `(database, schema)` / `(database, schema, table)` | Accessible databases `(database,)` / `(database, table)` |
| SQL default namespace | `(database, default_schema)` | `(database,)` |
| Allowlist | `database.schema.table`, with `*` per whole part | `database.table`, with `*` per whole part |
| Cost estimate | Unavailable; no fabricated bytes or credit estimate | Unavailable; no fabricated bytes or EXPLAIN estimate |
| Source-side control | Restricted role, warehouse controls and statement timeout | SELECT-only account, read-only transaction and server/client timeouts |

Snowflake lists schemas from multiple databases at the root as `DATABASE.SCHEMA` and their tables below, retaining full three-part table paths and the existing Explorer tree shape. MySQL lists databases at the root and their tables below. Both include permitted views. The source user's permissions remain authoritative; an allowlist is an additional application restriction, not a substitute for source grants. See [Snowflake setup and limitations](snowflake.md) and [MySQL design and setup](mysql.md).

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
3. **Snowflake connector (delivered).** Tests, root schema/table/view listing, single-table schema and bulk column discovery across accessible databases are implemented. Results use Arrow batches with application row/byte limits, statement timeout and a non-sensitive query tag. Driver failures are sanitized; query cancellation is explicitly unsupported.
4. **MySQL connector (deployed).** Uses parameterized, case-sensitive metadata queries, approved/pinned endpoints, verified TLS, read-only transactions, SELECT execution/socket timeouts and nonbuffered Arrow batches. Early truncation disconnects without draining results. Cancellation and cost estimation are explicitly unsupported.
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

## Confirmed decisions

1. Snowflake decisions are implemented: multiple databases and service-user key-pair or password authentication. A real account has now passed connection checks and user-reported end-to-end use.
2. Confirmed MySQL scope: multiple databases, MySQL 8.0/8.4 (no MariaDB initially), username/password, mandatory verified TLS with optional custom CA, and direct approved public/private endpoints. SSH tunnels, mutual TLS and cloud IAM are excluded initially.
3. Confirmed approval model: operator-managed exact endpoint and destination CIDR allowlists, checked on each connection with DNS pinning. See [MySQL approved destinations](mysql.md#approved-destinations).

## MySQL implementation handoff

The implementation, forward migration, encrypted password flow, SQL policy and application/UI integration are deployed in release `4f18039`. The combined release passed 255 focused backend tests and the frontend build; disposable verified-TLS MySQL 8.0/8.4 integration checks passed earlier. [MySQL verification](mysql.md#delivery-and-verification-status) distinguishes these checks from customer-hosted verification.

Next: configure an operator-approved customer/test endpoint and CIDRs, and verify its real grants, CA chain and multi-database allowlist. The production feature is installed, but no customer MySQL connection has been configured. Preserve existing Snowflake, BigQuery and PostgreSQL behavior.

## References

Provider documentation linked above is the research baseline; verify driver compatibility and deployment-specific policies during implementation. Existing BigQuery setup is documented in [BigQuery public data](../config/bigquery-public-data-setting.md); it is not a specification for Snowflake billing or MySQL security.
