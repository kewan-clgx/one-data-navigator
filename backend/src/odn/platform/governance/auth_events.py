"""Failed sign-in events for the organization's admin activity log (Organization tab).

Recorded as `auth.failed` in `admin_events` with the email, a reason code and the sign-in method; never the
password. A failure is only attributed to an organization whose verified domain or identity mapping matched,
so one tenant never sees another's attempts; unattributable failures go to the server log only. Refused tokens
are re-presented on every request, so a repeated failure is recorded at most once per window.
"""

from __future__ import annotations

import logging
import threading
import time
from uuid import UUID

from psycopg.types.json import Jsonb

from odn.db import tenant_transaction

log = logging.getLogger("odn.auth")

REPEAT_WINDOW_S = 600
_MAX_KEYS = 10_000
_recent: dict[tuple[str, str, str], float] = {}
_lock = threading.Lock()


def _first_in_window(key: tuple[str, str, str]) -> bool:
    now = time.monotonic()
    with _lock:
        if len(_recent) >= _MAX_KEYS:
            for k in [k for k, t in _recent.items() if now - t > REPEAT_WINDOW_S]:
                del _recent[k]
        last = _recent.get(key)
        if last is not None and now - last < REPEAT_WINDOW_S:
            return False
        _recent[key] = now
        return True


def record_auth_failure(
    tenant_id: UUID | None, email: str | None, reason: str, method: str, *, dedupe: bool = True
) -> None:
    """Best effort: a logging problem must never change the sign-in outcome."""
    email = (email or "").strip().lower()[:320] or None
    log.warning("sign-in refused: reason=%s method=%s tenant=%s", reason, method, tenant_id or "-")
    if tenant_id is None:
        return
    if dedupe and not _first_in_window((str(tenant_id), email or "", reason)):
        return
    try:
        with tenant_transaction(tenant_id) as conn:
            conn.execute(
                "INSERT INTO admin_events (tenant_id, action, target, detail) VALUES (%s, 'auth.failed', %s, %s)",
                (tenant_id, email, Jsonb({"reason": reason, "method": method})),
            )
    except Exception:
        log.exception("could not record a failed sign-in")
