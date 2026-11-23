"""Operator CLI for hosted environments: tenants, connections, user roles and LLM budgets.

Runs with the owner/migrations role (ODN_MIGRATIONS_DATABASE_URL), e.g. on the VM:

    docker compose ... run --rm api python -m odn.db.provision tenant datahop --name "QueryMuse"
    docker compose ... run --rm api python -m odn.db.provision connection cotality idap-int \\
        --project clgx-idap-bigquery-int-278e \\
        --secret-ref projects/<p>/secrets/odn-<tenant_id>-bq/versions/latest --allow 'clgx-idap-bigquery-int-278e.ds.*'
    docker compose ... run --rm api python -m odn.db.provision role cotality someone@cotality.com data_lead
    docker compose ... run --rm api python -m odn.db.provision show

Tenant directory (docs/Tenant_Isolation_User_system.md):

    provision tenant indulge --name Indulge --auth password --join invite --create-idp-tenant
    provision domain indulge indulgetheapp.com            # checks DNS TXT odn-domain-verification=<tenant_id>
    provision domain cotality cotality.com --attest        # operator attests ownership instead
    provision invite indulge alice@indulgetheapp.com --role admin
    provision user indulge bob@indulgetheapp.com --status disabled
    provision idp datahop https://accounts.google.com '*'  # issuer fallback tenant (personal Google accounts)
    provision collibra cotality prd --url https://example.collibra.com                     # users' own browser sign-in
    provision collibra cotality prd --url https://example.collibra.com --audience querymuse-collibra   # token mode
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
DOMAIN_TXT_PREFIX = "odn-domain-verification="


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


# The organization's sign-in link is /<slug>; keep in sync with migration 0025 (tenants_slug_not_reserved).
RESERVED_SLUGS = frozenset({
    "about", "admin", "api", "app", "assets", "auth", "blog", "contact", "docs", "downloads", "favicon", "help",
    "home", "insights", "legal", "login", "logout", "pricing", "privacy", "robots", "s", "security", "settings",
    "signin", "signup", "site", "sitemap", "static", "status", "support", "terms", "www",
})


def upsert_tenant(
    url: str,
    slug: str,
    name: str,
    *,
    auth: str | None = None,
    join: str | None = None,
    theme: str | None = None,
    status: str | None = None,
    idp_tenant_id: str | None = None,
) -> uuid.UUID:
    """Create or update a tenant; only the given fields change on update."""
    if slug in RESERVED_SLUGS:
        raise SystemExit(f"{slug!r} is reserved (its sign-in link /{slug} would clash with an app page); pick another slug")
    with _conn(url) as conn:
        row = conn.execute(
            """
            INSERT INTO tenants (slug, name, auth_method, join_policy, theme, status, idp_tenant_id)
            VALUES (%(slug)s, %(name)s, coalesce(%(auth)s, 'google'), coalesce(%(join)s, 'invite'),
                    coalesce(%(theme)s, 'default'), coalesce(%(status)s, 'active'), %(idp)s)
            ON CONFLICT (slug) DO UPDATE SET
              name = EXCLUDED.name,
              auth_method = coalesce(%(auth)s, tenants.auth_method),
              join_policy = coalesce(%(join)s, tenants.join_policy),
              theme = coalesce(%(theme)s, tenants.theme),
              status = coalesce(%(status)s, tenants.status),
              idp_tenant_id = coalesce(%(idp)s, tenants.idp_tenant_id),
              updated_at = now()
            RETURNING id
            """,
            {"slug": slug, "name": name, "auth": auth, "join": join, "theme": theme,
             "status": status, "idp": idp_tenant_id},
        ).fetchone()
        assert row is not None
        if idp_tenant_id:
            from odn.config import settings as cfg

            conn.execute(
                "INSERT INTO tenant_identity_providers (issuer, external_id, tenant_id) VALUES (%s, %s, %s) "
                "ON CONFLICT (issuer, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id",
                (f"https://securetoken.google.com/{cfg.idp_project}", idp_tenant_id, row["id"]),
            )
    return row["id"]


def map_identity(url: str, tenant: str, issuer: str, external_id: str) -> None:
    with _conn(url) as conn:
        tenant_id = _tenant_id(conn, tenant)
        conn.execute(
            "INSERT INTO tenant_identity_providers (issuer, external_id, tenant_id) VALUES (%s, %s, %s) "
            "ON CONFLICT (issuer, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id",
            (issuer, external_id, tenant_id),
        )


def dns_txt_records(name: str) -> list[str]:
    """TXT records via Google DNS-over-HTTPS (no resolver library needed)."""
    import httpx

    resp = httpx.get("https://dns.google/resolve", params={"name": name, "type": "TXT"}, timeout=10)
    resp.raise_for_status()
    return [a.get("data", "").strip('"') for a in resp.json().get("Answer", []) if a.get("type") == 16]


def add_domain(url: str, tenant: str, domain: str, attest: bool = False, txt_lookup=dns_txt_records) -> bool:
    """Register a domain for a tenant; verified by DNS TXT `odn-domain-verification=<tenant_id>` or by attestation.

    Google-SSO tenants also get the Google `hd` mapping for the domain. Returns whether the domain is verified."""
    domain = domain.strip().lower()
    with _conn(url) as conn:
        tenant_id = _tenant_id(conn, tenant)
        owner = conn.execute("SELECT tenant_id FROM tenant_domains WHERE domain = %s", (domain,)).fetchone()
        if owner and owner["tenant_id"] != tenant_id:
            raise SystemExit(f"{domain} already belongs to another tenant")
        verified = attest or f"{DOMAIN_TXT_PREFIX}{tenant_id}" in txt_lookup(domain)
        conn.execute(
            "INSERT INTO tenant_domains (domain, tenant_id, verified_at) VALUES (%s, %s, CASE WHEN %s THEN now() END) "
            "ON CONFLICT (domain) DO UPDATE SET verified_at = coalesce(tenant_domains.verified_at, EXCLUDED.verified_at)",
            (domain, tenant_id, verified),
        )
        auth = conn.execute("SELECT auth_method FROM tenants WHERE id = %s", (tenant_id,)).fetchone()
        if verified and auth and auth["auth_method"] == "google":
            conn.execute(
                "INSERT INTO tenant_identity_providers (issuer, external_id, tenant_id) VALUES (%s, %s, %s) "
                "ON CONFLICT (issuer, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id",
                (GOOGLE_ISSUER, domain, tenant_id),
            )
    return verified


def invite_user(url: str, tenant: str, email: str, role: str) -> None:
    from odn.tenancy.directory import DirectoryError, invite

    with _conn(url) as conn:
        tenant_id = _tenant_id(conn, tenant)
        try:
            invite(conn, tenant_id, email, role)
        except DirectoryError as exc:
            raise SystemExit(str(exc)) from None


def update_user(url: str, tenant: str, email: str, role: str | None, status: str | None) -> None:
    from odn.tenancy import directory

    with _conn(url) as conn:
        tenant_id = _tenant_id(conn, tenant)
        row = conn.execute(
            "SELECT id FROM users WHERE tenant_id = %s AND lower(email) = lower(%s)", (tenant_id, email)
        ).fetchone()
        if row is None:
            raise SystemExit(f"no user {email} in {tenant}")
        try:
            directory.update_user(conn, tenant_id, row["id"], role=role, status=status)
        except directory.DirectoryError as exc:
            raise SystemExit(str(exc)) from None


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
            ON CONFLICT (tenant_id, name) WHERE owner_user_id IS NULL DO UPDATE
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


def upsert_collibra(
    url: str, tenant: str, env: str, base_url: str, audience: str | None = None, principal_format: str = "email",
    enabled: bool = True,
) -> None:
    """Connect a tenant to a Collibra environment (docs/collibra_int_phase5_plan.md).

    With an audience: token mode (Collibra trusts QueryMuse-signed tokens). Without: browser mode (each user's own
    Collibra sign-in through the QueryMuse Collibra Bridge extension). Admins can also manage this in Settings.
    """
    mode = "token" if audience else "browser"
    with _conn(url) as conn:
        tenant_id = _tenant_id(conn, tenant)
        conn.execute(
            "INSERT INTO collibra_environments (tenant_id, env, base_url, access_mode, audience, principal_format, enabled) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, env) DO UPDATE SET base_url = EXCLUDED.base_url, "
            "access_mode = EXCLUDED.access_mode, audience = EXCLUDED.audience, principal_format = EXCLUDED.principal_format, "
            "enabled = EXCLUDED.enabled, updated_at = now()",
            (tenant_id, env, base_url.rstrip("/"), mode, audience, principal_format, enabled),
        )


def show(url: str) -> None:
    with _conn(url) as conn:
        tenants = conn.execute(
            "SELECT id, slug, name, status, auth_method, join_policy, theme, idp_tenant_id FROM tenants ORDER BY slug"
        ).fetchall()
    for t in tenants:
        print(
            f"tenant {t['slug']}  id={t['id']}  name={t['name']!r}  status={t['status']}  auth={t['auth_method']}"
            f"  join={t['join_policy']}  theme={t['theme']}" + (f"  idp_tenant={t['idp_tenant_id']}" if t["idp_tenant_id"] else "")
        )
        with _conn(url) as conn:
            for d in conn.execute(
                "SELECT domain, verified_at FROM tenant_domains WHERE tenant_id = %s ORDER BY domain", (t["id"],)
            ):
                print(f"  domain {d['domain']}  {'verified' if d['verified_at'] else 'UNVERIFIED'}")
            for p in conn.execute(
                "SELECT issuer, external_id FROM tenant_identity_providers WHERE tenant_id = %s ORDER BY 1, 2", (t["id"],)
            ):
                print(f"  identity {p['issuer']}  {p['external_id']}")
            # Filter explicitly: a superuser owner role bypasses RLS.
            tid = _tenant_id(conn, t["slug"])
            for c in conn.execute(
                "SELECT name, config, secret_ref, allowed_objects FROM connections WHERE tenant_id = %s ORDER BY name",
                (tid,),
            ):
                print(f"  connection {c['name']}  project={c['config'].get('project')}  secret_ref={c['secret_ref']}")
                print(f"    allowed: {', '.join(c['allowed_objects'])}")
            for e in conn.execute(
                "SELECT env, base_url, access_mode, audience, principal_format, enabled FROM collibra_environments "
                "WHERE tenant_id = %s ORDER BY env", (tid,),
            ):
                token = f"  audience={e['audience']}  sub={e['principal_format']}" if e["access_mode"] == "token" else ""
                print(f"  collibra {e['env']}  {e['base_url']}  mode={e['access_mode']}{token}"
                      + ("" if e["enabled"] else "  DISABLED"))
            for u in conn.execute(
                "SELECT email, role, status, last_login_at FROM users WHERE tenant_id = %s ORDER BY email", (tid,)
            ):
                print(f"  user {u['email']}  role={u['role']}  status={u['status']}  last_login={u['last_login_at']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m odn.db.provision", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tenant", help="create/update a tenant")
    p.add_argument("slug")
    p.add_argument("--name", required=True)
    p.add_argument("--auth", choices=["google", "password", "oidc", "saml"])
    p.add_argument("--join", choices=["domain", "invite"])
    p.add_argument("--theme")
    p.add_argument("--status", choices=["active", "suspended"])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--idp-tenant", help="existing Identity Platform tenant id")
    g.add_argument("--create-idp-tenant", action="store_true", help="create an Identity Platform tenant")

    p = sub.add_parser("domain", help="add/verify a tenant email domain")
    p.add_argument("tenant")
    p.add_argument("domain")
    p.add_argument("--attest", action="store_true", help="operator attests ownership (skip the DNS TXT check)")

    p = sub.add_parser("idp", help="map a token (issuer, tenant claim value or '*') to a tenant")
    p.add_argument("tenant")
    p.add_argument("issuer")
    p.add_argument("external_id")

    p = sub.add_parser("invite", help="invite a user (password tenants also get a set-password email)")
    p.add_argument("tenant")
    p.add_argument("email")
    p.add_argument("--role", choices=["member", "data_lead", "admin"], default="member")

    p = sub.add_parser("user", help="change a user's role or status")
    p.add_argument("tenant")
    p.add_argument("email")
    p.add_argument("--role", choices=["member", "data_lead", "admin"])
    p.add_argument("--status", choices=["active", "disabled"])

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

    p = sub.add_parser("collibra", help="connect a tenant to a Collibra environment (users' own permissions)")
    p.add_argument("tenant")
    p.add_argument("env", help="environment label, e.g. prd or int")
    p.add_argument("--url", required=True, help="Collibra origin, e.g. https://example.collibra.com")
    p.add_argument("--audience", help="token mode: JWT audience configured in Collibra (omit for browser mode)")
    p.add_argument("--principal", choices=["email", "email_local_part"], default="email",
                   help="Collibra username format (JWT sub)")
    p.add_argument("--disable", action="store_true")

    sub.add_parser("show", help="list tenants, connections and users")

    args = parser.parse_args(argv)
    url = settings.migrations_database_url
    if not url:
        raise SystemExit("ODN_MIGRATIONS_DATABASE_URL is not set")

    if args.cmd == "tenant":
        idp = args.idp_tenant
        if args.create_idp_tenant:
            from odn.platform.identity_platform import create_tenant

            idp = create_tenant(args.slug)
            print(f"created Identity Platform tenant {idp}")
        tid = upsert_tenant(
            url, args.slug, args.name, auth=args.auth, join=args.join, theme=args.theme,
            status=args.status, idp_tenant_id=idp,
        )
        print(f"tenant {args.slug}: {tid}")
        print(f"  verify a domain with DNS TXT: {DOMAIN_TXT_PREFIX}{tid}")
    elif args.cmd == "domain":
        ok = add_domain(url, args.tenant, args.domain, args.attest)
        print(f"{args.domain}: {'verified' if ok else 'added, NOT verified (DNS TXT record not found)'}")
    elif args.cmd == "idp":
        map_identity(url, args.tenant, args.issuer, args.external_id)
        print(f"{args.issuer} {args.external_id} -> {args.tenant}")
    elif args.cmd == "invite":
        invite_user(url, args.tenant, args.email, args.role)
        print(f"invited {args.email} to {args.tenant} as {args.role}")
    elif args.cmd == "user":
        update_user(url, args.tenant, args.email, args.role, args.status)
        print(f"updated {args.email}")
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
    elif args.cmd == "collibra":
        upsert_collibra(url, args.tenant, args.env, args.url, args.audience, args.principal, not args.disable)
        print(f"collibra {args.tenant}/{args.env}: {args.url}" + (" (disabled)" if args.disable else ""))
    else:
        show(url)


if __name__ == "__main__":
    main()
