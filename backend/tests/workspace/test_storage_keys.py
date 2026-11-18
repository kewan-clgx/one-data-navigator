import uuid

import pytest

from odn.platform.workspace.storage import assert_tenant_key, tenant_key

T, U = uuid.uuid4(), uuid.uuid4()


def test_own_keys_pass() -> None:
    key = tenant_key(T, U, "sess", "q1.parquet")
    assert assert_tenant_key(T, U, key) == key


@pytest.mark.parametrize(
    "key",
    [
        tenant_key(uuid.uuid4(), U, "sess", "q1.parquet"),  # another tenant
        tenant_key(T, uuid.uuid4(), "sess", "q1.parquet"),  # another user
        tenant_key(T, U) ,  # the workspace root itself
        tenant_key(T, U) + "/../" + str(uuid.uuid4()) + "/x.parquet",
        tenant_key(T, U) + "//x.parquet",
        f"other-prefix/{T}/{U}/sess/q1.parquet",
        f"{tenant_key(T, U)}x/sess/q1.parquet",  # prefix match without the separator
    ],
)
def test_foreign_or_malformed_keys_are_refused(key: str) -> None:
    with pytest.raises(PermissionError):
        assert_tenant_key(T, U, key)
