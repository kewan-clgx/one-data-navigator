# SQL workbench vs BigQuery console: comparison and proposal

*Status: P1–P3 implemented 2026-11-26 (live check, results panel, table pane, adaptive layout, Focus); P4 extras (Recent queries, header ▾ menus, Chart tab) still open. Current behaviour: [UI_Design_note.md](./UI_Design_note.md) §3.3b.*
*Inputs: side-by-side screenshots of the BigQuery console (Untitled query on `vw_edr_firmographics_view`) and our workbench (`vw_edr_firmographics_standardized`), the current code ([Explorer.tsx](../frontend/app/components/Explorer.tsx), [DictionaryPanel.tsx](../frontend/app/components/DictionaryPanel.tsx), [ColumnProfile.tsx](../frontend/app/components/ColumnProfile.tsx), [ResultsGrid.tsx](../frontend/app/components/ResultsGrid.tsx), [connectors/base.py](../backend/src/odn/platform/connectors/base.py)) and [UI_Design_note.md](./UI_Design_note.md) §3.3 and §3.3b.*

## 1. Summary

Our editor is already on par with BigQuery's: dialect-aware highlighting, completion of tables and columns, format, wrap, ⌘↵ to run, selection runs. Our **Explorer search** is ahead of it, because it finds tables by column name across 2,000+ tables, which BigQuery can't do.

Where BigQuery is clearly better is **everything around the editor**. The editor, the results and the table reference read as one tool:

1. A live validator line ("This query will process 4.02 GB when run").
2. A framed *Query results* panel with tabs and actions.
3. A readable grid: row numbers, faint zebra rows, muted `null`, header menus, page-size control.
4. A persistent *Reference* pane with a compact identity line ("9.97M rows • 4.02 GB • Last modified …") and a filterable field list.

Ours is a vertical stack (editor → note → results table → dictionary), and the bottom half reads like a separate document.

**Proposal: three panels with clear jobs, plus a header strip.**

| Panel | Job | Main changes |
| --- | --- | --- |
| **Query** | Write and validate | Editor plus a **live validator line** (bytes, policy, cap, "free preview") |
| **Results** | Read and act on output | A framed panel: header, tabs (Results · Chart · Run details), a readable grid, row #, actions incl. **Ask EasyQuery about this result** |
| **Table** | Understand the source | **Identity strip** (kind · rows · size · *last modified* · partition/cluster) plus **Fields** with profile stats and definitions in one table, a field filter, and a quiet **↻ Refresh profile** instead of the big Profile button |

Layout adapts: **side by side** (Query/Results left, Table right) when the workbench is wide, **tabs** (Results | Fields | About) when it's in the narrow chat column.

All five of your observations are adopted. Three are extended, and one is reframed so it shows up across the product (§3).

## 2. Side-by-side comparison

| Area | BigQuery console | EasyQuery workbench today | Verdict |
| --- | --- | --- | --- |
| Layout | Editor on top, *Query results* below it in one bordered workspace with a drag splitter; *Reference* pane on the right; query tabs across the top | Vertical stack in the chat column: editor card → note → results table → dictionary; no splitter; dictionary scrolls far below | **Borrow** the framed, resizable workspace; **adapt** the reference pane to our narrow column (§4) |
| Editor | Plain Monaco, File/Edit/Run/Tools menu bar, Run/Save/Schedule/Share | CodeMirror with dialect chip, Format, Wrap, Copy, .sql, Expand, Preview data, Run ⌘↵, status bar | **Ours is better.** Keep it; don't copy the menu bar |
| Pre-run feedback | Live dry run on every edit: ✓ "This query will process 4.02 GB when run", or an inline syntax error with line and column | Nothing until Run; cost and policy errors appear after Run (422/409) | **Borrow (high value).** We already have `estimate_cost` and the SQL policy on the server |
| Results frame | "Query results" header; tabs Job information · Results · Visualization · JSON · Execution details · Execution graph; actions Chat, View all jobs, Save results, Open in | One meta line ("10 rows · scanned 24 GB · 3826 ms") and a bare table | **Borrow** header plus tabs (Results · Chart · Run details); skip JSON and Execution graph |
| Grid readability | Row-number column, faint zebra striping, `null` in muted italic, per-column ▾ menus, sticky header, 50 rows/page selector, "1–50 of 100" | No row #, no striping, `NULL` in the same weight as data, header sort only in the Results tab, fixed 100-row pages | **Borrow** row #, zebra, muted null, sticky header and pager; add numeric alignment |
| Table identity | "9.97M rows • 4.02 GB • Last modified May 20, 2026, 2:39 PM" right under the table name | Long full path as H2, then "0 rows · 0 B · 0 of 66 fields have a business definition" (wrong for views: shows 0 rows / 0 B) | **Borrow and fix**: compact identity strip with last modified, plus correct view handling |
| Field reference | Filterable field list (name, value) in the side pane; click opens details | Full fields table (Field, Type, Fill rate, Range, Source description, Business definition) below results, collapsible | **Ours is richer**; borrow the filter and the side placement |
| Profiling | None built in (separate Data profile scans in Dataplex) | Profile button (10% sample) → fill rate and range per field | **Our edge.** Demote the button to an in-section refresh (§3, item 5) |
| Discovery | Resource search finds projects, datasets and tables by name only | Explorer search finds tables **and columns**, with highlighted matches and counts ("61 tables match, 809 matching fields") | **Our edge**; surface it in the workbench too (§3, item 4) |
| AI | Gemini "Chat" button in results | The whole app is the AI; the workbench is deliberately AI-free | Add a one-click **Ask EasyQuery about this result** hand-off |
| Free preview | "Preview" tab on a table (no scan) | `SELECT * … LIMIT n` on a table is free since today (tabledata.list) | Make it visible: validator says "Free preview (no scan)" |

## 3. Your observations, evaluated

### 1) Show "Last modified": agree, and put it in an identity strip, not inside the profile

"Last modified" describes the **table**, so it belongs next to the table name, not in the profile (which describes a sample of the data). Proposed strip under the table title, BigQuery-style and with a muted separator:

```
vw_edr_firmographics_standardized                                    ☆  ⧉ copy path
View · ≈10.3M rows (profiled) · Modified May 20, 2026 2:39 PM · project.dataset ▸
Table · 9.97M rows · 4.02 GB · Modified May 20, 2026 2:39 PM · Partitioned by load_date · Clustered by state
```

- **Show the dataset path, not the full path, as the title.** The full path goes in a "copy path" control and a tooltip. Today the 90-character H2 wraps to two lines and pushes everything down.
- **Views:** never show "0 rows · 0 B" (today's output). Show "View", plus "≈ N rows (profiled)" when a profile exists, otherwise just "View".
- **Freshness:** show relative time in muted text ("2 days ago") with the absolute date in a tooltip.
- **Backend:** add `modified_at` (and optionally `created_at`) to `TableSchema`.

  | Connector | Source of the timestamp | Reliability |
  | --- | --- | --- |
  | BigQuery | `Table.modified` on the call `get_schema` already makes | exact |
  | Snowflake | `INFORMATION_SCHEMA.TABLES.LAST_ALTERED` | exact |
  | MySQL | `information_schema.TABLES.UPDATE_TIME` | can be NULL for InnoDB; hide when NULL |
  | Oracle | `ALL_OBJECTS.LAST_DDL_TIME` | DDL changes only; label it "Schema changed" |
  | PostgreSQL | none reliable | omit |

### 2) More integrated SQL editing and query results: agree; make it one workspace

- **One bordered workspace.** Editor, validator line and results panel share one frame with a **drag splitter** between editor and results (keyboard-resizable, remembered). The editor can collapse to a one-line summary when you're reading results.
- **Validator line** (new) between editor and results, debounced about 600ms after typing stops. It calls a cheap server check: policy + dry run, and no run.
  - ✓ `This query will scan ~4.0 GB` (BigQuery dry run).
  - ✓ `Free preview, no scan` (our new tabledata.list path).
  - ⚠ `Above the 100 GB per-query limit. Name columns instead of *, filter on the partition column.`
  - ✕ `Table not in allowlist: …` or `Syntax error at line 2, col 8`.

  Users learn the cost before running, which is exactly what tripped `SELECT * … LIMIT 10` today. For connectors without a dry run (Postgres, MySQL, Oracle, Snowflake), show the policy result only, plus "cost estimate not available for <dialect>".
- **Results panel header:** `Query results · 10 rows · scanned 24 GB · 3.8 s`, with tabs **Results · Chart · Run details**, and actions on the right: **Download CSV**, **Copy**, **Open in Results tab** (full sort and paging), and **Ask EasyQuery about this result**. The last one is our differentiator: it sends the SQL and the result reference back to the chat.
- **Run details tab:** job ID (copyable), bytes processed and billed, duration, cached yes/no, the exact SQL that ran (important when a selection ran), and the policy decision. That replaces BigQuery's Job information and Execution details tabs without the noise.

### 3) Row # and slightly shaded rows: agree, with the full set of grid-readability rules

- **Row number gutter:** sticky on the left, muted, `tabular-nums`, not selectable. It numbers across pages (row 101 on page 2).
- **Zebra striping:** very light, `--surface-alt` at about 50%. It must also work in the dark appearances.
- **NULL:** render as muted italic `null`, not uppercase `NULL` at full weight. Today NULL competes visually with real values (see the `business_brand_name` column).
- **Numbers:** right-aligned with tabular figures; text left-aligned; long text truncated with an ellipsis, full value in a tooltip, and a cell click selects or copies.
- **Header:** sticky, with a type hint on hover (`INT64`, `STRING`) and a ▾ menu (Sort asc/desc, Copy column name, Filter in Fields).
- **Pager:** "1–50 of 100", with a rows-per-page selector (25/50/100) and first/prev/next/last, like BigQuery.
- **Density:** keep the current compact row height. Striping replaces the need for heavy row borders, so row lines can become hairlines.

### 4) Column-level search is our edge: agree; make it visible everywhere

- **In the workbench Fields section:** a **filter box** like BigQuery's. When you arrived from an Explorer search, it's pre-filled with that search term, so matched fields stay highlighted in context.
- **Click to insert:** a field name inserts it at the editor cursor; ⌘-click appends it to the SELECT list. This turns "found the column" into "used the column" with no typing.
- **Search business definitions too:** include Collibra and curated definitions, not only names. For example "revenue" finds `corporate_sales_amount` if its definition says revenue.
- **Product and marketing:** worth a line on the landing page ("Find any column across thousands of tables") and an empty-state hint in Explorer.

### 5) Reorganize into three integrated sections and demote the Profile button: agree

- **Fields and profile become one table.** Field · Type · Fill · Range/Top values · Definition, with fill as a small inline bar, not a percentage column. Source description and business definition sit under the field name in muted text, or in an expandable row, so the table doesn't have six wide columns.
- **Profile becomes a quiet section action.** The section header reads `Fields (66) · Profile from 10% sample · 2 h ago  ↻ Refresh`. With no profile yet, the fill and range columns show a single inline prompt instead of a big primary button: "No profile yet · **Profile this table** (samples 10%, ~0.4 GB)". Include the scan size so the cost is visible before clicking, which matches the governance story.
- **About** (table description, business definition, partitioning and clustering, owner, Collibra link) becomes its own small tab or block, instead of three bold label rows above the fields.

## 4. Proposed layout

**Wide** (workbench ≥ about 1,080px, for example with the side panel hidden or Expand on):

```
┌ SQL workbench ─ vw_edr_firmographics_standardized ─────────────────────── ⤢  Back to chat ┐
│┌ Query ─────────────────────────────────────────────┐┌ Table ───────────────────────────┐│
││ BigQuery · idap-int   Format Wrap Copy .sql   ▶ Run ││ vw_edr_firmographics_standardized │
││ 1 SELECT *                                          ││ View · ≈10.3M rows · Modified 2d  │
││ 2 FROM `…standardized`                              ││ [Fields 66] [About]               │
││ 3 LIMIT 10                                          ││ ⌕ Filter fields  (location rev…)  │
││ ✓ Free preview: no scan        Ln 3 · ⌘↵ run       ││ Field          Type  Fill  Range  │
│├═══════════════ drag ═════════════════════════════─┤│ location_rev…  INT64 ▇▇▇░  0–9.1M │
││ Query results · 10 rows · 0 B · 0.8 s               ││ …                                 │
││ [Results] [Chart] [Run details]   CSV Copy Ask AI ↗ ││ Profile: 10% sample · 2h ago ↻    │
││  #  clip_id     address_id  business_name   …      ││                                   │
││  1  7688336502  1996982430  ABBOTT LABORATORIES     ││                                   │
││  2  1063718642  1767944705  HARBORSIDE NH LTD …     ││                                   │
││              1–10 of 10   Rows per page 50 ▾        ││                                   │
│└─────────────────────────────────────────────────────┘└───────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Narrow** (today's default: the workbench in the chat column at about 860px). The same three panels, with the Table pane folded into tabs under the splitter:

```
Query (editor + validator)
════ drag ════
[Results] [Fields 66] [About] [Run details]        CSV  Copy  Ask AI ↗
<grid or fields table>
```

**Design decisions:**
- **Adaptive, not two layouts to maintain.** Same components, and a container query switches side pane ↔ tabs.
- **"⤢ Focus" (the existing Expand, upgraded)** is an explicit, remembered toggle that hides the app side panel for the workbench session and restores it on Back to chat. It is off by default; the user decides (see §7).
- **Default tab after Run is Results.** After opening a table from Explorer (no run yet), it's **Fields**.
- **Keep the scope chip and Back to chat.** The workbench stays a mode of the chat app, not a separate console. That's deliberate: BigQuery's 8 query tabs are power-user clutter we don't need yet. A small **Recent queries ▾** history dropdown in the Query header covers most of the need.

## 5. Things not to copy

- The File/Edit/Run/Tools menu bar and Save/Schedule/Share in the editor header. Too many controls for our users; Copy, .sql and Share already exist elsewhere.
- JSON and Execution graph tabs: low value for our audience.
- Multiple query tabs: defer (use Recent queries).
- Google's dense, uniform chrome. Our editor style and appearances (Elegant, Code, Studio) stay; only the structure is borrowed.

## 6. Implementation outline (when we build)

| Phase | Scope | Effort | Notes |
| --- | --- | --- | --- |
| **P1: quick wins** | Grid: row #, zebra, muted `null`, numeric alignment, sticky header, page-size selector. Identity strip with `modified_at` (BigQuery and Snowflake first) and correct view handling. Profile button → section action with scan-size hint. | S–M | Backend: `TableSchema.modified_at`; frontend: the workbench results table and DictionaryPanel header |
| **P2: integrated workspace** | Bordered Query + Results frame, drag splitter, results header and tabs (Results · Chart · Run details), actions incl. Ask EasyQuery about this result. **Live validator** endpoint `POST /v1/connections/{id}/query/check` → `{decision, reason, bytes_estimated, free_preview, over_cap}` (policy + dry run, no execution, audited as "checked") | M | Reuses `build_policy`, `estimate_cost`, `free_preview` and `scan_cap` from `query/service.py`; rate-limit per user |
| **P3: Table pane** | Adaptive side pane ↔ tabs (container query), Fields + profile merged table, field filter pre-filled from the Explorer search, click-to-insert into the editor, About block, Focus mode | M | Depends on the profile work currently in progress |
| **P4: extras** | Recent queries dropdown, header ▾ menus, definition-aware field search, landing-page line on column search | S each | |

**Coordination.** `DictionaryPanel.tsx`, `ColumnProfile.tsx` and `Explorer.tsx` currently have uncommitted changes from another session (column profiling and metadata catalog). P1's dictionary work and all of P3 should start **after that work lands**, to avoid conflicting edits. P1's grid polish and the backend `modified_at` field don't overlap with it.

**Accessibility.**
- The grid keeps a native `<table>` with `<th scope>`; the row number is a `<th scope="row">`.
- The splitter is a `role="separator"` with arrow-key resizing, like the existing side-panel handle.
- Tabs use `role="tablist"`.
- Validator messages go in an `aria-live="polite"` region, so screen-reader users hear the cost estimate.
- Zebra and `null` styling must meet 3:1 for non-text cues and 4.5:1 for the `null` text in every appearance.

## 7. Decisions (2026-11-26)

1. **Live check runs automatically, quietly.** It runs about 800ms after typing stops, only when the SQL actually changed and isn't empty. Results are cached per exact SQL text, and the request is cancelled if typing resumes.
   - *Why automatic:* the check's value is catching surprises (scan size, cap, allowlist) *before* the user decides to run. A Check button would be used least by the people who need it most.
   - *Why cheap enough:* BigQuery dry runs are free, and the debounce plus cache keeps it to roughly one call per pause in typing. The policy check is local and parses the SQL with sqlglot.
   - *Quiet by design:* a neutral status line under the editor, never a pop-up. While the user is mid-edit and the SQL doesn't parse yet, show "Checking…" or nothing, not a red syntax error. Errors appear only after the pause.
   - *Safeguards:* the checks are rate-limited per user and audited as `checked` (separate from executed queries). For connectors without a dry run, only the policy result is shown.
2. **"Ask EasyQuery about this result" pre-fills the chat box; it doesn't send.**
   - *Why:* the workbench promises "doesn't use the AI or any tokens". Sending automatically would quietly break that promise and spend the user's model budget. Pre-filling keeps the user in control, lets them sharpen the question, and makes the switch to AI explicit with the normal Send.
   - *Behaviour:* closes the workbench, focuses the chat input, and pre-fills it with a short editable prompt that references the result (for example "About result q7 (10 rows from vw_edr_firmographics_standardized): "), with the cursor at the end. The result is attached by reference (`qN`), never pasted as raw rows.
3. **The side panel stays as the user left it.** The workbench doesn't hide it automatically. The existing **Expand** becomes an explicit **Focus** toggle (⤢), which hides the side panel for the workbench session. The choice is remembered per browser and is off by default, and Back to chat restores the panel. The wide side-by-side layout (§4) applies whenever the workbench is wide enough, however the user got there.
