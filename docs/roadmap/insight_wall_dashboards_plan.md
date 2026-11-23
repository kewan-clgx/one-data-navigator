# QueryMuse — Insight Wall and dashboards plan

**Status:** W1 built (2026-11-22), not yet deployed. Builds on answer sharing ([../chat_session_share_plan.md](../chat_session_share_plan.md))
and the learning roadmap ([odn_mvp_extend_plan.md](./odn_mvp_extend_plan.md)).

**Idea:** a shared answer is an **insight**. Today it travels as a link to the people who receive it. The Insight
Wall puts insights in one place where people can find, upvote and reuse them (like Digg or Hacker News, but for
answers about data). Dashboards come later as a published, refreshable *collection* of insights, not as a separate
BI tool.

---

## 1. Two walls

| Wall | Who posts | Who reads | Where |
|---|---|---|---|
| **Organization wall** | Members of an enterprise organization | Signed-in members of that organization | `/insights` in the app (tab *Organization*) |
| **Public wall** (owned by QueryMuse) | Personal accounts (the open `personal` tenant), and enterprise members whose admin allows it | Anyone, no sign-in | `insights.querymuse.ai` and `/insights` in the app (tab *Public*) |

- **Personal accounts** (no enterprise email domain) belong to the open `personal` tenant (ODN_PERSONAL_TENANT).
  Anyone can join it, so **it has no organization wall**. Its "organization" is QueryMuse itself, and its users post
  to the public wall.
- **Enterprise users** choose, when they share: *not on a wall*, *my organization's wall*, or *the public QueryMuse
  wall*. The public option is shown only if their admin turned it on. It is **off by default for enterprise
  organizations**, because it publishes company data to the internet.
- An item on the public wall also appears on its organization's wall (public is a superset).

### Rules

| Rule | Why |
|---|---|
| Posting to a wall is an explicit choice (`wall` on the share), separate from who can open the link | Sending a link to Bob is not broadcasting to everyone. Existing links are not listed automatically. |
| Organization wall ⇒ link audience is *organization* or *anyone with the link*; public wall ⇒ *anyone with the link* | A reader who finds the item must be able to open it |
| An item stays on the wall only while its link is active (not revoked or expired) and its organization still allows that wall and its sharing | Turning a setting off hides items at once. Revoking removes them. |
| The public wall shows what the public link already shows: title, question, answer, chart (no SQL, result names, emails or tenant ids) and the author's display name. The organization's name is shown, except for the personal tenant | Same exposure as an "anyone with the link" share, now discoverable |
| A warning is shown when the answer used a personal connection or Collibra (same as the share dialog) | People see results even if they can't access that data. For personal accounts it's their own data. |
| Unauthenticated reads go only through `SECURITY DEFINER odn_public_wall()` (and `odn_public_share()` for one link) | One audited, fail-closed path for anonymous readers |

### Ranking (Digg / HN style)

Tabs: **Hot** (default) · **New** · **Top**. On the organization wall, **Featured** items (picked by an admin)
come first in Hot.

```
score = (upvotes + 2·follow-ups + 0.2·signed-in views + 1) / (hours since posted + 2) ^ 1.5
```

- **Upvotes:** one per signed-in user per item, and not on your own item. Users of any organization can upvote
  public items.
- **Follow-ups:** readers who pressed *Ask a follow-up* (`shared_conversation_views.continued_thread_id`). This is the
  strongest sign that an insight was useful.
- **Views:** signed-in first views (already recorded).
- **Search:** v1 matches the title and question text. Later: embeddings (pgvector) over title, question and
  answer.

### Data model (migration `0022_insight_wall.sql`)

```sql
ALTER TABLE shared_conversations
  ADD COLUMN wall text NOT NULL DEFAULT 'none' CHECK (wall IN ('none', 'organization', 'public')),
  ADD COLUMN listed_at timestamptz,          -- when it was (last) posted to a wall
  ADD COLUMN featured_at timestamptz;        -- pinned by an organization admin
-- wall = 'organization' needs audience organization|public; wall = 'public' needs audience public.

ALTER TABLE tenant_sharing_settings
  ADD COLUMN org_wall boolean,      -- NULL = default: on, except for the personal tenant (always off)
  ADD COLUMN public_wall boolean;   -- NULL = default: on for the personal tenant, off for organizations

CREATE TABLE insight_votes (       -- tenant_id is the VOTER's organization (RLS); counts across
  share_id text REFERENCES shared_conversations ON DELETE CASCADE,  -- organizations only via odn_public_wall()
  tenant_id uuid, user_id uuid, created_at timestamptz, PRIMARY KEY (share_id, user_id));

-- odn_public_wall(p_personal_slug, p_id, p_q, p_sort, p_limit, p_offset): SECURITY DEFINER; listed, public,
-- active, organization active and allowing public links + the public wall; returns card fields and counts only.
```

### API

| Endpoint | Who | What |
|---|---|---|
| `POST /v1/shares` `{…, wall}` | member | Create a link and post it to a wall in one step |
| `PUT /v1/shares/{id}/wall` `{wall}` | owner (admin: only `none`) | Post an existing link to a wall, move it or remove it |
| `GET /v1/wall?scope=organization\|public&sort=hot\|new\|top&q=&offset=` | member | Wall cards with counts, `my_vote`, `is_owner` |
| `POST` / `DELETE /v1/wall/{id}/vote` | member | Upvote or undo |
| `POST /v1/wall/{id}/feature` `{featured}` | admin | Pin on the organization wall |
| `GET /v1/public/wall?sort=&q=&offset=` | anyone | Public wall cards (via `odn_public_wall`) |
| `PUT /v1/admin/sharing` `{…, org_wall, public_wall}` | admin | Organization switches |

Activity log: `wall.posted`, `wall.removed`, `wall.featured`, `share.settings_saved` (with the wall switches).

### User experience

- **Share dialog:** a *Post to* choice under the preview: *Don't post* · *{Organization} Insight Wall* · *Public
  QueryMuse Insight Wall (insights.querymuse.ai)*. Personal accounts see only *Don't post* and *Public*.
- **Links for this conversation:** shows where each link is posted, with *Post / Move / Remove from wall*.
- **`/insights`:** cards in a responsive grid. Each card shows the title, a one- or two-sentence highlight, a small
  chart, the author (and organization on the public wall), the age, an ▲ upvote count and follow-ups. Clicking a
  card opens the report (`/s/{id}`), which has *Ask a follow-up*. The page has a search box and Hot/New/Top. Signed
  in: tabs *Organization* and *Public*. Signed out: Public only, and ▲ asks the visitor to sign in.
- **App:** an *Insights* button in the chat toolbar opens `/insights`. Signed in, the wall's top bar links back
  to the app's *Chats* tab (`/?tab=chats`), where every other tab is one click away. The app opens a side-panel
  tab from `?tab=<id>` and starts a new conversation from `?new=1`.
- **Organization → Sharing:** *Organization Insight Wall* on/off, *Allow posting to the public QueryMuse wall*
  (off by default), and a *Wall* column with *Remove from wall* / *Feature* on each link.
- **insights.querymuse.ai:** a Caddy host (`ODN_INSIGHTS_HOSTNAME`) that serves `/` → `/insights` (public wall)
  and the preview images. Every other path (report pages, sign-in, voting) redirects to the app host.

### Later

- Public-wall moderation by QueryMuse operators (`python -m odn.db.provision wall-hide <share_id>`), and readers
  reporting an item.
- Counting anonymous views (deduplicated, no personal data) on the public wall.
- Search by meaning (embeddings); tags and filters by dataset/connection; *Following* authors.
- Feeding highly upvoted items into **Learning** (`learning_suggestions` → `verified_queries`): the best signal
  that a question and its SQL are worth keeping.
- Search-engine indexing of public items (today `noindex`), once moderation exists.

---

## 2. Dashboards

**Yes, but as a collection of insights that refreshes, built from chat answers.** The value is: *ask → like the
answer → Add to dashboard → it stays current, published to the organization (and its wall)*.

**Don't build:** a drag-and-drop chart editor, a semantic/metrics layer, cross-filtering or pixel-perfect
layouts. Those compete with Looker/Power BI. Offer embedding or export instead.

### Design choices

1. **A tile is a saved query, not a frozen snapshot:** the SQL, connection, chart spec and parameters from the
   answer's `run_sql`/`generate_chart` calls. Parameters use the same `:param` format as `verified_queries`, so a
   tile can point to a verified query.
2. **Refresh runs as the publisher**, on organization-shared connections only (personal connections blocked). Each
   result is saved as Parquet in the artifact store, and viewers read the saved result: fast, cheap and the same
   numbers for everyone. *Ask a follow-up* on a tile re-runs under the viewer's own access. Running each tile under
   each viewer's access would be slow, costly on BigQuery and give different numbers per person, so it's not for v1.
3. **Schedule:** a worker job per dashboard (daily/weekly) with a BigQuery bytes limit. Each tile shows "Data as of
   …". A failed refresh keeps the last good result and notifies the owner.
4. **Filters:** v1 filters on saved results (DuckDB over the Parquet). Parameters that re-run the query come later.
5. **AI-native creation:** *Add to dashboard* on each answer. "Build me a weekly sales dashboard" makes the agent
   propose tiles. An "Explain what changed" note on each refresh.
6. **Governance:** draft → published, owner + editors, a *Certified* badge from data leads, and organization or
   chosen-people audience. Published dashboards appear on the Insight Wall as one kind of insight.

```sql
saved_queries(id, tenant_id, owner_user_id, connection_id, sql_text, params jsonb, chart_spec jsonb,
              source_share_id, verified_query_id NULL)
dashboards(id, tenant_id, owner_user_id, title, description, layout jsonb, audience,
           status draft|published, certified_by, refresh_cron, last_refreshed_at)
dashboard_tiles(id, dashboard_id, tenant_id, kind kpi|chart|table|text, saved_query_id, position jsonb)
dashboard_tile_results(tile_id, tenant_id, run_at, object_key, row_count, bytes_scanned, error)
-- all ENABLE + FORCE RLS, grants to odn_app (tests/db/test_rls.py guard)
```

---

## 3. Phases

| Phase | Scope | Matches share roadmap item | Effort |
|---|---|---|---|
| **W1** | Insight Wall: organization + public walls, post/move/remove, votes, Hot/New/Top, search by title, admin switches + feature, `/insights`, insights host | new | 2–3 d |
| W2 | Public-wall moderation and reporting, anonymous views, search by meaning, Learning feed | new | 2 d |
| D1 | **Boards**: several answers on one page (static snapshot), postable to a wall | #9 | 2 d |
| D2 | Refreshable tiles: save SQL, scheduled re-run, saved results, "Data as of" | #8 | 3–4 d |
| D3 | Parameter filters, agent-built dashboards, Certified badge, link to verified queries | Learning | 3–5 d |
| D4 | Scheduled delivery to Slack/Teams, threshold alerts | #14 | later |

**Before D2,** check whether wall usage (upvotes, follow-ups, Featured items) shows demand for recurring reports.

---

## 4. W1 to-do list

- [x] **Migration** [`0022_insight_wall.sql`](../../backend/src/odn/db/migrations/0022_insight_wall.sql): `wall` /
  `listed_at` / `featured_at` + check constraint, settings columns, `insight_votes` (RLS + grants),
  `odn_public_wall()` (SECURITY DEFINER, EXECUTE to odn_app only)
- [x] **Service** [`platform/sharing/wall.py`](../../backend/src/odn/platform/sharing/wall.py): organization wall
  query, public wall (definer), votes, feature, post/move/remove. `service.settings_for()` applies the wall
  defaults (personal tenant), `check_wall()` the posting rules. `create()` accepts `wall`
- [x] **Routes** [`api/wall_routes.py`](../../backend/src/odn/api/wall_routes.py): `/v1/wall`, `/v1/public/wall`,
  `PUT /v1/shares/{id}/wall`, admin settings fields
- [x] **Tests** [`tests/db/test_wall.py`](../../backend/tests/db/test_wall.py): organization wall visibility and
  isolation, votes (no self-vote, cross-organization on public), public wall requires the admin switch + public
  link, personal tenant defaults, switches hide items, anonymous payload has no ids/emails/SQL, Hot vs New
- [x] **Frontend:** `lib/wall.ts`, `lib/public-wall.ts`, `/insights` page + `InsightWall` (tabs, Hot/New/Top,
  search, cards with chart and highlight, upvote, paging; signed out: public only, server-rendered)
- [x] **Share dialog:** *Post to an Insight Wall* choice. *Links for this conversation:* wall status and
  post/move/remove
- [x] **Organization → Sharing:** wall switches, *Wall* column, Feature / Remove from wall
- [x] **App toolbar:** *Insights* button
- [x] **Caddy:** `ODN_INSIGHTS_HOSTNAME` host (`/` → `/insights`; everything else → the app),
  `docker-compose.prod.yml`, [deploy_gcp_vm.md](../deploy_gcp_vm.md)
- [ ] **Deploy:** DNS A record for `insights.querymuse.ai`, `ODN_INSIGHTS_HOSTNAME` in `.env`, `deploy/deploy.sh up`
