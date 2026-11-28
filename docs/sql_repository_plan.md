# Saved SQL ("my SQL repository"): plan

*Status: **S1 and S2 built, plus the S3 agent tool** (2026-11-27); sharing and folders are still to come. §8 records what changed from the plan. Related: SQL workbench ([UI_Design_sql_workbench.md](./UI_Design_sql_workbench.md)), Artifacts ([ArtifactsPanel.tsx](../frontend/app/components/ArtifactsPanel.tsx), [0008_artifacts_store.sql](../backend/src/odn/db/migrations/0008_artifacts_store.sql)).*

## 1. Goal

Today **.sql** in the workbench downloads `<table>.sql` and keeps nothing. Users want a **SQL repository** inside EasyQuery:

- save the editor's SQL under a **name they choose**;
- find it again quickly (search by name, text, table);
- open it back in the workbench, edit it, and **save over it**;
- still download it as a `.sql` file.

## 2. What exists today (findings)

| Piece | Today | Fit for a SQL repository |
| --- | --- | --- |
| `.sql` button (`Explorer.tsx` `downloadSql`) | Browser-only download named after the selected table (`orders.sql`), or `query.sql` | Keep as **Download** |
| Artifacts tab (`/v1/artifacts`, `list_store`) | Result tables, charts and uploaded files. Each belongs to a **conversation** (`session_id`), is **owner-only**, and is **insert-only** (`GRANT SELECT, INSERT`) | Right place to *show* saved SQL, but the wrong storage model: SQL files must be renamed, edited, deleted and kept across conversations |
| `workspace_files` + object store | Upload blobs (CSV/XLSX) | Overkill for a few KB of text, and no editing |

**Decision:** a dedicated, editable table `saved_queries`, listed in Artifacts as a fourth kind, **SQL**, and opened in the workbench.

## 3. UX

### 3.1 Workbench: the editor has a file identity

The workbench header shows which saved file you're editing:

```
SQL workbench · ▣ ca_businesses_by_naics.sql ●  [Save ⌘S] [Save as…] [.sql ↓]
```

- **Untitled** (new SQL, or SQL opened from a chat answer): the header shows `Untitled query`, and **Save** opens the *Save as* popover.
- **Save as…** is a small popover, not a modal:
  - **Name**: pre-filled with a suggestion (§3.2) and selected, so typing replaces it. `.sql` is shown as a fixed suffix.
  - **Description** (optional, one line).
  - **Save**. Enter saves, Esc cancels.
- **Opened from a saved file:** **Save** (⌘S / Ctrl+S) overwrites it. A dot (●) marks unsaved changes, and leaving with unsaved changes asks for confirmation.
- **Rename:** click the file name in the header and edit it in place.
- **Download (.sql ↓):** unchanged behaviour, but the file uses the saved name (or the suggestion when the SQL is unsaved).
- The saved file remembers its **connection** and the **tables** it reads. Opening it selects that connection and opens the first table, like "Open in SQL workbench" from the chat does today.

### 3.2 Suggested name

The name is generated locally, with no AI and no tokens, so it's instant. Built from the SQL:

- the main table: `businesses`;
- the grouping and filter columns, when short: `by_naics`, `ca`;
- the result: `top10`, `count`.

For example, `businesses_by_naics_ca.sql`. Fallback: `<table>_query_<yyyy-mm-dd>.sql`.

- Lower-case `[a-z0-9_-]`, at most 80 characters.
- Unique per user: a clash adds `_2`, and the popover says so.

### 3.3 Artifacts tab: SQL kind

- **Filter:** a new **SQL** chip, next to All / Tables / Charts / Files.
- **Search box** (new, for every kind; most useful for SQL). It matches name, description, SQL text and table names. Example: typing `naics` finds every query that uses that column.
- **SQL rows:**

  ```
  ▣ ca_businesses_by_naics.sql   BigQuery · idap-int · edr_…firmographics_view   edited 2h ago
    "Top industries in California"                       [Open in workbench] [⋯]
  ```

  - Clicking the row expands a read-only, highlighted preview (the same `SqlCode` used in the chat).
  - The **⋯** menu has Rename, Duplicate, Download `.sql`, Copy SQL, Delete (with undo for 5s).
- **Sort:** recently edited first. SQL files aren't tied to a conversation, so the "This conversation only" toggle skips them.

### 3.4 Other entry points (cheap, high value)

- **Chat:** next to *Open in SQL workbench* on each SQL block, a **Save SQL** action opens the same *Save as* popover without leaving the chat.
- **Explorer, selected table:** "Saved SQL (3) ›" when the user has saved queries that read this table. It opens Artifacts filtered to SQL and that table.

## 4. Backend

### 4.1 Store: migration `0040_saved_queries.sql`

```sql
CREATE TABLE saved_queries (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id        uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  name           text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,79}$'),   -- shown with ".sql"
  description    text NOT NULL DEFAULT '' CHECK (length(description) <= 300),
  sql_text       text NOT NULL CHECK (length(sql_text) BETWEEN 1 AND 100000),
  connection_id  uuid REFERENCES connections (id) ON DELETE SET NULL,          -- null: workspace SQL (q1, q2…)
  dialect        text NOT NULL,
  tables         text[] NOT NULL DEFAULT '{}',                                  -- from the SQL policy parser
  version        integer NOT NULL DEFAULT 1,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, user_id, name)
);
CREATE INDEX saved_queries_recent ON saved_queries (tenant_id, user_id, updated_at DESC);
CREATE INDEX saved_queries_search ON saved_queries
  USING gin (to_tsvector('simple', name || ' ' || description || ' ' || sql_text));

CREATE TABLE saved_query_versions (            -- the last 20 saves per query, for "restore"
  query_id   uuid NOT NULL REFERENCES saved_queries (id) ON DELETE CASCADE,
  tenant_id  uuid NOT NULL, user_id uuid NOT NULL,
  version    integer NOT NULL,
  sql_text   text NOT NULL,
  saved_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (query_id, version)
);
```

- **RLS:** `ENABLE` + `FORCE`, with an owner-only policy (`tenant_id = odn_current_tenant() AND user_id = odn_current_user()`) on both tables, like `workspace_files`. Grant `SELECT, INSERT, UPDATE, DELETE` to `odn_app`. The `tests/db/test_rls.py` guard covers both.
- **Optimistic concurrency:** `PUT` sends the `version` it edited. If the stored version is newer (the file was saved in another tab), the API returns 409. The UI then offers *Overwrite* or *Save as copy*.
- **Size and count limits:** 100 KB per query and 1,000 queries per user, so it stays a SQL library, not a data dump.
- **Tables** are parsed with the existing `SqlPolicy` parser at save time, so search-by-table and the Explorer count work. Saving is **not** blocked when the SQL wouldn't pass the allowlist: the check still happens on Run.

### 4.2 API: `odn/api/saved_query_routes.py`

| Method | Path | Behaviour |
| --- | --- | --- |
| GET | `/v1/saved-queries?q=&table=&limit=` | Search the list (no `sql_text` in list items; a 200-character snippet instead) |
| POST | `/v1/saved-queries` | `{name, description, sql, connection_id}`. Returns `201 {id, name, version}`; `409 name_taken` with a suggested free name |
| GET | `/v1/saved-queries/{id}` | Full SQL and metadata |
| PUT | `/v1/saved-queries/{id}` | `{sql?, name?, description?, connection_id?, version}`. `409 conflict` if the version is stale |
| DELETE | `/v1/saved-queries/{id}` | Delete. The UI's 5-second undo simply delays the call |
| GET | `/v1/saved-queries/{id}/versions` | The last 20 versions (phase 2 UI) |
| GET | `/v1/saved-queries/{id}/download` | `Content-Disposition: attachment; filename="<name>.sql"` |

`list_store` (Artifacts) adds `kind="sql"` items, so the **All** view interleaves SQL by `updated_at`.

### 4.3 Agent (optional, phase 2)

A `list_saved_queries` tool would let the user say "run my `ca_businesses_by_naics` query" or "adapt my revenue query to Texas". It is read-only, and the SQL still goes through `run_sql` and its approval flow.

## 5. Privacy and governance

- Saved SQL is **private to its owner** (RLS), like uploads and charts. Saving runs nothing and scans nothing.
- The SQL text can contain literals (names, IDs), which is one more reason it stays private in v1.
- **Sharing** is phase 3 (§6): a *Share with my organization* toggle, shown read-only to colleagues, who can *Save a copy*. It would reuse the shared-conversation pattern and never expose another user's personal connection.

## 6. Delivery

| Phase | Scope | Effort |
| --- | --- | --- |
| **S1** | Migration and RLS tests; CRUD API with name-clash and version checks; workbench file identity (header name, Save / Save as popover with suggested name, ⌘S, ● dirty marker, rename, download with the saved name); Artifacts **SQL** filter, search box, SQL rows with preview / Open / ⋯ menu | M |
| **S2** | "Save SQL" from chat SQL blocks; Explorer "Saved SQL (n) ›"; version history with Restore; conflict dialog (Overwrite / Save as copy) | S–M |
| **S3** | Share with organization (read-only, Save a copy); agent tool `list_saved_queries`; folders or tags if people collect many queries | M |

## 7. Decisions (2026-11-27, open to change)

1. **A separate editable store**, not `workspace_files`. SQL files are long-lived, editable and independent of conversations.
2. **Name format:** a user-chosen file name, `[a-z0-9_-]`, unique per user, with a locally generated suggestion. No AI call.
3. **Private in v1**; sharing comes later.
4. **Saving doesn't validate against the allowlist**; Run does. People save work in progress.
5. **The last 20 versions are kept** per query, so an overwrite is never a loss.

### Open questions for you

- **Name rules:** lower-case file-style names (`ca_businesses_by_naics.sql`), or free-text titles ("CA businesses by NAICS") with the file name derived from them? *Proposed: file-style, since it is a "SQL repository" and matches the download.*
- **Folders/tags:** leave them out until people have dozens of queries? *Proposed: search first, folders in S3.*
- **Sharing with the organization in S1** or later? *Proposed: later.*

## 8. As built (2026-11-27)

- **Store:** migration `0040_saved_queries.sql`. The tables are as in §4.1, except that search uses `ILIKE` on name, description, SQL and tables (the per-user lists are small) plus a GIN index on `tables`; there is no full-text index. RLS is owner-only on both tables.
- **Service and API:** `odn/platform/workspace/saved_queries.py` and `odn/api/saved_query_routes.py`, with the routes from §4.2.
  - `GET ?full=true` returns the whole SQL (at most 20 items) for the agent.
  - Renaming or changing the description doesn't create a version; only SQL changes do.
  - On `409`, `name_taken` includes `suggestion` (the next free `name_2`); `conflict` includes the current `version`.
- **Artifacts:** the panel merges `/v1/saved-queries` with `/v1/artifacts` itself, sorted by time, so `list_store` is unchanged.
  - The new search box filters every kind: saved SQL on the server, other kinds in the browser.
  - "This conversation only" leaves saved SQL out.
- **Workbench header:** `▣ name.sql ●` (click to rename), **History**, **Save** (⌘S), **Save as…** (⇧⌘S).
  - Replacing unsaved edits (opening another table, chat SQL or saved query) asks first.
  - On a version conflict, OK overwrites; Cancel saves your version as `name_copy`.
  - **.sql** downloads under the saved name, or the suggested name for unsaved SQL.
- **Suggested names** (`lib/savedSql.ts` `suggestName`): table, then `by_<group columns>`, a short quoted filter value, and `topN` or `count`, for example `edr_firmographics_view_by_addr_state_top10`.
- **Chat:** every SQL block gets **Save SQL**. It opens the workbench with that SQL and the *Save SQL* popover already filled in.
- **Explorer:** the selected table shows **Saved SQL (n) ›** when the user has saved queries that read it. It opens Artifacts on SQL, searched by the table name.
- **Agent:** `find_saved_sql(search)` finds saved queries for requests like "run my revenue query". The agent runs or adapts the SQL through `run_sql`, with the usual checks and approval.
- **Not yet:** sharing with the organization, folders and tags (S3).
