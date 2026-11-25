"""Destination guards for user-supplied URLs and operator-approved MySQL endpoints.

Without it a user could point the server at internal services or the cloud metadata server (SSRF).
"""

from __future__ import annotations

import ipaddress
import socket
from time import monotonic
from urllib.parse import urlsplit

from odn.config import settings
from odn.platform.egress import DestinationError, approved_addresses, canonical_host


class UnsafeUrl(ValueError):
    pass


def mysql_host(host: str) -> str:
    try:
        return canonical_host(host)
    except ValueError:
        raise ValueError("invalid MySQL host; use a hostname or IP address without a URL or port") from None


def mysql_socket(host: str, port: int, timeout: int = 10) -> socket.socket:
    """Resolve once, approve every address, then pin TCP while the driver retains the TLS hostname."""
    from odn.platform.connectors.base import ConnectorError

    try:
        addresses = approved_addresses(
            mysql_host(host), port, settings.mysql_allowed_endpoints, settings.mysql_allowed_cidrs, "MySQL"
        )
    except DestinationError as exc:
        raise ConnectorError(str(exc)) from None
    deadline = monotonic() + timeout
    for address in addresses:
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        try:
            return socket.create_connection((address, port), timeout=remaining)
        except OSError:
            continue
    raise ConnectorError("MySQL network connection failed; check approved endpoint reachability")


def check_public_url(url: str) -> str:
    """Return the normalized URL if it is https and every resolved address is public; else raise UnsafeUrl.

    Local development (ODN_ENV=local) also allows http and private addresses (e.g. a corporate proxy).
    """
    url = url.strip()
    parts = urlsplit(url)
    local = settings.env == "local"
    if parts.scheme not in (("https", "http") if local else ("https",)):
        raise UnsafeUrl("the endpoint must use https")
    if not parts.hostname or parts.username or parts.password:
        raise UnsafeUrl("the endpoint must be a plain https://host/path URL")
    if parts.query or parts.fragment:
        raise UnsafeUrl("the endpoint must not contain a query or fragment")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        raise UnsafeUrl("the endpoint host cannot be resolved") from None
    if not local:
        for info in infos:
            addr = ipaddress.ip_address(info[4][0])
            if not addr.is_global or addr.is_multicast:
                raise UnsafeUrl("the endpoint must resolve to a public address")
    return url.rstrip("/")
