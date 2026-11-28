# Table questions ("what you can ask"): implementation plan

*Status: **Q0–Q3 implemented** (2026-11-27). Plan approved 2026-11-27 (decisions in §7); where the build differs from the plan, §8 is authoritative.*
*Related: column profiles ([0038_column_profiles.sql](../backend/src/odn/db/migrations/0038_column_profiles.sql), [column_profiles.py](../backend/src/odn/platform/catalog/column_profiles.py)), LLM routing ([agent/budget.py](../backend/src/odn/agent/budget.py)), chat empty state ([AppShell.tsx](../frontend/app/components/AppShell.tsx)), [UI_Design_note.md](./UI_Design_note.md).*

## 1. Goal

A new conversation that's scoped to a table (from Explorer, a chat SQL hand-off, or a share link) should open with **up to 5 questions that fit that table**, not the static "Ask anything about your data, e.g. 'How many businesses per status in CA?'".

- **Generate once, reuse everywhere.** Questions are generated the first time anyone in the tenant opens that table. They're stored in Postgres next to the column profiles and reused by every later session and user, with no new LLM call.
- **Learn from real questions.** Questions people actually ask about the table, and that get answered, are collected. Over time they rank above generated ones ("Popular").
- **Stay governed.** Questions are tenant-scoped like profiles. One user's question is shown to others only when it's safe and common (§4.4).

## 2. Starter questions (every table, no AI to generate, cheap to answer)

Every scoped table always gets a fixed **"Get to know this table"** set. These are templates filled with the table's name, so they appear **instantly** (no generation, no wait) and work for tables nobody has asked about yet:

| # | Chip title | Question pre-filled | Answered from (no warehouse scan) |
| --- | --- | --- | --- |
| 1 | Fill rates | "What are the fill rates of the core fields in `{table}`?" | Column profile (fill %, min/max). If no profile exists yet, the agent offers to run one (10% sample, shows the scan size) instead of writing a scan query |
| 2 | Last updated | "When was `{table}` last updated?" | `TableSchema.modified_at`. For a **view**: "the view's definition last changed on …; data freshness depends on its source tables", listing the tables it reads where known |
| 3 | Core fields | "What are the 10 core fields in `{table}`, and what do they mean?" | Schema + curated definitions + profile. The agent ranks fields by meaning (IDs/keys, names, dates, measures, status/category), high fill rate and having a definition, and skips helper columns (`*_precision_code`, `*_confidence_score`, `*_source_id`, audit timestamps) |
| 4 | Possible insights | "What kinds of insights can I get from `{table}`?" | Schema + definitions + profile; the agent suggests 3–5 analyses, each naming the columns it would use |
| 5 | Sample rows / Empty fields | **Table:** "Show me 10 sample rows from `{table}`" · **View:** "Which fields in `{table}` are mostly empty?" | **Table:** a free preview (`SELECT * … LIMIT 10` is served from stored rows, so 0 bytes). **View:** the profile. A view preview would scan the whole view (e.g. 295 GB), so it's swapped for a metadata question |

**What this needs from the agent (small, done in phase Q0):**
- **Profile in the schema tool.** `get_table_schema` also returns the column profile, per column `fill_rate` and `min`/`max`, plus `profile_sampled_at` and `modified_at`. Then questions 1–4 cost one tool call and no SQL.
- **Prompt rule.** Add one line to the agent instructions: *"Questions about fill rates, freshness, core fields or what a table is useful for are answered from `get_table_schema` (profile, modified time, definitions). Don't write scan queries for them. If there's no profile, say so and offer to profile."*
- **Starters aren't collected.** When someone asks a starter, it isn't stored as an "asked" question (§4.4). Starters are matched by template ID, not by text, so they don't crowd out real questions.

The **generated** and **asked** questions (below) then add 3–5 *data* questions specific to the table ("Businesses by status", "Top 10 states by location revenue"). The generation prompt is told the starters exist, and must not repeat them.

## 3. Key decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Where questions live | New `metadata_table_questions` table, tenant-scoped, RLS, keyed like `metadata_column_profiles` (tenant, connection, table path) and cleared on the same connection metadata revision change | Same lifecycle as the other derived metadata; no new isolation model to reason about |
| Who generates | The **worker**, from a job queue (like profile jobs), using the **platform model**, charged to the **tenant's** LLM budget | The output is shared tenant-wide, so it shouldn't spend one user's own key or Copilot quota. Generation is about 1.5k tokens, roughly once per table per schema change |
| Generation input | Table name and description, column names/types/descriptions, curated definitions, profile fill rates and min/max (when present). **No row values beyond the profile.** | Enough to write grounded questions ("by `business_status_code`", "range of `year_established`"). Nothing the user couldn't already see in the Fields pane |
| When to (re)generate | On first request with no stored set. Again when the table's **schema fingerprint** (hash of column names and types) changes, or after a 30-day TTL. A data lead can also regenerate manually | "Generate once", but never stale after a schema change |
| Collected questions | The agent reports each **answered** question with the tables it actually read. The server normalizes, deduplicates and counts them | Only questions that produced an answer from that table count, so failed or off-topic ones don't pollute the list |
| Sharing collected questions | Shared tenant-wide **right after the first successful answer**, together with that answer, if it passes the privacy filter (§4.4); otherwise private to the asker | Fast FAQ growth and answer reuse; the filter (emails, IDs, phones, URLs, quoted names) keeps personal or confidential context out |
| Personal connections | Questions are stored **per owner** (`owner_user_id`) and never shared | Matches how personal connections and keys are already RLS-scoped by `odn.user_id` |
| Clicking a question | **Pre-fills the chat input** (cursor at end, "↵ to ask" hint) rather than sending | Same principle as "Ask EasyQuery about this result": the user stays in control of when tokens are spent, and can adjust the question first |

## 4. Backend

### 4.1 Store (migration `0039_table_questions.sql`)

```sql
CREATE TABLE metadata_table_questions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  connection_id    uuid NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  connection_revision uuid NOT NULL,          -- cleared on metadata revision change, like profiles
  owner_user_id    uuid REFERENCES users(id) ON DELETE CASCADE,  -- set for personal connections only
  table_path       text[] NOT NULL CHECK (cardinality(table_path) BETWEEN 2 AND 3),
  source           text NOT NULL CHECK (source IN ('generated', 'asked', 'curated')),
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'private', 'hidden')),
  title            text NOT NULL CHECK (length(title) <= 60),      -- chip label: "Businesses by status"
  question         text NOT NULL CHECK (length(question) <= 300),  -- full text pre-filled into the input
  normalized_hash  text NOT NULL,             -- sha256 of the normalized question: the dedup key
  schema_fingerprint text,                    -- for generated rows: which schema they were written for
  ask_count        integer NOT NULL DEFAULT 0,
  distinct_askers  integer NOT NULL DEFAULT 0,
  answered_count   integer NOT NULL DEFAULT 0,
  last_asked_at    timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_by       uuid REFERENCES users(id) ON DELETE SET NULL,
  UNIQUE (tenant_id, connection_id, table_path, normalized_hash)
);

-- Who asked (for distinct counts and "your own questions"), never exposed to other users.
CREATE TABLE metadata_question_askers (
  question_id uuid NOT NULL REFERENCES metadata_table_questions(id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  first_asked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (question_id, user_id)
);

CREATE TABLE metadata_question_jobs ( ... same shape as metadata_profile_jobs: status, lease, attempts,
  requested_by, table_path, error_code; UNIQUE active job per (tenant, connection, table_path) ... );
```

- **RLS:** ENABLE + FORCE on all three tables, with GRANTs to `odn_app` (the `tests/db/test_rls.py` guard requires it).
  - *Tenant policy:* `tenant_id = odn_current_tenant()` and the connection revision matches, as profiles do.
  - *Owner rows:* if `owner_user_id IS NOT NULL`, the row is visible only when `owner_user_id = odn_current_user()`.
  - *Askers table:* readable only through a SECURITY DEFINER count function, so user IDs never reach the client.
- **Trigger:** extend the existing `odn_clear_connection_profiles` trigger, or add a sibling, to delete question rows when `connections.metadata_revision` changes.
- **Indexes:** `(tenant_id, connection_id, table_path, status)` for reads; the job table gets the same pending/active indexes as profile jobs.

### 4.2 Generation (worker)

- **Module:** `odn/platform/catalog/table_questions.py` (service), and a loop `run_question_worker(stop)` in the existing worker process next to `run_worker` for profiles.
- **Prompt input** (a compact JSON brief, about 1–2k tokens):
  - dialect and table name/description;
  - up to 80 columns: name, type, description, fill %, min/max;
  - curated definitions;
  - partition and cluster columns, which favour cheap questions.
- **Instructions:**
  - Write 6 candidate questions for a business user, each with a ≤ 6-word title.
  - Mix the kinds: a count/total, a breakdown by a categorical column, a top-N, a trend over a date column (if there is one), a data-quality question (fill rate, nulls), and a comparison.
  - Use only listed columns, and prefer columns with high fill rates.
  - Don't repeat the starter questions (§2): fill rates, last updated, core fields, possible insights, sample rows / empty fields.
  - Return strict JSON.
- **Validation, server side.** Drop candidates that:
  - aren't valid JSON;
  - are over the length limits;
  - mention a word that looks like a column (snake_case, or present in the schema) but doesn't exist in the schema;
  - duplicate another candidate after normalization.

  Keep the best 5. If fewer than 3 survive, retry once, then mark the job `failed` (`error_code=invalid_output`).
- **Model and budget.**
  - Uses the platform LiteLLM model, the same as `RoutedLiteLlm`'s platform branch.
  - Tokens go to `llm_usage` with `purpose='table_questions'` (one new column, defaulting to `chat`), so it counts toward the tenant's daily token limit.
  - If the tenant budget is exhausted, the job is `skipped` (`budget_exceeded`) and the UI falls back to the static prompt.
  - Copilot-only tenants work too, because the platform model is used.
- **Concurrency:** one active job per table, using the unique partial index as profile jobs do. A second requester just polls.

### 4.3 API (`odn/api/routes.py`)

| Method | Path | Who | Behaviour |
| --- | --- | --- | --- |
| GET | `/v1/connections/{id}/questions?path=…` | anyone who can read the table (allowlist check as `/schema`) | Returns `{status: "ready" \| "generating" \| "unavailable", questions: [...], generated_at}`. With no generated set, or a stale fingerprint, it enqueues a job and returns `generating` plus whatever asked/curated questions already exist |
| POST | `/v1/connections/{id}/questions/regenerate?path=…` | data_lead, admin | Forces a new generated set; keeps asked and curated rows |
| PUT | `/v1/connections/{id}/questions/{qid}` | data_lead, admin | Edit title/text, `status` (`active`/`hidden`), or pin (sets `source='curated'`) |
| POST | `/v1/connections/{id}/questions` | data_lead, admin | Add a curated question |
| POST | `/v1/internal/questions/asked` | agent only (`X-ODN-Agent-Token`, like `/me/llm-config`) | `{question, connection_id, tables_read[], answered}` → normalize, privacy-filter, upsert and count (§4.4) |

**Response item:** `{id, title, question, source, popular: bool, asked_by_you: bool}`. No raw counts are exposed; "popular" means `distinct_askers ≥ 3`.

**Ranking (server side):**
1. Curated (pinned) first.
2. Then shareable asked questions, by `distinct_askers`, recency-weighted.
3. Then the caller's own asked questions.
4. Then generated questions.

Deduplicate by hash, cap at 5, and keep at least 2 generated questions while asked ones are few, so the list stays varied.

### 4.4 Collecting asked questions (agent)

- **Hook:** at the end of a turn, in the agent's after-run callback that already sees tool results. If the turn's user message led to at least one **successful** `run_query` or workspace query, report the message with the tables those queries read (from the audit decision's `tables`). One call per table read, capped at 3.
- **Normalize:** trim, collapse whitespace, strip trailing punctuation, lower-case for the hash only. The displayed text keeps the user's casing.
- **Skip** without storing when the message:
  - is under 12 or over 300 characters;
  - is obviously a follow-up ("and now by state", "same for 2023", "what about it", or starts with *and/also/now/then* plus a pronoun), because those don't stand alone;
  - matches the privacy filter: emails, phone numbers, long digit runs (≥ 6, e.g. IDs), URLs, or text in quotes over 3 words (likely names or values);
  - comes from the demo tenant (seeded questions there are curated instead).
- **Visibility:** a new asked question that passes the privacy filter is `active` (shared tenant-wide) after its first successful answer. One that trips the filter is stored as `private` and visible only to the asker.
- **Personal connections:** stored with `owner_user_id` and never shared.

### 4.5 Answer cache (reuse answers, refresh on demand)

Each answered FAQ question also stores **its answer**, so the next person (or the same person later) gets it instantly, with **no LLM tokens and no warehouse scan**. They can still ask for a fresh answer.

- **Store** (`metadata_question_answers`, one current row per question; tenant RLS and owner scoping exactly like the question row):
  - `answer_md`: the agent's final text, capped at 8 KB;
  - `sql[]`: what ran, at most 3 statements;
  - `result_preview`: columns plus at most 200 rows as JSON, capped at 256 KB;
  - `chart_spec`: optional;
  - `bytes_scanned`, `answered_at`, `answered_by`;
  - `data_modified_at`: the table's `modified_at` when answered;
  - `schema_fingerprint`.
- **Where it comes from:** the same agent hook as §4.4 adds the turn's final answer, its successful SQL, the first result's preview and any chart to the payload. Only turns that answered from that table, and **only the first question of a conversation or a standalone one**, are cached. Follow-ups depend on earlier context, so they aren't.
- **Fresh vs stale:** a cached answer is *fresh* while:
  - **Tables:** the table's current `modified_at` is no later than `data_modified_at`. Checked on read with one cheap metadata call, cached for 10 minutes.
  - **Views and databases with no modified time:** `answered_at` is within **30 days** (`ODN_ANSWER_CACHE_VIEW_TTL_DAYS`).
  - **All tables:** the schema fingerprint is unchanged.

  A stale answer is still shown, labelled "Data changed since this answer", with Refresh as the main action.
- **Starter questions** (§2) are answered from metadata anyway, so they cost no scan and aren't cached.
- **API:** `GET /questions` items gain `answer: {answered_at, fresh, bytes_scanned, has_chart}` (no body). The body comes from `GET /v1/connections/{id}/questions/{qid}/answer` (same access check as the table).
- **Refresh:** "Refresh answer" sends the question to the agent as a normal turn. Its answer replaces the cached one through the same hook, so every user sees the newest.
- **Privacy/governance:**
  - Answers live in the same tenant and connection scope as the data they came from; the shared connection's allowlist is identical for every tenant user.
  - Personal-connection answers are owner-only.
  - The privacy filter (§4.4) runs on the **question only**. The answer holds warehouse data that every user of the connection can already query, so it shares the question's visibility (see §8).

### 4.6 Tests

- **DB:** RLS isolation across tenants, owner-only rows, cleared on revision change, `test_rls` guard passes.
- **Service:**
  - generated-candidate validation (unknown column dropped, duplicates merged, too-few → retry/fail);
  - normalization and follow-up/privacy skips;
  - the privacy filter and private-vs-shared status;
  - answer cache freshness (table modified, view TTL, schema change) and refresh replacing the cached answer;
  - ranking order.
- **API:** `generating` → `ready` flow with a fake worker; allowlist enforced; data-lead-only edits; the agent endpoint rejects without the token.
- **Budget:** a skipped job when the tenant is over budget; tokens recorded with `purpose='table_questions'`.

## 5. Frontend (UI/UX)

### 5.1 Empty state of a scoped conversation

When `messages.length === 0` and a table is in scope, replace the static first assistant bubble with a **Questions card**.

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Ask about  vw_edr_firmographics_standardized                             │
│  View · ≈10.3M rows · 66 fields · modified 6 months ago                   │
│                                                                           │
│  GET TO KNOW THIS TABLE                                     (instant)    │
│  (Fill rates) (Last updated) (Core fields) (Possible insights)            │
│  (Empty fields)                                                           │
│                                                                           │
│  QUESTIONS ABOUT THE DATA                                                │
│  [★ Businesses by status ↵]  [✦ Top 10 states by location revenue ↵]     │
│  [✦ New businesses per year ↵]  [✦ Largest employers ↵]                  │
│                                                         Edit questions ⚙ │
└───────────────────────────────────────────────────────────────────────────┘
```

- **Two rows on purpose.** *Get to know this table* uses small outline chips that are always there and instant. *Questions about the data* uses slightly stronger chips: up to 5, popular first, then generated. While generation runs, only the second row shimmers, so the card is useful from the first frame.

- **Chips:**
  - The title is the label; the full question shows in a tooltip and the screen-reader label.
  - A small ★ marks *Popular* (asked by several people); ✦ marks *Suggested* (generated); "asked by you" shows in muted text.
  - The order comes from the server.
- **Click a chip:** pre-fills the chat input with the full question, focuses it, and puts the cursor at the end. A brief hint shows: "Press Enter to ask, or edit first".
- **Loading (`generating`):** the starter row shows immediately; the data row shows shimmer chips and "Writing questions for this table…". The client polls every 2s for up to 20s. On timeout or `unavailable`, fall back to a **scoped static prompt**: "Ask anything about `vw_edr_…`, for example how many rows per `<first high-fill categorical column>`" (built locally from the schema), so even the fallback isn't stale.
- **FAQ questions with a cached answer** show a ⚡ and "answered 2 days ago". Clicking one opens the **cached answer** right in the chat area, instantly and with no tokens:
  - It has a header line: "Answered 2 days ago · scanned 746 MB then · 0 B now".
  - It shows the answer text, the result table/chart, and "Show SQL".
  - Actions: **↻ Refresh answer** (sends the question to the agent for a fresh answer) and **Ask a follow-up** (focuses the input).
  - When the user continues, the cached question and answer are passed to the agent as context, through the same readable-context mechanism used for the table scope. Follow-ups then work without re-running the original query.
  - If the answer is stale, the header says "Data changed since this answer" and **Refresh** becomes the primary button.
- **Questions without a cached answer** pre-fill the input, as decided in §7.
- **Unscoped conversations** keep today's prompt and the demo suggestions.
- **Input placeholder** when scoped: "Ask about vw_edr_firmographics_standardized…".
- **Multiple tables in scope** (a chat SQL hand-off): show questions for the first table, with a small "for" selector listing the others.
- **Edit questions ⚙** (data_lead/admin only): opens the Questions section of the table pane (§5.2).

### 5.2 Table pane: Questions section (workbench)

- **New tab:** next to *Fields* and *About*, "Questions (5)", showing the same list with source badges.
- **Data-lead/admin actions:**
  - pin ⭐ (becomes curated);
  - hide 👁;
  - edit text;
  - add a question;
  - **Regenerate** (shows "Generated 3 days ago for this schema");
  - Recently added asked questions (newest first), with Hide / Edit / Pin, so a lead can tidy the shared list.
- **Clicking a question:** closes the workbench and pre-fills the chat, using the existing `onAskAbout` path.

### 5.3 Explorer (lightweight)

- The *Selected table* panel gets a single line, "5 questions people ask ›", which opens a new conversation scoped to the table with the Questions card. Only shown when the questions are `ready`, so it never triggers generation by itself.

### 5.4 Implementation notes

- **Component:** `TableQuestions.tsx` (card plus hook `useTableQuestions(connectionId, path)` with polling), rendered by AppShell above `CopilotChat` when the thread is empty and scoped. CopilotKit's `initial` label becomes an empty string in that case.
- **Pre-fill:** reuse the `askAbout` helper in AppShell (native value setter + `input` event + focus), which is already proven by "Ask EasyQuery ↗".
- **Theming:** tokens only (`--surface-alt` chips, `--accent` ★); works in every appearance; checked for contrast.
- **Accessibility:** the card is a `region` named "Suggested questions for <table>". Chips are buttons in a list; loading uses `aria-busy` with a polite status message.

## 6. Delivery plan

| Phase | Scope | Effort |
| --- | --- | --- |
| **Q0** | Starter questions (client-side templates, view/table variant for #5), the Questions card with the starter row, pre-fill on click; agent: `get_table_schema` returns profile + `modified_at`, plus the "answer from metadata" prompt rule. **No migration needed**: shippable on its own | S |
| **Q1** | Migration and store, worker generation and validation, `GET /questions`, the Questions card in the chat empty state, scoped fallback prompt, pre-fill on click | M |
| **Q2** | Agent collection hook + **answer cache** (§4.5: store, freshness, cached-answer view, Refresh) and `/internal/questions/asked`, normalization/privacy/sharing, ranking with Popular/Your badges | M |
| **Q3** | Data-lead curation (Questions tab in the table pane: pin/hide/edit/add/approve/regenerate), Explorer "questions people ask" link | S–M |
| **Later** | Semantic dedup (embeddings) so "businesses per status" ≈ "count of businesses by status"; "this question worked" thumbs feedback feeding the ranking; per-question SQL caching for instant answers | — |

## 7. Decisions (2026-11-27)

1. **Shared right away, no approval.** A newly asked question, *and its cached answer*, joins the tenant's shared FAQ after its first successful answer if it passes the privacy filter (§4.4). If the filter trips, it stays private to the asker. Data leads can still pin, hide or edit after the fact (§5.2). This replaces the earlier 2-asker threshold; `distinct_askers` is kept for the ★ Popular ranking only.
2. **Generation is charged to the tenant**, on the platform model (`purpose='table_questions'`), never to a user's own key or Copilot seat.
3. **Clicking a question pre-fills the chat input**; the user presses Enter to send. A question with a fresh **cached answer** opens that answer instead (no tokens), with Refresh.
4. **Answer cache** (§4.5): tables are fresh until their data or schema changes; **views expire after 30 days**.

## 8. As built (2026-11-27)

Where this differs from §4–§5, this section wins.

**Backend**
- **Store:** migration `0039_table_questions.sql`: `metadata_question_sets` (generation status per table), `metadata_table_questions`, `metadata_question_askers`, `metadata_question_answers`. All four have forced RLS:
  - Rows are visible only when the connection is visible, so personal connections stay owner-only.
  - Private questions are visible to `private_to` only.
  - Answers also need the connection's current `metadata_revision`. A trigger deletes a connection's answers when its revision changes (allowlist, credentials, owner).
- **Service:** `odn/platform/catalog/table_questions.py`. The API generates in a small thread pool, not in the worker; the client polls `GET /questions`.
  - Generation runs on the first visit, on a schema change, after 30 days, 6 hours after a failure, or when a run has been stuck for more than 10 minutes.
  - The platform model is called through the agent's `POST /internal/complete` (agent token plus the user's bearer), because litellm and its settings live only in the agent.
  - Usage is recorded with `record_llm_usage(..., "platform")`, so it counts toward the tenant budget; a budget that has run out skips generation.
- **API** (`odn/api/question_routes.py`):
  - `GET /v1/connections/{id}/questions?path=` returns `{status, questions ≤ 5}`;
  - `POST …/questions/{qid}/open`;
  - `POST /v1/questions/capture` (agent only).
- **Popular** means at least 3 distinct askers.
- **Capture:** an `after_agent_callback` in the agent (`odn/agent/capture.py`).
  - It stores only the **first question of a conversation**, never imported or shared threads.
  - The answer must come from one connection, with every `run_sql` successful and nothing awaiting approval. An approval granted in a later invocation still counts.
  - Tables come from the successful SQL, through the connection's policy, or from `get_table_schema` for metadata-only answers. At most 3 tables are recorded.
  - Starters, follow-ups, notices and clarifying replies (ending in "?") are skipped. A question that trips the privacy filter is stored privately for the asker.
- **Saved answers:**
  - Each stores the final text and its SQL. No result preview or chart is stored; the SQL can rerun them.
  - **Opening one** (`…/open`) imports a new conversation owned by the user, with the same mechanism as continuing a shared answer. It starts with "*Saved answer from <date>. The data hasn't changed since / has changed since…*", and the SQL appears as folded blocks with **Open in SQL workbench**.
  - Follow-ups work as in any conversation. A stale answer still opens, with the "has changed" note.

**Frontend** ([TableQuestions.tsx](../frontend/app/components/TableQuestions.tsx))
- **Two sections:**
  - **Get to know this table:** the starter chips.
  - **Questions about the data:** full-sentence rows, because generated questions read better than 6-word titles. Columns show as inline code.
- **Badges:** ★ Popular, You asked, Asked by a teammate, ✦ Suggested, Private.
- **Saved answers:** marked ⚡ *Saved answer* (or *data changed*). A click opens the saved answer instantly; ↻ pre-fills the question to ask again. Other questions pre-fill on click.
- **While generating:** three shimmer rows, polling every 2s for up to 25s.
- **No manual curation** (decided 2026-11-27): every asked question joins the list automatically, so there is no Edit UI or curation API. The `curated` source and `hidden` flag stay in the schema, unused.
- **Empty:** the section is hidden until the table has questions (suggested or asked); the ⚡ hint shows only when a saved answer is listed.
- **Explorer:** the selected table gets **Suggested questions ›**, which starts a new conversation scoped to it.
