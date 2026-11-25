# Oracle connections: research and implementation plan

Status: implemented locally, not committed or deployed to production. Research/design was written and scope confirmed before implementation. Existing Snowflake and MySQL changes remain intact.

## Confirmed initial scope

- Official `python-oracledb` driver in Thin mode, without Oracle Instant Client libraries.
- Oracle 19c as the required customer version, with Oracle Database Free for local verification. Free testing alone will not establish live 19c compatibility.
- Username/password, direct verified TCPS (TLS), optional PEM CA bundle, and personal/organization-shared profiles.
- One Oracle service/PDB per connection, with multiple accessible schemas. Separate PDBs/services use separate connections; do not implement cross-database access through database links.
- Host, listener port, service name, username, default schema and explicit `schema.table` allowlist. Do not accept arbitrary Oracle Net descriptors, filesystem configuration paths, SYSDBA modes or proxy authentication in the initial form.
- Bounded read-only results, catalog/schema discovery, Explorer preview, chat queries, encrypted write-only passwords, and sanitized errors consistent with existing connectors.

The user confirmed this scope, direct endpoints without a RAC/SCAN requirement, operator-approved public/private endpoints and IP ranges as for MySQL, and adding a restricted CONNECT proxy. Oracle Native Network Encryption, wallets/mTLS, RAC/SCAN, SID-only legacy deployments and older database versions are outside the initial scope.

## Provider research

### Driver, authentication and compatibility

[python-oracledb's feature matrix](https://python-oracledb.readthedocs.io/en/latest/user_guide/appendix_a.html) distinguishes Thin and Thick mode:

- Thin connects directly without Oracle Client libraries and supports Oracle Database 12.1 and later at the driver level. This does not mean every such version will be an application-supported or live-tested release.
- Thick requires separately installed Oracle Client libraries. Native Network Encryption, external authentication and Kerberos/RADIUS differ from Thin capabilities; do not claim Thin compatibility for those deployments.
- Both TCPS and Autonomous Database connections are supported, but one-way TLS differs from wallet-based mutual TLS. Wallet support would need encrypted storage of client key material, bounded/safe import and a secure temporary-file lifecycle.
- The driver is [dual-licensed under UPL 1.0 or Apache 2.0](https://github.com/oracle/python-oracledb/blob/main/LICENSE.txt). Database/server images and Oracle Client libraries have separate licensing considerations.

Use structured [ConnectParams](https://python-oracledb.readthedocs.io/en/latest/api_manual/connect_params.html) with a service name, rather than combining passwords and connection descriptors. The default protocol is `tcp`, so explicitly require `tcps`. Enable certificate verification and `ssl_server_dn_match`; hostname matching applies unless an explicit certificate distinguished name is configured. Never silently switch to plaintext or disable verification.

### Schemas, identifiers and metadata

An Oracle connection selects a service, often associated with a PDB. Schema owners are the appropriate two-level catalog namespace:

| Contract | Implemented value |
|---|---|
| Connection kind / SQLglot dialect | `oracle` |
| Root nodes / table paths | `(schema,)` / `(schema, table)` |
| Default namespace | `(default_schema,)` |
| Allowlist | `schema.table`, whole-component `*` |
| Generated SQL | `"SCHEMA"."TABLE"` and `FETCH FIRST n ROWS ONLY` for supported modern versions |
| Cost estimate / external cancellation | Unavailable initially |

[Oracle identifier rules](https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/Database-Object-Names-and-Qualifiers.html) distinguish uppercase-resolved unquoted names from exact-case quoted names. Length limits depend on the database's `COMPATIBLE` setting (30 or 128 bytes for most identifiers). Initially support simple ASCII schema/table components, preserve catalog case in allowlists/favorites/usage, and reject dots, `@`, wildcard characters inside literal identifiers and ambiguous paths.

Use privilege-scoped metadata such as [ALL_TABLES](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/ALL_TABLES.html), `ALL_VIEWS`, `ALL_TAB_COLUMNS`, `ALL_TAB_COMMENTS` and `ALL_COL_COMMENTS`, binding owner/table filters. Do not require DBA catalog privileges or expose system owners. Table statistics are estimates and may be missing or stale.

Synonyms can redirect names to other objects or remote databases. Catalog queries join `ALL_OBJECTS` to `ALL_USERS` and exclude Oracle-maintained owners, with only table/view objects eligible. Before executing user SQL, referenced objects must exist as accessible non-system tables/views; synonym names are rejected even under a wildcard allowlist. Reject database links (`table@link`), dynamic performance views, system catalogs, PL/SQL, writes, locking reads, sequence access, user-defined/qualified functions and side-effecting/network/file-access packages. `SYS.DUAL` is used only by the connector's fixed diagnostic; user SQL cannot access it. Source owners and grants remain trusted: authorized views can themselves depend on other objects.

### Read-only execution, result types and timeouts

[SET TRANSACTION READ ONLY](https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/SET-TRANSACTION.html) must start the transaction and provides transaction-level read consistency. It is not a complete authorization boundary: Oracle documents exceptions and it is not supported for user `SYS`. Require a dedicated non-administrative source user with `CREATE SESSION` and only intended object read grants. Do not give it write, DDL, broad catalog or unnecessary package/routine privileges.

The implementation uses bounded cursor `fetchmany(100)` batches, disabled prefetch, a stable metadata-derived Arrow schema and existing row/byte caps. It does not use [fetch_df_batches](https://python-oracledb.readthedocs.io/en/latest/user_guide/dataframes.html), whose inferred numeric conversions require additional precision analysis.

NUMBER fetches use `Decimal`, never float. Constrained numbers map to Arrow decimal128/decimal256, including negative scales. Unconstrained NUMBER maps deliberately to a decimal **string** because its exponent range exceeds Arrow decimals; cast explicitly in SQL when numeric workspace operations are needed. Oracle DATE retains its time component; TIMESTAMP up to microsecond precision, RAW, text, small CLOB/NCLOB/BLOB, null-leading and empty results are supported. TIMESTAMP precision above six, timezone/local-timezone timestamps, intervals, LONG and object types fail visibly and require an explicit supported SQL conversion.

LOB length is checked before materialization, with a conservative four-byte-per-character bound for character LOBs. Each cell is limited to 1 MiB and converted batches to 8 MiB. Overly wide declared projections are rejected before fetching if their estimated 100-row buffers exceed 8 MiB. Select fewer columns or smaller source expressions rather than silently truncating values. Downstream workspace engines may support fewer decimal types/ranges than Arrow.

[Connection.call_timeout](https://python-oracledb.readthedocs.io/en/latest/api_manual/connection.html#oracledb.Connection.call_timeout) limits each database round-trip, not total query wall-clock duration. Combine connection/per-call timeouts, an execution deadline and source resource controls. `Connection.cancel()` acts on a live connection; the existing job-ID cancellation SPI needs a safe lifecycle design before cancellation can be advertised. Initially report it unsupported and close resources on error or early truncation.

### Destination approval and listener redirects

Reuse the MySQL operator approval model conceptually: approve exact Oracle endpoints and destination CIDRs, deny loopback/link-local/cloud-metadata/multicast/unspecified destinations, and require verified TLS.

The MySQL supplied-socket implementation cannot simply be copied: Oracle's public connection API differs, and Oracle listeners/RAC can redirect clients to other endpoints. Merely validating DNS before letting the driver independently resolve/connect is insufficient. The selected proxy path below pins each target without monkey-patching global DNS or relying on driver-internal hooks. Arbitrary descriptors, address lists, redirects and remote database links must not bypass network approval.

The [official Thin connection implementation](https://github.com/oracle/python-oracledb/blob/v26.2.0/src/oracledb/impl/thin/connection.pyx) automatically opens another TCP connection on a listener redirect. A direct endpoint requirement does not itself disable this behavior. The public connection parameters expose `https_proxy` / `https_proxy_port` for tunneling secure connections, but no supplied-socket or redirect-veto parameter was identified in the reviewed API.

The approved implementation uses those public parameters with `oracle-egress`, a standard-library-only CONNECT proxy with no host-published port or TLS termination. Every CONNECT request checks the exact endpoint and every DNS result, then opens a socket to a validated numeric IP. The same guard now serves MySQL's pinned socket, preserving its behavior. Proxy responses/logs contain static categories, not credentials or SQL. There are at most 64 active tunnels, bounded headers and connect timeouts. Its container is non-root, read-only, capability-free and resource-limited.

Driver `oracledb==26.2.0` was live-tested with a correctly encoded synthetic TCPS listener redirect: the redirected CONNECT retained the proxy and its unapproved target port was denied. Real-server certificate identity checks also remain end-to-end. This is not a claim of RAC/SCAN support; rerun these checks on driver upgrades.

## Operator configuration

Configure both variables for the proxy service:

```dotenv
ODN_ORACLE_ALLOWED_ENDPOINTS=oracle.example.com:2484
ODN_ORACLE_ALLOWED_CIDRS=10.20.0.0/16
```

An empty list denies access. Use the actual TCPS listener port; 2484 is a form default, not an assumption about the server. Approve only intended database destinations. IPv6 endpoint entries use `[address]:port`. Loopback, link-local/cloud-metadata, multicast, unspecified and reserved addresses remain blocked even under a broad CIDR.

Compose sets API-only `ODN_ORACLE_PROXY_HOST=oracle-egress` and `ODN_ORACLE_PROXY_PORT=8080`. Outside Compose, operators must set these to their restricted proxy; the connector fails closed when it is missing or unavailable. Never expose the unauthenticated proxy on a public/host port or substitute an unrestricted proxy. The proxy host is operator-controlled, not part of user connection profiles.

Apply the forward migration `0031_oracle_connections.sql`, rebuild the API for the new driver, and build/start the additional proxy:

```sh
docker compose build api oracle-egress
docker compose up -d --no-deps api oracle-egress
```

Production uses the existing production overlay/deployment process; no production deployment occurred in this task. Recreate `oracle-egress` after changing its approval environment.

In Settings choose Oracle, enter host/TCPS port, service name, default schema, non-administrative username/password, optional public PEM CA and exact-case `schema.table` patterns. Blank edit passwords keep the encrypted stored secret; clearing the CA restores system trust. Secrets never enter public config/API responses. Personal/shared tenant boundaries reuse existing encrypted credential and RLS mechanisms.

## Implementation sequence

1. Initial versions, service/PDB scope, direct topology and password/TCPS requirements confirmed above.
2. Resolve destination pinning and redirect handling with supported driver APIs and live verification. Document the chosen enforcement mechanism before coding the integration.
3. Add the driver to the API extra and refresh the lockfile; add a forward connection-kind migration without changing existing profiles or RLS.
4. Add typed profile/config validation and encrypted credential resolution, preserving same-kind passwords on edit and rejecting cross-kind edits.
5. Extend Oracle SQL parsing, case-aware policy, system/synonym/database-link restrictions and catalog filtering together.
6. Implement connection diagnostics, accessible schemas/tables/views/columns, typed bounded results and cleanup.
7. Add personal/shared Settings forms, per-component quoted Explorer SQL, dictionary/search integration, favorites/usage and chat dialect guidance.
8. Run focused regression tests and real Oracle integration checks; explicitly record unavailable versions/topologies as unverified. Deploy only with approval.

## Acceptance

- Encryption, write-only secrets, personal/shared tenancy isolation and unchanged existing connectors.
- Verified TCPS, wrong CA/identity rejection, wrong credentials, approved/private endpoints and rejected redirect targets.
- Multiple schemas in one service/PDB; quoted case variants, denied objects, synonyms/database links and system-owner restrictions.
- NUMBER/DATE/timestamp/RAW/LOB behavior, empty/null-leading results, multi-batch fetching, caps, deadlines and cleanup.
- Correct Oracle preview SQL and successful catalog-to-query use against a dedicated read-only account.

## Delivery and verification

- Added Thin driver dependency/lockfile, profile/secret handling, forward migration, diagnostics, metadata, read-only execution and typed results.
- Added Oracle SQL policy, case-preserving favorites/usage/definitions, Settings form, per-component quoted Explorer SQL and chat dialect instructions.
- Added the restricted proxy and local/production Compose wiring; no changes to existing source permissions.
- Focused backend regression suite: **255 passed**, including existing connector regressions, credential encryption/isolation and new Oracle/proxy/policy cases.
- Frontend production build/type-check and new Oracle form ESLint passed. Targeted Oracle/network mypy and Ruff passed.
- Broader shared-file mypy still reports the same 13 pre-existing errors in shared policy/profile/routes typing; these were not expanded into unrelated cleanup.
- Local API rebuilt and readiness passed. Browser verification confirmed the Oracle picker/form and TCPS/service fields without changing existing saved connections.
- Disposable Oracle Database Free `23.26.4.0.0` passed all four checks, two-schema tables/views/case distinctions, comments, exact decimal/unconstrained NUMBER handling, DATE/timestamp, Unicode, RAW/BLOB/CLOB, empty/null-leading results, rejected writes/synonyms/database links, wrong CA/hostname/password rejection, early close of a million-row stream, expensive-query timeout, and rejection of unsupported nanosecond precision.
- Synthetic listener redirect reached the proxy and its unapproved destination was denied.
- Stopping the isolated proxy made connection checks fail closed. Final local API readiness and the Compose proxy's empty-approval denial were verified.

Oracle 19c remains **live-unverified**; Database Free testing does not replace a customer 19c check. No real customer Oracle connection has been configured, and no Oracle changes have been committed or deployed to production. RAC/SCAN, wallet/mTLS, Native Network Encryption, external authentication and asynchronous job cancellation remain outside this release.
