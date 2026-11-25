"""Standard-library-only destination approval shared by database transports."""

from __future__ import annotations

import ipaddress
import re
import socket


class DestinationError(ValueError):
    pass


def canonical_host(host: str) -> str:
    host = host.lower().rstrip(".")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if len(host) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")
        ):
            raise ValueError("use a hostname or IP address without a URL or port") from None
    return host


def approved_addresses(host: str, port: int, endpoints: str, cidrs: str, label: str) -> list[str]:
    host = canonical_host(host)
    if type(port) is not int or not 1 <= port <= 65535:
        raise DestinationError(f"{label} port must be 1-65535")
    endpoint = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    if endpoint not in {entry.strip().lower() for entry in endpoints.split(",") if entry.strip()}:
        raise DestinationError(f"{label} endpoint is not approved; ask an operator to approve the hostname and port")
    try:
        networks = [ipaddress.ip_network(entry.strip()) for entry in cidrs.split(",") if entry.strip()]
    except ValueError:
        raise DestinationError(f"operator {label} destination CIDRs are invalid") from None
    if not networks:
        raise DestinationError(f"{label} destination IP ranges are not approved; contact an operator")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise DestinationError(f"{label} hostname could not be resolved") from None
    addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addresses:
        raise DestinationError(f"{label} hostname resolved to no addresses")
    for address in addresses:
        addr = ipaddress.ip_address(address)
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        if (
            addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified
            or addr.is_reserved or not any(addr in network for network in networks)
        ):
            raise DestinationError(
                f"{label} hostname resolves to a blocked or unapproved address; contact an operator"
            )
    return addresses
