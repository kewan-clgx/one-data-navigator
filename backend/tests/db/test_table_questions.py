"""Table questions against the real Postgres (skipped if down): isolation, capture, cached answers, generation."""

import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from odn.config import settings
from odn.platform.catalog import table_questions as tq
from odn.platform.connections.service import ConnectionProfile
from odn.platform.connectors.base import ColumnSchema, TableSchema
from odn.tenancy.context import RequestContext
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")

PATH = ("p", "sales", "orders")
SQL = "SELECT status, COUNT(*) FROM `p.sales.orders` GROUP BY 1"


class FakeConnector:
    dialect = "bigquery"
    default_namespace = ("p", "sales")

    def __init__(self, modified: datetime, columns: tuple[str, ...] = ("id", "status", "amount", "order_date")) -> None:
        self.modified = modified
        self.columns = columns

    def get_schema(self, path: tuple[str, ...]) -> TableSchema:
        return TableSchema(path=path, kind="table", modified_at=self.modified,
                           columns=[ColumnSchema(name=c, type="STRING") for c in self.columns])


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch):
    from odn.db import pool
    from odn.db.migrate import migrate

    migrate(OWNER_URL)
    settings.database_url = APP_URL
    pool.cache_clear()
    tq._info_cache.clear()

    class Sync:  # run generation inline
        def submit(self, fn, *args):
            fn(*args)

    monkeypatch.setattr(tq, "_executor", Sync())
    ta, tb = uuid.uuid4(), uuid.uuid4()
    u1, u2, u3, ub = (uuid.uuid4() for _ in range(4))
    shared, personal, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(OWNER_URL) as conn:
        for t in (ta, tb):
            conn.execute("INSERT INTO tenants (id, slug, name) VALUES (%s, %s, 'x')", (t, f"t-{t.hex[:12]}"))
        for u, t, role in ((u1, ta, "member"), (u2, ta, "member"), (u3, ta, "data_lead"), (ub, tb, "member")):
            conn.execute("INSERT INTO users (id, tenant_id, subject, role) VALUES (%s, %s, %s, %s)", (u, t, u.hex, role))
        for cid, t, owner in ((shared, ta, None), (personal, ta, u1), (other, tb, None)):
            conn.execute(
                "INSERT INTO connections (id, tenant_id, owner_user_id, name, kind, config, secret_ref, allowed_objects) "
                "VALUES (%s, %s, %s, %s, 'bigquery', '{}', %s, '{p.sales.*}')",
                (cid, t, owner, cid.hex[:8], None if owner else "file:x"),
            )
    ctx = {name: RequestContext(tenant_id=t, user_id=u, subject=u.hex, role=r)
           for name, t, u, r in (("u1", ta, u1, "member"), ("u2", ta, u2, "member"), ("lead", ta, u3, "data_lead"),
                                 ("other", tb, ub, "member"))}
    profiles = {name: ConnectionProfile(id=cid, tenant_id=t, owner_user_id=owner, name="bq", kind="bigquery",
                                        config={"project": "p"}, secret_ref=None if owner else "file:x",
                                        allowed_objects=["p.sales.*"])
                for name, cid, t, owner in (("shared", shared, ta, None), ("personal", personal, ta, u1),
                                            ("other", other, tb, None))}
    yield ctx, profiles
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", ([ta, tb],))


def _list(ctx, profile, connector, llm=None):
    return tq.list_questions(ctx, profile, connector, PATH,
                             llm=llm or (lambda m: (_ for _ in ()).throw(tq.LlmUnavailable("off"))))


def _capture(ctx, profile, connector, question="How many orders are there per status?"):
    return tq.capture(ctx, profile, connector, question=question, answer="**12 statuses.**\n- open: 40%",
                      sqls=[SQL], tables=[], bytes_scanned=1234)


def test_asked_question_and_answer_are_shared_in_the_tenant_only(world) -> None:
    ctx, p = world
    conn = FakeConnector(datetime(2026, 1, 1, tzinfo=UTC))
    assert _capture(ctx["u1"], p["shared"], conn) == {"stored": True, "tables": ["p.sales.orders"], "private": False}
    seen = _list(ctx["u2"], p["shared"], conn).questions
    assert [(q.source, q.asked_by_you, q.answer is not None and q.answer.fresh) for q in seen] == [("asked", False, True)]
    _capture(ctx["u2"], p["shared"], conn, "how many orders are there per status")
    again = _list(ctx["u2"], p["shared"], conn).questions
    assert len(again) == 1 and again[0].asked_by_you  # same question, deduplicated
    assert _list(ctx["other"], p["other"], conn).questions == []


def test_private_and_personal_questions_stay_with_their_owner(world) -> None:
    ctx, p = world
    conn = FakeConnector(datetime(2026, 1, 1, tzinfo=UTC))
    assert _capture(ctx["u1"], p["shared"], conn, "Orders for jane.doe@acme.com by status")["private"]
    assert _list(ctx["u2"], p["shared"], conn).questions == []
    assert [q.private for q in _list(ctx["u1"], p["shared"], conn).questions] == [True]
    _capture(ctx["u1"], p["personal"], conn)
    assert len(_list(ctx["u1"], p["personal"], conn).questions) == 1
    with tq.tenant_transaction(ctx["u2"].tenant_id, ctx["u2"].user_id) as c:
        n = c.execute("SELECT count(*) AS n FROM metadata_table_questions WHERE connection_id = %s",
                      (p["personal"].id,)).fetchone()["n"]
    assert n == 0  # another user can't see a personal connection's questions


@pytest.mark.parametrize(("question", "answer", "sqls", "reason"), [
    ("When was orders last updated?", "- Modified 2026-01-01", [SQL], "starter"),
    ("and now break that down by state", "x", [SQL], "follow_up"),
    ("How many orders are there per status?", "Which table should I use?", [SQL], "no_answer"),
    ("How many orders are there per status?", "x", ["SELECT * FROM `p.hr.salaries`"], "not_allowed"),
    ("How many orders are there per status?", "x", [], "no_table"),
])
def test_capture_skips(world, question, answer, sqls, reason) -> None:
    ctx, p = world
    out = tq.capture(ctx["u1"], p["shared"], FakeConnector(datetime(2026, 1, 1, tzinfo=UTC)), question=question,
                     answer=answer, sqls=sqls, tables=[], bytes_scanned=None)
    assert out == {"stored": False, "reason": reason}


def test_cached_answer_goes_stale_and_is_dropped_on_new_access(world) -> None:
    ctx, p = world
    old = FakeConnector(datetime(2026, 1, 1, tzinfo=UTC))
    _capture(ctx["u1"], p["shared"], old)
    qid = _list(ctx["u2"], p["shared"], old).questions[0].id
    saved = tq.cached_answer(ctx["u2"], p["shared"], old, qid)
    content = saved["messages"][1]["content"]
    assert content.startswith("_Saved answer from") and "hasn't changed" in content and f"```sql\n{SQL}\n```" in content
    assert "(#ask=How%20many%20orders%20are%20there%20per%20status%3F)" in content  # "Ask again" pre-fills it
    tq._info_cache.clear()
    newer = FakeConnector(datetime(2026, 12, 1, tzinfo=UTC))  # data modified after the answer
    assert not _list(ctx["u2"], p["shared"], newer).questions[0].answer.fresh
    assert "has changed since" in tq.cached_answer(ctx["u2"], p["shared"], newer, qid)["messages"][1]["content"]
    with psycopg.connect(OWNER_URL) as c:  # new allowlist -> new metadata revision -> answers produced before are gone
        c.execute("UPDATE connections SET allowed_objects = '{p.sales.*,p.ops.*}' WHERE id = %s", (p["shared"].id,))
    after = _list(ctx["u2"], p["shared"], newer).questions
    assert len(after) == 1 and after[0].answer is None


def test_generation_once_per_schema_and_validated(world) -> None:
    ctx, p = world
    calls = []

    def llm(messages):
        calls.append(messages)
        return tq.LlmReply(
            text='{"questions": [' + ",".join(
                f'{{"title": "Q{i}", "question": "How many orders per `status` in group {i}?"}}' for i in range(5)
            ) + ', {"title": "Bad", "question": "Average `churn_score` by region?"}]}',
            model="m", prompt_tokens=100, completion_tokens=50)

    conn = FakeConnector(datetime(2026, 1, 1, tzinfo=UTC))
    first = _list(ctx["u1"], p["shared"], conn, llm)
    assert len(calls) == 1 and [q.source for q in first.questions] == ["generated"] * 5
    assert all("churn" not in q.question for q in first.questions)
    _list(ctx["u2"], p["shared"], conn, llm)
    assert len(calls) == 1  # reused by another user, no new call
    with psycopg.connect(OWNER_URL) as c:
        used = c.execute("SELECT sum(prompt_tokens + completion_tokens) FROM llm_usage WHERE tenant_id = %s",
                         (ctx["u1"].tenant_id,)).fetchone()[0]
    assert used == 150
    tq._info_cache.clear()
    _list(ctx["u1"], p["shared"], FakeConnector(datetime(2026, 1, 1, tzinfo=UTC), ("id", "status")), llm)
    assert len(calls) == 2  # schema changed -> regenerated


def test_starter_answers_are_shared_and_insight_questions_become_suggestions(world) -> None:
    ctx, p = world
    conn = FakeConnector(datetime(2026, 1, 1, tzinfo=UTC))
    answer = ("**Orders by status and amount.**\n| Area | Try asking |\n"
              "| Status | [How many orders are there per status?](#ask) |\n"
              "| Value | [What is the total amount per order_date month?](#ask) |\n"
              "| Bad | [What is the average churn_score by region?](#ask) |")
    out = tq.capture(ctx["u1"], p["shared"], conn, question="What kinds of insights can I get from orders?",
                     answer=answer, sqls=[], tables=["p.sales.orders"], bytes_scanned=None)
    assert out["stored"] and not out["private"]
    listing = _list(ctx["u2"], p["shared"], conn)
    assert [s.question for s in listing.starters] == ["What kinds of insights can I get from orders?"]
    assert sorted(q.question for q in listing.questions) == [
        "How many orders are there per status?", "What is the total amount per order_date month?"]  # churn_score: no such column
    assert all(q.source == "generated" for q in listing.questions)
