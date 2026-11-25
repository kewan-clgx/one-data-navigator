import asyncio

import pytest

from odn.platform.oracle_proxy import OracleProxy


@pytest.mark.asyncio
async def test_proxy_rejects_non_connect_and_blocked_destinations():
    proxy = OracleProxy("127.0.0.1:2484", "0.0.0.0/0")
    async with await asyncio.start_server(proxy.handle, "127.0.0.1", 0, limit=8192) as server:
        port = server.sockets[0].getsockname()[1]
        for request, status in (
            (b"GET / HTTP/1.1\r\n\r\n", b"400"),
            (b"CONNECT 127.0.0.1:2484 HTTP/1.1\r\n\r\n", b"403"),
            (b"CONNECT other.example.com:2484 HTTP/1.1\r\n\r\n", b"403"),
            (b"CONNECT ::1:2484 HTTP/1.1\r\n\r\n", b"403"),
        ):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(request)
            await writer.drain()
            assert status in await asyncio.wait_for(reader.read(), timeout=2)
            writer.close()
            await writer.wait_closed()
    assert proxy.active == 0


@pytest.mark.asyncio
async def test_proxy_pins_approved_target_and_relays_without_tls_termination(monkeypatch):
    async def echo(reader, writer):
        try:
            writer.write(await reader.read(100))
            await writer.drain()
        finally:
            writer.close()

    async with await asyncio.start_server(echo, "127.0.0.1", 0) as source:
        source_port = source.sockets[0].getsockname()[1]
        targets = []

        def approve(host, port, *args):
            targets.append((host, port))
            return ["127.0.0.1"]

        monkeypatch.setattr("odn.platform.oracle_proxy.approved_addresses", approve)
        proxy = OracleProxy("oracle.example.com", "test-only")
        async with await asyncio.start_server(proxy.handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"CONNECT oracle.example.com:{source_port} HTTP/1.1\r\n\r\n".encode())
            await writer.drain()
            assert b"200" in await reader.readuntil(b"\r\n\r\n")
            writer.write(b"opaque-tls-bytes")
            await writer.drain()
            assert await reader.readexactly(16) == b"opaque-tls-bytes"
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0)
        assert targets == [("oracle.example.com", source_port)]
