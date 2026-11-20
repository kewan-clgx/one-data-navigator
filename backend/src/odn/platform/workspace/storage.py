"""Result-workspace object storage (GCS). Credentials are separate from any warehouse connection's."""

from __future__ import annotations

import os
from functools import cache

from google.auth.credentials import AnonymousCredentials
from google.cloud import storage

from odn.config import settings


@cache
def gcs_client() -> storage.Client:
    if os.environ.get("STORAGE_EMULATOR_HOST"):
        return storage.Client(project="local", credentials=AnonymousCredentials())
    if settings.gcs_credentials_file:
        return storage.Client.from_service_account_json(settings.gcs_credentials_file)
    # Cloud Run: the service's own identity.
    return storage.Client()


def workspace_prefix(*parts: str) -> str:
    """Object key under the configured prefix, e.g. odn/<tenant>/<session>/<query>.parquet."""
    return "/".join(p.strip("/") for p in (settings.gcs_prefix, *parts) if p)


def tenant_key(tenant_id: object, user_id: object, *parts: str) -> str:
    """Object key inside one user's workspace: <prefix>/<tenant>/<user>/..."""
    return workspace_prefix(str(tenant_id), str(user_id), *parts)


def assert_tenant_key(tenant_id: object, user_id: object, key: str) -> str:
    """Refuse any object key outside the caller's own workspace (defence in depth: one bucket, one identity)."""
    prefix = tenant_key(tenant_id, user_id) + "/"
    if not key.startswith(prefix) or any(p in ("", ".", "..") for p in key[len(prefix):].split("/")):
        raise PermissionError("object key is outside the caller's workspace")
    return key


def share_key(tenant_id: object, share_id: str, *parts: str) -> str:
    """Object key of a shared conversation's copied artifacts: <prefix>/<tenant>/shares/<share>/...

    "shares" can't collide with a user directory (those are UUIDs)."""
    return workspace_prefix(str(tenant_id), "shares", share_id, *parts)


def assert_share_key(tenant_id: object, share_id: str, key: str) -> str:
    """Refuse any object key outside one share's copied artifacts."""
    prefix = share_key(tenant_id, share_id) + "/"
    if not key.startswith(prefix) or any(p in ("", ".", "..") for p in key[len(prefix):].split("/")):
        raise PermissionError("object key is outside the share")
    return key


def copy_object(src: str, dst: str) -> None:
    bucket = gcs_client().bucket(settings.gcs_bucket)
    bucket.copy_blob(bucket.blob(src), bucket, dst)


def check_access() -> None:
    """Raises unless objects under the workspace prefix can be listed (bucket-level IAM is not required)."""
    next(iter(gcs_client().list_blobs(settings.gcs_bucket, prefix=workspace_prefix(""), max_results=1)), None)
