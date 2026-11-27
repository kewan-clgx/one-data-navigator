"""Execute a claimed refresh without holding a metadata transaction during source calls."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal
from uuid import UUID

import psycopg
from google.api_core import exceptions as gexc
from google.auth import exceptions as auth_exc

from odn.platform.catalog import metadata_collect, metadata_queue, metadata_store
from odn.platform.catalog.metadata_queue import RefreshJob, RefreshNotAuthorized
from odn.platform.catalog.metadata_store import RefreshLeaseLost
from odn.platform.connections.service import CredentialError, build_connector, build_policy, get_connection
from odn.platform.connectors.base import ConnectorError

log = logging.getLogger(__name__)
_SOURCE_ERRORS = (ConnectorError, gexc.GoogleAPIError, auth_exc.GoogleAuthError, psycopg.Error, ValueError, OSError)
_HEARTBEAT_SECONDS = 30


@contextmanager
def _keep_lease(tenant_id: UUID, job: RefreshJob) -> Iterator[threading.Event]:
    stop, lost = threading.Event(), threading.Event()

    def renew() -> None:
        while not stop.wait(_HEARTBEAT_SECONDS):
            try:
                if not metadata_queue.heartbeat(tenant_id, job):
                    lost.set()
                    return
            except Exception as exc:  # noqa: BLE001 - signal the worker and never continue with an uncertain lease
                log.error("metadata lease renewal failed: %s", type(exc).__name__)
                lost.set()
                return

    thread = threading.Thread(target=renew, name="metadata-heartbeat")
    thread.start()
    try:
        yield lost
    finally:
        stop.set()
        thread.join()


def _fail(tenant_id: UUID, job: RefreshJob, reason: Literal["source_error", "incomplete", "credential_error"]) -> None:
    if not metadata_queue.fail(tenant_id, job, reason):
        raise RefreshLeaseLost("metadata refresh could not record failure because its lease changed")


def run_job(tenant_id: UUID, actor_id: UUID, connection_id: UUID, job_id: UUID) -> Literal[
    "not_claimed", "succeeded", "partial", "failed"
]:
    """Run one tenant job. A separate scheduler must discover and dispatch authorized jobs."""
    job = metadata_queue.claim(tenant_id, actor_id, connection_id, job_id)
    if job is None:
        return "not_claimed"
    with _keep_lease(tenant_id, job) as lost:
        try:
            profile = get_connection(tenant_id, actor_id, connection_id)
            if profile is None:
                raise RefreshNotAuthorized("connection is no longer visible")
            # Mutable references can rotate without changing the connection revision.
            if profile.secret_ref and not re.search(r"/versions/[0-9]+$", profile.secret_ref):
                _fail(tenant_id, job, "credential_error")
                return "failed"
            try:
                connector = build_connector(profile)
                policy = build_policy(profile, connector)
                roots = metadata_collect.collect_containers(connector, policy)
            except (CredentialError, *_SOURCE_ERRORS) as exc:
                log.warning("metadata root collection failed: %s", type(exc).__name__)
                _fail(tenant_id, job, "credential_error" if isinstance(exc, CredentialError) else "source_error")
                return "failed"
            if lost.is_set():
                raise RefreshLeaseLost("metadata refresh heartbeat lost")
            if not metadata_queue.set_progress(tenant_id, job, 0, len(roots)):
                raise RefreshLeaseLost("metadata refresh could not record progress")
            failures: set[tuple[str, ...]] = set()
            for done, root in enumerate(roots, start=1):
                if lost.is_set():
                    raise RefreshLeaseLost("metadata refresh heartbeat lost")
                try:
                    container = metadata_collect.collect_container(connector, policy, root)
                except _SOURCE_ERRORS as exc:
                    log.warning("metadata container collection failed: %s", type(exc).__name__)
                    failures.add(root.path)
                else:
                    if lost.is_set():
                        raise RefreshLeaseLost("metadata refresh heartbeat lost")
                    metadata_store.publish_container(tenant_id, actor_id, connection_id, job.id, job.token, container)
                if lost.is_set() or not metadata_queue.set_progress(tenant_id, job, done, len(roots)):
                    raise RefreshLeaseLost("metadata refresh could not record progress")
            if lost.is_set():
                raise RefreshLeaseLost("metadata refresh heartbeat lost")
            metadata_store.finish_refresh(tenant_id, job, tuple(root.path for root in roots), frozenset(failures))
            return "partial" if failures else "succeeded"
        except (RefreshLeaseLost, RefreshNotAuthorized):
            raise
        except Exception as exc:
            log.error("metadata refresh failed: %s", type(exc).__name__)
            _fail(tenant_id, job, "source_error")
            raise
