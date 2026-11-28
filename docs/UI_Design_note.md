# One Data Navigator — UI/UX Design Note

*Status: reflects the frontend as shipped to `odn.datahop.ai` on 2026-11-17 (commit `6f83298`).*
*Audience: anyone changing the UI, adding a client theme, or adding a panel/tool. Read this before redesigning a screen.*

Related: [MVP dev plan](one_data_navigator_mvp_dev_plan.md) (progress and roadmap), [architecture](One_Data_Navigator_Architecture.md).

---

## 1. Design principles

These are the decisions the current UI is built on. Keep them unless there is a reason recorded here to change them.

1. **Chat first, tools on the side.** Following the Google ADK web UI and the `navigator_docker_env` reference, the conversation is always visible on the right. Exploration, results, history, files and settings live in a side panel that supports the chat and never replaces it.
2. **Show evidence, not just answers.** Every result shows what ran (plain-English explanation first, SQL collapsed), where it came from (source tables, "derived from q1"), and whether it is partial. Agent tool calls are visible as step cards on demand.
3. **Nothing expensive or irreversible happens silently.** Large warehouse scans pause for an approval card. Deleting a conversation asks for confirmation and says what is kept.
4. **The model gets the minimum.** Files and results stay in the workspace; the model receives notes (columns, a few rows) and queries the data with tools. The UI should never encourage pasting data into the chat.
5. **Brand is a theme, not a fork.** Components use semantic tokens only. A client's look is one CSS file plus brand copy (see §5).
6. **Honest empty and error states.** Say what is missing ("Definition unavailable", "From a deleted conversation", "you cannot see its contents") instead of hiding it or inventing content.

---

## 2. Information architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ● cotality | Smart Data Navigator                     BETA  Dev User  Sign out │  Brand bar
├──────────────────────────────┬─┬─────────────────────────────────────────────┤
│ About Chats Explorer Results │║│  [Show side panel]   ◐ Show agent steps  [New conversation]
│ Artifacts Settings   Hide ✕  │║│  Trial banner (shared model usage)                 │
│──────────────────────────────│║│  Scoped to project.dataset.table ✕                 │
│                              │║│                                                    │
│  Side panel (dark)           │║│  Chat (light)                                      │
│  - tab content               │║│  messages · step cards · approval cards            │
│                              │║│  ┌──────────────────────────────────────────────┐  │
│  Explorer footer:            │║│  │ SQL workbench overlay (opens over the chat)  │  │
│  Selected table · Details&SQL│║│  └──────────────────────────────────────────────┘  │
│                              │║│  [📎 attachment chips]  Type a message…    [Send] │
└──────────────────────────────┴─┴─────────────────────────────────────────────┘
                                ↑ draggable / keyboard-resizable splitter (width remembered)
```

| Region | Purpose | Notes |
|---|---|---|
| Brand bar | Identity, environment tag, account | Content comes from the theme (`company`, `product`, `tag`). |
| Side panel | Six tabs, all kept mounted so state survives tab switches | Collapsible ("Hide ✕" / "Show side panel"); open/closed is remembered. Default width 38%, min 360 px; chat min 420 px. |
| Chat | The conversation (CopilotKit `CopilotChat`) | Toolbar, trial banner and scope chip sit above it; the workbench overlays it. |
| Workbench | IDE-style SQL editor + results, then the table dictionary | Opens over the chat (not a new page) so the conversation is one click away ("Back to chat", or Esc outside the editor). |

### Side panel tabs

| Tab | What it is for | Surface |
|---|---|---|
| **About** | Product pitch carousel (3 slides from the theme). Also shown on the sign-in screen. | Dark, with brand decoration |
| **Chats** | List of the user's conversations (title from first question, time). Open, start new, delete with inline confirm. | Dark list |
| **Explorer** | Connection picker, keyword search over datasets/tables/fields, favorites and recent tables, dataset tree, hide-snapshots switch. Selecting a table scopes the chat; the footer offers **Details & SQL**. | Dark |
| **Results** | Latest chart and latest result of the current conversation, with **‹ q11 · 12 of 12 · q13 ›** to step back through its earlier results: explanation, lineage, collapsed SQL, sortable grid (row paging only past 100 rows), CSV download. | Light sheet inside the dark panel |
| **Artifacts** | Everything the user produced or uploaded, across conversations: tables, charts, files. Filters, "This conversation only", preview, downloads, link back to the source conversation, **Upload files**. | Dark list, light previews |
| **Settings** | One section at a time behind a switcher that shows each section's status: **AI model** (trial usage meter or active key/GitHub account; **GitHub Copilot** is the first provider: **Sign in with GitHub** shows a one-time device code + "Open github.com/login/device", polls until approved, then lists the models the user's Copilot seat allows. Other providers: Provider → API key → Model: the model dropdown lists the chat models your key can use, loaded automatically from the provider; "type a model name" is the fallback), **Data sources** (logo rows grouped shared / only you; **+ Add** opens a logo picker, add/edit opens as its own view with a back link), **Collibra** (setup steps; admins manage environments). The last section is remembered; the trial banner opens AI model. | Light sheet |

**Why light sheets inside a dark panel:** dense content (grids, forms, charts) is far more legible on a light surface, while navigation chrome stays dark to match the brand. Rule of thumb: *navigation and lists → dark panel; tables, forms, charts → light sheet.*

---

## 3. Key flows

### 3.1 Ask a question
1. User types in the chat (or picks a table in Explorer first; the chat shows **Scoped to …** and the agent is told about the selection).
2. Agent steps appear as cards (Preparing → Running → Done) if **Show agent steps** is on. Failures and approval requests always show, even when steps are hidden.
3. A new result or chart opens the **Results** tab automatically. Exception: if the user is in **Settings** (probably typing), only a dot appears on the Results tab.
4. Step cards for result-producing tools offer **View in Results**.

### 3.2 Approve an expensive query
The approval card shows the agent's hint (scan size and SQL) with **Run query** / **Cancel**. The server re-checks that the approved payload matches the paused query, so the model cannot approve on the user's behalf.

### 3.3 Explore a table manually
Explorer → search or browse → click a table (chat becomes scoped) → **Details & SQL** opens the workbench with a prefilled `SELECT … LIMIT 100`, **Run** and **Preview data** (sampled), and the data dictionary below. Esc (outside the editor) or **Back to chat** closes it; Explorer keeps its state.

### 3.3b SQL editor (workbench)
One IDE panel ([`SqlEditor.tsx`](../frontend/app/components/SqlEditor.tsx), CodeMirror 6, loaded only when the workbench opens) in the theme's warm **paper** style rather than a dark slab, so code sits inside the light working surface: cream toolbar and status bar, near-white code area with a cream gutter, coral caret/selection/focus ring, syntax hues from the brand palette (all ≥ 4.5:1). One highlighter ([`sqlSyntax.ts`](../frontend/lib/sqlSyntax.ts): tags → `sqlh-*` classes coloured by `--code-*` tokens) serves the editor and chat SQL, so both always match.

- **Code style** (Settings → Appearance only, so the workbench toolbar stays focused on SQL; remembered per browser): **Match app** (default: follows the app appearance), **Elegant** (the brand theme's paper style), **Code Dark** (Dark+) and **Code Light** (softened Light+). Each explicit choice is a `--code-*` override set keyed by `<html data-code-theme>` ([`themes/code.css`](../frontend/app/themes/code.css); Elegant lives in the brand theme file); chat SQL follows the same choice.

- **Toolbar:** dialect · connection chip (BigQuery, PostgreSQL, Snowflake, MySQL, Oracle; DuckDB for conversation results), **Format** (`sql-formatter`, per dialect), **Wrap** (on by default, remembered), **Copy**, **.sql** download, **⤢ Focus** (hides the side panel while the workbench is open; the user's choice, remembered, off by default; Back to chat restores it); on the right **Preview data** and **▶ Run ⌘↵**.
- **Editor:** line numbers, bracket matching/auto-close, folding, ⌘F search & replace, multi-cursor, ⌘/ comment, selection-match highlight. Completion (Ctrl+Space or typing): keywords, tables in scope, and the opened table's columns with types.
- **Run:** ⌘/Ctrl+Enter or the button; with text selected it runs **only the selection** (button reads "Run selection"). "Run anyway" after the cost check repeats exactly what was run.
- **Live check** (line under the editor, [`QueryCheck.tsx`](../frontend/app/components/QueryCheck.tsx)): about 800ms after typing stops, `POST /v1/connections/{id}/query/check` returns what Run would decide, without reading data: allowlist/policy, *free preview, no scan*, BigQuery dry-run size, confirmation threshold or over the per-query cap. Checked against what Run would send (the selection if any), cached per exact SQL, cancelled when typing resumes, rate-limited per user (40/min). Incomplete SQL shows "Waiting for complete SQL…", not a red error. Databases without a dry run show the policy verdict only.
- **Status bar:** Ln/Col, lines, chars or selection size, shortcut hints and brief confirmations (Formatted, Copied).
- **Results panel** ([`WorkbenchResults.tsx`](../frontend/app/components/WorkbenchResults.tsx)): header "Query results · N rows · scanned X (or *cached* / *free preview*) · ms"; tabs **Results · Run details** (job ID, bytes estimated vs scanned, tables read, the exact SQL that ran); actions **CSV**, **Copy** (tab-separated for spreadsheets), **Ask EasyQuery ↗** (closes the workbench and *pre-fills* the chat input with the SQL; never sends, so the workbench stays AI-free until the user presses Send). Grid: sticky row-number gutter, light zebra rows, muted italic `null`, right-aligned tabular numbers, rows-per-page 25/50/100 (remembered) with first/prev/next/last. Wide results ([`HScroll.tsx`](../frontend/app/components/HScroll.tsx), also used by the Results tab grid) make hidden columns obvious: edge shadows where columns are off-screen, a "Columns 4–9 of 47" counter with ‹ › page buttons, and an always-visible scrollbar (macOS otherwise hides it until you scroll).
- **Table pane** ([`DictionaryPanel.tsx`](../frontend/app/components/DictionaryPanel.tsx)): table name as title, dataset path with **Copy path**, identity line *Table/View · rows · size · Modified 6 months ago · partitioned/clustered · N of M fields defined* (`TableSchema.modified_at`: BigQuery `Table.modified`, Snowflake `LAST_ALTERED`, MySQL `UPDATE_TIME`; omitted when unknown; views never show "0 rows · 0 B"). **Fields** table merges profile and definitions (Field with source description and definition beneath · Type · Fill bar · Range), a **filter** (name, description or definition) pre-filled from the Explorer search, and click a field to **insert it at the editor cursor**. Profiling is a quiet section action: "Profile: 10% sample · 3 hours ago ↻ Refresh profile" / "No profile yet · Profile this table".
- **Layout:** when the workbench is at least 1,080px wide, the table pane sits to the right of Query + Results (compact fields: Range moves to the Fill tooltip); narrower, it folds into a **Fields (N)** tab of the results panel. Design rationale: [UI_Design_sql_workbench.md](./UI_Design_sql_workbench.md).
- **Keyboard:** Tab indents; Esc then Tab leaves the editor; Esc inside the editor never closes the workbench.

### 3.3a SQL in chat answers
Most readers are managers, so SQL blocks in answers start **folded** ("▸ Show SQL · N lines"); expanding shows the code highlighted exactly like the editor (line numbers, [`SqlCode.tsx`](../frontend/app/components/SqlCode.tsx), loaded on first expand) with copy and download in the bar. Each block also has **Open in SQL workbench** ([`FoldableSql.tsx`](../frontend/app/components/FoldableSql.tsx)):

- The tables it reads (FROM/JOIN, CTEs and subqueries skipped) pick the connection whose allowlist covers them ([`sqlRefs.ts`](../frontend/lib/sqlRefs.ts)); the chat is scoped to all of them and the first table's dictionary opens. The SQL is kept as written.
- SQL that only reads the conversation's results (`q1`, `q2`, …) runs on the workspace (DuckDB, no warehouse scan) and saves a new `qN`.
- Workbench runs call the query APIs directly and never the agent, so they use no AI tokens; the workbench says so under the editor.

### 3.3c Starter questions for a scoped table
A new (empty) conversation with a table in scope shows **Ask about `<table>`** with the table's kind, rows, fields and when it was modified, plus five **Get to know this table** chips: *Fill rates*, *Last updated*, *Core fields*, *Possible insights*, and *Sample rows* (tables, a free preview) or *Empty fields* (views, whose preview would scan everything). A chip **pre-fills** the message box; Enter sends. The agent answers these from `get_table_schema` alone (stored profile fill rates and ranges, `modified_at`, definitions) without running SQL. The input placeholder reads "Ask about `<table>`…". **Answer style** (agent instructions, all answers): a bold one-line answer, then at most 5 one-line bullets (about 2 after a table, which is capped at 10 rows); no method notes, repeated caveats or closing offers. Below the starters, **Questions about the data** lists up to 5 of the table's questions, in this order: pinned, popular, yours, then generated. A ⚡ *Saved answer* opens instantly as a new conversation, with no tokens and no scan, and shows its SQL folded. ↻ asks again. Data leads curate inline (**Edit**). The Explorer's **Suggested questions ›** starts such a conversation. Details: [table_questions_plan.md](./table_questions_plan.md) §8. **Suggested follow-ups in answers:** the agent writes them as `[question](#ask)` links, e.g. one *Try asking* question per row of the "possible insights" table. The chat renders them as accent question buttons (`AskLink.tsx`) that pre-fill the message box, like the starter chips.

### 3.4 Attach a file
Paperclip in the chat input (or drag a file onto the chat, or **Upload files** in Artifacts).
- The file is uploaded to the Artifacts store immediately and shows as a chip in the input.
- CSV/XLSX become a workspace table (`q1`…); the importer finds the real header row (skipping title/instruction lines) and keeps those lines as notes for the agent.
- When the message is sent, the model gets a short note (table, columns, first rows, notes), never the file.
- Images/documents are stored and previewable, but the agent cannot see their contents (it is told so).

Limits: 20 MB; CSV, XLSX, PNG, JPG, GIF, WebP, PDF, TXT, MD, JSON.

### 3.5 Come back later
Reload reopens the last conversation with its messages, step cards and latest result. **Chats** lists older ones; opening one restores it. Deleting a conversation removes the chat only; its tables, charts and files stay in **Artifacts** labelled "From a deleted conversation".

---

## 4. Components and files

| Concern | File |
|---|---|
| Page shell, tabs, chat, conversation/thread handling, context sent to the agent | [`app/components/AppShell.tsx`](../frontend/app/components/AppShell.tsx) |
| Brand bar | [`BrandBar.tsx`](../frontend/app/components/BrandBar.tsx) |
| About carousel | [`AboutSlides.tsx`](../frontend/app/components/AboutSlides.tsx) |
| Chats tab | [`Conversations.tsx`](../frontend/app/components/Conversations.tsx) |
| Explorer + workbench (portal into the chat) | [`Explorer.tsx`](../frontend/app/components/Explorer.tsx), [`CatalogSearch.tsx`](../frontend/app/components/CatalogSearch.tsx), [`DictionaryPanel.tsx`](../frontend/app/components/DictionaryPanel.tsx) |
| Results tab | [`Results.tsx`](../frontend/app/components/Results.tsx), [`ResultsGrid.tsx`](../frontend/app/components/ResultsGrid.tsx), [`Chart.tsx`](../frontend/app/components/Chart.tsx) |
| Artifacts tab | [`ArtifactsPanel.tsx`](../frontend/app/components/ArtifactsPanel.tsx), PNG export in [`lib/chartExport.ts`](../frontend/lib/chartExport.ts) |
| Step cards, approval card, steps toggle | [`AgentSteps.tsx`](../frontend/app/components/AgentSteps.tsx), preference in [`lib/displayPrefs.ts`](../frontend/lib/displayPrefs.ts) |
| Uploads and chat attachments | [`lib/uploads.ts`](../frontend/lib/uploads.ts) |
| Settings | [`Settings.tsx`](../frontend/app/components/Settings.tsx), [`CollibraSettings.tsx`](../frontend/app/components/CollibraSettings.tsx), connection forms (`*ConnectionForm.tsx`) |
| Splitter | [`SplitPane.tsx`](../frontend/app/components/SplitPane.tsx) |
| Themes | [`app/themes/`](../frontend/app/themes/) |
| All styles | [`app/globals.css`](../frontend/app/globals.css) (layout/components) + theme CSS (tokens) |

**Conventions**
- Class prefix `odn-` for shell/layout; older component classes (`tree`, `grid`, `badge`, `settings-*`) are kept and styled from tokens.
- In light contexts (sheets, workbench, chat), a plain `<button>` with no class is automatically a primary flat button; use `odn-btn` variants (`--outline`, `--small`, `--on-panel`, `--ghost-panel`) when you need something else.
- Persisted UI preferences use `localStorage` keys prefixed `odn.` (`odn.panelOpen`, `odn.split.shell`, `odn.activeThreadId`, `odn.showAgentSteps`, `odn.explorer.hideSnapshots`).

---

## 5. Theming

### How it works
- `ODN_THEME` (server env, default `cotality`) selects the theme; [`themes/server.ts`](../frontend/app/themes/server.ts) resolves it and the root layout sets `<html data-theme="…">`. Unknown values fall back to the default.
- [`themes/index.ts`](../frontend/app/themes/index.ts) holds brand copy: company, product name, tag, browser theme colour, About slides.
- [`themes/cotality.css`](../frontend/app/themes/cotality.css) sets the tokens under `[data-theme="cotality"]` plus brand-only decoration (the coral dot, the triangle motif on About and sign-in).
- CopilotKit's colours are derived from the same tokens in `globals.css`, so the chat follows the theme.
- **Appearance** (Settings → Appearance, per browser): **Elegant** (the default for everyone: the brand theme as designed; the others apply only after a user picks them), **Code Dark** and **Code Light** (VS Code Dark/Light Modern). An appearance re-sets every semantic token, including `--code-*` and charts, under `:root[data-appearance="…"]` in [`themes/appearance.css`](../frontend/app/themes/appearance.css), layered over the brand theme, so it works for any client theme. Brand copy and decoration stay; colours follow the appearance. Code Dark and Code Light keep blue for primary actions and focus only (VS Code button blues `#0e639c` / `#005fb8`, steel-blue rules `#2f6aa3` / `#3a6fa8`); secondary buttons, chat bubbles and message icons are grey via optional tokens `--btn-hover-*`, `--btn-secondary-*`, `--bubble-*` (unset in a brand theme = today's behaviour).
- The choice is applied before first paint by an inline `beforeInteractive` script ([`lib/htmlPrefs.ts`](../frontend/lib/htmlPrefs.ts)), so a dark appearance never flashes light. Settings preview cards render each option with its real tokens via `[data-appearance-preview]` (brand themes add `[data-theme="<id>"] [data-appearance-preview="elegant"]` to their token selectors).

### Token reference

| Group | Tokens | Cotality value (theme A) |
|---|---|---|
| Type & shape | `--font`, `--font-mono`, `--radius` | Inter/system stack; square corners (`0`) |
| Frame | `--app-bg`, `--chrome-bg`, `--chrome-fg`, `--chrome-border` | cream `#f7efe2`; ink `#1e1405` |
| Dark panel | `--panel-bg`, `--panel-fg`, `--panel-muted`, `--panel-border`, `--panel-border-strong`, `--panel-hover`, `--panel-selected`, `--panel-input-bg`, `--panel-focus` | ink background, cream text, orange focus ring |
| Light surfaces | `--surface`, `--surface-alt`, `--fg`, `--fg-muted`, `--border` | white, cream-soft `#fbf6ee`, ink text |
| Actions | `--primary`, `--primary-fg`, `--accent`, `--accent-strong`, `--accent-2`, `--on-accent` | ink buttons; coral `#f2735e`; darker coral `#d9492f` for focus rings (3:1 on white); orange `#f4a259` |
| Highlight | `--mark-bg`, `--mark-fg` | orange search highlight |
| Status | `--danger`, `--danger-bg`, `--success`, `--success-bg`, `--warning-bg`, `--warning-border` | |
| Charts | `--chart-1` … `--chart-8` | ink, coral, orange, then supporting hues |

### Adding theme B (checklist)
1. Copy `cotality.css` to `<client>.css`, change the selector to `[data-theme="<client>"]`, set every token. Keep `--accent-strong` at ≥ 3:1 against `--surface` (it is the focus ring).
2. Add brand decoration only under that theme's selector (or none). Include `[data-theme="<client>"] [data-appearance-preview="elegant"]` in the token selector, and `:root[data-theme="<client>"][data-code-theme="elegant"]` in the `--code-*` block, as `cotality.css` does.
3. Add an entry to `THEMES` in `themes/index.ts` (copy, About slides, `themeColor`).
4. Import the CSS in `app/layout.tsx`.
5. Set `ODN_THEME=<client>` for that deployment. No component changes should be needed; if one is, a hard-coded value slipped in, so fix it with a token.

---

## 6. Accessibility (what exists today)

- Side panel tabs follow the ARIA tabs pattern (roving `tabIndex`, ←/→/Home/End). Switches use `role="switch"`; toggles use `aria-pressed`; the splitter is a focusable `separator` (arrow keys, Shift for bigger steps, double-click resets).
- Visible focus rings from tokens (`--accent-strong` on light, `--panel-focus` on dark).
- Live regions: chat messages (`aria-live="polite"`), search status, trial banner, upload status.
- Sortable grid headers are buttons; the workbench is a labelled dialog that closes on Esc.
- Charts are SVG with a text alternative and a data table/CSV fallback.

**Not yet verified:** no automated accessibility or keyboard tests have been run on the new panel tabs, and contrast has only been checked for the tokens noted above. Treat these as open work (see §8).

---

## 7. Known limitations and quirks

| Area | Limitation |
|---|---|
| Results tab | Earlier *results* are one click back, but only the latest chart is shown; earlier charts are in Artifacts. |
| Auto-open Results | Can pull the user away from Explorer/Chats mid-task (by design, except in Settings). |
| Tabs | Six tabs fit at the default width; at the 360 px minimum the tab bar scrolls horizontally. |
| Attachments | Removing a chip does not delete the uploaded file. CopilotKit labels sent attachments generically (e.g. "TXT · text/csv") instead of the file name. The agent cannot see images. |
| Artifacts | No delete/rename/pin of individual artifacts; no search box; long lists are not paginated (API caps at 500). |
| Chats | No rename or search; a brand-new conversation appears in the list only after its first message. |
| Chart PNG | Exports the SVG plot only (not the title/legend HTML). |
| Workbench | Focus is not returned to the opening button when it closes. |
| Dev only | CopilotKit's Inspector button floats top-right in local dev (the chat toolbar is padded to avoid it); it is disabled in production builds. |
| Sign-in | Google blocks sign-in from automated/embedded browsers, so UI smoke tests on production need a real browser session. |

---

## 8. Future proposals and suggestions

Ordered roughly by user value vs. effort. ★ = recommended next.

### Near term (polish what exists)
1. ~~**Results history in the Results tab.**~~ Done (2026-11-25): previous/next result navigation from `GET /v1/workspace/{session}/artifacts`. Charts are not included yet.
2. ★ **Inline result cards in the chat.** A compact card under the agent's message (name, rows, mini-preview, "Open in Results") so the answer and its evidence stay together when scrolling back through a conversation.
3. ★ **Accessibility pass + tests.** Keyboard walk-through of every tab, return focus after the workbench closes, axe checks in CI, contrast check of all token pairs per theme.
4. **Artifacts management.** Search box, rename/pin, delete individual items (needs the retention decision in the dev plan), pagination.
5. **Chats management.** Rename, search, group by date (Today / Last 7 days / Older).
6. **Starter prompts.** On an empty conversation, 3–4 suggestion chips built from the user's favorites/recent tables ("Top 10 … in `vw_edr_firmographics_view`").
7. **Attachment chips with real names.** Custom renderer for sent attachments (file name, table name `q1`, "Open in Artifacts").
8. **Auto-open preference.** A setting to turn off auto-switching to Results (some users will prefer the dot only).

### Medium term
9. **Cost and scope cues before running.** Show the estimated scan size in the approval card and a small "this will scan ~X GB" hint in the workbench before **Run**.
10. **Charts: more control in the UI.** Change chart type/sort/limit from the Results tab without asking the agent; export PNG with title and legend.
11. **Responsive layout.** Today narrow screens stack the panel above the chat. Consider a bottom tab bar or a slide-over panel on mobile/tablet.
12. **Per-tenant theme.** Move theme selection from `ODN_THEME` (per deployment) to a tenant setting, so one deployment can serve several clients (needs an API/DB change).
13. **Images to vision models.** Pass attached images to models that support vision (behind a per-tenant switch, with size limits), and say clearly in the UI when the model can or cannot see an image.
14. **Usage view.** Small panel (or Settings section) with today's token use and recent query costs, using the existing telemetry API.

### With Phase 5 (Collibra)
15. **Collibra tab** in the side panel (search, asset detail, select to scope the chat), and Collibra definitions in the Explorer dictionary labelled "Collibra · PRD/INT". Field-mapping results should render as a reviewable table (match, runner-ups, evidence links) the user can accept/reject before downloading.

### Design-system hygiene
16. **Token documentation in code.** Add a short comment block or a hidden `/design` page rendering every token and component state per theme, so theme authors can check a new theme visually.
17. **Retire legacy class names** (`tree`, `grid`, `badge`, `settings-*`) into the `odn-` namespace as components are touched, to avoid collisions as the UI grows.
18. **Visual regression snapshots** for the main screens per theme (sign-in, each tab, workbench) to catch theme leaks when adding B/C/D.
