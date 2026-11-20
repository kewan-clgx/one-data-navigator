# Share a chat session with teammates (Teams / Slack) — plan for review

**Status:** Phase 1 + 1b built (2026-11-20); next: phase 2 *Continue* and the admin switch in the UI. See [§10](#10-progress). **Decided:** MVP = model A (snapshot + continue); live shared thread (model C) is a nice-to-have, sized in §8.1. Audience: pick people by default. Expiry: 30 days by default. Results included by default (with warnings). Admins can turn sharing off. Delivery: in-app + email + Teams chat link (§6.1).
**Goal:** a user shares a conversation (questions, answers, SQL, result tables, charts) through a link they paste
in Microsoft Teams or Slack; a teammate in the same organization opens it in QueryMuse and **asks follow-up
questions** that build on it.

---

## 1. Where we are today

| Piece | Today | Implication for sharing |
|---|---|---|
| Conversation | ADK session in Postgres (`adk` schema), `user_id` = tenant + user (`agent/main.py` `_user_id`); thread id = session id | Only the owner can load or continue it |
| Chats tab | `GET /conversations` lists the caller's own sessions | Teammates can't see it |
| History restore | Frontend hydrates messages, step cards and the latest result from the agent state | A teammate's browser can't call this for someone else's thread |
| Result tables `q1..qN` | `workspace_artifacts` + Parquet in GCS under `odn/<tenant>/<user>/...` (`assert_tenant_key`) | Follow-ups via DuckDB only see the owner's prefix |
| Charts, uploaded files | `workspace_charts`, `workspace_files`, RLS **owner_only** | Teammates can't read them |
| Data access | Each user's connections (shared or **personal**), allowlists, Collibra with the user's own permissions | A teammate may not be allowed to query what the owner queried |

So sharing needs (a) a way to hand over a conversation's content and results across users, and (b) a clear rule for
whose permissions apply to follow-ups.

## 2. Is AG-UI / CopilotKit "Rich Threads" related?

Only loosely; we don't need it for this.

- **Rich Threads** is the Inspector's name for **CopilotKit Intelligence AG-UI Streams**: a hosted (or self-hosted
  Kubernetes) service that records the AG-UI events of a thread so the **same user** can reconnect, replay and
  continue on another tab or device. It complements the framework's thread store (our ADK sessions).
- It doesn't give sharing between users, organization-level permissions or chat-app integration, and it would add
  a third-party store of conversation content (a data-handling question for clients like Cotality).
- We already restore threads from our own ADK sessions. The pieces sharing needs (snapshot, permissions, fork)
  are ours to build either way. **Recommendation:** don't adopt Intelligence for this; revisit only if we want
  multi-device live reconnect later.

## 3. What "share" should mean (recommendation)

Three models:

| Model | How it works | Pros | Cons |
|---|---|---|---|
| **A. Snapshot + continue (recommended)** | Owner shares a link to a **frozen copy** of the conversation as of now. Teammate views it read-only, then **Continue** creates *their own* conversation seeded with it, and asks follow-ups there | Clear consent (exactly what was shared); owner's thread untouched; each person's follow-ups run with **their own** permissions; no concurrency issues | Two threads diverge (fine for analysis work) |
| B. Live view link | Link shows the owner's thread as it keeps growing | Always current | Owner may later add things they didn't mean to share; harder to reason about |
| C. Shared thread (collaborative) | Several people post in the **same** thread | Like a team chat | Whose permissions run queries? Concurrent runs, attribution, ADK session ownership; much larger change |

**Recommend A for v1**, with C kept as a later option if teams want one shared workspace.

### Permissions rules (v1)

1. **Same organization only.** The link works only for signed-in users of the owner's organization (tenant). No
   public links, no cross-organization sharing. A link alone grants nothing.
2. **The owner decides what's shared.** Sharing is like sending a screenshot: the shared copy includes the answers,
   SQL and (if chosen) result rows and charts, even if the teammate couldn't query that data themselves. The share
   dialog warns when the conversation used a **personal connection** or **Collibra** (data the teammate may not have
   access to), and offers **"Share without result data"** (answers + SQL only).
3. **Follow-ups use the teammate's own access.** In the continued conversation, re-using the shared result tables
   (DuckDB follow-ups, charts) works because they are copied with the share; **new warehouse queries** run on the
   teammate's own connections and allowlists. If the teammate lacks the connection, the agent says so.
4. **Owner and admin control.** Owners can revoke links; links expire (default 30 days); admins can turn sharing off
   for the organization and see all shares; every share, view and continue is recorded in the activity log.

## 4. User experience

**Owner**

1. In the chat toolbar (and on each item in the Chats tab): **Share**.
2. Dialog:
   - Who: **Specific people** (default): pick teammates from the organization's members (type-ahead on name/email;
     only active users of the same organization). Option: *Everyone in {organization}*.
   - Include: answers, SQL and results ✓ (or *answers and SQL only*).
   - Expires: 7 days / **30 days** (default) / 90 days; no "never expires" option. The owner can extend or
     revoke from *Manage sharing*.
   - Warning line if personal connections or Collibra were used.
3. **Create link** → `https://app.querymuse.ai/s/<share-id>` with buttons:
   - **Copy link** (paste in Slack or Teams),
   - **Share to Teams** (Microsoft's share URL `https://teams.microsoft.com/share?href=<link>&msgText=<title>`
     opens Teams' share dialog: pick a person, group chat or channel),
   - **Copy for Slack** (link plus one-line title).
4. Chats tab shows a "Shared" badge; **Manage sharing** lists links with views and **Revoke**.

**Teammate**

1. Clicks the link in Slack/Teams → QueryMuse (signs in if needed; must be the same organization and one of the
   chosen people, otherwise "This conversation wasn't shared with you — ask {owner} to add you").
2. Sees a read-only view: title, "Shared by Ann, 19 Nov", the messages with step cards and SQL, result tables
   (paged), charts, files.
3. **Continue this conversation** → a new conversation in their Chats tab ("Continued from: …"), with the shared
   history and result tables, ready for follow-up questions.

**Link previews in Slack/Teams.** Both apps fetch the URL without signing in to build a preview. The share page
returns only a generic preview (*"QueryMuse — a conversation shared with {organization} · sign in to view"*), never
the title, data or names, because the link may be pasted into channels with outsiders.

## 5. Design

```mermaid
sequenceDiagram
    autonumber
    actor A as Ann (owner)
    actor B as Ben (teammate, same org)
    participant UI as QueryMuse (BFF)
    participant API as API
    participant AG as Agent (ADK sessions)
    participant DB as Postgres (RLS)
    participant G as GCS workspace

    A->>UI: Share conversation (options)
    UI->>API: POST /v1/shares {thread_id, audience, include_results, expires}
    API->>AG: export thread (owner's token) → messages, tool results, artifact refs
    API->>DB: insert shared_conversations (snapshot) + artifact copies
    API->>G: copy Parquet / chart / file objects → odn/<tenant>/shares/<share_id>/...
    API-->>A: https://app.querymuse.ai/s/<share_id>
    A->>B: pastes link in Teams / Slack
    B->>UI: opens /s/<share_id> (signed in)
    UI->>API: GET /v1/shares/<id> (tenant + audience + not revoked/expired)
    API-->>B: read-only transcript + artifacts
    B->>UI: Continue this conversation
    UI->>API: POST /v1/shares/<id>/continue
    API->>G: copy share artifacts → odn/<tenant>/<ben>/<new session>/...
    API->>AG: import snapshot as Ben's new session (events appended)
    AG-->>B: new thread in Ben's Chats; follow-ups run with Ben's access
```

### 5.1 Data model (migration)

```sql
CREATE TABLE shared_conversations (
  id               text PRIMARY KEY,            -- 22-char random URL id (not a secret; access is checked)
  tenant_id        uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  owner_user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  source_thread_id text NOT NULL,
  title            text NOT NULL,
  audience         text NOT NULL DEFAULT 'people' CHECK (audience IN ('people', 'organization')),
  include_results  boolean NOT NULL,
  snapshot         jsonb NOT NULL,              -- AG-UI messages + tool results (as the frontend renders them)
  artifacts        jsonb NOT NULL DEFAULT '[]', -- copied tables/charts/files: name, kind, object key, row count
  used_personal_connections boolean NOT NULL DEFAULT false,
  used_collibra    boolean NOT NULL DEFAULT false,
  created_at       timestamptz NOT NULL DEFAULT now(),
  expires_at       timestamptz NOT NULL,
  revoked_at       timestamptz
);
CREATE TABLE shared_conversation_recipients (share_id text REFERENCES shared_conversations ON DELETE CASCADE,
  tenant_id uuid NOT NULL, user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  PRIMARY KEY (share_id, user_id));   -- chosen people (existing users of the organization)
CREATE TABLE shared_conversation_views (share_id text, tenant_id uuid NOT NULL, user_id uuid NOT NULL,
  first_viewed_at timestamptz, continued_thread_id text, PRIMARY KEY (share_id, user_id));
```

- RLS **ENABLE + FORCE**, policy *tenant isolation* (the guard test requires it); readers further filtered by
  audience/recipient in the API. Owner-only writes except view records.
- Organization setting `sharing_enabled` (default on) and max lifetime on `tenants` (admin-editable).
- Activity-log events: `share.created`, `share.revoked`, `share.viewed` (first view per user), `share.continued`.

### 5.2 Snapshot and storage

- **Export** (agent service, owner's token): the thread's AG-UI messages (user/assistant text, tool calls and
  results as rendered by step cards) — the same data the history hydration uses — plus its artifacts. Large tool
  results are already truncated samples; full tables come from the copied Parquet.
- **Copy artifacts** at share time to `odn/<tenant>/shares/<share_id>/...` (a new `share_key()` + assertion next
  to `assert_tenant_key`), so revoking or the owner deleting the conversation doesn't break or leak anything.
  *Answers and SQL only* skips this and strips result samples from the snapshot.
- Size limits: e.g. ≤ 200 messages, ≤ 50 artifacts, ≤ 1 GB copied per share; otherwise ask to share a shorter
  conversation.

### 5.3 Continue (fork)

- API copies the share's artifacts into the teammate's workspace for a **new session id** (rows in
  `workspace_artifacts` / `workspace_charts` / `workspace_files` with the teammate as owner; objects under their
  prefix), keeping names `q1..qN`, `c1..`, so the history and the model's context stay consistent.
- Agent creates the teammate's ADK session and appends the snapshot as events, plus a short system note: *"This
  conversation was shared by Ann on 19 Nov. Earlier results are in the workspace; new warehouse queries use your
  own connections."* The model therefore sees the prior context and continues naturally.
- The chat opens on the new thread; Chats lists it as "Continued from: <title> (shared by Ann)".

### 5.4 API

| Method | Path | Who |
|---|---|---|
| `GET` | `/v1/shares/people?q=` | Signed-in member: type-ahead of active users in the same organization (name, email) |
| `POST` | `/v1/shares` | Owner of the thread |
| `GET` | `/v1/shares?mine=1` | Owner (manage); admins: all in organization |
| `GET` | `/v1/shares/{id}` | Same organization + audience, not revoked/expired |
| `GET` | `/v1/shares/{id}/artifacts/...` | Same as above (rows paged, chart, file download) |
| `POST` | `/v1/shares/{id}/continue` | Same as above |
| `DELETE` | `/v1/shares/{id}` | Owner or admin (revoke) |

Frontend: `/s/[id]` page (read-only transcript reusing the step-card and results components), Share dialog,
Manage sharing, Organization → Sharing settings. Generic Open Graph tags on `/s/*`.

## 6. Teams and Slack

| Level | What | Effort | When |
|---|---|---|---|
| **1. Link (v1)** | Copy link; **Share to Teams** button (Microsoft share URL); copy for Slack; generic link preview | Small | v1 |
| 2. Slack app | Slack app with **link unfurling**: when `app.querymuse.ai/s/...` is posted, our app checks the poster's QueryMuse identity (one-time account link) and shows a richer preview (title, owner, first answer) only in channels the organization allows | Medium: Slack app review, per-workspace install, OAuth, signing secret | After v1, if asked |
| 3. Teams app | Teams **message extension** with link unfurling (Adaptive Card), same rules; optionally "Share to QueryMuse" from Teams | Medium: Azure bot registration, Teams admin approval per client tenant | After v1, if asked |

Level 1 covers "send it to my teammate in Teams/Slack" without any install or client IT approval. Levels 2–3 mainly
add nicer previews and require each client's Slack/Teams admin to approve an app.

### 6.1 Better: deliver it to the chosen people directly (MVP)

Because the owner picks people, QueryMuse knows who the recipients are and can bring the share to them, so pasting a
link is optional:

| Idea | How | Effort |
|---|---|---|
| **"Shared with me" in the Chats tab** | Recipients see new shares at the top of Chats (badge on the tab), open the read-only view or **Continue** in one click | 0.5 day |
| **Email notification** | On share, each recipient gets a branded email via Resend (already used for invites): *"Ann shared 'Corporate revenue by state' with you"* + **Open in QueryMuse** button. No content beyond the title the owner chose (owner can edit the title in the dialog) | 0.5 day |
| **Open a Teams chat with those people** | **Send in Teams** opens Microsoft Teams' chat deep link `https://teams.microsoft.com/l/chat/0/0?users=<email1>,<email2>&message=<title + link>` — a new or existing chat with exactly the chosen people, message pre-filled; the owner presses Send | 0.25 day |
| Slack | Slack has no equivalent pre-filled link for people by email without a Slack app, so **Copy for Slack** (title + link) stays | — |
| Phone/tablet | **Share…** uses the device's share sheet (`navigator.share`) when available | 0.1 day |

Later (separate scope): share a **single result table or chart** instead of the whole conversation, and an optional
**"Copy summary"** (owner-approved text of the final answer) for pasting into channels.

## 7. Security and privacy checklist

- Same-tenant only; audience/recipient check on every read; revocation and expiry enforced server-side.
- Snapshot is immutable; artifacts copied under a share-specific prefix with key assertions; RLS on all new tables.
- No content in link previews; `Cache-Control: private, no-store` on share pages.
- Warnings for personal connections and Collibra; "answers and SQL only" option.
- Follow-ups never inherit the owner's connections, keys or Collibra session.
- Audit: create, view, continue, revoke in the activity log; admins can list and revoke all shares.
- Tests: cross-tenant access denied, wrong audience denied, revoked/expired denied, artifact keys can't escape the
  share prefix, continued thread owned by the teammate, owner's thread unchanged.

## 8. Phases and effort (estimate)

| Phase | Scope | Estimate |
|---|---|---|
| 1 | Migration, share create/list/revoke, export snapshot, artifact copy, read-only `/s/[id]` page, Share dialog (**people picker**, Copy link, Share to Teams), generic previews, audit, tests | 3.5–4.5 days |
| 1b | Direct delivery (§6.1): "Shared with me" in Chats, email notification, **Send in Teams** chat link, device share sheet | 1–1.5 days |
| 2 | Continue (fork into teammate's workspace + ADK session import), Chats "Continued from", agent note | 2–3 days |
| 3 | Admin settings (enable, max lifetime), admin list | 1 day |
| Later | Slack/Teams apps with authenticated unfurling | separate plan |

### 8.1 Nice-to-have: live shared thread (model C) — what it would take

Several people in one conversation, seeing each other's questions and answers as they happen.

| Work item | Why it's needed | Estimate |
|---|---|---|
| Thread membership (`thread_members`: thread, tenant, user, role owner/member) and a **Share as live thread** option | ADK sessions are stored per user today; a shared thread stays under the owner's session, members get access through membership | 1 day |
| Agent runs on a shared thread: load the owner's session, but run tools with the **asking member's** token (their connections, allowlists, Collibra) | Each person's queries keep their own permissions | 1–1.5 days |
| One run at a time per thread (lock) + "Ann is asking…" status for others | Two simultaneous runs in one ADK session would corrupt its history | 0.5–1 day |
| Shared results: tables/charts created in the thread readable by all members (thread-scoped workspace prefix and RLS policy on thread membership) | Today results are owner-only | 1–1.5 days |
| Live updates: members' open chats refresh when someone else's run finishes (poll the thread state every few seconds, or server-sent events) | Others' messages must appear without reload | 1 day |
| Attribution: each user message shows who asked; activity log for joins/leaves | Clarity and audit | 0.5 day |
| Tests (membership, isolation, concurrent runs, permissions per asker) | | 1 day |

**Total ≈ 6–8 days on top of the MVP.** It reuses most of the MVP (share links, audience checks, previews,
activity log), so it is cheaper after the MVP than as a separate project. Main risk: results visible to the whole
thread can include data some members couldn't query themselves (same rule and warning as for snapshots).
Recommendation: ship the MVP, see how teams use *Continue*, then decide on C.

## 9. Questions for review

1. ~~Model A or C?~~ **Decided:** A for the MVP; C nice-to-have (§8.1, ≈ 6–8 extra days).
2. ~~Default audience?~~ **Decided:** pick people (members of the same organization); *everyone in the organization* stays an option. People not yet in QueryMuse can't be picked (invite them first).
3. ~~Default expiry?~~ **Decided:** 30 days by default (7 / 30 / 90 to choose from), no "never expires"; owners can extend.
4. ~~Include results by default?~~ **Decided:** yes, with the personal-connection / Collibra warning and the *answers and SQL only* option.
5. ~~Admin switch?~~ **Decided:** yes; sharing on by default, admins can turn it off for their organization.
6. ~~Teams/Slack level?~~ **Decided:** level 1 (link + Share to Teams + copy for Slack), plus the direct delivery in §6.1.

## 10. Progress

**2026-11-20 — Phase 1 + 1b built** (share, view, direct delivery):

- Migration `0017`: `shared_conversations` (snapshot of messages, copied artifacts, flags, expiry, revoke),
  `shared_conversation_recipients`, `shared_conversation_views`, `tenant_sharing_settings`; RLS on all.
- API `/v1/shares`: `preview`, `people` (type-ahead, active members of the organization), create, list (`mine` /
  `with_me`), get, table rows, file download, extend, revoke; `/v1/admin/sharing` (on/off). The snapshot is read
  **server-side from the agent with the owner's own token** (never sent by the browser); tool results are blanked
  for *answers and SQL only*; system messages dropped. Result tables and files are copied to
  `odn/<tenant>/shares/<share>/` (`share_key` / `assert_share_key`); charts are copied inline.
- Access: owner, chosen people, or everyone in the organization; same tenant only; expiry (7/30/90 days) and revoke
  enforced; others get 404. Activity log: `share.created`, `share.viewed` (first view), `share.revoked`,
  `share.settings_saved`.
- Email to recipients (Resend, title only), **Send in Teams** (chat with the chosen people, message pre-filled),
  **Share to Teams…**, **Copy for Slack**, device share sheet.
- UI: **Share** button in the chat toolbar → dialog (title, people picker / everyone, results on/off with warnings for
  personal connections and Collibra, expiry; then link + delivery buttons; links for this conversation with views,
  *Extend 30 days*, *Revoke*); **Shared with me** in the Chats tab (new until opened); read-only page `/s/<id>`
  (transcript with step cards and SQL, results tables with paging, charts, files); signed-out visitors are asked to
  sign in and returned to the link afterwards; link previews show only *"QueryMuse — shared conversation · Sign in
  to view"* (verified: no title or data in the unauthenticated HTML).
- Tests (`tests/db/test_shares.py`): chosen person can read, unchosen colleague and other organization get 404,
  artifacts copied under the share prefix, answers-only blanks results, expiry and revoke, organization switch,
  audit events. Verified locally end to end (10 result tables copied and paged from the copy).

**Next:** phase 2 *Continue this conversation* (fork into the viewer's workspace + ADK session), phase 3 admin
switch in the Organization tab (API exists).
