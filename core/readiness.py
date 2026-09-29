"""Minimal Railway-compatible readiness server.

Runs inside the bot/worker asyncio process. It exposes no application data and
only returns 200 when the supplied async readiness probe succeeds.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

Probe = Callable[[], Awaitable[tuple[bool, str]]]


async def start_readiness_server(probe: Probe, host: str = "0.0.0.0", port: int = 8080):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        status = 503
        body = {"ok": False, "service": "social-saver"}
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=2)
            path = line.decode("ascii", errors="ignore").split(" ")[1] if b" " in line else "/"
            # Consume a bounded amount of headers so clients can cleanly reuse/close.
            for _ in range(32):
                header = await asyncio.wait_for(reader.readline(), timeout=2)
                if header in (b"\r\n", b"\n", b""):
                    break
            if path == "/health":
                ok, detail = await asyncio.wait_for(probe(), timeout=3)
                status = 200 if ok else 503
                body.update(ok=ok, detail=detail)
            else:
                status = 404
                body = {"ok": False, "error": "not_found"}
        except Exception:
            status = 503
            body = {"ok": False, "service": "social-saver", "detail": "probe_failed"}
        payload = json.dumps(body, separators=(",", ":")).encode()
        reason = {200: "OK", 404: "Not Found", 503: "Service Unavailable"}[status]
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
            + payload
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    return await asyncio.start_server(handle, host, port)
