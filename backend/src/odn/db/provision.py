"""Operator CLI for hosted environments: tenants, connections, user roles and LLM budgets.

Runs with the owner/migrations role (ODN_MIGRATIONS_DATABASE_URL), e.g. on the VM:

    docker compose ... run --rm api python -m odn.db.provision tenant datahop --name "DataHop"
    docker compose ... run --rm api python -m odn.db.provision connection cotality idap-int \\
        --project clgx-idap-bigquery-int-278e \\
        --secret-ref projects/<p>/secrets/odn-<tenant_id>-bq/versions/latest --allow 'clgx-idap-bigquery-int-278e.ds.*'
    docker compose ... run --rm api python -m odn.db.provision role cotality someone@cotality.com data_lead
    docker compose ... run --rm api python -m odn.db.provision show
"""

from __future__ import annotations

import argparse
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from odn.config import settings

GOOGLE_ISSUER = "https://accounts.google.com"


@contextmanager
def _conn(url: str) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(url, row_factory=dict_row) as conn, conn.transaction():
        yield conn


def _tenant_id(conn: psycopg.Connection[dict[str, Any]], slug: str) -> uuid.UUID:
    row = conn.execute("SELECT id FROM tenants WHERE slug = %s", (slug,)).fetchone()
    if row is None:
        raise SystemExit(f"unknown tenant {slug!r}; create it with: provision tenant {slug} --name ...")
    # Tenant tables use FORCE RLS, which applies to the owner role too.
    conn.execute("SELECT set_config('odn.tenant_id', %s, true)", (str(row["id"]),))
    return row["id"]


def upsert_tenant(url: str, slug: str, name: str, issuer: str) -> uuid.UUID:
    with _conn(url) as conn:
        row = conn.execute(
            "INSERT INTO tenants (slug, name, oidc_issuer) VALUES (%s, %s, %s) "
            "ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, oidc_issuer = EXCLUDED.oidc_issuer "
            "RETURNING id",
            (slug, name, issuer),
        ).fetchone()
    assert row is not None
    return row["id"]


def upsert_connection(
    url: str, tenant: str, name: str, config: dict[str, Any], secret_ref: str, allowed: list[str]
) -> uuid.UUID:
    from odn.platform.connections.service import ConnectionInput, is_tenant_secret_ref

    data = ConnectionInput(name=name, kind="bigquery", config=config, secret_ref=secret_ref, allowed_objects=allowed)
    with _conn(url) as conn:
        tenant_id = _tenant_id(conn, tenant)
        if not is_tenant_secret_ref(tenant_id, data.secret_ref):
            raise SystemExit(
                "secret_ref must be projects/<project>/secrets/odn-"
                f"{tenant_id}-<name>/versions/<latest|N> (file: refs only work with ODN_ENV=local)"
            )
        row = conn.execute(
            """
            INSERT INTO connections (tenant_id, name, kind, config, secret_ref, allowed_objects)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, name) DO UPDATE
              SET config = EXCLUDED.config, secret_ref = EXCLUDED.secret_ref,
                  allowed_objects = EXCLUDED.allowed_objects
            RETURNING id
            """,
            (tenant_id, data.name, data.kind, Jsonb(data.config), data.secret_ref, data.allowed_objects),
        ).fetchone()
    assert row is not None
    return row["id"]


def set_role(url: str, tenant: str, email: str, role: str) -> int:
    """Users appear on first sign-in, so this only updates existing users (matched by email)."""
    with _conn(url) as conn:
        tenant_id = _tenant_id(conn, tenant)
        cur = conn.execute(
            "UPDATE users SET role = %s WHERE tenant_id = %s AND lower(email) = lower(%s)", (role, tenant_id, email)
        )
        return cur.rowcount


def set_budget(url: str, tenant: str, tokens: int) -> None:
    with _conn(url) as conn:
        tenant_id = _tenant_id(conn, tenant)
        conn.execute(
            "INSERT INTO tenant_llm_budgets (tenant_id, daily_token_limit) VALUES (%s, %s) "
            "ON CONFLICT (tenant_id) DO UPDATE SET daily_token_limit = EXCLUDED.daily_token_limit, updated_at = now()",
            (tenant_id, tokens),
        )


def show(url: str) -> None:
    with _conn(url) as conn:
        tenants = conn.execute("SELECT id, slug, name, oidc_issuer FROM tenants ORDER BY slug").fetchall()
    for t in tenants:
        print(f"tenant {t['slug']}  id={t['id']}  issuer={t['oidc_issuer']}  name={t['name']!r}")
        with _conn(url) as conn:
            # Filter explicitly: a superuser owner role bypasses RLS.
            tid = _tenant_id(conn, t["slug"])
            for c in conn.execute(
                "SELECT name, config, secret_ref, allowed_objects FROM connections WHERE tenant_id = %s ORDER BY name",
                (tid,),
            ):
                print(f"  connection {c['name']}  project={c['config'].get('project')}  secret_ref={c['secret_ref']}")
                print(f"    allowed: {', '.join(c['allowed_objects'])}")
            for u in conn.execute(
                "SELECT email, role, last_login_at FROM users WHERE tenant_id = %s ORDER BY email", (tid,)
            ):
                print(f"  user {u['email']}  role={u['role']}  last_login={u['last_login_at']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m odn.db.provision", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tenant", help="create/update a tenant")
    p.add_argument("slug")
    p.add_argument("--name", required=True)
    p.add_argument("--issuer", default=GOOGLE_ISSUER)

    p = sub.add_parser("connection", help="create/update a BigQuery connection (Secret Manager credentials)")
    p.add_argument("tenant")
    p.add_argument("name")
    p.add_argument("--project", required=True)
    p.add_argument("--secret-ref", required=True)
    p.add_argument("--allow", action="append", required=True, help="allowlist pattern, e.g. project.dataset.*")
    p.add_argument("--default-dataset")
    p.add_argument("--location")
    p.add_argument("--billing-project")

    p = sub.add_parser("role", help="set a signed-in user's role")
    p.add_argument("tenant")
    p.add_argument("email")
    p.add_argument("role", choices=["member", "data_lead", "admin"])

    p = sub.add_parser("budget", help="set a tenant's daily LLM token limit")
    p.add_argument("tenant")
    p.add_argument("tokens", type=int)

    sub.add_parser("show", help="list tenants, connections and users")

    args = parser.parse_args(argv)
    url = settings.migrations_database_url
    if not url:
        raise SystemExit("ODN_MIGRATIONS_DATABASE_URL is not set")

    if args.cmd == "tenant":
        print(f"tenant {args.slug}: {upsert_tenant(url, args.slug, args.name, args.issuer)}")
    elif args.cmd == "connection":
        config = {"project": args.project}
        for key in ("default_dataset", "location", "billing_project"):
            if getattr(args, key):
                config[key] = getattr(args, key)
        cid = upsert_connection(url, args.tenant, args.name, config, args.secret_ref, args.allow)
        print(f"connection {args.tenant}/{args.name}: {cid}")
    elif args.cmd == "role":
        n = set_role(url, args.tenant, args.email, args.role)
        print(f"updated {n} user(s)" if n else f"no user {args.email} in {args.tenant} yet; they must sign in first")
    elif args.cmd == "budget":
        set_budget(url, args.tenant, args.tokens)
        print(f"{args.tenant}: {args.tokens} tokens/day")
    else:
        show(url)


if __name__ == "__main__":
    main()
