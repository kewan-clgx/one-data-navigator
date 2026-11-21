"""/v1/shares against the real Postgres: who can read a shared conversation, artifacts copied, revoke/expiry."""

import dataclasses
import uuid

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from odn.api.deps import request_context
from odn.api.main import app
from odn.config import settings
from odn.db import pool
from odn.db.migrate import migrate
from odn.db.provision import add_domain, upsert_tenant
from odn.platform.sharing import service
from odn.platform.workspace.storage import tenant_key
from odn.tenancy.context import resolve_context
from odn.tenancy.oidc import VerifiedToken
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")

THREAD = "thread-1"
SESSION = "session-1"
MESSAGES = [
    {"id": "m1", "role": "user", "content": "How many businesses per state?"},
    {"id": "m2", "role": "assistant", "content": "", "toolCalls": [
        {"id": "t1", "type": "function", "function": {"name": "run_sql", "arguments": '{"sql": "SELECT 1"}'}}]},
    {"id": "m3", "role": "tool", "toolCallId": "t1", "content": '{"rows": [{"state": "CA", "n": 5}]}'},
    {"id": "m4", "role": "assistant", "content": "CA has 5."},
    {"id": "m5", "role": "system", "content": "internal"},
]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    migrate(OWNER_URL)
    monkeypatch.setattr(settings, "database_url", APP_URL)
    monkeypatch.setattr(settings, "public_url", "https://app.example.com")
    monkeypatch.setattr(settings, "resend_api_key", "")
    pool.cache_clear()
    created: list[uuid.UUID] = []
    copies: list[tuple[str, str]] = []
    monkeypatch.setattr(service, "copy_object", lambda src, dst: copies.append((src, dst)))

    def fake_agent(method, path, token, body=None):
        if path == "/conversations":
            return httpx.Response(200, json={"conversations": [{"threadId": THREAD, "sessionId": SESSION, "title": "Biz by state"}]})
        assert body == {"threadId": THREAD}
        return httpx.Response(200, json={"threadExists": True, "state": {}, "messages": MESSAGES})

    monkeypatch.setattr(service, "_agent", fake_agent)

    def tenant() -> str:
        slug = f"t-{uuid.uuid4().hex[:12]}"
        created.append(upsert_tenant(OWNER_URL, slug, slug, auth="google", join="domain"))
        add_domain(OWNER_URL, slug, f"{slug}.com", attest=True)
        return slug

    def user(slug: str, name: str, role: str = "member"):
        dom = f"{slug}.com"
        ctx = resolve_context(VerifiedToken("https://accounts.google.com", f"{name}-{dom}", dom, f"{name}@{dom}", True, name))
        return dataclasses.replace(ctx, role=role) if role != "member" else ctx

    def as_(ctx) -> TestClient:
        app.dependency_overrides[request_context] = lambda: ctx
        return TestClient(app, headers={"Authorization": "Bearer test-token"})

    yield tenant, user, as_, copies
    app.dependency_overrides.clear()
    pool.cache_clear()
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", (created,))


def add_table(ctx) -> None:
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute(
            "INSERT INTO workspace_artifacts (tenant_id, user_id, session_id, name, kind, sql_text, object_key, row_count, "
            "arrow_bytes, truncated, columns) VALUES (%s, %s, %s, 'q1', 'warehouse', 'SELECT 1', %s, 1, 10, false, '[]')",
            (ctx.tenant_id, ctx.user_id, SESSION, tenant_key(ctx.tenant_id, ctx.user_id, SESSION, "a.parquet")),
        )


def test_share_with_people_copies_results_and_controls_access(env) -> None:
    tenant, user, as_, copies = env
    slug, other = tenant(), tenant()
    ann, ben, cat = user(slug, "ann"), user(slug, "ben"), user(slug, "cat")
    outsider = user(other, "ann")
    add_table(ann)

    preview = as_(ann).get("/v1/shares/preview", params={"thread_id": THREAD}).json()
    assert preview["title"] == "Biz by state" and preview["tables"] == 1 and preview["used_personal_connections"] is False

    people = as_(ann).get("/v1/shares/people", params={"q": "ben"}).json()
    assert [p["email"] for p in people] == [f"ben@{slug}.com"]  # not herself, not other organizations

    resp = as_(ann).post("/v1/shares", json={"thread_id": THREAD, "recipient_ids": [str(ben.user_id)]})
    assert resp.status_code == 201, resp.text
    share = resp.json()
    assert share["url"] == f"https://app.example.com/s/{share['id']}" and share["emailed"] == 0
    src, dst = copies[0]
    assert src.endswith("/a.parquet") and f"/{ann.tenant_id}/shares/{share['id']}/q1.parquet" in dst

    seen = as_(ben).get(f"/v1/shares/{share['id']}").json()
    assert seen["title"] == "Biz by state" and seen["owner_email"] == f"ann@{slug}.com"
    assert [m["role"] for m in seen["messages"]] == ["user", "assistant", "tool", "assistant"]  # system dropped
    assert seen["artifacts"][0]["name"] == "q1" and "object_key" not in seen["artifacts"][0]
    assert [s["id"] for s in as_(ben).get("/v1/shares", params={"scope": "with_me"}).json()] == [share["id"]]

    assert as_(cat).get(f"/v1/shares/{share['id']}").status_code == 404  # same org, not chosen
    assert as_(outsider).get(f"/v1/shares/{share['id']}").status_code == 404  # other organization
    assert as_(cat).delete(f"/v1/shares/{share['id']}").status_code == 404

    mine = as_(ann).get("/v1/shares", params={"scope": "mine", "thread_id": THREAD}).json()
    assert mine[0]["views"] == 1 and mine[0]["recipients"][0]["email"] == f"ben@{slug}.com"

    assert as_(ann).delete(f"/v1/shares/{share['id']}").status_code == 204
    assert as_(ben).get(f"/v1/shares/{share['id']}").status_code == 404
    assert as_(ann).get(f"/v1/shares/{share['id']}").json()["status"] == "revoked"

    admin = as_(user(slug, "boss", role="admin"))
    actions = {e["action"] for e in admin.get("/v1/admin/events").json()}
    assert {"share.created", "share.viewed", "share.revoked"} <= actions


def test_answers_only_organization_audience_expiry_and_switch(env) -> None:
    tenant, user, as_, copies = env
    slug = tenant()
    ann, ben = user(slug, "ann"), user(slug, "ben")
    add_table(ann)

    share = as_(ann).post("/v1/shares", json={
        "thread_id": THREAD, "audience": "organization", "include_results": False, "expires_days": 7,
    }).json()
    seen = as_(ben).get(f"/v1/shares/{share['id']}").json()
    assert seen["artifacts"] == [] and copies == []
    assert next(m for m in seen["messages"] if m["role"] == "tool")["content"] == service.RESULTS_OMITTED

    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE shared_conversations SET expires_at = now() - interval '1 minute' WHERE id = %s", (share["id"],))
    assert as_(ben).get(f"/v1/shares/{share['id']}").status_code == 404

    assert as_(ann).post("/v1/shares", json={"thread_id": THREAD}).status_code == 422  # people audience needs people
    assert as_(ann).post("/v1/shares", json={"thread_id": THREAD, "audience": "organization", "expires_days": 365}).status_code == 422
    assert as_(ann).post("/v1/shares", json={"thread_id": "someone-elses", "audience": "organization"}).status_code == 404

    admin = as_(user(slug, "boss", role="admin"))
    assert admin.put("/v1/admin/sharing", json={"enabled": False}).json() == {"enabled": False, "public_links": True}
    blocked = as_(ann).post("/v1/shares", json={"thread_id": THREAD, "audience": "organization"})
    assert blocked.status_code == 403


TWO_TURNS = [
    {"id": "u1", "role": "user", "content": "How many businesses?"},
    {"id": "a0", "role": "assistant", "content": "Which data source should I query?"},
    {"id": "u1b", "role": "user", "content": "3"},
    {"id": "a1", "role": "assistant", "content": "There are 10."},
    {"id": "u2", "role": "user", "content": "Top cities by count, as a chart"},
    {"id": "a2", "role": "assistant", "content": "", "toolCalls": [
        {"id": "t1", "type": "function", "function": {"name": "run_sql", "arguments": "{}"}},
        {"id": "t2", "type": "function", "function": {"name": "generate_chart", "arguments":
            '{"source": "q1", "title": "Top cities (renamed when saved)", "chart_type": "bar", "x": "city"}'}}]},
    {"id": "r1", "role": "tool", "toolCallId": "t1", "content": '{"status": "ok", "workspace_table": "q1"}'},
    {"id": "r2", "role": "tool", "toolCallId": "t2", "content": '{"status": "ok", "chart": "c1", "points_plotted": 3}'},
    {"id": "a3", "role": "assistant", "content": "Irvine leads with 5."},
    {"id": "a4", "role": "assistant", "content": "You've used today's free trial ... in Settings → AI model ..."},
]


def test_just_the_answer_shares_one_question_and_its_chosen_result(env, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant, user, as_, copies = env
    slug = tenant()
    ann, ben = user(slug, "ann"), user(slug, "ben")
    add_table(ann)
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute(
            "INSERT INTO workspace_charts (tenant_id, user_id, session_id, name, title, chart_type, result) "
            "VALUES (%s, %s, %s, 'c1', 'Top cities', 'bar', %s)",
            (ann.tenant_id, ann.user_id, SESSION,
             '{"spec": {"source": "q1", "type": "bar", "x": "city", "title": "Top cities"}, "points": []}'),
        )

    def agent(method, path, token, body=None):
        if path == "/conversations":
            return httpx.Response(200, json={"conversations": [{"threadId": THREAD, "sessionId": SESSION, "title": "t"}]})
        return httpx.Response(200, json={"threadExists": True, "state": {}, "messages": TWO_TURNS})

    monkeypatch.setattr(service, "_agent", agent)

    turns = as_(ann).get("/v1/shares/preview", params={"thread_id": THREAD}).json()["turns"]
    assert [t["question"] for t in turns] == ["How many businesses?", "Top cities by count, as a chart"]
    assert turns[0]["answer"] == "There are 10."
    last = turns[-1]
    assert last["answer"] == "Irvine leads with 5." and [c["name"] for c in last["charts"]] == ["c1"]
    assert [t["name"] for t in last["tables"]] == ["q1"] and last["default_artifacts"] == ["c1"]

    share = as_(ann).post("/v1/shares", json={
        "thread_id": THREAD, "audience": "organization", "mode": "answer", "turn_id": "u2",
        "artifact_names": ["c1", "q99"],  # q99 is not from this question: ignored
    }).json()
    seen = as_(ben).get(f"/v1/shares/{share['id']}").json()
    assert seen["kind"] == "answer"
    assert seen["messages"] == [
        {"id": "u2", "role": "user", "content": "Top cities by count, as a chart"},
        {"id": "u2-answer", "role": "assistant", "content": "Irvine leads with 5."},
    ]
    assert [a["name"] for a in seen["artifacts"]] == ["c1"] and copies == []  # chart only, no table copied

    assert as_(ann).post("/v1/shares", json={
        "thread_id": THREAD, "audience": "organization", "mode": "answer", "turn_id": "nope"}).status_code == 422


def test_anyone_with_the_link_is_on_by_default_and_admins_can_turn_it_off(env) -> None:
    tenant, user, as_, _copies = env
    slug = tenant()
    ann, boss = user(slug, "ann"), user(slug, "boss", role="admin")

    def admin() -> TestClient:
        return as_(boss)  # the client override is global: re-select the admin before each call

    add_table(ann)
    public = {"thread_id": THREAD, "audience": "public"}
    assert as_(ann).get("/v1/shares/preview", params={"thread_id": THREAD}).json()["public_links"] is True
    share = as_(ann).post("/v1/shares", json=public).json()
    org_only = as_(ann).post("/v1/shares", json={"thread_id": THREAD, "audience": "organization"}).json()

    anonymous = TestClient(app)
    seen = anonymous.get(f"/v1/public/shares/{share['id']}")
    assert seen.status_code == 200 and seen.headers["cache-control"] == "private, no-store"
    body = seen.json()
    assert body["title"] == "Biz by state" and body["owner_name"] == "ann" and "owner_email" not in body
    assert body["artifacts"][0]["name"] == "" and "object_key" not in body["artifacts"][0]  # no result names
    assert "tenant_id" not in body
    assert anonymous.get(f"/v1/public/shares/{org_only['id']}").status_code == 404  # sign-in shares stay private

    admin().put("/v1/admin/sharing", json={"enabled": True, "public_links": False})
    assert anonymous.get(f"/v1/public/shares/{share['id']}").status_code == 404  # switch off hides them at once
    assert as_(ann).post("/v1/shares", json=public).status_code == 403
    assert {s["id"] for s in admin().get("/v1/shares", params={"scope": "all"}).json()} == {share["id"], org_only["id"]}


def test_ask_a_follow_up_copies_the_share_into_the_readers_workspace(env, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant, user, as_, copies = env
    slug, other = tenant(), tenant()
    ann, ben, outsider = user(slug, "ann"), user(slug, "ben"), user(other, "ben")
    add_table(ann)
    share = as_(ann).post("/v1/shares", json={"thread_id": THREAD, "audience": "organization"}).json()

    imported: list[dict] = []
    original = service._agent

    def agent(method, path, token, body=None):
        if path == "/conversations/import":
            imported.append(body)
            return httpx.Response(201, json={"threadId": body["threadId"]})
        return original(method, path, token, body)

    monkeypatch.setattr(service, "_agent", agent)
    thread = as_(ben).post(f"/v1/shares/{share['id']}/continue").json()["thread_id"]
    assert imported[0]["threadId"] == thread and imported[0]["owner"] == "ann"
    assert [m["role"] for m in imported[0]["messages"]] == ["user", "assistant", "tool", "assistant"]
    src, dst = copies[-1]
    assert f"/shares/{share['id']}/q1.parquet" in src and dst.startswith(tenant_key(ben.tenant_id, ben.user_id, thread) + "/")
    with psycopg.connect(OWNER_URL) as conn:
        rows = conn.execute("SELECT name, user_id FROM workspace_artifacts WHERE session_id = %s", (thread,)).fetchall()
        view = conn.execute("SELECT continued_thread_id FROM shared_conversation_views WHERE share_id = %s",
                            (share["id"],)).fetchone()
    assert rows == [("q1", ben.user_id)] and view == (thread,)
    assert as_(outsider).post(f"/v1/shares/{share['id']}/continue").status_code == 404


def test_charts_from_older_results_are_matched_by_source_title_and_type() -> None:
    messages = [
        {"id": "u", "role": "user", "content": "Chart it"},
        {"id": "a", "role": "assistant", "toolCalls": [{"id": "t", "function": {"name": "generate_chart", "arguments":
            '{"source": "q2", "title": "By city", "chart_type": "bar"}'}}]},
        {"id": "r", "role": "tool", "toolCallId": "t", "content": '{"status": "ok", "points_plotted": 2}'},
        {"id": "b", "role": "assistant", "content": "Done."},
    ]
    charts = [{"name": "c1", "result": {"spec": {"source": "q1", "title": "Other", "type": "bar"}}},
              {"name": "c2", "result": {"spec": {"source": "q2", "title": "By city", "type": "bar"}}}]
    assert service.turns(messages, charts)[0]["charts"] == ["c2"]
