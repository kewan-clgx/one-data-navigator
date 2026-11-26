"""Personal connections and bring-your-own LLM keys: per-user visibility, encryption, trial budget (needs Postgres)."""

import base64
import json
import os
import uuid

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from odn.config import settings
from odn.platform.connections.service import (
    DuplicateName,
    PersonalConnectionInput,
    delete_personal_connection,
    get_connection,
    list_connections,
    resolve_credentials,
    save_personal_connection,
)
from odn.platform.governance.llm_budget import budget_status, record_llm_usage
from odn.platform.llm_keys import (
    LlmKeyInput,
    LlmKeyTestInput,
    check_llm_key,
    get_llm_key,
    own_key_usage_today,
    resolve_llm_config,
    save_llm_key,
)
from odn.tenancy.context import RequestContext
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")

PEM = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()


def sa_key(project: str = "my-proj-123", email: str = "sa@my-proj-123.iam.gserviceaccount.com") -> str:
    return json.dumps({
        "type": "service_account", "project_id": project, "private_key_id": "k1", "private_key": PEM,
        "client_email": email, "token_uri": "https://oauth2.googleapis.com/token",
    })


@pytest.fixture(scope="module")
def users():
    from odn.db import pool
    from odn.db.migrate import migrate

    migrate(OWNER_URL)
    settings.database_url = APP_URL
    settings.secrets_key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    pool.cache_clear()
    t, t2 = uuid.uuid4(), uuid.uuid4()
    u1, u2, u3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(OWNER_URL) as conn:
        for tenant, members in ((t, (u1, u2)), (t2, (u3,))):
            conn.execute("INSERT INTO tenants (id, slug, name) VALUES (%s, %s, 'x')",
                         (tenant, f"t-{tenant.hex[:12]}"))
            for u in members:
                conn.execute("INSERT INTO users (id, tenant_id, subject) VALUES (%s, %s, %s)", (u, tenant, u.hex))
        conn.execute(
            "INSERT INTO connections (tenant_id, name, kind, config, secret_ref, allowed_objects) "
            "VALUES (%s, 'shared', 'bigquery', '{\"project\": \"p\"}', 'file:x', '{p.d.*}')",
            (t,),
        )
    yield (
        RequestContext(tenant_id=t, user_id=u1, subject="a", role="member"),
        RequestContext(tenant_id=t, user_id=u2, subject="b", role="member"),
        RequestContext(tenant_id=t2, user_id=u3, subject="c", role="member"),
    )
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", ([t, t2],))


def _names(ctx: RequestContext) -> list[tuple[str, bool]]:
    # Platform connections (tenant_id NULL, e.g. bigquery-public-data) are visible to everyone; not under test here.
    return [(p.name, p.personal) for p in list_connections(ctx.tenant_id, ctx.user_id) if p.tenant_id is not None]


def test_personal_connection_is_visible_only_to_its_owner(users) -> None:
    a, b, other = users
    mine = save_personal_connection(
        a.tenant_id, a.user_id, PersonalConnectionInput(name="mine", credentials_json=sa_key())
    )
    assert mine.config == {"project": "my-proj-123"} and mine.allowed_objects == ["my-proj-123.*.*"]
    assert ("mine", True) in _names(a) and ("shared", False) in _names(a)
    assert _names(b) == [("shared", False)] and _names(other) == []
    assert get_connection(b.tenant_id, b.user_id, mine.id) is None
    # Without a user in the transaction, personal rows stay hidden (fail closed).
    assert get_connection(a.tenant_id, None, mine.id) is None  # type: ignore[arg-type]
    assert delete_personal_connection(b.tenant_id, b.user_id, mine.id) is False
    with pytest.raises(LookupError):
        save_personal_connection(b.tenant_id, b.user_id, PersonalConnectionInput(name="x"), mine.id)


def test_snowflake_key_is_encrypted_and_owner_scoped(users) -> None:
    from odn.platform.connections.service import resolve_postgres_credentials

    a, b, _ = users
    data = PersonalConnectionInput(name="snowflake", kind="snowflake", account="org-account",
                                   user="svc", warehouse="WH", database="DB", schema="PUBLIC",
                                   allowed_objects=["DB.PUBLIC.*", "OTHER.PUBLIC.*"], private_key=PEM)
    saved = save_personal_connection(a.tenant_id, a.user_id, data)
    assert saved.secret_ciphertext and b"BEGIN PRIVATE KEY" not in saved.secret_ciphertext
    assert get_connection(b.tenant_id, b.user_id, saved.id) is None
    updated = save_personal_connection(a.tenant_id, a.user_id, data.model_copy(update={"private_key": None}), saved.id)
    assert resolve_postgres_credentials(updated) == PEM
    with pytest.raises(ValueError, match="kind cannot be changed"):
        save_personal_connection(a.tenant_id, a.user_id, PersonalConnectionInput(name="other"), saved.id)


def test_snowflake_password_storage_and_auth_switch(users) -> None:
    from odn.platform.connections.service import resolve_postgres_credentials, unsaved_connector

    a, b, _ = users
    data = PersonalConnectionInput(name="sf-password", kind="snowflake", account="org-account",
                                   user="svc", warehouse="WH", database="DB", schema="PUBLIC",
                                   allowed_objects=["DB.*.*"], auth_method="password", password="test-secret")
    saved = save_personal_connection(a.tenant_id, a.user_id, data)
    assert saved.secret_ciphertext and b"test-secret" not in saved.secret_ciphertext
    assert saved.config["auth_method"] == "password"
    assert get_connection(b.tenant_id, b.user_id, saved.id) is None
    edited = data.model_copy(update={"password": None})
    saved = save_personal_connection(a.tenant_id, a.user_id, edited, saved.id)
    assert resolve_postgres_credentials(saved) == "test-secret"
    assert unsaved_connector(edited, saved).dialect == "snowflake"
    switching = edited.model_copy(update={"auth_method": "key_pair"})
    with pytest.raises(ValueError, match="private key is required"):
        save_personal_connection(a.tenant_id, a.user_id, switching, saved.id)
    saved = save_personal_connection(a.tenant_id, a.user_id, switching.model_copy(update={"private_key": PEM}), saved.id)
    assert saved.config["auth_method"] == "key_pair"
    assert resolve_postgres_credentials(saved) == PEM


def test_mysql_password_is_encrypted_scoped_and_retained(users) -> None:
    from odn.api.routes import ConnectionOut
    from odn.platform.connections.service import (
        resolve_postgres_credentials,
        save_connection,
        unsaved_connector,
    )

    a, b, other = users
    data = PersonalConnectionInput(
        name="mysql", kind="mysql", host="mysql.example.com", database="Sales", user="reader",
        password="mysql-test-only", allowed_objects=["Sales.*", "Reporting.Orders"],
    )
    saved = save_personal_connection(a.tenant_id, a.user_id, data)
    assert saved.secret_ciphertext and b"mysql-test-only" not in saved.secret_ciphertext
    assert get_connection(b.tenant_id, b.user_id, saved.id) is None
    assert get_connection(other.tenant_id, other.user_id, saved.id) is None
    assert "mysql-test-only" not in ConnectionOut.of(saved).model_dump_json()
    edited = data.model_copy(update={"password": ""})
    saved = save_personal_connection(a.tenant_id, a.user_id, edited, saved.id)
    assert resolve_postgres_credentials(saved) == "mysql-test-only"
    assert unsaved_connector(edited, saved).dialect == "mysql"
    for kind in ("postgres", "snowflake", "bigquery"):
        with pytest.raises(ValueError, match="kind cannot be changed"):
            unsaved_connector(edited.model_copy(update={"kind": kind}), saved)
    shared = save_connection(a.tenant_id, a.user_id, data.model_copy(update={"name": "shared-mysql"}), shared=True)
    assert get_connection(b.tenant_id, b.user_id, shared.id) is not None
    assert get_connection(other.tenant_id, other.user_id, shared.id) is None


def test_oracle_password_encryption_scope_and_edit_retention(users):
    from odn.api.me_routes import ConnectionOut
    from odn.platform.connections.service import (
        resolve_postgres_credentials,
        save_connection,
        unsaved_connector,
    )

    a, b, other = users
    data = PersonalConnectionInput(
        name="oracle", kind="oracle", host="oracle.example.com", service_name="reports",
        schema="HR", user="READER", password="oracle-test-only", allowed_objects=["HR.*"],
    )
    saved = save_personal_connection(a.tenant_id, a.user_id, data)
    assert saved.secret_ciphertext and b"oracle-test-only" not in saved.secret_ciphertext
    assert get_connection(b.tenant_id, b.user_id, saved.id) is None
    assert get_connection(other.tenant_id, other.user_id, saved.id) is None
    assert "oracle-test-only" not in ConnectionOut.of(saved).model_dump_json()
    edited = data.model_copy(update={"password": ""})
    saved = save_personal_connection(a.tenant_id, a.user_id, edited, saved.id)
    assert resolve_postgres_credentials(saved) == "oracle-test-only"
    assert unsaved_connector(edited, saved).dialect == "oracle"
    with pytest.raises(ValueError, match="kind cannot be changed"):
        unsaved_connector(edited.model_copy(update={"kind": "postgres"}), saved)
    shared = save_connection(a.tenant_id, a.user_id, data.model_copy(update={"name": "shared-oracle"}), shared=True)
    assert get_connection(b.tenant_id, b.user_id, shared.id) is not None
    assert get_connection(other.tenant_id, other.user_id, shared.id) is None


def test_key_is_encrypted_and_kept_on_update(users) -> None:
    a, b, _ = users
    created = save_personal_connection(
        a.tenant_id, a.user_id,
        PersonalConnectionInput(name="enc", credentials_json=sa_key(), allowed_objects=["sales", "hr.people", "o.x.*"]),
    )
    assert created.allowed_objects == ["my-proj-123.sales.*", "my-proj-123.hr.people", "o.x.*"]
    assert created.secret_ciphertext and b"BEGIN PRIVATE KEY" not in created.secret_ciphertext
    assert resolve_credentials(created).service_account_email == "sa@my-proj-123.iam.gserviceaccount.com"

    updated = save_personal_connection(
        a.tenant_id, a.user_id, PersonalConnectionInput(name="enc2", default_dataset="sales"), created.id
    )
    assert updated.name == "enc2" and updated.config["default_dataset"] == "sales"
    assert updated.allowed_objects == ["my-proj-123.*.*"]
    assert resolve_credentials(updated).service_account_email == "sa@my-proj-123.iam.gserviceaccount.com"

    # A ciphertext moved to another user's row does not decrypt.
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute(
            "INSERT INTO connections (tenant_id, owner_user_id, name, kind, config, allowed_objects, secret_ciphertext)"
            " SELECT tenant_id, %s, 'stolen', kind, config, allowed_objects, secret_ciphertext FROM connections "
            " WHERE id = %s",
            (b.user_id, created.id),
        )
    [stolen] = [p for p in list_connections(b.tenant_id, b.user_id) if p.name == "stolen"]
    from odn.platform.connections.service import CredentialError

    with pytest.raises(CredentialError):
        resolve_credentials(stolen)


def test_names_are_unique_per_user_and_inputs_validated(users) -> None:
    a, b, _ = users
    save_personal_connection(a.tenant_id, a.user_id, PersonalConnectionInput(name="dup", credentials_json=sa_key()))
    with pytest.raises(DuplicateName):
        save_personal_connection(a.tenant_id, a.user_id, PersonalConnectionInput(name="dup", credentials_json=sa_key()))
    save_personal_connection(b.tenant_id, b.user_id, PersonalConnectionInput(name="dup", credentials_json=sa_key()))
    for bad in (
        PersonalConnectionInput(name="k1"),  # no key on create
        PersonalConnectionInput(name="k2", credentials_json='{"type": "authorized_user"}'),
        PersonalConnectionInput(name="k3", credentials_json=sa_key(), allowed_objects=["a..b"]),
        PersonalConnectionInput(name="k4", credentials_json=sa_key(), project="Bad Project!"),
    ):
        with pytest.raises(ValueError):
            save_personal_connection(a.tenant_id, a.user_id, bad)


def test_personal_connection_cannot_reference_tenant_secrets(users) -> None:
    a, _, _ = users
    with psycopg.connect(APP_URL) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("SELECT set_config('odn.tenant_id', %s, false), set_config('odn.user_id', %s, false)",
                     (str(a.tenant_id), str(a.user_id)))
        conn.execute(
            "INSERT INTO connections (tenant_id, owner_user_id, name, kind, secret_ref, allowed_objects) "
            "VALUES (%s, %s, 'sneaky', 'bigquery', 'projects/x/secrets/odn-t-bq/versions/latest', '{p.d.*}')",
            (a.tenant_id, a.user_id),
        )


def test_llm_key_is_per_user_and_encrypted(users) -> None:
    a, b, _ = users
    saved = save_llm_key(a, LlmKeyInput(provider="openai", model="openai/gpt-x", api_key="sk-test-123456789"))
    assert (saved.model, saved.key_hint) == ("gpt-x", "…6789")
    assert get_llm_key(b) is None and resolve_llm_config(b) is None
    cfg = resolve_llm_config(a)
    assert cfg is not None and cfg["api_key"] == "sk-test-123456789" and cfg["litellm_model"] == "openai/gpt-x"
    with psycopg.connect(OWNER_URL) as conn:
        [raw] = conn.execute("SELECT key_ciphertext FROM user_llm_keys WHERE user_id = %s", (a.user_id,)).fetchone()
    assert b"sk-test" not in bytes(raw)

    # Changing the model keeps the key; switching provider requires re-entering it.
    assert save_llm_key(a, LlmKeyInput(provider="openai", model="gpt-y")).model == "gpt-y"
    with pytest.raises(ValueError):
        save_llm_key(a, LlmKeyInput(provider="anthropic", model="claude-x"))


def test_listing_models_needs_no_model_and_uses_the_saved_key(users, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from odn.platform import llm_keys

    a, _, _ = users
    save_llm_key(a, LlmKeyInput(provider="openai", model="gpt-x", api_key="sk-saved-123456789"))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"data": [{"id": "gpt-z"}, {"id": "gpt-x"}]})

    real_client = httpx.Client
    monkeypatch.setattr(llm_keys.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    result = check_llm_key(a, LlmKeyTestInput(provider="openai"))
    assert (result.ok, result.models, result.model_found) == (True, ["gpt-x", "gpt-z"], None)
    assert seen == ["Bearer sk-saved-123456789"]


def test_trial_budget_is_per_user_and_own_key_is_unmetered(users, monkeypatch: pytest.MonkeyPatch) -> None:
    _, b, other = users
    monkeypatch.setattr(settings, "llm_trial_user_daily_tokens", 100)
    monkeypatch.setattr(settings, "llm_daily_token_budget", 1000)
    status = record_llm_usage(other, "m", 60, 50)
    assert (status.key_source, status.exceeded, status.exceeded_scope) == ("platform", True, "user")
    assert status.user_tokens_used_today == 110

    save_llm_key(other, LlmKeyInput(provider="gemini", model="gemini-x", api_key="AIza-test-key-1"))
    status = record_llm_usage(other, "gemini-x", 5000, 0, key_source="user")
    record_llm_usage(other, "gemini-x", 3000, 10, key_source="user", cached_tokens=2500)
    [today] = own_key_usage_today(other)
    assert (today["requests"], today["prompt_tokens"], today["cached_tokens"]) == (2, 8000, 2500)
    assert (status.key_source, status.exceeded, status.tokens_used_today) == ("user", False, 110)
    assert budget_status(b.tenant_id, b.user_id).user_tokens_used_today == 0
