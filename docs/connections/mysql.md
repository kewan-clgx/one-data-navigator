# MySQL connections

## Delivery and verification status

Implemented and deployed to production (`easyquery.ai`) on 2026-11-25 in release `4f18039`. The API image includes PyMySQL 1.2.4 and migration `0030_mysql_connections` is applied. Settings offers MySQL for personal and organization-shared connections, with catalog/search/schema/dictionary integration, exact-case favorites/recent tables, separately backticked Explorer SQL, and chat dialect guidance.

Verification on 2026-11-24:

- 225 focused backend connection, policy, query, encryption/isolation and favorites tests passed.
- Disposable real MySQL 8.0 and 8.4 servers passed authentication/network/metadata/read checks; discovery and reads across two databases, views and case-distinct table names; unsigned integers, decimals, TIME, Unicode, binary, empty and null-leading results.
- Both server versions rejected an untrusted CA, wrong certificate hostname, wrong password and source writes. Expensive queries timed out, and a million-row unbuffered result closed after one batch without draining the remainder.
- Frontend production build/type-check and new MySQL form lint passed; the form was checked in the local browser. New backend connector/network code passes targeted mypy and Ruff. Existing shared-file lint/type-check issues remain outside this change.

These are disposable-server integration checks, not verification of a customer-hosted database or every case mode/authentication plugin. The combined release regression suite passed 255 tests; production API readiness and public homepage/sign-in passed after deployment. Operator endpoint/CIDR approval and a customer-hosted database test remain required. No customer MySQL credentials have been configured.

## Confirmed scope and design

The initial implementation targets MySQL 8.0 and 8.4 (not MariaDB), multiple databases per connection, username/password authentication, and personal or organization-shared profiles. Connections use direct TCP with verified TLS and an optional PEM CA certificate bundle. SSH tunnels, mutual TLS, cloud IAM, query cancellation and cost estimates are not included.

The default database resolves unqualified table names; it does not restrict discovery to one database. Root catalog nodes are accessible databases `(database,)`; tables and views use `(database, table)`. Allowlist entries use `database.table` with whole-component `*` wildcards. Names match exact catalog case, including on servers configured for case-insensitive table names; use the spelling shown by Explorer. Simple ASCII identifiers only (letters, digits, `_`, `$`, starting with a letter or `_`, at most 64 characters) avoid ambiguity in dotted allowlists. System databases are excluded.

### Driver and execution research

- Use [PyMySQL](https://pymysql.readthedocs.io/en/latest/modules/connections.html), a pure-Python, [MIT-licensed](https://github.com/PyMySQL/PyMySQL/blob/main/LICENSE) driver. Its supported Python versions include the backend's Python 3.12. The [`rsa` extra](https://pymysql.readthedocs.io/en/latest/user/installation.html) provides support for `caching_sha2_password`/`sha256_password`. The minimum version is 1.2.4; passing an explicit verified SSL context requires TLS rather than the driver's implicit preferred-TLS mode.
- Use an unbuffered `SSCursor` and bounded `fetchmany` batches, not `fetchall`, for user results. Close the connection before closing an unfinished unbuffered cursor to avoid draining a large result on truncation.
- Use a read-only session/transaction, `MAX_EXECUTION_TIME` for SELECT execution, and socket read/write timeouts. MySQL's [execution-time limit](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_max_execution_time) is not a universal wall-clock limit for every server operation; source privileges, resource controls and application result limits remain necessary.
- Read-only transactions alone do not prevent every side effect. Require a SELECT-only source user; reject writes, locking reads, multiple statements, file access, user variables, optimizer hints and unknown/qualified functions. Stored routines and indirect access through views must be governed at the source.
- Use `INFORMATION_SCHEMA` metadata, binding filter values and comparing names case-sensitively. Inspect server version and `lower_case_table_names` before use; modes 0, 1 and 2 use the same conservative exact-case application allowlist.

### Approved destinations

Operators approve destinations through both environment settings:

```dotenv
ODN_MYSQL_ALLOWED_ENDPOINTS=mysql.example.com:3306,private-db.example.com:3306
ODN_MYSQL_ALLOWED_CIDRS=203.0.113.10/32,10.20.30.0/24
```

These are illustrative addresses, not working database settings. An empty endpoint or CIDR list denies MySQL access. Approval is operator-wide; it does not grant users database credentials or application allowlist access. Use narrow IP ranges and dedicated database endpoints.

Every connection re-resolves the exact approved hostname/port, requires all resolved addresses to belong to an approved CIDR, and pins the TCP connection to a validated address. TLS verifies the original hostname, not a substituted IP. Loopback, link-local (including cloud metadata), multicast and unspecified destinations are always denied. Operators must also maintain outbound firewall rules; an application allowlist is not a substitute for network segmentation.

### TLS and credentials

TLS and hostname verification are mandatory, with no insecure toggle or plaintext fallback. The optional CA bundle augments the system trust store and is supplied as PEM text, not a server filesystem path. A server that does not advertise TLS must be rejected before credentials are sent.

Passwords reuse the existing encrypted, write-only per-connection credential flow. Blank passwords on same-kind edits keep the stored password; a new connection requires a password. Cross-kind edits involving MySQL are rejected. CA certificates are public trust material, stored in connection config; never paste a private key into the CA field.

## Setup and acceptance

1. Provision a dedicated MySQL user with `REQUIRE SSL` and only SELECT on intended databases/tables. Do not grant administrative, FILE, EXECUTE, write or temporary-table privileges. Review view definers and indirect access.
2. Have an operator approve the endpoint and narrow destination CIDRs, ensure network reachability, and install/rebuild the API with the MySQL driver.
3. Configure the hostname, port, default database, user, password, optional CA bundle and explicit database/table allowlist in Settings. Use a DNS name present in the server certificate.
4. Test authentication, network, metadata and read; save, browse tables across permitted databases, and run a bounded query.

The checks above cover basic integration on both supported versions, and a focused driver test verifies rejection before credentials are sent when a server does not advertise TLS. Before customer rollout, verify the intended hosting environment, networking and CA chain, the actual SELECT-only grants and views, and configured allowlist. MySQL modes 1/2, key authentication-plugin variations, CA rotation and pathological source values need deployment-specific testing. Zero dates and unsupported result values fail explicitly rather than being silently converted. DECIMAL precision is preserved in Arrow, but downstream workspace engines can have narrower type limits.

No query cancellation or cost estimate is advertised. Source timeouts and closing the result connection are safeguards, not a guarantee of immediate server cancellation. BigQuery byte-billed limits do not cap MySQL resource use.
