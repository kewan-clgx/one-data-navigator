"""SSH bastion tunnels for private Postgres databases.

A tunnel connects to the bastion with the connection's private key, accepts the bastion only if its host key
matches the pinned SHA256 fingerprint (checked during the SSH handshake, before authentication), and
forwards a local port on 127.0.0.1 to the database host:port as seen from the bastion. Tunnels run on one
background event loop, are shared by identical specs, and close after sitting idle.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import threading
import time
from dataclasses import dataclass, field

import asyncssh

IDLE_CLOSE_S = 600
CONNECT_TIMEOUT_S = 15
# A bastion usually has several host keys; the admin may have pinned any one of them, so each algorithm is
# offered in turn until the presented key matches the pin.
HOST_KEY_ALGS = (
    "ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "rsa-sha2-512", "rsa-sha2-256",
)

_FP_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}=?$")


class TunnelError(Exception):
    """The tunnel could not be opened (message is safe to show)."""


def normalize_fingerprint(value: str) -> str:
    """Accept "SHA256:...", the bare base64 digest, or a public key line ("ssh-ed25519 AAAA..."); return SHA256:..."""
    value = (value or "").strip()
    if not value:
        raise ValueError("the bastion's host key fingerprint is required (SHA256:... or its public key)")
    if value.startswith(("ssh-", "ecdsa-")):
        try:
            return asyncssh.import_public_key(value).get_fingerprint("sha256")
        except (asyncssh.KeyImportError, ValueError):
            raise ValueError("the bastion public key could not be read") from None
    if not value.startswith("SHA256:"):
        value = f"SHA256:{value}"
    value = value.rstrip("=")
    if not _FP_RE.match(value):
        raise ValueError("the fingerprint must look like SHA256: followed by 43 base64 characters")
    return value


def check_private_key(text: str) -> None:
    try:
        asyncssh.import_private_key(text)
    except asyncssh.KeyImportError as exc:
        if "passphrase" in str(exc).lower():
            raise ValueError("passphrase-protected SSH keys are not supported; use a key without a passphrase") from None
        raise ValueError("the SSH private key could not be read (OpenSSH or PEM format)") from None


@dataclass(frozen=True)
class TunnelSpec:
    bastion_host: str
    bastion_port: int
    bastion_user: str
    host_key_fingerprint: str
    db_host: str
    db_port: int
    private_key: str = field(repr=False)

    def cache_key(self) -> str:
        raw = "\x00".join(
            (self.bastion_host, str(self.bastion_port), self.bastion_user, self.host_key_fingerprint,
             self.db_host, str(self.db_port), self.private_key)
        )
        return hashlib.sha256(raw.encode()).hexdigest()


class _PinnedHostKey(asyncssh.SSHClient):
    def __init__(self, expected: str) -> None:
        self.expected = expected
        self.seen: str | None = None

    def validate_host_public_key(self, host: str, addr: str, port: int, key: asyncssh.SSHKey) -> bool:
        self.seen = key.get_fingerprint("sha256")
        return hmac.compare_digest(self.seen, self.expected)


@dataclass
class _Tunnel:
    conn: asyncssh.SSHClientConnection
    listener: asyncssh.SSHListener
    port: int
    last_used: float


class TunnelPool:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._tunnels: dict[str, _Tunnel] = {}

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="odn-ssh-tunnels", daemon=True).start()
                asyncio.run_coroutine_threadsafe(self._reap_idle(), loop)
                self._loop = loop
            return self._loop

    def local_port(self, spec: TunnelSpec) -> int:
        """Open (or reuse) the tunnel for `spec`; returns the local port forwarding to the database."""
        future = asyncio.run_coroutine_threadsafe(self._open(spec), self._ensure_loop())
        try:
            return future.result(timeout=CONNECT_TIMEOUT_S + 5)
        except TimeoutError:
            future.cancel()
            raise TunnelError(f"timed out connecting to the bastion {spec.bastion_host}:{spec.bastion_port}") from None

    def close_all(self) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._close_all(), self._loop).result(timeout=10)

    async def _open(self, spec: TunnelSpec) -> int:
        key = spec.cache_key()
        tunnel = self._tunnels.get(key)
        if tunnel is not None and not tunnel.conn.is_closed():
            tunnel.last_used = time.monotonic()
            return tunnel.port
        if tunnel is not None:
            self._tunnels.pop(key, None)

        conn = await self._connect(spec)
        try:
            listener = await conn.forward_local_port("127.0.0.1", 0, spec.db_host, spec.db_port)
        except (OSError, asyncssh.Error):
            conn.close()
            raise TunnelError("the bastion refused port forwarding to the database") from None
        self._tunnels[key] = _Tunnel(conn, listener, listener.get_port(), time.monotonic())
        return listener.get_port()

    async def _connect(self, spec: TunnelSpec) -> asyncssh.SSHClientConnection:
        try:
            private_key = asyncssh.import_private_key(spec.private_key)
        except asyncssh.KeyImportError:
            raise TunnelError("the SSH private key could not be read") from None
        seen: list[str] = []
        for alg in HOST_KEY_ALGS:
            client = _PinnedHostKey(spec.host_key_fingerprint)
            try:
                conn, _ = await asyncio.wait_for(
                    asyncssh.create_connection(
                        lambda client=client: client,
                        spec.bastion_host,
                        port=spec.bastion_port,
                        username=spec.bastion_user,
                        client_keys=[private_key],
                        known_hosts=([], [], []),  # trust nothing by default: the pinned fingerprint decides
                        server_host_key_algs=[alg],
                        agent_path=None,
                        password=None,
                        keepalive_interval=30,
                    ),
                    timeout=CONNECT_TIMEOUT_S,
                )
                return conn
            except asyncssh.HostKeyNotVerifiable:
                if client.seen:
                    seen.append(client.seen)
            except asyncssh.PermissionDenied:
                # Authentication runs only after the host key matched.
                raise TunnelError("the bastion rejected the SSH user or private key") from None
            except (asyncssh.KeyExchangeFailed, asyncssh.ConnectionLost):
                continue  # no host key of this type: servers fail or drop the key exchange
            except (OSError, TimeoutError, asyncssh.Error) as exc:
                raise TunnelError(
                    f"could not reach the bastion {spec.bastion_host}:{spec.bastion_port} ({type(exc).__name__})"
                ) from None
        if not seen:
            raise TunnelError(f"could not reach the bastion {spec.bastion_host}:{spec.bastion_port} (no SSH host key offered)")
        raise TunnelError(
            "the bastion's host keys (" + ", ".join(dict.fromkeys(seen)) + ") do not match the pinned fingerprint"
        )

    async def _reap_idle(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for key, tunnel in list(self._tunnels.items()):
                if tunnel.conn.is_closed() or now - tunnel.last_used > IDLE_CLOSE_S:
                    self._tunnels.pop(key, None)
                    tunnel.listener.close()
                    tunnel.conn.close()

    async def _close_all(self) -> None:
        for tunnel in self._tunnels.values():
            tunnel.listener.close()
            tunnel.conn.close()
        self._tunnels.clear()


pool = TunnelPool()


def fingerprint_of_public_key(b64_blob: str) -> str:
    """SHA256 fingerprint of a raw base64 public-key blob (as in known_hosts)."""
    digest = hashlib.sha256(base64.b64decode(b64_blob)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")
