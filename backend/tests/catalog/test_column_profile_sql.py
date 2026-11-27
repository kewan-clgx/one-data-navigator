import pytest
import sqlglot
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.cloud.bigquery import PolicyTagList, SchemaField

from odn.platform.catalog.column_profile_sql import profile_sql
from odn.platform.catalog.column_profiles import query_paths
from odn.platform.connections import service as connections
from odn.platform.connectors.bigquery import BigQueryConnector


def test_profiles_nested_and_repeated_values_per_parent_row():
    schema = [
        SchemaField("name", "STRING"),
        SchemaField("contacts", "RECORD", mode="REPEATED", fields=[
            SchemaField("email", "STRING"),
            SchemaField("orders", "RECORD", mode="REPEATED", fields=[
                SchemaField("amount", "NUMERIC"),
            ]),
        ]),
        SchemaField("score", "FLOAT"),
    ]
    query, fields = profile_sql(("project-123", "dataset", "table"), schema)
    assert [f.path for f in fields] == [
        ("name",), ("contacts",), ("contacts", "email"), ("contacts", "orders"),
        ("contacts", "orders", "amount"), ("score",),
    ]
    assert "TRIM(element_0.`email`) != ''" in query
    assert "EXISTS (SELECT 1 FROM UNNEST(t.`contacts`) AS element_0" in query
    assert "UNNEST(element_0.`orders`) AS element_1" in query
    assert "IS_NAN(t.`score`)" in query and "IS_INF(t.`score`)" in query
    assert not fields[0].has_range and fields[-1].has_range
    assert sqlglot.parse_one(query, read="bigquery") is not None


def test_escapes_identifiers_and_rejects_ambiguous_table_paths():
    query, fields = profile_sql(("project-123", "dataset", "table"), [SchemaField("strange`name", "STRING")])
    assert "strange\\`name" in query
    assert fields[0].path == ("strange`name",)
    assert sqlglot.parse_one(query, read="bigquery") is not None
    with pytest.raises(ValueError):
        profile_sql(("project", "dataset", "table.name"), [SchemaField("id", "INTEGER")])


def test_policy_tags_are_rejected_even_on_nested_fields():
    schema = [SchemaField("parent", "RECORD", fields=[
        SchemaField("secret", "INTEGER", policy_tags=PolicyTagList(names=["sensitive"])),
    ])]
    with pytest.raises(ValueError, match="policy-tagged"):
        profile_sql(("project", "dataset", "table"), schema)


def test_query_paths_preserve_spelling_and_ignore_ctes_and_ambiguous_names():
    connector = object.__new__(BigQueryConnector)
    connector.project = "Project"
    connector.default_dataset = "Data"
    assert query_paths(
        "WITH t AS (SELECT * FROM `Project.Data.Orders`) SELECT * FROM t",
        connector, ("project.data.orders",),
    ) == [("Project", "Data", "Orders")]
    assert query_paths(
        "SELECT * FROM `Project.Data.Orders` UNION ALL SELECT * FROM `Project.Data.orders`",
        connector, ("project.data.orders",),
    ) == []


def test_credential_fingerprint_tracks_key_and_identity(monkeypatch):
    def key():
        return rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ).decode()

    info = {"private_key": key(), "client_email": "first@example.test", "token_uri": "https://example.test/token"}
    monkeypatch.setattr(connections, "_service_account_info", lambda _: info)
    _, original = connections.resolve_profile_credentials(None)
    assert connections.resolve_profile_credentials(None)[1] == original
    info["client_email"] = "second@example.test"
    assert connections.resolve_profile_credentials(None)[1] != original
    info["client_email"] = "first@example.test"
    info["private_key"] = key()
    assert connections.resolve_profile_credentials(None)[1] != original
