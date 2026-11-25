"""Restricted CONNECT tunnels. Oracle TLS stays end-to-end; every target is approved and DNS-pinned."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from urllib.parse import urlsplit

from odn.platform.egress import DestinationError, approved_addresses

log = logging.getLogger(__name__)
_MAX_TUNNELS = 64


async def _reply(writer: asyncio.StreamWriter, code: int, reason: str) -> None:
    writer.write(f"HTTP/1.1 {code} {reason}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode("ascii"))
    await writer.drain()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()


class OracleProxy:
    def __init__(self, endpoints: str, cidrs: str) -> None:
        self.endpoints, self.cidrs = endpoints, cidrs
        self.active = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.active >= _MAX_TUNNELS:
            await _reply(writer, 503, "Busy")
            writer.close()
            return
        self.active += 1
        upstream: asyncio.StreamWriter | None = None
        tunnel = False
        try:
            async with asyncio.timeout(5):
                headers = await reader.readuntil(b"\r\n\r\n")
            if len(headers) > 8192:
                raise ValueError("oversized CONNECT request")
            method, target, version = headers.split(b"\r\n", 1)[0].decode("ascii").split(" ")
            if method != "CONNECT" or version not in ("HTTP/1.0", "HTTP/1.1"):
                raise ValueError("CONNECT required")
            # python-oracledb formats numeric IPv6 CONNECT targets without brackets.
            if target.count(":") > 1 and not target.startswith("["):
                host, _, port_text = target.rpartition(":")
                ipaddress.IPv6Address(host)
                target = f"[{host}]:{port_text}"
            parts = urlsplit("//" + target)
            if not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
                raise ValueError("invalid CONNECT destination")
            port = parts.port
            if port is None:
                raise ValueError("explicit port required")
            async with asyncio.timeout(10):
                addresses = await asyncio.to_thread(
                    approved_addresses, parts.hostname, port, self.endpoints, self.cidrs, "Oracle"
                )
                for address in addresses:
                    sock = socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM)
                    sock.setblocking(False)
                    try:
                        await asyncio.get_running_loop().sock_connect(sock, (address, port))
                        remote, upstream = await asyncio.open_connection(sock=sock)
                    except OSError:
                        sock.close()
                        continue
                    except BaseException:
                        sock.close()
                        raise
                    break
                else:
                    raise OSError("destination unreachable")
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            tunnel = True
            tasks = [asyncio.create_task(_pipe(reader, upstream)), asyncio.create_task(_pipe(remote, writer))]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except DestinationError:
            log.warning("Oracle tunnel destination denied")
            await _reply(writer, 403, "Destination denied")
        except (ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            log.warning("Invalid Oracle CONNECT request")
            await _reply(writer, 400, "Invalid CONNECT request")
        except (OSError, TimeoutError):
            log.warning("Oracle tunnel network failure")
            if not tunnel:
                try:
                    await _reply(writer, 502, "Tunnel unavailable")
                except OSError:
                    log.warning("Oracle tunnel client disconnected")
        finally:
            self.active -= 1
            if upstream is not None:
                upstream.close()
            writer.close()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    proxy = OracleProxy(
        os.environ.get("ODN_ORACLE_ALLOWED_ENDPOINTS", ""), os.environ.get("ODN_ORACLE_ALLOWED_CIDRS", "")
    )
    server = await asyncio.start_server(proxy.handle, "0.0.0.0", 8080, limit=8192)
    log.info("Oracle egress proxy listening")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
