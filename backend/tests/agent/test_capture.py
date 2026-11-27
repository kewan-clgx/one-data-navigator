"""Which conversations become saved table questions."""

from google.adk.events import Event
from google.genai import types

from odn.agent.capture import collect, ready

CID = "c1"


def _user(text: str) -> Event:
    return Event(author="user", content=types.Content(role="user", parts=[types.Part(text=text)]))


def _model(text: str) -> Event:
    return Event(author="odn_analyst", content=types.Content(role="model", parts=[types.Part(text=text)]))


def _tool(call_id: str, name: str, args: dict, response: dict) -> list[Event]:
    call = types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=args))
    resp = types.Part(function_response=types.FunctionResponse(id=call_id, name=name, response=response))
    return [Event(author="odn_analyst", content=types.Content(role="model", parts=[call])),
            Event(author="odn_analyst", content=types.Content(role="user", parts=[resp]))]


def _sql(call_id: str, status: str, sql: str = "SELECT 1 FROM `p.d.t`") -> list[Event]:
    body = {"status": status, "bytes_processed": 100} if status == "ok" else {"status": status}
    return _tool(call_id, "run_sql", {"connection_id": CID, "sql": sql}, body)


def test_first_question_with_successful_query_is_captured() -> None:
    turn = collect([_user("How many orders per status?"), *_sql("a", "ok"), _model("**3 statuses.**")])
    assert ready(turn)
    assert (turn.question, turn.answer, turn.sqls, turn.bytes_scanned) == (
        "How many orders per status?", "**3 statuses.**", ["SELECT 1 FROM `p.d.t`"], 100)


def test_metadata_only_answer_uses_the_schema_table() -> None:
    events = [_user("When was t last updated?"),
              *_tool("s", "get_table_schema", {"connection_id": CID, "table": "p.d.t"}, {"columns": []}),
              _model("- Modified 2026-01-01")]
    turn = collect(events)
    assert ready(turn) and turn.tables == ["p.d.t"] and turn.sqls == []


def test_follow_ups_failures_and_pending_approval_are_not_captured() -> None:
    base = [_user("How many orders per status?"), *_sql("a", "ok"), _model("**3.**")]
    assert collect([*base, _user("and by state?"), _model("…")]) is None
    assert not ready(collect([_user("How many orders per status?"), *_sql("a", "failed"), _model("Sorry")]))
    pending = [_user("How many orders per status?"), *_sql("a", "awaiting_user_confirmation"), _model("Approve?")]
    assert not ready(collect(pending))
    assert ready(collect([*pending, *_sql("b", "ok"), _model("**3.**")]))  # approved in a later invocation
    assert not ready(collect([_user("Hi"), _model("Hello!")]))  # no data used
