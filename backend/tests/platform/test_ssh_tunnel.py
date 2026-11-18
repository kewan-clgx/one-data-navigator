"""SSH tunnels: host-key pinning, encrypted key storage, and (when a test bastion is configured) end to end.

End-to-end tests need a bastion that can reach a Postgres the test host cannot, e.g.:

    ODN_TEST_SSH_HOST=127.0.0.1 ODN_TEST_SSH_PORT=2222 ODN_TEST_SSH_USER=tunnel \\
    ODN_TEST_SSH_KEY_FILE=/tmp/id_ed25519 ODN_TEST_SSH_FINGERPRINT=SHA256:... \\
    ODN_TEST_SSH_DB_HOST=odn-tunnel-db ODN_TEST_SSH_DB_USER=reader ODN_TEST_SSH_DB_PASSWORD=... \\
    ODN_TEST_SSH_DB_NAME=private_db
"""

from __future__ import annotations

import base64
import os
import uuid

import asyncssh
import psycopg
import pytest

from odn.api.routes import _public_config
from odn.config import settings
from odn.platform.connections.service import (
    PersonalConnectionInput,
    SSHTunnelInput,
    postgres_connector,
    resolve_ssh_key,
    save_personal_connection,
)
from odn.platform.connectors.base import QueryLimits
from odn.platform.connectors.ssh_tunnel import TunnelError, check_private_key, normalize_fingerprint

KEY = asyncssh.generate_private_key("ssh-ed25519")
KEY_TEXT = KEY.export_private_key().decode()
FP = KEY.convert_to_public().get_fingerprint("sha256")


def test_fingerprint_forms_normalize_to_sha256() -> None:
    public_line = KEY.export_public_key().decode().strip()
    assert normalize_fingerprint(public_line) == FP
    assert normalize_fingerprint(FP) == FP
    assert normalize_fingerprint(FP.removeprefix("SHA256:") + "=") == FP
    for bad in ("", "MD5:aa:bb", "SHA256:short", "ssh-ed25519 notbase64!"):
        with pytest.raises(ValueError):
            normalize_fingerprint(bad)


def test_private_key_checks() -> None:
    check_private_key(KEY_TEXT)
    with pytest.raises(ValueError, match="could not be read"):
        check_private_key("not a key")
    protected = asyncssh.generate_private_key("ecdsa-sha2-nistp256").export_private_key("pkcs8-pem", passphrase="secret").decode()
    with pytest.raises(ValueError, match="passphrase"):
        check_private_key(protected)


def test_private_key_never_lands_in_config_or_api_output() -> None:
    data = PersonalConnectionInput(
        name="tunnelled", kind="postgres", host="db.internal", database="d", user="u",
        ssh_tunnel={"bastion_host": "bastion.example.com", "bastion_user": "ec2-user",
                    "known_hosts_fingerprint": FP, "ssh_private_key": KEY_TEXT},
    )
    config = data.resolved(None).config
    assert config["ssh_tunnel"] == {"bastion_host": "bastion.example.com", "bastion_port": 22,
                                    "bastion_user": "ec2-user", "known_hosts_fingerprint": FP}
    assert "PRIVATE KEY" not in repr(data)
    leaky = {"host": "h", "password": "p", "ssh_tunnel": {"ssh_private_key": "k", "bastion_host": "b"}}
    assert _public_config(leaky) == {"host": "h", "ssh_tunnel": {"bastion_host": "b"}}


def test_tunnel_requires_a_fingerprint() -> None:
    with pytest.raises(ValueError):
        SSHTunnelInput(bastion_host="b", bastion_user="u", known_hosts_fingerprint="")


def test_tunnel_without_key_fails_clearly() -> None:
    cfg = {"host": "db", "database": "d", "user": "u",
           "ssh_tunnel": {"bastion_host": "b", "bastion_user": "u", "known_hosts_fingerprint": FP}}
    with pytest.raises(TunnelError, match="private key"):
        postgres_connector(cfg, None, None)


# --- encrypted storage (local Postgres) ------------------------------------------------------------------

def _db_reachable() -> bool:
    from tests.db.test_rls import _reachable

    return _reachable()


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch):
    if not _db_reachable():
        pytest.skip("local Postgres not running")
    from odn.db import pool
    from odn.db.migrate import migrate
    from tests.db.test_rls import APP_URL, OWNER_URL

    migrate(OWNER_URL)
    monkeypatch.setattr(settings, "database_url", APP_URL)
    monkeypatch.setattr(settings, "secrets_key", base64.urlsafe_b64encode(os.urandom(32)).decode())
    pool.cache_clear()
    tenant, user = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("INSERT INTO tenants (id, slug, name) VALUES (%s, %s, 'x')", (tenant, f"t-{tenant.hex[:12]}"))
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tenant),))
        conn.execute("INSERT INTO users (id, tenant_id, subject) VALUES (%s, %s, 's')", (user, tenant))
    yield tenant, user
    pool.cache_clear()
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = %s", (tenant,))


def _input(**tunnel: object) -> PersonalConnectionInput:
    base = {"bastion_host": "bastion.example.com", "bastion_user": "u", "known_hosts_fingerprint": FP}
    return PersonalConnectionInput(name="t", kind="postgres", host="db", database="d", user="u",
                                   ssh_tunnel={**base, **tunnel} if tunnel is not None else None)


def test_ssh_key_is_encrypted_kept_on_edit_and_dropped_with_the_tunnel(owner) -> None:
    from tests.db.test_rls import OWNER_URL

    tenant, user = owner
    with pytest.raises(ValueError, match="SSH private key is required"):
        save_personal_connection(tenant, user, _input())
    saved = save_personal_connection(tenant, user, _input(ssh_private_key=KEY_TEXT))
    with psycopg.connect(OWNER_URL) as conn:
        raw = conn.execute("SELECT config::text, ssh_key_ciphertext FROM connections WHERE id = %s", (saved.id,)).fetchone()
    assert "PRIVATE KEY" not in raw[0] and b"PRIVATE KEY" not in bytes(raw[1])
    assert resolve_ssh_key(saved) == KEY_TEXT.strip() + "\n"

    edited = save_personal_connection(tenant, user, _input(bastion_port=2200), saved.id)  # no key: keep it
    assert edited.config["ssh_tunnel"]["bastion_port"] == 2200 and resolve_ssh_key(edited) == resolve_ssh_key(saved)

    plain = PersonalConnectionInput(name="t", kind="postgres", host="db", database="d", user="u")
    dropped = save_personal_connection(tenant, user, plain, saved.id)
    assert dropped.ssh_key_ciphertext is None and "ssh_tunnel" not in dropped.config


def test_database_refuses_plaintext_keys_in_config(owner) -> None:
    from tests.db.test_rls import OWNER_URL

    tenant, user = owner
    with psycopg.connect(OWNER_URL) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tenant),))
        conn.execute(
            "INSERT INTO connections (tenant_id, owner_user_id, name, kind, config) VALUES "
            "(%s, %s, 'x', 'postgres', '{\"ssh_tunnel\": {\"ssh_private_key\": \"k\"}}')",
            (tenant, user),
        )


# --- end to end through a real bastion -------------------------------------------------------------------

E2E = {k: os.environ.get(f"ODN_TEST_SSH_{k}") for k in
       ("HOST", "PORT", "USER", "KEY_FILE", "FINGERPRINT", "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")}
e2e = pytest.mark.skipif(not all(E2E.values()), reason="no test bastion configured (ODN_TEST_SSH_*)")


def _cfg(fingerprint: str | None = None) -> dict:
    return {
        "host": E2E["DB_HOST"], "port": 5432, "database": E2E["DB_NAME"], "user": E2E["DB_USER"],
        "ssl_mode": "disable",
        "ssh_tunnel": {"bastion_host": E2E["HOST"], "bastion_port": int(E2E["PORT"] or 22),
                       "bastion_user": E2E["USER"], "known_hosts_fingerprint": fingerprint or E2E["FINGERPRINT"]},
    }


def _key() -> str:
    with open(E2E["KEY_FILE"] or "") as f:
        return f.read()


@e2e
def test_query_through_the_tunnel() -> None:
    connector = postgres_connector(_cfg(), E2E["DB_PASSWORD"], _key())
    assert [s.ok for s in connector.test_connection()] == [True] * len(connector.test_connection())
    batches = list(connector.execute_paged("SELECT current_database() AS db", QueryLimits(timeout_s=30)).batches())
    assert batches[0].to_pylist() == [{"db": E2E["DB_NAME"]}]


@e2e
def test_wrong_host_key_is_refused_before_authentication() -> None:
    with pytest.raises(TunnelError, match="do not match the pinned fingerprint"):
        postgres_connector(_cfg(fingerprint=FP), E2E["DB_PASSWORD"], _key())


@e2e
def test_wrong_private_key_is_refused() -> None:
    with pytest.raises(TunnelError, match="rejected the SSH user or private key"):
        postgres_connector(_cfg(), E2E["DB_PASSWORD"], KEY_TEXT)
