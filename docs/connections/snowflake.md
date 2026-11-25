# Snowflake connections

Snowflake connections use a service user's RSA key pair or password and can browse permitted schemas in multiple databases. The default database and schema are used to resolve partially qualified SQL names; they do not limit the allowlist to one database. Personal connections are private to their owner; admins and data leads can create organization-shared connections.

## Delivery and verification status

Snowflake support is implemented and deployed. On 2026-11-24, the user confirmed successful live operation on production (`easyquery.ai`):

- All four connection checks passed: authentication, network, metadata, and read.
- After being asked to save, browse a table in Explorer, and run a small read query, the user confirmed it works. This is user-reported end-to-end verification, not an exhaustive integration test.

Delivered functionality includes encrypted write-only password/key-pair credentials, personal and organization-shared profiles, browser-side TOML paste/upload, multi-database catalog discovery, Snowflake identifier-aware allowlists, and read-only query integration.

The clearer diagnostic for an ungranted role (`390186`) is implemented locally but not yet deployed. Its regression test and the existing Snowflake connector suite pass (14 tests), with targeted Ruff checks passing.

Live key-pair authentication/rotation, multi-database and quoted-name edge cases, denied objects, timeout behavior, large-result limits, and warehouse-credit impact have not been independently verified in this live session. Query cancellation, interactive MFA/SSO, and Snowflake credit estimation remain unsupported.

## Snowflake prerequisites

1. Create a dedicated service user. For recommended key-pair authentication, follow [Snowflake key-pair authentication](https://docs.snowflake.com/en/user-guide/key-pair-auth), register the public key and retain the private key securely. QueryMuse accepts a PEM private key of at least 2048 bits and an optional passphrase. Password authentication is also supported where the Snowflake account policy allows it; interactive MFA and SSO are not supported.
2. Give the service user a dedicated role with `USAGE` on the intended warehouse, databases and schemas and `SELECT` only on the intended tables or views. Grant the role to the service user. Snowflake's own privileges are the primary security boundary. Do not grant write, administrative, external-access, or unnecessary function permissions. Views may expose underlying data, and SQL policy cannot inspect their definitions.
3. Choose a warehouse with appropriate auto-suspend, resource-monitor and statement-timeout settings. Both connection tests and metadata discovery can use warehouse resources. Queries consume Snowflake credits; QueryMuse's BigQuery bytes-billed limits and dry-run confirmations do **not** cap Snowflake credits. See [Snowflake warehouse cost controls](https://docs.snowflake.com/en/user-guide/cost-controlling-controls).
4. Ensure the API service can reach the Snowflake account and that `ODN_SECRETS_KEY` is configured for encrypted credential storage.

## Add a connection

In **Settings → Data connections → Add data source → Snowflake**, enter:

| Field | Example | Notes |
|---|---|---|
| Account identifier | `organization-account` | No `snowflakecomputing.com` suffix; legacy region-qualified identifiers are also accepted. |
| Service user login | `QUERYMUSE_SVC` | Snowflake `LOGIN_NAME`, which may differ from the user object name. |
| Warehouse | `ANALYTICS_WH` | Existing warehouse; the selected role must have access. |
| Default database / schema | `ANALYTICS` / `PUBLIC` | Required for unqualified SQL. |
| Role | `QUERYMUSE_READER` | Optional; if omitted, Snowflake's default role applies. |
| Allowlist | `ANALYTICS.PUBLIC.*` and `FINANCE.REPORTING.ORDERS` | One three-part pattern per line; each component is an exact name or `*`. At least one entry is required. |
| Authentication | Key pair or password | Choose the method allowed by the Snowflake account policy. |
| Private key / passphrase or password | PEM key and optional passphrase, or password | Write-only; leave the credential blank on edit to retain it when using the same method. When switching methods, provide the new credential. To rotate a key, submit the new key and its passphrase together. |

Alternatively, open **Import Snowflake connections TOML** and paste or upload one `[connections.name]` table with `account`, `user`, `password`, `warehouse`, `database`, `schema`, and optionally `role`. Parsing happens in the browser; import fills the form without saving or testing it. The allowlist defaults to `DATABASE.*.*` using the supplied database name and can be edited before saving. An immediately repeated identical opening table header is accepted to accommodate pasted snippets; other invalid TOML and multiple connection tables are rejected. Only the listed fields are supported. Use a dedicated read-only role rather than `ACCOUNTADMIN`.

Use uppercase simple unquoted names for the default warehouse, database, schema and role. Allowlist names use **exact Snowflake catalog spelling**, including quoted mixed-case names: `ANALYTICS.PUBLIC.Orders` is different from `ANALYTICS.PUBLIC.ORDERS`. Unquoted SQL identifiers resolve to uppercase; quote each component to address mixed-case objects, for example `SELECT "AMOUNT" FROM "FINANCE"."REPORTING"."Orders"`. Use only simple identifier components (letters, digits, `_`, `$`, starting with a letter or `_`); names containing dots or other special characters cannot be safely represented by the current allowlist and are unsupported.

Use **Test connection**, then save. Testing checks authentication/network, metadata access and `SELECT 1`; it does not prove access to every allowlisted table. Explorer lists `DATABASE.SCHEMA` entries from all accessible databases, filtered by the allowlist. Check a table in each desired database and run a small read query to verify actual access.

The password or private key is decrypted server-side only when needed and stored encrypted with connection-specific tenant/owner binding. Credentials and passphrases are not returned in connection settings. Do not paste credentials into chat or commit them to the repository.

## Limitations and troubleshooting

- The role's source privileges and view/UDF configuration are critical: policy checks and allowlists do not replace them. Unknown or qualified functions are rejected by the SQL analyzer; some valid Snowflake built-ins may therefore be unsupported. Stored procedures, writes and system functions are not supported.
- Snowflake result rows and Arrow bytes are capped and statement execution has a timeout. Query cancellation is not currently exposed; the server-side timeout is the execution safeguard. There is no Snowflake credit estimate or per-query credit cap in QueryMuse.
- Schema discovery fails visibly at Snowflake's 10,000-row `SHOW` limit; restrict the role's visible schemas if necessary. Only simple identifiers are exposed.
- A failed test includes a sanitized Snowflake error code, not the underlying raw SQL or secrets. Check the Snowflake account identifier, service login and password or registered public key, authentication policy, role grants, network access, and warehouse state in Snowflake. An account requiring interactive MFA or SSO cannot use the password flow. A credential change requires updating the connection.
- Error `390186` means the role specified in the connection is not granted to the user. Choose a granted read-only role, ask a Snowflake administrator to grant the intended read-only role, or clear the optional Role field to use the user's default role. Do not grant `ACCOUNTADMIN` just to make the connection test pass.
- A TLS failure can indicate an untrusted certificate or network inspection. Snowflake does not support altered TLS certificates; work with your network administrator to allow certificate passthrough to the required [Snowflake endpoints](https://docs.snowflake.com/en/user-guide/hostname-allowlist). Do not disable certificate verification.
- Basic live verification has passed as recorded above. For broader acceptance, test against a read-only role in at least two databases, including quoted names, denied objects, preview/query, timeouts, result limits, and warehouse costs; do not treat a passing connection test as proof of all these cases.
