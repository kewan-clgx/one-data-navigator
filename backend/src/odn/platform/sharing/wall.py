"""Insight Wall (docs/roadmap/insight_wall_dashboards_plan.md): shared answers posted to a wall, ranked by upvotes,
follow-ups and age.

Two walls: an organization's own (signed-in members, tenant RLS) and the public QueryMuse wall (anyone, read only
through SECURITY DEFINER odn_public_wall()). An item is a share whose owner chose to post it; it disappears when the
link is revoked or expires, or when its organization turns that wall off.
"""

from __future__ import annotations

from typing import Any, Literal

from odn.config import settings
from odn.db import system_transaction, tenant_transaction
from odn.platform.sharing.service import ShareError, Wall, check_wall, settings_for
from odn.tenancy.context import RequestContext

Sort = Literal["hot", "new", "top"]
PAGE = 30

# The same card fields odn_public_wall() returns, for the organization wall (under tenant RLS).
_ORG_WALL = """
SELECT * FROM (
  SELECT s.id, s.kind, s.title, s.wall, s.featured_at IS NOT NULL AS featured, s.owner_user_id,
    (SELECT e.m->>'content' FROM jsonb_array_elements(s.messages) WITH ORDINALITY e(m, i)
      WHERE e.m->>'role' = 'user' AND coalesce(e.m->>'content', '') <> '' ORDER BY e.i LIMIT 1) AS question,
    (SELECT e.m->>'content' FROM jsonb_array_elements(s.messages) WITH ORDINALITY e(m, i)
      WHERE e.m->>'role' = 'assistant' AND coalesce(e.m->>'content', '') <> '' ORDER BY e.i DESC LIMIT 1) AS answer,
    (SELECT a FROM jsonb_array_elements(s.artifacts) a WHERE a->>'kind' = 'chart' LIMIT 1) AS chart,
    coalesce(nullif(u.display_name, ''), split_part(u.email, '@', 1)) AS owner_name,
    s.created_at, s.listed_at, s.expires_at, c.votes, c.follow_ups, c.views,
    EXISTS (SELECT 1 FROM insight_votes v WHERE v.share_id = s.id AND v.user_id = %(me)s) AS my_vote,
    ((c.votes + 2 * c.follow_ups + 0.2 * c.views + 1)
      / power(extract(epoch FROM now() - s.listed_at) / 3600 + 2, 1.5))::double precision AS score
  FROM shared_conversations s
  JOIN users u ON u.id = s.owner_user_id
  CROSS JOIN LATERAL (SELECT
    (SELECT count(*) FROM insight_votes v WHERE v.share_id = s.id) AS votes,
    (SELECT count(*) FROM shared_conversation_views v WHERE v.share_id = s.id AND v.continued_thread_id IS NOT NULL) AS follow_ups,
    (SELECT count(*) FROM shared_conversation_views v WHERE v.share_id = s.id) AS views) c
  WHERE s.wall IN ('organization', 'public') AND s.revoked_at IS NULL AND s.expires_at > now()
    AND (%(id)s::text IS NULL OR s.id = %(id)s)
) w
WHERE %(q)s::text IS NULL OR w.title ILIKE %(q)s OR w.question ILIKE %(q)s
ORDER BY CASE WHEN %(sort)s = 'hot' THEN w.featured END DESC,
         CASE WHEN %(sort)s = 'top' THEN w.votes END DESC NULLS LAST,
         CASE WHEN %(sort)s = 'hot' THEN w.score END DESC NULLS LAST,
         w.listed_at DESC
LIMIT %(limit)s OFFSET %(offset)s
"""


def _pattern(q: str | None) -> str | None:
    q = (q or "").strip()
    if not q:
        return None
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _chart(chart: dict[str, Any] | None) -> dict[str, Any] | None:
    """The plot only, as on the public report: no SQL, source tables or result names."""
    if not chart:
        return None
    result = chart.get("result") or {}
    return {"title": chart.get("title") or "", "result": {
        "spec": {**(result.get("spec") or {}), "source": ""}, "points": result.get("points") or [],
        "truncated": bool(result.get("truncated")), "sql": "", "source_tables": [], "source_truncated": False}}


def _card(row: dict[str, Any], me: Any = None) -> dict[str, Any]:
    return {
        "id": row["id"], "kind": row["kind"], "title": row["title"], "question": row["question"] or "",
        "answer": (row["answer"] or "")[:4000], "chart": _chart(row["chart"]), "owner_name": row["owner_name"],
        "organization": row.get("organization"), "created_at": row["created_at"], "listed_at": row["listed_at"],
        "expires_at": row["expires_at"], "votes": row["votes"], "follow_ups": row["follow_ups"],
        "featured": bool(row.get("featured")), "wall": row.get("wall", "public"),
        "my_vote": bool(row.get("my_vote")), "is_owner": me is not None and row["owner_user_id"] == me,
    }


def _public_rows(item_id: str | None, q: str | None, sort: Sort, limit: int, offset: int) -> list[dict[str, Any]]:
    with system_transaction() as conn:
        return conn.execute(
            "SELECT * FROM odn_public_wall(%s, %s, %s, %s, %s, %s)",
            (settings.personal_tenant or None, item_id, _pattern(q), sort, limit, offset),
        ).fetchall()


def public_wall(sort: Sort = "hot", q: str | None = None, offset: int = 0) -> list[dict[str, Any]]:
    """The public wall for anyone (no sign-in): no ids of people or organizations, no SQL."""
    out = []
    for row in _public_rows(None, q, sort, PAGE, offset):
        card = _card(row)
        del card["my_vote"], card["is_owner"]
        out.append(card)
    return out


def wall(ctx: RequestContext, scope: Literal["organization", "public"], sort: Sort = "hot", q: str | None = None,
         offset: int = 0) -> dict[str, Any]:
    """A signed-in member's view of a wall, with their own upvotes and which items are theirs."""
    org = settings_for(ctx)
    if scope == "public":
        rows = _public_rows(None, q, sort, PAGE, offset)
        ids = [r["id"] for r in rows]
        with tenant_transaction(ctx.tenant_id) as conn:
            mine = {r["share_id"] for r in conn.execute(
                "SELECT share_id FROM insight_votes WHERE user_id = %s AND share_id = ANY(%s)", (ctx.user_id, ids),
            ).fetchall()} if ids else set()
        items = [_card({**r, "my_vote": r["id"] in mine}, ctx.user_id) for r in rows]
        return {"enabled": True, "items": items, "settings": org}
    if not (org["enabled"] and org["org_wall"]):
        return {"enabled": False, "items": [], "settings": org}
    with tenant_transaction(ctx.tenant_id) as conn:
        rows = conn.execute(_ORG_WALL, {"me": ctx.user_id, "id": None, "q": _pattern(q), "sort": sort,
                                        "limit": PAGE, "offset": offset}).fetchall()
    return {"enabled": True, "items": [_card(r, ctx.user_id) for r in rows], "settings": org}


def _item(ctx: RequestContext, share_id: str) -> tuple[str, dict[str, Any]] | None:
    """The wall item this member may see: on their organization's wall, else on the public wall."""
    org = settings_for(ctx)
    if org["enabled"] and org["org_wall"]:
        with tenant_transaction(ctx.tenant_id) as conn:
            row = conn.execute(_ORG_WALL, {"me": ctx.user_id, "id": share_id, "q": None, "sort": "new",
                                           "limit": 1, "offset": 0}).fetchone()
        if row:
            return "organization", row
    rows = _public_rows(share_id, None, "new", 1, 0)
    return ("public", rows[0]) if rows else None


def vote(ctx: RequestContext, share_id: str, up: bool) -> dict[str, Any]:
    found = _item(ctx, share_id)
    if found is None:
        raise ShareError("This insight isn't on a wall you can see.", 404)
    where, row = found
    if row["owner_user_id"] == ctx.user_id:
        raise ShareError("You can't upvote your own insight.", 422)
    with tenant_transaction(ctx.tenant_id) as conn:
        if up:
            conn.execute(
                "INSERT INTO insight_votes (share_id, tenant_id, user_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (share_id, ctx.tenant_id, ctx.user_id),
            )
        else:
            conn.execute("DELETE FROM insight_votes WHERE share_id = %s AND user_id = %s", (share_id, ctx.user_id))
    _, after = _item(ctx, share_id) or (where, row)
    return {"votes": after["votes"], "my_vote": up}


def set_wall(ctx: RequestContext, share_id: str, wall_to: Wall) -> dict[str, Any]:
    """Post a link to a wall, move it or take it down. Admins may only take items down."""
    org = settings_for(ctx)
    with tenant_transaction(ctx.tenant_id) as conn:
        share = conn.execute(
            "SELECT id, owner_user_id, audience, wall, revoked_at, expires_at > now() AS active "
            "FROM shared_conversations WHERE id = %s", (share_id,),
        ).fetchone()
        owner = share is not None and share["owner_user_id"] == ctx.user_id
        if share is None or not (owner or (ctx.role == "admin" and wall_to == "none")):
            raise ShareError("Share not found.", 404)
        if wall_to != "none":
            if share["revoked_at"] is not None or not share["active"]:
                raise ShareError("This link has expired or was revoked; create a new one to post it.", 422)
            check_wall(org, share["audience"], wall_to)
        conn.execute(
            "UPDATE shared_conversations SET wall = %s, "
            "listed_at = CASE WHEN %s = 'none' THEN NULL WHEN wall = 'none' THEN now() ELSE listed_at END, "
            "featured_at = CASE WHEN %s = 'organization' OR %s = 'public' THEN featured_at END WHERE id = %s",
            (wall_to, wall_to, wall_to, wall_to, share_id),
        )
        conn.execute(
            "INSERT INTO admin_events (tenant_id, actor_user_id, action, target, detail) VALUES (%s, %s, %s, %s, "
            "jsonb_build_object('wall', %s::text, 'from', %s::text))",
            (ctx.tenant_id, ctx.user_id, "wall.removed" if wall_to == "none" else "wall.posted", share_id,
             wall_to, share["wall"]),
        )
    return {"wall": wall_to}


def feature(ctx: RequestContext, share_id: str, featured: bool) -> dict[str, bool]:
    """Admins pin an item to the top of their organization's wall (Hot)."""
    with tenant_transaction(ctx.tenant_id) as conn:
        row = conn.execute(
            "UPDATE shared_conversations SET featured_at = CASE WHEN %s THEN coalesce(featured_at, now()) END "
            "WHERE id = %s AND wall <> 'none' RETURNING id",
            (featured, share_id),
        ).fetchone()
        if row is None:
            raise ShareError("This link isn't on your organization's wall.", 404)
        conn.execute(
            "INSERT INTO admin_events (tenant_id, actor_user_id, action, target, detail) VALUES (%s, %s, %s, %s, "
            "jsonb_build_object('featured', %s::boolean))",
            (ctx.tenant_id, ctx.user_id, "wall.featured", share_id, featured),
        )
    return {"featured": featured}
