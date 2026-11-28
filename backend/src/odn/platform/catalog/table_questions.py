"""Table questions: what people can ask about a table, with cached answers (docs/table_questions_plan.md).

Four kinds of question share one list per table: starters (client-side templates, never stored), generated (written
once per schema by the platform model from the table's metadata), asked (questions people asked first in a
conversation and got answered) and curated (pinned or added by a data lead). An asked question also keeps its answer,
so the next person can open it without spending tokens or a warehouse scan.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from odn.config import settings
from odn.db import tenant_transaction
from odn.platform.connections.service import ConnectionProfile, build_connector, build_policy
from odn.platform.connectors.base import BaseConnector, TableSchema
from odn.tenancy.context import RequestContext

log = logging.getLogger(__name__)

SHOWN = 5
GENERATED_TTL = timedelta(days=30)
RETRY_FAILED_AFTER = timedelta(hours=6)
STUCK_AFTER = timedelta(minutes=10)
ANSWER_MAX = 8192
MAX_TABLES_PER_QUESTION = 3
INFO_TTL_S = 600

Source = Literal["generated", "asked", "curated"]


class QuestionNotFound(Exception):
    pass


class AnswerInfo(BaseModel):
    answered_at: datetime
    fresh: bool
    bytes_scanned: int | None = None


class QuestionItem(BaseModel):
    id: UUID
    title: str
    question: str
    source: Source
    popular: bool = False
    asked_by_you: bool = False
    hidden: bool = False
    private: bool = False
    answer: AnswerInfo | None = None


class QuestionList(BaseModel):
    status: Literal["ready", "generating", "unavailable"]
    generated_at: datetime | None = None
    questions: list[QuestionItem]


# -- text rules -----------------------------------------------------------------------------------------------------

_STARTERS = [
    re.compile(p) for p in (
        r"^what are the fill rates of the core fields in ", r"^when was .+ last updated",
        r"^what are the 10 core fields in ", r"^what kinds of insights can i get from ",
        r"^show me 10 sample rows from ", r"^which fields in .+ are mostly empty",
    )
]
_FOLLOW_UP = re.compile(
    r"^(and|also|now|then|ok|okay|thanks|thank you|yes|no|what about|how about|same|again|instead|more)\b", re.IGNORECASE
)
_PRONOUN = re.compile(r"\b(it|that|those|them|these|above|previous|same)\b", re.IGNORECASE)
# Things that make a question personal or identifying: shown to colleagues only when none matches.
_PRIVATE = [
    re.compile(p, re.IGNORECASE) for p in (
        r"[\w.+-]+@[\w-]+\.[\w.]+",            # email
        r"\+?\d[\d\s().-]{8,}\d",               # phone number
        r"\d{6,}",                              # long ids (clip_id, account numbers)
        r"https?://|www\.",                     # links
        r"[\"“‘'][^\"”’']*\s[^\"”’']*\s[^\"”’']*[\"”’']",  # a quoted phrase of 3+ words (names, values)
    )
]
_COLUMN_LIKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def question_hash(text: str) -> str:
    key = clean(text.replace("`", "")).lower().rstrip(" ?.!")
    return hashlib.sha256(key.encode()).hexdigest()


def is_starter(text: str) -> bool:
    lowered = clean(text).lower()
    return any(p.search(lowered) for p in _STARTERS)


def is_follow_up(text: str) -> bool:
    t = clean(text)
    return bool(_FOLLOW_UP.search(t)) or (len(t) < 45 and bool(_PRONOUN.search(t)))


def is_shareable(text: str) -> bool:
    return not any(p.search(text) for p in _PRIVATE)


def short_title(text: str, limit: int = 60) -> str:
    t = clean(text).rstrip(" ?.!")
    if len(t) <= limit:
        return t
    return t[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def schema_fingerprint(schema: TableSchema) -> str:
    cols = sorted((c.name, c.type.upper()) for c in schema.columns)
    return hashlib.sha256(json.dumps(cols).encode()).hexdigest()


# -- table metadata (cheap: one metadata call, cached) ------------------------------------------------------------

@dataclass(frozen=True)
class TableInfo:
    kind: str
    modified_at: datetime | None
    fingerprint: str
    schema: TableSchema


_info_cache: dict[tuple[UUID, tuple[str, ...]], tuple[float, TableInfo]] = {}
_info_lock = threading.Lock()


def table_info(profile: ConnectionProfile, connector: BaseConnector, path: tuple[str, ...]) -> TableInfo:
    key = (profile.id, path)
    with _info_lock:
        hit = _info_cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
    schema = connector.get_schema(path)
    info = TableInfo(schema.kind, schema.modified_at, schema_fingerprint(schema), schema)
    with _info_lock:
        if len(_info_cache) > 2000:
            _info_cache.clear()
        _info_cache[key] = (time.monotonic() + INFO_TTL_S, info)
    return info


def is_fresh(answer: dict[str, Any], info: TableInfo, now: datetime | None = None) -> bool:
    """A cached answer is fresh while the table's data and schema are unchanged (views and sources without a modified
    time: for ODN_ANSWER_CACHE_VIEW_TTL_DAYS)."""
    if answer.get("schema_fingerprint") and answer["schema_fingerprint"] != info.fingerprint:
        return False
    if info.kind == "table" and info.modified_at is not None and answer.get("data_modified_at") is not None:
        return info.modified_at <= answer["data_modified_at"]
    now = now or datetime.now(UTC)
    return answer["answered_at"] >= now - timedelta(days=settings.answer_cache_view_ttl_days)


# -- listing ------------------------------------------------------------------------------------------------------

def _rows(conn: Any, ctx: RequestContext, connection_id: UUID, path: tuple[str, ...]) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT q.id, q.title, q.question, q.source, q.hidden, q.private_to, q.distinct_askers, q.last_asked_at,
               q.created_at,
               EXISTS (SELECT 1 FROM metadata_question_askers k WHERE k.question_id = q.id AND k.user_id = %s) AS mine,
               a.answered_at, a.bytes_scanned, a.data_modified_at, a.schema_fingerprint
        FROM metadata_table_questions q
        LEFT JOIN metadata_question_answers a ON a.question_id = q.id
        WHERE q.tenant_id = %s AND q.connection_id = %s AND q.table_path = %s
        ORDER BY q.created_at, q.id
        """,
        (ctx.user_id, ctx.tenant_id, connection_id, list(path)),
    ).fetchall()


def _item(row: dict[str, Any], info: TableInfo | None) -> QuestionItem:
    answer = None
    if row["answered_at"] is not None:
        answer = AnswerInfo(
            answered_at=row["answered_at"], bytes_scanned=row["bytes_scanned"],
            fresh=info is not None and is_fresh(row, info),
        )
    return QuestionItem(
        id=row["id"], title=row["title"], question=row["question"], source=row["source"],
        popular=row["distinct_askers"] >= 3, asked_by_you=row["mine"], hidden=row["hidden"],
        private=row["private_to"] is not None, answer=answer,
    )


def rank(items: list[QuestionItem], rows: dict[UUID, dict[str, Any]], limit: int = SHOWN) -> list[QuestionItem]:
    """Curated first, then what colleagues ask most (recent first), your own questions, then generated ones; keeps two
    generated questions while asked ones are few so the list stays varied."""
    visible = [i for i in items if not i.hidden]

    def key(i: QuestionItem) -> tuple[Any, ...]:
        r = rows[i.id]
        last = r["last_asked_at"].timestamp() if r["last_asked_at"] else 0.0
        return (i.source != "curated", -(r["distinct_askers"] >= 2), -i.asked_by_you, -r["distinct_askers"], -last,
                r["created_at"].timestamp())

    ordered = sorted(visible, key=key)
    top = ordered[:limit]
    generated = [i for i in ordered if i.source == "generated"]
    want = min(2, len(generated))
    have = sum(1 for i in top if i.source == "generated")
    for g in generated:
        if have >= want:
            break
        if g not in top:
            # Replace the weakest non-generated, non-curated question.
            for idx in range(len(top) - 1, -1, -1):
                if top[idx].source == "asked" and rows[top[idx].id]["distinct_askers"] < 2:
                    top[idx] = g
                    have += 1
                    break
            else:
                break
    return top


def list_questions(
    ctx: RequestContext, profile: ConnectionProfile, connector: BaseConnector, path: tuple[str, ...], *,
    llm: Callable[[list[dict[str, str]]], LlmReply],
) -> QuestionList:
    info = table_info(profile, connector, path)
    status = ensure_generated(ctx, profile, path, info, llm=llm)
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        rows = _rows(conn, ctx, profile.id, path)
        generated_at = conn.execute(
            "SELECT generated_at FROM metadata_question_sets WHERE tenant_id = %s AND connection_id = %s "
            "AND table_path = %s", (ctx.tenant_id, profile.id, list(path)),
        ).fetchone()
    by_id = {r["id"]: r for r in rows}
    items = [_item(r, info) for r in rows]
    return QuestionList(
        status=status, generated_at=generated_at["generated_at"] if generated_at else None,
        questions=rank(items, by_id),
    )


# -- generation ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class LlmReply:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int


def platform_llm(token: str) -> Callable[[list[dict[str, str]]], LlmReply]:
    """The platform model, through the agent runtime (which holds the platform LLM key). The tenant's budget pays,
    never a user's own key; the caller records usage."""
    import httpx

    def call(messages: list[dict[str, str]]) -> LlmReply:
        try:
            resp = httpx.post(
                f"{settings.agent_url}/internal/complete", json={"messages": messages}, timeout=90,
                headers={"Authorization": f"Bearer {token}", "X-ODN-Agent-Token": settings.agent_token},
            )
        except httpx.HTTPError as exc:
            raise LlmUnavailable(type(exc).__name__) from None
        if resp.status_code != 200:
            raise LlmUnavailable(f"HTTP {resp.status_code}")
        body = resp.json()
        return LlmReply(text=body.get("text") or "", model=body.get("model") or "platform",
                        prompt_tokens=int(body.get("prompt_tokens") or 0),
                        completion_tokens=int(body.get("completion_tokens") or 0))

    return call


class LlmUnavailable(Exception):
    pass


_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="table-questions")


def ensure_generated(
    ctx: RequestContext, profile: ConnectionProfile, path: tuple[str, ...], info: TableInfo, *,
    llm: Callable[[list[dict[str, str]]], LlmReply],
) -> Literal["ready", "generating", "unavailable"]:
    """Claims generation when there is no current set (first visit, schema change, expired, retry after failure)."""
    now = datetime.now(UTC)
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        row = conn.execute(
            "SELECT status, schema_fingerprint, generated_at, updated_at FROM metadata_question_sets "
            "WHERE tenant_id = %s AND connection_id = %s AND table_path = %s FOR UPDATE",
            (ctx.tenant_id, profile.id, list(path)),
        ).fetchone()
        due = row is None or row["schema_fingerprint"] != info.fingerprint or (
            row["status"] == "ready" and (row["generated_at"] is None or row["generated_at"] < now - GENERATED_TTL)
        ) or (row["status"] in ("failed", "skipped") and row["updated_at"] < now - RETRY_FAILED_AFTER) or (
            row["status"] == "generating" and row["updated_at"] < now - STUCK_AFTER
        )
        if not due:
            if row["status"] == "generating":
                return "generating"
            return "ready" if row["status"] == "ready" else "unavailable"
        conn.execute(
            "INSERT INTO metadata_question_sets (tenant_id, connection_id, table_path, schema_fingerprint, status, "
            "requested_by, updated_at) VALUES (%s, %s, %s, %s, 'generating', %s, now()) "
            "ON CONFLICT (tenant_id, connection_id, table_path) DO UPDATE SET status = 'generating', "
            "schema_fingerprint = EXCLUDED.schema_fingerprint, requested_by = EXCLUDED.requested_by, error_code = NULL, "
            "updated_at = now()",
            (ctx.tenant_id, profile.id, list(path), info.fingerprint, ctx.user_id),
        )
    _executor.submit(_generate_safely, ctx, profile, path, info, llm)
    return "generating"


def _generate_safely(ctx: RequestContext, profile: ConnectionProfile, path: tuple[str, ...], info: TableInfo,
                     llm: Callable[[list[dict[str, str]]], LlmReply]) -> None:
    try:
        status, error = generate(ctx, profile, path, info, llm)
    except Exception as exc:  # noqa: BLE001 - recorded on the set; never crash the pool
        log.warning("table question generation failed: %s", type(exc).__name__)
        status, error = "failed", type(exc).__name__[:60]
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        conn.execute(
            "UPDATE metadata_question_sets SET status = %s, error_code = %s, updated_at = now(), "
            "generated_at = CASE WHEN %s = 'ready' THEN now() ELSE generated_at END "
            "WHERE tenant_id = %s AND connection_id = %s AND table_path = %s",
            (status, error, status, ctx.tenant_id, profile.id, list(path)),
        )


def brief(ctx: RequestContext, profile: ConnectionProfile, path: tuple[str, ...], info: TableInfo) -> dict[str, Any]:
    """Compact table description for the generator: names, types, descriptions, definitions, fill rates, ranges."""
    defs: dict[str, str] = {}
    table_def: str | None = None
    try:
        from odn.platform.catalog.dictionary import table_dictionary

        connector = build_connector(profile)
        d = table_dictionary(ctx, profile.id, connector, build_policy(profile, connector), path)
        defs = {f.name: f.curated.definition for f in d.fields if f.curated}
        table_def = d.curated.definition if d.curated else None
    except Exception as exc:  # noqa: BLE001 - definitions are optional
        log.info("table questions: no definitions (%s)", type(exc).__name__)
    fill: dict[str, dict[str, Any]] = {}
    if profile.kind == "bigquery" and settings.column_profiling_preview:
        try:
            from odn.platform.catalog.column_profiles import get_profile

            p = get_profile(ctx, profile, path)
            if p.sample_rows:
                fill = {f.path[0]: {"fill": round(100 * f.filled_rows / p.sample_rows, 1), "min": f.min_value,
                                    "max": f.max_value} for f in p.fields if len(f.path) == 1}
        except Exception as exc:  # noqa: BLE001 - the profile is optional
            log.info("table questions: no profile (%s)", type(exc).__name__)
    s = info.schema
    columns = []
    for c in s.columns[:80]:
        col: dict[str, Any] = {"name": c.name, "type": c.type}
        if c.description:
            col["description"] = c.description[:160]
        if c.name in defs:
            col["definition"] = defs[c.name][:200]
        col.update({k: v for k, v in (fill.get(c.name) or {}).items() if v is not None})
        columns.append(col)
    return {
        "table": path[-1], "kind": s.kind, "description": (s.description or "")[:300], "definition": table_def,
        "partitioned_by": s.partitioning, "clustered_by": s.clustering, "columns": columns,
        "more_columns": max(0, len(s.columns) - 80), "dialect": profile.kind,
    }


_PROMPT = """You write example questions business users can ask an AI data analyst about ONE table.
Return JSON only: {"questions": [{"title": "...", "question": "..."}]} with exactly 6 items.
Rules:
- title: 2-6 words, sentence case, no trailing punctuation (e.g. "Businesses by status").
- question: one plain-English question, 20-160 characters, answerable with a single SQL query on this table.
- Mix kinds: a total/count, a breakdown by a category column, a top-N, a trend over a date column (only if one
  exists), a comparison, and one more useful one. Prefer columns with high fill rates and clear meaning.
- Only refer to columns that exist; you may name them in `backticks`. Never invent values, ids, people or places
  that aren't implied by the column names.
- Do NOT ask about fill rates, empty fields, freshness/last update, "core fields", "what insights", or sample
  rows: those are offered separately.
Table:
"""


def _parse(text: str) -> list[dict[str, str]]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return []
    items = data.get("questions") if isinstance(data, dict) else None
    return [i for i in items or [] if isinstance(i, dict)]


def validate(candidates: list[dict[str, str]], columns: set[str], limit: int = SHOWN) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    lower_cols = {c.lower() for c in columns}
    for c in candidates:
        title, question = clean(str(c.get("title", ""))), clean(str(c.get("question", "")))
        if not (1 <= len(title) <= 60 and 12 <= len(question) <= 300) or is_starter(question):
            continue
        if any(tok not in lower_cols for tok in _COLUMN_LIKE.findall(question.lower())):
            continue  # names a column the table doesn't have
        h = question_hash(question)
        if h in seen:
            continue
        seen.add(h)
        out.append((title.rstrip(" .?!"), question))
        if len(out) == limit:
            break
    return out


def generate(ctx: RequestContext, profile: ConnectionProfile, path: tuple[str, ...], info: TableInfo,
             llm: Callable[[list[dict[str, str]]], LlmReply]) -> tuple[str, str | None]:
    from odn.platform.governance.llm_budget import budget_status, record_llm_usage

    if budget_status(ctx.tenant_id, ctx.user_id).exceeded:
        return "skipped", "budget_exceeded"
    payload = brief(ctx, profile, path, info)
    messages = [{"role": "user", "content": _PROMPT + json.dumps(payload, default=str)}]
    columns = {c.name for c in info.schema.columns}
    chosen: list[tuple[str, str]] = []
    for _ in range(2):
        try:
            reply = llm(messages)
        except LlmUnavailable:
            return "skipped", "llm_unavailable"
        record_llm_usage(ctx, reply.model, reply.prompt_tokens, reply.completion_tokens, "platform")
        chosen = validate(_parse(reply.text), columns)
        if len(chosen) >= 3:
            break
    if len(chosen) < 3:
        return "failed", "invalid_output"
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        # Replace the previous generated set, keeping any generated question people asked or that has an answer.
        conn.execute(
            "DELETE FROM metadata_table_questions q WHERE q.tenant_id = %s AND q.connection_id = %s "
            "AND q.table_path = %s AND q.source = 'generated' AND q.ask_count = 0 "
            "AND NOT EXISTS (SELECT 1 FROM metadata_question_answers a WHERE a.question_id = q.id)",
            (ctx.tenant_id, profile.id, list(path)),
        )
        for title, question in chosen:
            conn.execute(
                "INSERT INTO metadata_table_questions (tenant_id, connection_id, table_path, source, title, question, "
                "normalized_hash, schema_fingerprint) VALUES (%s, %s, %s, 'generated', %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, connection_id, table_path, normalized_hash) DO NOTHING",
                (ctx.tenant_id, profile.id, list(path), title, question, question_hash(question), info.fingerprint),
            )
    return "ready", None


# -- capturing asked questions and their answers ------------------------------------------------------------------

_NOTICE_MARKERS = ("in Settings → AI model", "I can't check your AI usage budget")


def capture(
    ctx: RequestContext, profile: ConnectionProfile, connector: BaseConnector, *, question: str, answer: str,
    sqls: list[str], tables: list[str], bytes_scanned: int | None,
) -> dict[str, Any]:
    """Stores the first question of a conversation (and its answer) under each table it was answered from."""
    q = clean(question)
    if not 12 <= len(q) <= 300:
        return {"stored": False, "reason": "length"}
    if is_starter(q):
        return {"stored": False, "reason": "starter"}
    if is_follow_up(q):
        return {"stored": False, "reason": "follow_up"}
    text = answer.strip()
    if not text or any(m in text for m in _NOTICE_MARKERS) or text.endswith("?"):
        return {"stored": False, "reason": "no_answer"}
    policy = build_policy(profile, connector)
    paths: list[tuple[str, ...]] = []
    for sql in sqls:
        decision = policy.validate(sql)
        if not decision.allowed:
            return {"stored": False, "reason": "not_allowed"}
        paths += [tuple(t.split(".")) for t in decision.tables]
    paths += [tuple(t.split(".")) for t in tables if policy.allows_object(tuple(t.split(".")))]
    unique = list(dict.fromkeys(paths))[:MAX_TABLES_PER_QUESTION]
    if not unique:
        return {"stored": False, "reason": "no_table"}
    private_to = None if is_shareable(q) else ctx.user_id
    stored = []
    for path in unique:
        try:
            info = table_info(profile, connector, path)
        except Exception as exc:  # noqa: BLE001 - a table we can't describe isn't worth an FAQ entry
            log.info("table questions: skipped %s (%s)", ".".join(path), type(exc).__name__)
            continue
        with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
            revision = conn.execute("SELECT metadata_revision FROM connections WHERE id = %s", (profile.id,)).fetchone()
            if revision is None:
                continue
            row = conn.execute(
                "INSERT INTO metadata_table_questions (tenant_id, connection_id, table_path, source, private_to, title, "
                "question, normalized_hash, schema_fingerprint, ask_count, last_asked_at) "
                "VALUES (%s, %s, %s, 'asked', %s, %s, %s, %s, %s, 1, now()) "
                "ON CONFLICT (tenant_id, connection_id, table_path, normalized_hash) DO UPDATE "
                "SET ask_count = metadata_table_questions.ask_count + 1, last_asked_at = now() RETURNING id",
                (ctx.tenant_id, profile.id, list(path), private_to, short_title(q), q, question_hash(q),
                 info.fingerprint),
            ).fetchone()
            new_asker = conn.execute(
                "INSERT INTO metadata_question_askers (tenant_id, question_id, user_id) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING RETURNING user_id", (ctx.tenant_id, row["id"], ctx.user_id),
            ).fetchone()
            if new_asker:
                conn.execute("UPDATE metadata_table_questions SET distinct_askers = distinct_askers + 1 WHERE id = %s",
                             (row["id"],))
            conn.execute(
                "INSERT INTO metadata_question_answers (question_id, tenant_id, connection_id, connection_revision, "
                "answer_md, sql, bytes_scanned, answered_by, data_modified_at, schema_fingerprint) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (question_id) DO UPDATE SET answer_md = EXCLUDED.answer_md, sql = EXCLUDED.sql, "
                "bytes_scanned = EXCLUDED.bytes_scanned, answered_at = now(), answered_by = EXCLUDED.answered_by, "
                "connection_revision = EXCLUDED.connection_revision, data_modified_at = EXCLUDED.data_modified_at, "
                "schema_fingerprint = EXCLUDED.schema_fingerprint",
                (row["id"], ctx.tenant_id, profile.id, revision["metadata_revision"], text[:ANSWER_MAX],
                 Jsonb(sqls[:3]), bytes_scanned, ctx.user_id, info.modified_at, info.fingerprint),
            )
            stored.append(".".join(path))
    return {"stored": bool(stored), "tables": stored, "private": private_to is not None}


# -- opening a cached answer --------------------------------------------------------------------------------------

def cached_answer(ctx: RequestContext, profile: ConnectionProfile, connector: BaseConnector,
                  question_id: UUID) -> dict[str, Any]:
    """The cached question and answer as chat messages, with a header saying when it was answered."""
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        row = conn.execute(
            "SELECT q.question, q.table_path, a.answer_md, a.sql, a.answered_at, a.data_modified_at, a.schema_fingerprint "
            "FROM metadata_table_questions q JOIN metadata_question_answers a ON a.question_id = q.id "
            "WHERE q.id = %s AND q.connection_id = %s AND q.tenant_id = %s",
            (question_id, profile.id, ctx.tenant_id),
        ).fetchone()
    if row is None or not build_policy(profile, connector).allows_object(tuple(row["table_path"])):
        raise QuestionNotFound(str(question_id))
    info = table_info(profile, connector, tuple(row["table_path"]))
    when = row["answered_at"].strftime("%b %-d, %Y")
    note = (f"_Saved answer from {when}. The data hasn't changed since; ask again for a fresh answer._"
            if is_fresh(row, info) else
            f"_Saved answer from {when}. The data has changed since, so ask again for a fresh answer._")
    # The SQL behind the answer, folded in the chat with "Open in SQL workbench", so it can be checked or rerun.
    sql = "".join(f"\n\n```sql\n{q.strip()}\n```" for q in (row["sql"] or []) if isinstance(q, str) and q.strip())
    return {
        "title": row["question"], "answered_at": row["answered_at"],
        "messages": [
            {"id": f"faq-{question_id}", "role": "user", "content": row["question"]},
            {"id": f"faq-{question_id}-answer", "role": "assistant", "content": f"{note}\n\n{row['answer_md']}{sql}"},
        ],
    }
