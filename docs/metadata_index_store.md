# Persistent metadata index and search

Status: an opt-in Explorer preview is implemented in the worktree, including stored API search, a shared
downloadable index and the original source-built browser index. Manual preview refresh is available; an
automatic scheduler and agent integration are not yet implemented. This is not a production rollout.

Implementation decisions: refresh daily and allow manual refresh by a personal connection's owner or a
shared connection's admins/data leads. Keep the last successful metadata indefinitely after a failed refresh,
with a prominent stale/error warning. Source-side permission revocation may therefore leave metadata visible
until a successful refresh; live queries still enforce source permissions. Platform refresh is
operator-managed only, using a shared index. Source quota and cost budgets still need to be established
before rollout.

Implementation progress: connectors expose a separate complete-enumeration operation for future safe
reconciliation. BigQuery and PostgreSQL do not impose the legacy 10,000-item listing cap on that operation;
the other providers explicitly fail when their existing limits are exceeded. Migration
[`0034_metadata_catalog.sql`](../backend/src/odn/db/migrations/0034_metadata_catalog.sql) defines initial
catalog, container, table, field and job storage with connection-scoped RLS, single-flight job uniqueness
and a connection revision. [`publish_container`](../backend/src/odn/platform/catalog/metadata_store.py)
checks the job lease, actor authorization, revision and current allowlist before atomically replacing one
complete scope. [`metadata_queue`](../backend/src/odn/platform/catalog/metadata_queue.py) provides scoped
enqueue/coalescing, lease claims, heartbeats, failure and bounded lease recovery.
[`metadata_collect`](../backend/src/odn/platform/catalog/metadata_collect.py) fetches authorized complete
scopes outside database transactions. [`run_job`](../backend/src/odn/platform/catalog/metadata_refresh.py)
claims a job, keeps its lease alive while fetching, publishes successful scopes, retains failed scopes and
atomically reconciles a complete root listing. Mutable `file:` and `latest` secret references are refused
until out-of-band credential rotation can be detected; numeric Secret Manager versions and connection-row
encrypted credentials are supported by this runner. A table with no returned fields is currently treated conservatively as
incomplete, including a genuinely empty schema. BigQuery's current bulk field path is stored as one opaque
component, not split on dots; nested hierarchy/mode need a richer unambiguous connector representation.
The preview dispatcher runs an explicitly requested job in a bounded API-process thread; it is not a
durable scheduler. Preview search, status and index download read its published data.

## Problem and goals

A real Explorer catalog contains approximately **28,143 searchable tables**, with **2,008 snapshot-named tables hidden** by the current UI filter. Building its metadata index takes the user three minutes or more. The existing index expires after one hour, is lost when the API process restarts, and can be rebuilt separately by multiple processes or requests.

The goals are to:

1. Reuse indexed metadata across page visits, users authorized for the same connection, API restarts and deployments.
2. Show existing metadata immediately while refresh runs in the background; allow manual and targeted refresh.
3. Search on the server without repeatedly downloading and scanning the entire catalog in the browser.
4. Give the LLM a small, relevant, understandable set of authorized tables and fields rather than the whole catalog.
5. Preserve source permissions, connection visibility, tenant/user isolation, object allowlists and current Explorer functionality.
6. Observe refresh cost, latency, failures, staleness and search quality before choosing more expensive optimizations.

This is a metadata catalog, not a copy of source table data. Do not fetch or store example rows, values,
secrets, query results or credentials as part of indexing. The approved numeric/date/time profile extrema
below are an explicitly protected exception implemented only in the default-off profiling preview.

## Current behavior

- [`get_search_index`](../backend/src/odn/platform/catalog/search_index.py) lists the connection's allowed root containers, then lists tables and columns per container using up to four threads. A successful result is cached **in the API process** for 3,600 seconds; partial results are not cached. Concurrent requests do not share an in-progress build.
- [`search-index`](../backend/src/odn/api/routes.py) returns the whole nested index. With the preview disabled,
  [Explorer](../frontend/app/components/Explorer.tsx) uses this path; with it enabled, the index is loaded
  when **Browser full index** is selected. [CatalogSearch](../frontend/app/components/CatalogSearch.tsx)
  filters and ranks it in the browser. Snapshot detection, favorites and recency are applied in the UI.
- The [BigQuery connector](../backend/src/odn/platform/connectors/bigquery.py) lists datasets/tables and queries `INFORMATION_SCHEMA.COLUMN_FIELD_PATHS` per dataset. The three-minute observation is a whole-flow measurement, not evidence that any single source call is the bottleneck.
- The agent's [`search_tables_by_keyword`](../backend/src/odn/agent/tools.py) independently calls the root catalog and scores returned table names. The root normally contains datasets/schemas, not tables, so this path can return no matches despite existing tables. It does not currently reuse Explorer's index or search descriptions as its docstring suggests.
- [`data_definitions`](../backend/src/odn/db/migrations/0003_dictionary.sql) already stores curated definitions and their status/history. User favorites and query usage already exist separately. PostgreSQL has pgvector installed, but installation alone does not provide a semantic retrieval pipeline.

## Metadata inventory: BigQuery example

The goal is to collect as much **useful and authorized** metadata as practical, without copying source
rows or values. The following is an inventory and proposed collection policy, not a description of current
implementation. Availability depends on object type, source permissions, provider capabilities and cost.
Represent unavailable, unsupported, not collected and collection failed separately from an empty value.

| Category | Candidate metadata | Sources and value |
|---|---|---|
| Project and dataset | Project/dataset identifiers, location, descriptions, labels, friendly names, creation and expiration settings. | BigQuery [dataset resource](https://cloud.google.com/bigquery/docs/reference/rest/v2/datasets) and `SCHEMATA`/`SCHEMATA_OPTIONS`; improves context and discovery. |
| Tables and views | Exact identifiers, descriptions, friendly names, labels, object kind, creation and expiration time, available modification/version information. Distinguish tables, views, materialized views, external tables, clones and snapshots. | [`TABLES`](https://cloud.google.com/bigquery/docs/information-schema-tables), [`TABLE_OPTIONS`](https://cloud.google.com/bigquery/docs/information-schema-table-options) and table resource API. |
| Columns and nested fields | Names and structured paths, order, SQL type, description, nullability, repeated/array mode, nested hierarchy, collation, defaults and other available type attributes. | [`COLUMNS`](https://cloud.google.com/bigquery/docs/information-schema-columns), [`COLUMN_FIELD_PATHS`](https://cloud.google.com/bigquery/docs/information-schema-column-field-paths), and table schema API as needed. Do not assume one view supplies every nested field's mode or order. |
| Column profile | Fill rate and observed numeric/date/time minimum and maximum, with sample size, sampling method, observation time and explicit status. | Requires a separately authorized query over source data; `IS_NULLABLE` is a schema constraint, not an observed fill rate. Sampled results are estimates, not ordinary schema metadata. |
| Query optimization | Partitioning type/column, clustering columns and order, required partition filter and expiration configuration. | `COLUMNS`, `TABLE_OPTIONS` and table resource API; helps generate valid, efficient SQL. |
| Size and freshness | Available row counts, logical/physical storage bytes, partition counts, creation and last-modified timestamps, and source observation time. | [`TABLE_STORAGE`](https://cloud.google.com/bigquery/docs/information-schema-table-storage), table resource API and, where justified, `PARTITIONS`. Treat counts and timestamps as potentially delayed, not live truth. |
| Declared relationships | Primary/foreign keys and their ordered local/referenced columns. | [`TABLE_CONSTRAINTS`, `KEY_COLUMN_USAGE` and `CONSTRAINT_COLUMN_USAGE`](https://cloud.google.com/bigquery/docs/primary-foreign-keys#view_constraints). BigQuery does **not enforce** these constraints; they are evidence, not proof of data integrity or correct joins. |
| Governance | Available policy tags, data-governance tags and data-policy references attached to columns or nested fields. | `COLUMNS` and `COLUMN_FIELD_PATHS`; visibility and permissions vary. Tags alone do not authorize a user or make cached metadata safe to disclose. |
| Snapshots and clones | Actual object type, base table identity and snapshot time. | `TABLES`; distinguish real snapshots from the Explorer's current name-based heuristic. Preserve both signals if needed for compatibility. |
| Views and transformations | View definition and SQL dialect; materialized-view metadata and refresh options where available. | [`VIEWS`](https://cloud.google.com/bigquery/docs/information-schema-views), `TABLES` and `TABLE_OPTIONS`. SQL may contain sensitive literals or references; protect it separately and do not send it to the LLM by default. |
| Other source objects | Routine names, parameters, descriptions, supported routine options, and search/vector-index metadata. | Available [INFORMATION_SCHEMA views](https://cloud.google.com/bigquery/docs/information-schema-intro); add only when the product can use and authorize them. |
| Business context and lineage | Existing curated definitions, glossary terms, ownership and available table/column lineage from connected governance systems. | Reuse the app's definitions; external lineage is a separate, optional integration, not guaranteed by BigQuery itself. Preserve source, approval status and provenance. |

### Collection tiers and boundaries

- **Core target:** dataset/table identities and descriptions, actual object kinds, nested schemas and
  descriptions, partitioning/clustering, declared keys, relevant tags and available lightweight size/freshness
  metadata. Batch by authorized dataset or region where supported rather than issuing one request per table.
  Richer collection remains subject to permissions, quota/cost budgets and source-specific capability checks.
- **Separate or on demand:** detailed partition metadata, view definitions, routines, indexes and external
  lineage. Partition identifiers and SQL definitions can themselves contain sensitive data or literals; do not
  send them to the LLM or expose them broadly by default. BigQuery's
  [`PARTITIONS`](https://cloud.google.com/bigquery/docs/information-schema-partitions) view is preview and
  limited to 1,000 tables per query; batch narrowly and do not interpret a failed batch as an empty result.
- **Not automatic in the default configuration:** row samples, data profiling, query history, raw access policies or exporting
  metadata to an embedding provider. The gated BigQuery profiling work below is an approved exception;
  other data-derived profiling or external export needs a separate design and approval.

### BigQuery column profiling: experimental implementation

The worktree contains a **default-off, partially validated preview** (`ODN_COLUMN_PROFILING_PREVIEW`). It queues
per-table work after successful queries and table favorites, and from the workbench Profile button. A
durable worker reserves the shared connection/day scan budget before each BigQuery attempt, records the
source job ID and reservation, and stores successful aggregates separately from metadata refresh.
Explorer and the workbench display estimated fill rates; the workbench also shows supported ranges,
sample time and the latest attempt status. Profile reads check the current source schema and hide
fields whose structured path, type or mode changed. Empty samples and unknown or zero dry-run estimates
are skipped. A failed attempt retains the previous successful profile.

**Do not enable this preview yet.** PostgreSQL isolation and concurrent budget reservation have been tested
in an isolated database with a simulated BigQuery connector. One approved BigQuery view scan has also been
tested (below), but nested aggregation, policy changes, crash recovery and browser behavior still need
validation.
Column-policy-tagged schemas are rejected; changes to underlying view/row access policies and source
identity equivalence need further security validation. The preview fingerprints the resolved service
account's public key and credential identity without storing the private key in the profile. Reads and
publication re-resolve the credential and reject a mismatch, including changes behind local `file:` and
Secret Manager `versions/latest` references. Profiles for platform connections are
tenant-scoped, while their daily budget is shared globally. This preview is not a production-ready rollout.

#### Work completed, 2026-11-26

- Implemented BigQuery table/view profiling separately from metadata refresh, with PostgreSQL storage for
  the last successful sampled counts and numeric/date/time extrema, observation time, connection revision
  and credential fingerprint. Profile jobs and their latest status are stored separately.
- Added automatic first-profile requests after a successful query or table favorite and a manual **Profile**
  button in the SQL workbench. Dataset favorites do not request profiles. Requests for the same tenant,
  connection and table coalesce while queued/running; automatic retries after failures or skips wait 24 hours.
- Added estimated fill rates to Explorer's selected-table **Field fill rates** and fill rates, ranges and
  nested-field profiles to the workbench. An empty sample is skipped, not displayed as 0%. The UI shows
  sample time and the latest failure or skip while retaining the last successful profile.
- Added connection-wide UTC-day reservations, a BigQuery per-job billing cap, source job IDs and lease-based
  recovery. Automatic and manual requests share the **2,000 decimal GB/day/connection** budget; each job is
  capped at **50 decimal GB** or a lower applicable role/tenant cap. Reservations are not refunded after
  uncertain execution. These are byte limits, not guarantees of slot-based monetary cost.
- Tested 84 targeted backend cases against an isolated PostgreSQL database, including profile API gating,
  shared-budget concurrency, RLS, credential changes and catalog regressions. Targeted backend lint and
  type checks and frontend TypeScript checks passed; existing unrelated lint findings remain in older
  components. The isolated database was removed after testing.

#### Storage and sharing

Successful profiles are keyed by **tenant, connection and table path**, not by the requesting user. Other
authorized users in the same tenant can reuse a profile on a shared connection without another data scan.
Personal connections remain visible only to their owner; platform-connection profiles are shared within
each tenant, not across tenants, even though their budget is connection-global. Different connection IDs
do not share profiles, even if they point to the same source.

Reads require access to the connection and table under its current allowlist. They re-resolve the credential
and check the live BigQuery schema before returning saved fields; a changed credential hides the profile,
and changed field paths, types or modes are omitted. A connection revision change clears saved profiles.
These checks do **not** establish that source row/column permissions or a view's underlying policies are
unchanged; that remains a rollout blocker. Failed refreshes preserve the previous successful profile only
when it remains eligible for display.

#### App test procedure (after validation and approval)

The preview is disabled by default; enabling it also enables automatic requests, so it may incur scans
before anyone clicks **Profile**. On a shared local stack, coordinate API interruption and database
migrations first. To enable it in local Compose after approval:

```sh
ODN_COLUMN_PROFILING_PREVIEW=true docker compose up -d --no-deps --force-recreate api
```

The API startup applies pending migrations, including `0038_column_profiles`; recreating it interrupts
the shared API. Reload the app, select an eligible BigQuery connection and table or view, open **Details &
SQL**, click **Profile**, and watch queued/running status. Expand **Fields** for rates and ranges or
Explorer's **Field fill rates** for rates. Clicking **Profile** again requests a fresh, potentially billed
scan. Other connector types do not support this preview. Disabling the flag stops preview requests and the
worker after restart; it does not delete already stored profiles or reservations.

#### Approved BigQuery view test

On the approved `idap-int` connection, one standalone, uncached profiling query over
`clgx-idap-bigquery-int-278e.edr_ent_property_firmographics.vw_edr_firmographics_view` completed in
**2.31 seconds** after dry run. The estimate and actual processed bytes were **4,313,772,124**; actual
billed bytes were **4,313,841,664**, equal to the configured per-job billing cap. The 10% sample contained
997,833 parent rows; all 47 fill counts were within the sample size, and 14 fields supported ranges.
No field values or extrema were printed. This was a single explicitly approved source query. The worker's
reservation, storage, credential checks and read path were subsequently exercised in an isolated database
by replaying that completed job's result, without running another source scan; the browser path remains
untested. The local `idap-int` credential is a mutable `file:` reference and is subject to the fingerprint
checks above.

The requested feature profiles eligible BigQuery tables and views **on demand**, independently of
metadata refresh. Store results in the app's PostgreSQL metadata store, show field fill rates in Explorer,
and show fill rates and ranges in the SQL workbench's table dictionary **Fields** section. Other
connectors are initially unsupported.

- **Triggers:** automatically request a profile after a successful query uses a table or a user favorites
  that table, but only if it has no successful profile. A workbench **Profile** button can request a
  fresh profile manually. Any user who can access the connection and the table can request profiling.
  Coalesce concurrent requests; bound retries so repeated failed attempts cannot create a query storm.
  Favoriting a dataset must not automatically profile every table in it. The user explicitly included
  **platform connections and demo users**: profiling requests may originate from them too, subject to the
  same global per-connection budget and source-authorization checks. This is a profiling-specific exception
  to operator-only platform **metadata refresh**, not permission to modify platform connections or catalogs.
- **Fill:** fraction of sampled parent rows containing a value. `NULL` and blank or whitespace-only
  strings are unfilled. For repeated/nested fields, a parent row is filled if at least one reachable
  value is non-NULL and, for strings, nonblank. Record sampled parent-row and filled-row counts; an
  empty sample has no rate, not a fabricated 0%.
- **Range:** observed minimum and maximum for supported numeric and date/time values only, including
  reachable repeated values. Strings and other types have no range. These extrema are actual source
  values and can be sensitive even when numeric: protect them at least as strictly as the source
  connection and its object/column authorization, and do not log them. This is an explicit exception
  to the existing no-source-values metadata rule; do not store sample rows or string extrema.
- **Sampling:** estimates, labeled as such. The preview uses a 10% row sample; the rate was not specified
  in the request and remains subject to further validation.
  BigQuery block sampling can be biased by clustering and partitioning, and is unsupported for views
  and tables with row-level security. Row sampling with `RAND()` still scans the underlying columns;
  for views, including the example in the request, use a supported approach and count the full scan
  against the budget. See [BigQuery table sampling](https://cloud.google.com/bigquery/docs/table-sampling).
- **Budgets:** at most **2,000 GB per connection per UTC day** across automatic and manual profiling,
  and **50 GB per table job**, decimal GB, with conservative dry-run estimates and BigQuery job billing
  caps. Reserve budget durably before launching jobs so retries, crashes or concurrent attempts cannot
  silently exceed the daily budget; a job crossing midnight remains charged to its reservation day.
  A dry run may report zero for a table protected by row-level security, even when it is not empty;
  treat an unknown or unreliable estimate as unavailable, not proof of zero cost. An exhausted budget
  is an explicit skipped status, never a zero fill rate. Bytes scanned do not directly cap slot-hours
  or cost on capacity-based pricing; see [BigQuery cost controls](https://cloud.google.com/bigquery/docs/best-practices-costs).
- **Freshness and failures:** attach connection revision, table and field identity, observation time,
  method, sample counts and status. Expose unavailable, unsupported, skipped, empty-sample and failed
  distinctly. Keep the last successful profile after a failed attempt and display its observation
  timestamp rather than a stale warning; also report that the latest attempt failed. Never carry it
  across a connection revision or silently attach it to a changed field.
- **Authorization:** use the requesting actor's eligible connection credential and current policy; recheck
  lease, actor, connection revision and allowlist before publication. Source row and column policies
  affect the computed result. Reuse a profile only where the requesting user's effective source
  visibility is equivalent; otherwise fail closed rather than sharing a more privileged profile.
  PostgreSQL RLS alone is not per-object or per-column authorization.
- **Operational boundary:** independently bound query time, concurrency and result size; do not log
  profiling queries or returned values. Metadata-only refresh remains useful if profiling fails,
  and the UI must not imply that missing or estimated data is exact. Validate nested identifiers,
  sampled views, policy changes, budget exhaustion and retry behavior before enabling on real data.

#### Implementation plan and remaining validation

The following captures the implementation objectives and outstanding verification; it is not a claim that
all security and rollout criteria have been met.

1. **Schema and authorization:** add revision-scoped profile records keyed by connection, table path
   and field path, retaining profile type, sample counts, observed extrema, observation timestamp and
   latest-attempt outcome. Protect records with forced RLS and current connection, object and field
   authorization. Define how shared-connection users establish equivalent source visibility before
   exposing data-derived results. Design a narrowly scoped, auditable platform-profile request and
   worker path: app users must not gain general write access to platform connections or metadata.
2. **Durable background work:** enqueue profiling after successful table queries, table favorites or an
   explicit workbench request, independently of metadata refresh. Only trigger automatically when
   there is no successful profile. Coalesce concurrent jobs and throttle retries. Track per-connection
   UTC-day budget reservations and source job identities durably across worker crashes and lease retries;
   bound concurrency, execution time and result size. Unknown or unreliable estimates must fail closed.
3. **BigQuery collection:** profile authorized tables and views, preserve structured nested identifiers,
   calculate parent-row fill for repeated fields, and capture supported numeric/temporal extrema. Treat
   empty samples, unsupported types, non-finite numeric values, permissions and source errors explicitly.
   Store only aggregated profile results, not sampled rows.
4. **Read and presentation:** provide an authorized table-profile read including timestamps, sampled
   counts, latest attempt outcome and per-field availability. Add fill rates to Explorer's field display;
   add fill rates and ranges to the workbench dictionary Fields table shown in the request. Label estimates
   and show the observation timestamp. Avoid hiding the last successful profile merely because a later
   attempt failed, while reporting that failure.
5. **Verification and rollout:** test budget reservation across retries/crashes, isolation and policy
   changes, empty samples, nested/repeated fields, views, large/unsupported values and UI state. Validate
   costs and sampling on a specifically approved source before enabling the feature; metadata refresh,
   existing search modes and other providers must continue to work without profiling.

BigQuery `INFORMATION_SCHEMA` queries are not cached; on-demand queries have a minimum billing amount and
capacity-based queries consume slots. Dataset- and region-scoped views have different permission requirements;
execution location must match the dataset or region. Region scopes are not hierarchical, and region results
may include recently deleted datasets. The views do not contain hidden datasets.
Crucially, Google documents that `INFORMATION_SCHEMA` checks permissions at the parent level rather than
per individual row, so conditional deny policies targeting individual resources can be ignored. Do not treat
a region-wide listing as proof of individual-object authorization; use a verified access strategy and apply
the current app allowlist to all stored/read metadata, counts and LLM responses. See
[BigQuery INFORMATION_SCHEMA scope, pricing and limitations](https://cloud.google.com/bigquery/docs/information-schema-intro).

For the LLM, retrieve a small ranked set with field descriptions, key/partition hints, freshness and
provenance; fetch details only for selected assets. Treat source descriptions and definitions as untrusted
data, label unenforced relationships, and never invent joins or business meaning from missing metadata.
Record collection status and observed timestamps by scope so stale or incomplete metadata is visible.

## Architecture design and benchmark results

The Explorer preview evolved through three approaches. They remain selectable so their costs and behavior
can be compared on the same connection.

### 1. Cold source build and browser search

```text
source database ──> API enumerates datasets, tables and fields ──> full index ──> browser search
                            └── successful result cached in this API process for one hour
```

The original path collects metadata from the source and sends the full index to the browser. Once loaded,
typing searches the in-memory index without a request per query, so results are fast. But a cold build
requires source enumeration again after a cache expiry or process restart; the cache is process-local,
not a durable index shared across API processes. The reported large catalog had about 28,143 searchable
tables and took more than three minutes to index. That observation does not identify a particular source
call as the bottleneck.

### 2. Persisted metadata and PostgreSQL search API

```text
source database ──> authorized refresh ──> PostgreSQL metadata catalog
                                                │
browser query ──> authenticated search API ─────┘ ──> paginated matching tables and fields
```

The refresh collects source metadata once and publishes it to the app's PostgreSQL catalog, scoped to the
connection and its revision. Authorized users of the same shared connection can reuse that catalog. Searches
read PostgreSQL instead of rebuilding from the source, and return small pages rather than the whole index.
The initial build and later refreshes still query the source. Tree browsing and table details still use
the live connector.

This removed repeated source indexing, but each search now includes browser-to-API latency, database search,
response transfer and parsing. Explorer also applies a 250 ms typing debounce. Disabling costly PostgreSQL
JIT for these requests and adding an RLS-safe field-path index improved the API, but the browser's
in-memory scan remained faster on the measured connection.

### 3. Shared downloadable index and browser search

```text
source database ──> authorized refresh ──> PostgreSQL metadata catalog
                                                │
browser open ──> authenticated index download ──┘ ──> in-memory browser search
```

The shared-download path combines the durable catalog with the original fast browser scan. Each authorized
user downloads a browser-shaped index assembled from PostgreSQL; opening it does **not** query the source
or rebuild the catalog. Search then runs locally as the user types. Sharing means reusing the server-side
catalog for the same authorized connection, not sharing another user's browser memory or equating separate
connections merely because they point at the same database. Downloads are private and are repeated when
switching modes or opening a new session; persistent browser caching is not implemented.

The download checks connection visibility, revision and current object policy, and includes only
authorized metadata and tenant-scoped curated definitions. Favorites and recent usage stay per-user.
Status and catalog rows are read in one repeatable-read transaction, but publication is atomic per
container, not across the whole catalog. During a running refresh a new download returns 409; a previously
downloaded index and stored API search remain available. Partial or stale data is marked accordingly.
The initial source build, source authorization changes, refresh failures and download-size limits still
need operational attention; an already-delivered browser copy cannot be recalled.

### Measurements and interpretation

Local observations on the approved personal BigQuery development connection, with **9 datasets, 1,575
tables and 45,453 fields**:

| Measurement | Observed time | What it includes |
|---|---:|---|
| Initial persisted catalog build | 22.4 s | Source collection and publication to PostgreSQL. |
| Earlier source-built browser index load | 6.0 s; 0.3 s warm | Index load; the warm result used the API process cache. |
| Shared index download in Explorer | 0.42–0.80 s | PostgreSQL-backed request, transfer and browser JSON parsing. |
| Browser `listing` search | 3–5 ms shared; 5–6 ms source-built | In-memory search, excluding index load and rendering. |
| Optimized stored API `listing` search | 73–109 ms in five requests; 130 ms in a later request | Browser-to-API request and response, excluding the 250 ms typing debounce and rendering. |

For `listing`, shared browser search and stored API search returned the same **31 table paths** with the
same snapshot setting. This verifies that sample's table-path parity, not complete field or query parity.
The shared response was 3,371,509 bytes of decoded JSON in three fetches; the earlier source-index
measurement was approximately 242 KB **encoded**, so these sizes are not directly comparable.

Separately, an isolated synthetic catalog with **28,143 tables and 281,430 fields** produced 28,458,831
bytes of JSON. Three shared-index service reads plus serialization took **1.12–1.20 s**. That is not a
download time: it excludes HTTP transfer, browser parsing, search and rendering, and does not model all
real descriptions. These are individual local samples, not p95 or production results. The actual
28k-table source still needs end-to-end cold/warm, payload, browser-memory and concurrency measurements.
The detailed samples and remaining limitations are recorded in the comparison preview below.

## Proposed architecture

```text
source metadata ──> bounded background refresh ──> PostgreSQL metadata catalog
                                                     │              │
                                             Explorer search    agent search
                                                     │              │
                                              pages + fields   top candidates
                                                                   │
                                                        selected table context
```

PostgreSQL is the durable source of *indexed metadata*, not the authority for source access. The existing connection profile/credentials and live source remain authoritative. Both Explorer and the agent use the same server-side search service and authorization checks; they do not talk to the source for every keyword.

### Logical data model

Exact migration names and physical indexes should follow a schema/benchmark review, but the first implementation needs these concepts:

| Entity | Purpose and key information |
|---|---|
| Catalog state | Connection identity/scope, config or credential revision, refresh status, last success, published generation(s), counts, timestamps and sanitized errors. |
| Container | Connection and ordered path components, display name, provider kind, published generation and source-observed timestamp. |
| Table/view | Container, ordered path, exact catalog spelling, type, source description, source modification/version when available, snapshot classification and observed timestamp. |
| Field | Table, ordered nested field path, display name, source type/mode, description and source order. Preserve field hierarchy where the provider exposes it. |
| Refresh job | Connection, requesting actor or approved service identity, scope (full/container/table), status, claim/lease, progress, timestamps and sanitized failure/retry information. |

Use structured path components and provider-specific normalization for comparisons; do not make a dotted display string the unique identity. Preserve exact catalog spelling, including quoted names and BigQuery nested fields. Never conflate `Orders` with `ORDERS` where the connector distinguishes them. The existing connector/allowlist limitations on ambiguous identifiers still apply; storage does not silently expand supported identifiers.

Link existing curated definitions at read/search time, respecting their **approved/draft/deprecated** status and tenant visibility. Source descriptions and curated definitions have distinct provenance and ownership. Do not overwrite definitions when metadata refreshes or remove user favorites and usage when a table disappears.

Use indexed foreign keys and efficient batch writes. Capture table descriptions where the provider can supply them at reasonable cost; do not issue 28,000 individual schema requests by default. Some providers or bulk endpoints expose less detail or impose listing caps: represent unsupported/incomplete data explicitly and measure before adding extra calls.

### Refresh and publication

- On first use of an unindexed connection, enqueue a durable job and return an explicit `building` state. Explorer shows progress; browsing can continue through the existing live catalog where feasible. No fabricated empty-success result.
- Serve an existing published catalog while an authorized refresh runs. Initial proposed schedule: **daily**, subject to source quotas, cost and operator configuration. Also support manual refresh; targeted container/table refresh is a later milestone if not needed for the first usable release. Display last successful refresh, age, progress and partial failures.
- Persist job claims with a unique active-job constraint per connection, lease/heartbeat and bounded retries. A process crash or deploy must allow a new worker to recover abandoned jobs. Duplicate requests coalesce; refresh work must not depend on a browser remaining open.
- Fetch outside a database transaction, stage changes per container, then atomically publish a complete container generation in a short transaction. Search must not observe a half-written container. Bound source calls, DB batch size, parallelism and timeouts; do not hold a DB connection while waiting on remote source calls.
- Only a **successful, complete** enumeration of a scope may remove objects previously observed within that scope. A failed, timed-out, truncated, capped or cancelled enumeration must preserve that scope's previous generation and report it as stale/partial. Detect list limits rather than silently treating truncated output as complete.
- A failed root listing must not delete existing containers. A successful complete root listing may retire containers no longer visible; a successful complete container listing may retire missing tables/fields within that container. Retired metadata must no longer appear in normal search. Retain/clean up old generations and job records on a bounded schedule.
- Each job captures an immutable connection/config/credential/allowlist revision. Before publication, verify it is still current; discard stale work and enqueue a new job when needed. Deletion or ownership changes cancel jobs and remove or hide indexed data. Credential or endpoint changes must not publish metadata produced under the old identity as the new connection's results.
- The initial migration conservatively hides all stored container/table/field metadata when the connection
  revision changes, including an allowlist-only edit, until a new revision is published. Refining this to
  preserve still-authorized scopes on allowlist changes requires read-time filtering and explicit tests.
  The revision detects changes stored on the connection row, not out-of-band rotation of a Secret Manager
  `latest` version or local credential file; resolve and verify immutable credential versions before enabling
  durable refresh for such references.
- Where a provider exposes trustworthy modification/change information, use it for incremental refresh; otherwise explicitly perform a scoped full scan. Initial delivery need not claim every provider supports incremental discovery.

The existing DuckDB workspace worker is deliberately isolated from source credentials. Do **not** give it database credentials merely to schedule refreshes. Implement a bounded API-side worker or a dedicated refresh service with an explicit identity and lifecycle; decide the execution mechanism during implementation. A durable PostgreSQL queue coordinates workers but is not itself a running scheduler.

### Security and trust boundaries

Metadata can reveal sensitive names and descriptions. Treat it as protected tenant data even though it contains no table rows.

- Use the existing connection lookup and current policy before reads, search, refresh requests and job status. Personal connections are visible only to their owner; organization-shared connections only within their tenant. Use `tenant_transaction(tenant_id, user_id)` where personal connection visibility is required.
- All new public metadata/job tables must follow the repository's `ENABLE` **and** `FORCE ROW LEVEL SECURITY` plus explicit grants invariant. Test with `odn_app`, never only a superuser. Policies must enforce the underlying connection's visibility, including `owner_user_id`; tenant-only RLS is insufficient for personal connections.
- Platform connections have `tenant_id IS NULL` and a special visibility/write model. Decide and test a narrowly scoped refresh/read mechanism for those records; do not make all null-tenant rows globally writable, add a general RLS bypass, or leak tenant-specific definitions/favorites across tenants. Platform catalogs may be shared only when visibility and source identity are equivalent.
- Apply the **current** object allowlist at every read, search result, count and agent response, not merely when indexing. Tightening an allowlist must immediately hide objects, fields, snippets and counts even if a refresh is running or an older generation remains. Expansion schedules refresh for newly allowed objects.
- Source permissions can change independently of the app. The selected default retains stale metadata indefinitely after a failed refresh, with clear age/error warnings; live queries still enforce source permissions. This can expose previously authorized metadata after source revocation. A stricter fail-closed expiration mode for sensitive deployments remains future work; do not silently treat an unsuccessful permission refresh as proof that previous access still exists.
- Run personal refreshes only under an explicitly authorized owner context or a narrowly scoped service mechanism that preserves owner, tenant, connection and credential boundaries. If the initiating user is deleted, loses access or the credential is revoked, fail closed. Shared refreshes similarly require authorized credentials; never persist plaintext credentials in job rows.
- Treat descriptions and business text as **untrusted data**, not instructions to the LLM. Do not include unrestricted descriptions in system prompts, logs or error messages. Review and approve any future export of metadata to external embedding providers; do not enable embeddings automatically.

### Search service and Explorer

Add a bounded server-side search API returning matching containers, tables and matching fields, with structured paths, source/curated snippets, pagination, status and freshness. Browse endpoints return paginated children and fetch a selected table's fields separately; avoid a 28,000-table response on initial load. Retain live connector fallbacks only when explicitly reported and authorized.

Start with PostgreSQL lexical retrieval, exact/prefix matches and appropriate indexes. Compare full-text search and substring/trigram behavior on real identifiers before selecting extensions/indexes; names like `customer_id`, nested paths, abbreviations and mixed case require tests. Preserve current behavior: every search term can match anywhere in the container/table/field hierarchy, favorites and recency influence ordering, matching field highlights remain available, and users can hide/show snapshot-named tables. Do not exclude snapshots from storage solely because they are hidden by default. Counts must obey current visibility and clearly distinguish exact versus estimated totals.

Keep browsing, favorites, table scope and SQL preview usable while a catalog is stale or rebuilding. Clearly distinguish `not indexed`, `building`, `ready`, `stale`, `partial` and `failed`; report per-scope errors without leaking raw SQL, secrets or source-driver details. Show last successful timestamp, refresh progress and a manual refresh action; don't label a cache read as active indexing.

Suggested API contracts (names provisional):

```text
GET  /connections/{id}/metadata/status
GET  /connections/{id}/metadata/containers?cursor=...
GET  /connections/{id}/metadata/tables?parent=...&cursor=...
GET  /connections/{id}/metadata/search?q=...&include_snapshots=false&cursor=...
GET  /connections/{id}/metadata/table?path=...
POST /connections/{id}/metadata/refresh       { scope: all | container | table, path?: [...] }
```

Each list has a bounded limit and stable cursor, a freshness/status envelope and an explicit partial/error indication. Refresh returns an accepted or already-running job identifier; implement and test the selected refresh roles and rate limits. Keep the existing endpoint temporarily for compatibility, but avoid using its full-index response in the new UI or agent. Remove/deprecate it only after consumers migrate.

### Agent retrieval and metadata quality

Replace the agent's root-catalog keyword scan with the same authorized search API. First return a bounded default of **5–10** ranked candidates with structured path, table/view kind, short source/approved-definition snippets, matching fields, score or rank reason, and freshness. On selection fetch only that table's full schema and authorized curated definitions. Apply bounded field/description/token budgets and indicate truncation. Preserve the existing behavior of asking the user when multiple candidates are plausible.

Favor exact names and relevant approved definitions; use favorites/recent usage as a ranking signal, not as evidence that an object is relevant or authorized. Clearly distinguish source descriptions from approved, draft and deprecated definitions. The agent should cite the selected table/fields and request clarification instead of inventing relationships, measures, units or join keys. Record useful quality signals such as selected result rank, no-match rate and schema-tool calls without storing sensitive questions or metadata in logs.

Optional later: add pgvector-based semantic retrieval only if a labeled evaluation demonstrates material gains over lexical search. Specify the embedding provider, consent/data handling, cost, dimension/model version, update/deletion lifecycle and per-tenant authorization **before** indexing text externally. Keep lexical fallback; vector similarity is not permission or truth.

## Operational targets and verification

These are proposed targets to validate with a representative 28k-table catalog, not claims about current performance:

- Warm catalog initial status and first page: **p95 under 2 seconds** after API restart, including auth and DB access.
- Typical indexed keyword search: **p95 under 1 second** end-to-end for bounded responses; report cold/warm measurements and result sizes.
- While refresh is running, previously published metadata remains searchable; source indexing must not monopolize API threads, the ten-connection pool or source quotas.
- First build may still take minutes. Measure build duration, source call counts/cost, per-container failures, catalog storage/index size and memory; choose concurrency from measurements rather than hard-coding a promise of speed.
- Define a representative evaluation set of real/approved table-and-field queries, expected candidates and permission contexts. Track relevance (e.g. top-5 recall), latency, no-match rate and LLM tool-call/token usage against the current baseline.
- Monitor job age/progress, duplicate suppression, failures/retries, stale age, DB/storage use and refresh source load. Alert on stuck/old jobs and repeated failures without logging metadata content or secrets.

Acceptance must include two tenants, two users with personal/shared/platform connections, explicit allowlist narrowing and expansion, source permission changes, credential/ownership changes during a run, mixed-case/nested identifiers, snapshots, empty/large catalogs, restart/lease recovery, concurrent refreshes, capped listings, partial failures and deletions. Verify API/agent results and counts never disclose unauthorized metadata. Add production-scale load tests and compare first build, warm open, search, refresh and LLM relevance before rollout.

## Explorer comparison preview

The current worktree has an **opt-in preview**, not a production rollout. Set
`ODN_METADATA_SEARCH_PREVIEW=true` in the API process environment and apply the catalog migrations before
starting that process. The default is false: Explorer then uses the existing full-index download. When enabled,
Explorer offers **Stored metadata API**, **Shared downloaded index**, and **Browser full index**; each
browser index is loaded only when selected.
Search and refresh remain gated by the authenticated connection and current policy. Tree browsing and opening
table details still query the source. Agent retrieval is unchanged.

For local Compose, the API environment accepts `ODN_METADATA_SEARCH_PREVIEW=true` when the service is
created. Recreating the API interrupts that service and runs pending migrations, so coordinate before doing
so on a shared installation.

An eligible connection owner, or a shared connection's administrator/data lead, can explicitly build or
refresh the index. The preview starts at most one refresh thread per API process; this is neither a distributed
worker nor the approved daily scheduler. After a process crash or expired lease, an authorized user must
retry. Platform connections require an operator-only refresh mechanism that is not implemented. Mutable
`file:` credentials and Secret Manager `latest` references are rejected because their contents can change
without changing the connection revision; use encrypted connection-row credentials or a numeric secret
version. Source authorization, quotas and costs must be checked before running a real build.

Explorer displays index-load time for browser mode, browser search time, and request time for stored search
(network plus server). These are different measurements: compare first-build time and payload separately from
warm search, using the same source, permissions, queries and snapshot setting. API results are paged in
batches of 50 (up to 200 displayed), with up to six matching fields per table; the browser path downloads
all fields and can expand additional matches. Stored pagination does not freeze a catalog generation or
favorite/recent rankings, so concurrent refreshes or preference changes can affect subsequent pages.

### Shared downloadable index, 2026-11-26

The gated `GET /v1/connections/{id}/metadata-index` builds a browser-shaped index from PostgreSQL, not the
source. It is authorized for the current tenant, user, connection revision and `SqlPolicy`; containers and
tables are individually filtered before fields and curated definitions are included. Tenant-specific
definitions are merged at download time, while favorites and recent activity remain per-user. No rows,
sample values or credentials are included. The response is private and `no-store`; switching modes or
opening a new session downloads again. There is no persistent browser cache or conditional download.

The read uses a repeatable-read transaction for a consistent database view of status and published rows.
Publication is atomic per container, not across the whole catalog: a partial result can combine scopes
from different attempts and is prominently marked stale/incomplete. A new download returns 409 during a
queued/running refresh instead of presenting mixed in-progress scopes as complete; a previously downloaded
index remains usable with a refresh notice, and stored API search remains available. Unindexed/failed
catalogs also return 409. Downloads are bounded to 100,000 authorized tables, 1,000,000 authorized fields
and 64 MiB of accumulated metadata text; excess returns 413, and database statement timeout returns 503.
These are safety limits, not guarantees about final JSON size or browser memory. As with any download,
permissions are checked at request time and data already delivered to a browser cannot be recalled.
Source-side permission changes still require a successful refresh, as described above.

On the same approved local development connection (1,575 tables, 45,453 fields), the first observed Explorer
shared download including JSON parsing took **0.42 s**; three subsequent mode-switch loads were
**0.59, 0.54 and 0.42 s**. Three direct browser-to-BFF fetch-and-parse samples took **594, 318 and 356 ms**
and returned **3,371,509 bytes decoded JSON** each. For `listing`, the shared browser scan took **3–5 ms**,
and returned the same **31 table paths** as stored API search; one API request took **130 ms** excluding
its 250 ms typing debounce. The earlier source-built index load on this connection took **6.0 s**, and
its warm process-cached load took **0.3 s**; its browser search was 5–6 ms. These are small, local,
non-simultaneous samples, not p95, and encoded transfer sizes differ from decoded JSON. They do not predict
download time, memory, or parity for the real 28k-table source. UI search time excludes result rendering.
An isolated synthetic catalog of 28,143 tables and 281,430 fields produced 28,458,831 bytes of JSON;
three local service-read-plus-serialization samples took **1.12–1.20 s**. This excludes HTTP transfer,
browser parsing/search/rendering and realistic source descriptions, and is not an end-to-end benchmark.

On an isolated local PostgreSQL catalog containing **28,143 synthetic tables and 10 fields per table**,
three warm direct-service calls per query with 50-result pages took 1,292–1,598 ms for a table-name query,
1,409–1,471 ms for a field query, and 1,205–1,224 ms for a no-match query. This is not a p95, an HTTP or
browser benchmark, a comparison with the full-index path, or a production result. It does not meet the
proposed sub-second target; further indexed-search work and representative end-to-end measurements are
required.

On the approved local personal BigQuery development connection (9 datasets, 1,575 tables and 45,453 fields),
one complete stored build took 22.4 seconds. In the same browser session, loading the full index took 6.0
seconds and transferred approximately 242 KB encoded; a warm `listing` search took 5–6 ms in the browser,
versus 130–1,394 ms for stored API requests before the optimization below. Both returned the same 31 table paths, including with the
snapshot toggle changed; a no-match query also returned zero in both modes. These are individual local
observations, not a p95 or a test of the 28k-table source. The browser search timing excludes its initial
download, while the API timing includes the request and server response. More queries, result-page and
field parity, source load and cold/warm repetitions remain to be measured.

### Search optimization, 2026-11-26

The preview now disables PostgreSQL JIT for this search transaction: RLS-inflated planner costs triggered
expensive compilation for a short query. Migration `0037` adds a length-prefixed scalar path key and a
B-tree index to fetch only the current result page's fields. The original structured-path equality and
forced RLS checks remain in place. The migration computes keys for existing rows; it rewrites and indexes
the field table, so plan for a table lock and storage during rollout. The index occupied about 6.7 MiB on
the local catalog and 18 MiB for a synthetic 281,430-field catalog.

On the same local development connection and `listing` query, five warm browser-to-API requests took
73–109 ms, versus the earlier 130–1,394 ms; the browser's in-memory scan took 6 ms and the two modes
returned the same 31 table paths. The API figure excludes Explorer's 250 ms typing debounce; neither
figure includes full rendering. Browser mode still requires its initial source index load (6.0 seconds
in the earlier run, 0.3 seconds in a later warm run). These samples are not a controlled p95 benchmark.

On a new isolated synthetic catalog with 28,143 tables and 10 fields per table, five warm direct service
calls after optimization took 260–388 ms for a table-name search, 298–329 ms for a field search,
249–258 ms for no matches and 307–342 ms for a two-character search. A pre-change run on the same
catalog took 370–545 ms, 430–487 ms, 345–383 ms and 405–411 ms respectively (three calls per query).
These include local database connection setup but not HTTP, debounce or rendering. They are not a real
28k-table browser comparison. Candidate matching still scans source fields, and typical in-memory browser
scans remain faster; a safe substring index, concurrency testing and further profiling are still needed.

## Actionable plan

Status as of 2026-11-26. Checked items are completed in the current worktree, not necessarily committed or
deployed. Unchecked items are remaining work; no latency target or production validation is claimed.
The storage, queue, runner, connector and preview-search tests run against an isolated local PostgreSQL database.
Explorer has an opt-in comparison preview; the agent still uses its original path. None of this is deployed.

1. **Baseline and scope — in progress**
   - [x] Document the observed 28,143-table, 2,008-snapshot-name and 3+ minute user-reported baseline.
   - [x] Agree on daily refresh, manual refresh roles, operator-only platform refresh and indefinite stale
     visibility with warnings after failure.
   - [x] Identify the current index flow, listing caps and initial BigQuery metadata opportunities.
   - [ ] Measure source-call timings, payload size, memory, DB capacity, column counts and current LLM baseline.
   - [ ] Set source quota/cost budgets and validate bulk metadata availability and authorization against the
     actual customer source, locations and permissions.

2. **Schema and authorization — in progress**
   - [x] Add an initial forward migration for catalog state, containers, tables, fields and jobs, with
     structured paths, explicit grants, forced RLS and connection revisions.
   - [x] Test tenant and personal visibility, platform read-only access for the app role, operator-owned
     platform writes under RLS, connection deletion and revision-driven hiding in an isolated PostgreSQL DB.
   - [x] Apply and test current allowlists and provider-specific object restrictions before preview search pagination;
     fail closed if the SQL filter disagrees with `SqlPolicy`.
   - [x] Reuse non-deprecated curated definitions and current-user favorites/recent history in preview search.
   - [ ] Collect richer BigQuery metadata and verify credential revision handling for externally rotated secrets.
   - [ ] Implement the narrowly scoped platform operator refresh mechanism and authorization; schema
     permissions alone do not provide a running operator service.

3. **Durable refresh — runner implemented; scheduler not started**
   - [x] Add [`list_catalog_complete`](../backend/src/odn/platform/connectors/base.py) as a separate
     complete-listing-or-error operation. BigQuery/PostgreSQL remove the legacy 10,000-item cap for this
     operation; MySQL/Oracle/Snowflake report their existing caps rather than returning a successful truncated
     scope. Focused tests cover these behaviors and a local PostgreSQL integration case.
   - [x] Add an atomic per-container publication primitive with bounded write batches and checks for current
     connection revision, job lease and allowlist. Tests cover rollback, expired/incorrect leases,
     revision changes and rejected out-of-scope objects.
   - [x] Add scoped queue primitives for concurrent enqueue/coalescing, claims, heartbeat, sanitized
     failure and bounded lease recovery; recheck current owner/role, active user and tenant, and revision
     before operations and publication. Platform refresh remains operator-only and is not implemented.
   - [x] Add source collection primitives using complete enumeration, current allowlist and explicit failure
     for incomplete columns.
   - [x] Implement a single-job runner with independent heartbeat, complete-root reconciliation,
     atomic per-container publication, partial-scope preservation and sanitized job failure status.
     Root failure leaves the existing catalog untouched; mutable external secret references fail closed.
   - [x] Record processed container counts and the total after root discovery, including failed scopes.
     Progress updates require the current actor, revision and lease; a reclaimed attempt resets progress
     and must publish its own successful scopes before completion.
   - [x] Add an explicitly requested, per-process bounded preview dispatcher and a gated refresh endpoint;
     coalesced jobs run under their original authorized actor.
   - [ ] Implement a bounded service to discover and dispatch jobs, automatic crash recovery, daily/manual
     scheduling, operator-only platform refresh and distributed rate limits.
   - [ ] Support genuinely empty schemas, richer nested fields, immutable credential version verification
     and per-scope errors without mistaking incomplete results for complete listings.

4. **Read and search APIs — preview in progress**
   - [x] Add an opt-in `GET /v1/connections/{id}/metadata-search` preview with bounded keyword/field results,
     name-based snapshot filtering, name-match ranking, revision-bound keyset cursors and freshness/job status.
     Connection and current-policy authorization are applied before pagination; no counts are returned.
     `ODN_METADATA_SEARCH_PREVIEW` defaults to false. A five-second database statement limit surfaces a
     timeout as 503; an authorization-filter disagreement also fails closed with 503.
   - [x] Add an independent, connection-authorized `GET /v1/connections/{id}/metadata-status` behind the same
     preview flag. Search returns the same status, including processed/total scopes and the most recent
     completed outcome and sanitized error. While a new job is running, consumers must still warn about a
     previous failed, cancelled or partial refresh. Revision changes hide old catalog and job status.
     This endpoint only reports status; it does not schedule or start a refresh.
   - [x] Add current-user favorite/recent ranking, curated definitions and field relevance ordering.
   - [x] Add an RLS-safe scalar path index for result-page field retrieval and disable costly query JIT;
     verify the index is used on the synthetic catalog.
   - [x] Add a gated, bounded, no-store shared-index download with repeatable-read status and current
     per-object policy filtering; reject in-progress downloads and surface partial/stale state.
   - [ ] Add paginated container/table browsing, detail endpoints, per-scope freshness,
     indexed substring candidate search and allowlist-safe counts.
     Cursors do not freeze a catalog generation: concurrent refresh can change results between pages.
   - [ ] Benchmark realistic field counts, permissions, concurrent users, API latency and production-like data.
     Candidate matching still scans persisted fields and is not yet a production-ready indexed search.

5. **Explorer migration — preview in progress**
   - [x] Add a gated search-method selector, paginated stored keyword search, snapshot toggle, highlights,
     favorites/recent, refresh/progress/warnings and timing indicators; retain browser mode for comparison.
   - [x] Exercise the preview on an approved local BigQuery source, including build progress, mode switching,
     a same-query table-path comparison, snapshot toggle, no-match search and connection switching.
   - [x] Add shared-download mode and measure local download and `listing` table-path parity with stored search.
   - [ ] Expand end-to-end validation to field/result-page parity, stale/error handling and representative
     repeated measurements and browser memory on the real 28k-table source.
   - [ ] Replace source-backed tree/detail browsing if needed and complete the rollout only after acceptance.

6. **Agent integration — not started**
   - [ ] Use shared authorized search with bounded candidates, provenance and freshness; fix root-only
     matching and fetch details only for selected tables.
   - [ ] Test ambiguity, untrusted descriptions, relationship caveats and token limits.

7. **Verification and rollout — not started**
   - [ ] Run targeted integration, RLS/tenancy and crash/concurrency tests; benchmark the 28k-table case and
     compare agent relevance with baseline. Validate source authorization and costs.
   - [ ] Roll out behind a feature flag, retain rollback and monitor load, size, latency and relevance.

8. **Iterate — deferred**
   - [ ] Evaluate targeted/incremental refresh and optional semantic retrieval only after measured results and
     the necessary data-handling approvals.
