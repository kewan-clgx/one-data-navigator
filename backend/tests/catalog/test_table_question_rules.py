"""Table questions: which questions are stored, shared, kept and how they rank (no database)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from odn.platform.catalog import table_questions as tq
from odn.platform.connectors.base import ColumnSchema, TableSchema


@pytest.mark.parametrize("text", [
    "What are the fill rates of the core fields in orders?",
    "When was vw_orders last updated?",
    "What are the 10 core fields in orders, and what do they mean?",
    "What kinds of insights can I get from orders?",
    "Show me 10 sample rows from orders.",
    "Which fields in orders are mostly empty?",
])
def test_starters_are_recognized(text: str) -> None:
    assert tq.is_starter(text)


@pytest.mark.parametrize(("text", "follow_up"), [
    ("and now by state", True),
    ("What about 2023?", True),
    ("break that down by city", True),
    ("How many businesses per status in CA?", False),
    ("Which 10 states have the most restaurants by revenue?", False),
])
def test_follow_ups_do_not_stand_alone(text: str, follow_up: bool) -> None:
    assert tq.is_follow_up(text) is follow_up


@pytest.mark.parametrize(("text", "shareable"), [
    ("How many businesses per status in CA 92618?", True),
    ("Top 10 industries by employee count in 2024", True),
    ("Show orders for jane.doe@acme.com", False),
    ("What is the record for clip_id 1234567890?", False),
    ("Call +1 (949) 555-0100 and check status", False),
    ('Find "Acme Holdings North America" revenue', False),
    ("Compare https://example.com listings", False),
])
def test_privacy_filter(text: str, shareable: bool) -> None:
    assert tq.is_shareable(text) is shareable


def test_hash_ignores_case_spacing_and_trailing_punctuation() -> None:
    assert tq.question_hash("How many  businesses per status?") == tq.question_hash("how many businesses per status")


def test_generated_questions_must_name_real_columns() -> None:
    candidates = [
        {"title": "Businesses by status", "question": "How many businesses are there per `business_status_code`?"},
        {"title": "Invented column", "question": "What is the average `customer_churn_score` by state?"},
        {"title": "Fill rates", "question": "What are the fill rates of the core fields in t?"},
        {"title": "Businesses by status", "question": "how many businesses are there per business_status_code"},
        {"title": "x" * 80, "question": "Too long a title for a chip?"},
    ]
    out = tq.validate(candidates, {"business_status_code", "state"})
    assert out == [("Businesses by status", "How many businesses are there per `business_status_code`?")]


def _row(source: str, created: int, distinct: int = 0, mine: bool = False) -> dict:
    return {"id": uuid.uuid4(), "title": source, "question": f"{source} {created}", "source": source, "hidden": False,
            "private_to": None, "distinct_askers": distinct, "last_asked_at": None, "mine": mine,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=created), "answered_at": None,
            "bytes_scanned": None, "data_modified_at": None, "schema_fingerprint": None}


def test_ranking_curated_popular_yours_then_generated_with_variety() -> None:
    rows = [_row("generated", i) for i in range(5)] + [
        _row("asked", 10, distinct=4), _row("asked", 11, distinct=1, mine=True), _row("curated", 12),
        _row("asked", 13, distinct=1), _row("asked", 14, distinct=1),
    ]
    by_id = {r["id"]: r for r in rows}
    ranked = tq.rank([tq._item(r, None) for r in rows], by_id)
    assert [i.source for i in ranked][:3] == ["curated", "asked", "asked"]
    assert ranked[1].popular and ranked[2].asked_by_you
    assert sum(i.source == "generated" for i in ranked) == 2 and len(ranked) == 5


def _info(kind: str, modified: datetime | None) -> tq.TableInfo:
    schema = TableSchema(path=("p", "d", "t"), kind=kind, columns=[ColumnSchema(name="id", type="INT64")],
                         modified_at=modified)
    return tq.TableInfo(kind, modified, tq.schema_fingerprint(schema), schema)


def test_answer_freshness() -> None:
    answered = datetime(2026, 11, 1, tzinfo=UTC)
    table = _info("table", datetime(2026, 10, 1, tzinfo=UTC))
    answer = {"answered_at": answered, "data_modified_at": table.modified_at, "schema_fingerprint": table.fingerprint}
    assert tq.is_fresh(answer, table)
    assert not tq.is_fresh(answer, _info("table", datetime(2026, 11, 5, tzinfo=UTC)))  # data changed since
    view = _info("view", datetime(2026, 1, 1, tzinfo=UTC))
    view_answer = {**answer, "schema_fingerprint": view.fingerprint}
    assert tq.is_fresh(view_answer, view, now=answered + timedelta(days=29))
    assert not tq.is_fresh(view_answer, view, now=answered + timedelta(days=31))  # view TTL
    assert not tq.is_fresh({**answer, "schema_fingerprint": "other"}, table)  # schema changed
