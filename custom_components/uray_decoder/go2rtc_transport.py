"""Real async HTTP GET helper for go2rtc (raw-socket, dependency-free).

go2rtc returns chunked-encoded JSON, so we reuse the same lenient chunked-body reader
used for the decoder (strip the leading ``^<hex>\r\n`` size lines and the trailing
``\r\n0\r\n`` terminator). No aiohttp dependency -> manifest requirements: [].
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Optional
from urllib.parse import urlparse


async def raw_get_json(
    url: str, auth: Optional[tuple[str, str]] = None
) -> tuple[int, str]:
    """GET a URL, return (status_code, body_text). auth = (user, pass) or None."""
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=10
    )
    try:
        auth_hdr = ""
        if auth:
            token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
            auth_hdr = f"Authorization: Basic {token}\r\n"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"{auth_hdr}"
            f"Connection: close\r\n"
            f"User-Agent: HomeAssistant-Uray/1.0.0\r\n"
            f"\r\n"
        )
        writer.write(request.encode())
        await writer.drain()

        status_line = await asyncio.wait_for(reader.readline(), timeout=10)
        if not status_line:
            return 0, ""
        status = int(status_line.decode("latin-1", "replace").split(" ", 2)[1])

        # Consume headers; detect chunked
        chunked = False
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not line or line == b"\r\n":
                break
            low = line.decode("latin-1", "replace").lower()
            if low.startswith("transfer-encoding:") and "chunked" in low:
                chunked = True

        body = b""
        if chunked:
            while True:
                chunk_line = await asyncio.wait_for(reader.readline(), timeout=10)
                if not chunk_line:
                    break
                chunk_line = chunk_line.decode("latin-1", "replace").strip()
                if not chunk_line:
                    continue
                try:
                    size = int(chunk_line, 16)
                except ValueError:
                    break
                if size == 0:
                    await reader.readline()  # trailing CRLF
                    break
                body += await asyncio.wait_for(reader.readexactly(size), timeout=10)
                await reader.readline()  # CRLF after chunk
        else:
            body = await reader.read()
        return status, body.decode("utf-8", "replace")
    finally:
        writer.close()
        await writer.wait_closed()
