"""Guard for user-supplied URLs the platform will call (e.g. an OpenAI-compatible LLM endpoint).

Without it a user could point the server at internal services or the cloud metadata server (SSRF).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from odn.config import settings


class UnsafeUrl(ValueError):
    pass


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
