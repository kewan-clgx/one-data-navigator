# Artifact actions: delete, archive, share

*Status: built 2026-11-28. Code: [manage.py](../backend/src/odn/platform/workspace/manage.py), [0041_artifact_actions.sql](../backend/src/odn/db/migrations/0041_artifact_actions.sql), `share_artifact` in [sharing/service.py](../backend/src/odn/platform/sharing/service.py), [ArtifactsPanel.tsx](../frontend/app/components/ArtifactsPanel.tsx), [ArtifactShareDialog.tsx](../frontend/app/components/ArtifactShareDialog.tsx).*

Every artifact (result table, chart, file, saved SQL) has a ⋯ menu with **Share…**, **Archive…** and **Delete**. Saved SQL also has Rename, Duplicate and Copy SQL.

## Delete

- **What it removes:** the row, at once, and its stored object (table Parquet, uploaded file). It runs after a **5-second Undo** window.
- **API:** `DELETE /v1/artifacts/{kind}/{id}`. Owner only: `workspace_artifacts` now allows UPDATE and DELETE only by the owner, while reads stay tenant-wide, so sharing can still copy.
- **Names aren't reused.** `workspace_name_floor` records the highest `q`/`c` number deleted in each conversation, and new results continue above it. The conversation's earlier mentions of `q3` therefore never point at a different table.
- **What stays:** shared links keep working, because they hold their own copy. The conversation's own history still mentions the deleted result.

## Archive

- **What it does:** `POST /v1/artifacts/{kind}/{id}/archive {keep: "30d" | "forever"}` sets `archived_at`, and `purge_at` (now + 30 days, or NULL for forever).
- **Where archived items disappear from:**
  - the Artifacts list and its search;
  - saved SQL search, the Explorer's "Saved SQL (n)" and the agent's `find_saved_sql`.
- **Where they still appear:** the conversation they came from.
- **Archived view:** an **Archived** switch in Artifacts shows only archived items, each labelled "deleted for good on <date>" or "kept until you delete it". They offer **Restore** (`POST …/restore`) and **Delete**. Archive also offers Undo right away.
- **Purge:** an hourly thread in the API (`run_purger`) calls the `SECURITY DEFINER` function `odn_purge_archived_artifacts()`.
  - The function deletes only rows whose `purge_at` has passed (across tenants, in batches) and returns their storage keys, which are then deleted.
  - It's safe on several instances, since each row is deleted once.

## Share

- **What a link is:** a share of kind `artifact` in `shared_conversations`. It is a **frozen copy**: table Parquet and file copied under the share, chart result and SQL text stored with it.
- **What it inherits from shared answers:** links, expiry (7/30/90 days), extend, revoke, view counts, admin events, email notifications, the `/s/<id>` page and its Teams/Slack/email preview.
- **API:**
  - `POST /v1/shares/artifact {kind, id, title, audience, expires_days}`;
  - `GET /v1/shares/options` (what the organization allows);
  - `POST /v1/shares/{id}/save-sql`, which saves a shared SQL into the reader's own Artifacts.
- **Audiences:**
  - **People in your organization** (after sign-in): every kind.
  - **Anyone with the link:** tables (first rows) and charts (the plot), and only when the organization allows public links.
  - SQL and files always need sign-in. That matches answers, whose public view shows no SQL or files. Unauthenticated reads still go only through `odn_public_share()`.
- **Signed-in reader's page:**
  - SQL with **Copy SQL** and **Save to my SQL**;
  - a paged table with its SQL;
  - the chart;
  - a file download.
- **Not available:** "Ask a follow-up". An artifact isn't a conversation.
- **Limits:** archived items can't be shared. The share dialog lists earlier links for the same item, each with Extend and Revoke.
