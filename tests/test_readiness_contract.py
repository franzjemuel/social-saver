import asyncio
import json
from core.readiness import start_readiness_server


def request(port, path="/health"):
    async def _go():
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
        await writer.drain()
        raw = await reader.read()
        writer.close(); await writer.wait_closed()
        return raw
    return _go()


def test_readiness_200():
    async def run():
        async def probe(): return True, "database_ready"
        server = await start_readiness_server(probe, host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        raw = await request(port)
        server.close(); await server.wait_closed()
        assert b"200 OK" in raw
        assert json.loads(raw.split(b"\r\n\r\n",1)[1])["ok"] is True
    asyncio.run(run())


def test_readiness_503():
    async def run():
        async def probe(): return False, "database_unavailable"
        server = await start_readiness_server(probe, host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        raw = await request(port)
        server.close(); await server.wait_closed()
        assert b"503 Service Unavailable" in raw
    asyncio.run(run())


def test_readiness_does_not_expose_other_paths():
    async def run():
        async def probe(): return True, "database_ready"
        server = await start_readiness_server(probe, host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        raw = await request(port, "/status")
        server.close(); await server.wait_closed()
        assert b"404 Not Found" in raw
    asyncio.run(run())
