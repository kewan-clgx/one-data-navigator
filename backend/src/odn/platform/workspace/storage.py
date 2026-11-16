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


def check_access() -> None:
    """Raises unless objects under the workspace prefix can be listed (bucket-level IAM is not required)."""
    next(iter(gcs_client().list_blobs(settings.gcs_bucket, prefix=workspace_prefix(""), max_results=1)), None)
