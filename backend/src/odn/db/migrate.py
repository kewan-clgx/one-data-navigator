"""Apply SQL migrations in order. Run with the owner/migrations role:

    python -m odn.db.migrate [--seed-local]
"""

from __future__ import annotations

import argparse
import os
import re
import uuid
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from odn.config import settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
LOCAL_TENANT_SLUG = "local"


def migrate(url: str) -> list[str]:
    applied_now: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        # Serializes concurrent starters (several api replicas).
        conn.execute("SELECT pg_advisory_lock(hashtext('odn_migrations'))")
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.stem in applied:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text())  # type: ignore[arg-type]
                    conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
                applied_now.append(path.stem)
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext('odn_migrations'))")
    return applied_now


def seed_local(url: str, issuer: str) -> None:
    """Idempotently create the local dev tenant bound to the mock OIDC issuer."""
    with psycopg.connect(url) as conn, conn.transaction():
        conn.execute(
            "INSERT INTO tenants (id, slug, name, oidc_issuer) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (slug) DO UPDATE SET oidc_issuer = EXCLUDED.oidc_issuer",
            (uuid.uuid4(), LOCAL_TENANT_SLUG, "Local Development", issuer),
        )


def _env_list(raw: str) -> list[str]:
    """Accept "a,b", or a multi-line "( a, b, ... )" block from .env; "#" starts a comment."""
    raw = "\n".join(line.split("#", 1)[0] for line in raw.splitlines()).strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1]
    return [d for d in re.split(r"[,\s]+", raw) if d]


_DATASET_RE = r"[A-Za-z0-9_]{1,1024}"
_TABLE_RE = r"[A-Za-z0-9_\-]{1,1024}|\*"


def local_bigquery_allowlist(env: dict[str, str]) -> tuple[str, list[str], str | None] | None:
    """(project, allowed_objects, default_dataset) from BQ_PROJECT_ID + BQ_DATASET_IDS (or legacy
    BQ_DATASET_ID*) + BQ_TABLES ("dataset.table" entries; such a dataset is limited to its listed tables)."""
    project = env.get("BQ_PROJECT_ID", "").strip()
    if not project:
        return None
    datasets = _env_list(
        env.get("BQ_DATASET_IDS")
        or ",".join(v for k, v in sorted(env.items()) if re.fullmatch(r"BQ_DATASET_ID(_\d+)?", k) and v)
    )
    tables = _env_list(env.get("BQ_TABLES", ""))
    if "*" in datasets or not (datasets or tables):
        return project, [f"{project}.*.*"], None
    for d in datasets:
        if not re.fullmatch(_DATASET_RE, d):
            raise SystemExit(f"invalid dataset id in BQ_DATASET_IDS: {d!r}")
    table_refs: list[tuple[str, str]] = []
    for t in tables:
        parts = t.split(".")
        if len(parts) == 3 and parts[0] == project:
            parts = parts[1:]
        if len(parts) != 2 or not re.fullmatch(_DATASET_RE, parts[0]) or not re.fullmatch(_TABLE_RE, parts[1]):
            raise SystemExit(f"invalid entry in BQ_TABLES (expected dataset.table in {project}): {t!r}")
        table_refs.append((parts[0], parts[1]))
    # A dataset named in BQ_TABLES is limited to the listed tables, even if BQ_DATASET_IDS lists it too.
    restricted = {d for d, _ in table_refs}
    allowed = [f"{project}.{d}.*" for d in datasets if d not in restricted] + [
        f"{project}.{d}.{t}" for d, t in table_refs
    ]
    default_dataset = datasets[0] if datasets else table_refs[0][0]
    return project, list(dict.fromkeys(allowed)), default_dataset


def seed_local_connection(url: str, env: dict[str, str]) -> str | None:
    """Create/update the local tenant's BigQuery connection so its allowlist follows .env."""
    spec = local_bigquery_allowlist(env)
    key_file = env.get("BQ_SA_KEY_FILE", "").strip()
    if spec is None or not key_file:
        return None
    project, allowed, default_dataset = spec
    config = {"project": project, **({"default_dataset": default_dataset} if default_dataset else {})}
    with psycopg.connect(url) as conn, conn.transaction():
        tenant_id = conn.execute("SELECT id FROM tenants WHERE slug = %s", (LOCAL_TENANT_SLUG,)).fetchone()[0]  # type: ignore[index]
        # RLS applies to the owner too outside local superuser setups.
        conn.execute("SELECT set_config('odn.tenant_id', %s, true)", (str(tenant_id),))
        conn.execute(
            """
            INSERT INTO connections (tenant_id, name, kind, config, secret_ref, allowed_objects)
            VALUES (%s, %s, 'bigquery', %s, %s, %s)
            ON CONFLICT (tenant_id, name) WHERE owner_user_id IS NULL DO UPDATE
              SET config = EXCLUDED.config, secret_ref = EXCLUDED.secret_ref,
                  allowed_objects = EXCLUDED.allowed_objects
            """,
            (tenant_id, env.get("ODN_LOCAL_CONNECTION_NAME", "idap-int"), Jsonb(config),
             f"file:{os.path.basename(key_file)}", allowed),
        )
    return ", ".join(allowed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-local", action="store_true")
    args = parser.parse_args()
    url = settings.migrations_database_url
    applied = migrate(url)
    print(f"migrations applied: {applied or 'none'}")
    if args.seed_local:
        if settings.env != "local":
            raise SystemExit("--seed-local is only allowed when ODN_ENV=local")
        seed_local(url, settings.oidc_issuer)
        print("local tenant ready")
        allowed = seed_local_connection(url, dict(os.environ))
        print(f"local BigQuery connection allowlist: {allowed}" if allowed else "no local BigQuery connection in .env")


if __name__ == "__main__":
    main()
