"""Raw-socket HTTP client + clean-shell telnet client for the Uray UHCVD265-1-4K decoder.

Design notes (from embedded-device-truth-verification + the Uray runbook):
  * The Uray web server (box.d3_uni) returns valid HTTP but we still use a raw socket
    client so the component has ZERO third-party deps (manifest requirements: []).
  * The Uray telnet shell is CLEAN: no app-console grab, no CTRL-C x2 breakout, no
    console flood. Reads return immediately. (This is the OPPOSITE of ISEEVY, where a
    shell-break + sentinel verify was mandatory. Do NOT port the CTRL-C dance here.)
  * All writes go through the live HTTP API (/set_playlist, /set_*). No /set.cgi-style
    corruption surface exists on this device, but we still never hand-edit /box/box.ini
    while the app runs — the HTTP API is the safe control surface.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any
from xml.etree import ElementTree as ET

from .const import (
    ENDPOINT_GET_PLAYLIST,
    ENDPOINT_GET_STATUS,
    ENDPOINT_SET_PLAYLIST,
    WINDOW_COUNT,
)
from .telnet_client import UrayTelnetClient

_LOGGER = logging.getLogger(__name__)


class UrayAPIError(Exception):
    """Base error for Uray API."""


class UrayAuthError(UrayAPIError):
    """Authentication failed."""


class UrayConnectionError(UrayAPIError):
    """Connection failed."""


async def _raw_http(
    host: str,
    port: int,
    path: str,
    username: str,
    password: str,
    timeout: int = 10,
    method: str = "GET",
) -> tuple[int, bytes]:
    """Lenient raw-socket HTTP request. Returns (status_code, body_bytes).

    Handles both Content-Length and Transfer-Encoding: chunked responses. Strips
    leading chunk-size artifacts and the trailing chunk terminator so the XML parser
    never chokes on embedded ``^<hex>\\r\\n`` sequences.
    """
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )
    try:
        auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        # box.d3_uni accepts Basic auth; Authorization header must carry real creds.
        request = (
            f"{method} {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Authorization: Basic {auth}\r\n"
            f"Connection: close\r\n"
            f"User-Agent: HomeAssistant-Uray/1.0.0\r\n"
            f"\r\n"
        )
        writer.write(request.encode())
        await writer.drain()

        status_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not status_line:
            raise UrayConnectionError("Empty response from decoder")
        status_line = status_line.decode("latin-1", "replace").strip()
        parts = status_line.split(" ", 2)
        if len(parts) < 2:
            raise UrayAPIError(f"Invalid status line: {status_line!r}")
        status_code = int(parts[1])

        headers: dict[str, str] = {}
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not line or line == b"\r\n":
                break
            line = line.decode("latin-1", "replace").rstrip("\r\n")
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        body = b""
        if "chunked" in headers.get("transfer-encoding", "").lower():
            while True:
                chunk_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
                if not chunk_line:
                    break
                chunk_line = chunk_line.decode("latin-1", "replace").strip()
                if not chunk_line:
                    continue
                try:
                    chunk_size = int(chunk_line, 16)
                except ValueError:
                    break
                if chunk_size == 0:
                    await reader.readline()  # trailing CRLF
                    break
                body += await asyncio.wait_for(
                    reader.readexactly(chunk_size), timeout=timeout
                )
                await reader.readline()  # CRLF after chunk
        elif headers.get("content-length"):
            try:
                length = int(headers["content-length"])
                body = await asyncio.wait_for(
                    reader.readexactly(length), timeout=timeout
                )
            except (ValueError, asyncio.IncompleteReadError):
                body = await reader.read()
        else:
            body = await reader.read()
        return status_code, body
    finally:
        writer.close()
        await writer.wait_closed()


def _strip_chunk_artifacts(text: str) -> str:
    text = re.sub(r"^[0-9a-fA-F]+\r\n", "", text.strip())
    text = re.sub(r"\r\n0\r\n$", "", text)
    return text.strip()


class UrayClient:
    """HTTP client for the Uray quad decoder.

    The telnet client is retained for safe read-back verification of writes (per the
    truth-verification skill: confirm a change with an independent source, not just the
    API 'succeed' string). Telnet is NOT used for writes here (the HTTP API is safer and
    applies live) but it is the ground-truth cross-check.
    """

    def __init__(
        self,
        host: str,
        username: str = "admin",
        password: str = "admin",
        port: int = 8080,
        telnet_username: str = "root",
        telnet_password: str = "unisheen",
        timeout: int = 10,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self.telnet = UrayTelnetClient(host, telnet_username, telnet_password)
        # Serialize telnet access (single console on the device).
        self._telnet_lock = asyncio.Lock()

    async def close(self) -> None:
        """No persistent HTTP session in the raw-socket client; provided for parity."""
        return None

    # ---- low level ----
    async def _request(self, endpoint: str, params: dict[str, Any] | None = None) -> str:
        path = endpoint
        if params:
            query = "&".join(
                f"{k}={_urlencode(str(v))}" for k, v in params.items()
            )
            path = f"{endpoint}?{query}"
        try:
            status, body = await _raw_http(
                self.host, self.port, path, self.username, self.password, self.timeout
            )
        except asyncio.TimeoutError as err:
            raise UrayConnectionError(f"Timeout: {err}") from err
        except (ConnectionError, OSError) as err:
            raise UrayConnectionError(f"Connection failed: {err}") from err

        if status == 401:
            raise UrayAuthError("Authentication failed (HTTP 401)")
        if status >= 400:
            raise UrayAPIError(f"HTTP {status}")
        return body.decode("utf-8", "replace")

    # ---- status ----
    async def get_status(self) -> dict[str, Any]:
        """GET /get_status -> rich dict (per-stream alive/fps/bps + device health)."""
        raw = await self._request(ENDPOINT_GET_STATUS)
        return _parse_status(raw)

    async def get_playlist(self) -> dict[str, Any]:
        """GET /get_playlist -> {wnd, channels:[{uri,audio,cache,pindex,record}]}."""
        raw = await self._request(ENDPOINT_GET_PLAYLIST)
        return _parse_playlist(raw)

    # ---- writes (scene switch) ----
    async def set_scene(self, wnd: int, channels: list[dict[str, Any]]) -> bool:
        """Apply a scene via /set_playlist (live, persists, no reboot).

        channels: list of WINDOW_COUNT dicts with keys:
            uri (str), audio (0|1), pindex (int), cache (int), record (0|1)
        Slots beyond the provided list are left empty (single-view case).
        """
        if wnd not in (1, 4):
            raise ValueError("wnd must be 1 or 4")
        params: dict[str, Any] = {"wnd": wnd}
        for i in range(WINDOW_COUNT):
            ch = channels[i] if i < len(channels) else {}
            uri = ch.get("uri", "")
            params[f"uri{i}"] = uri
            params[f"uri{i}_audio"] = int(ch.get("audio", 0))
            params[f"uri{i}_pindex"] = int(ch.get("pindex", 0))
            params[f"uri{i}_cache"] = int(ch.get("cache", 10))
            params[f"uri{i}_record"] = int(ch.get("record", 0))
            params[f"uri{i}_recordtime"] = int(ch.get("recordtime", 1800))
        await self._request(ENDPOINT_SET_PLAYLIST, params)
        return True

    # ---- ground-truth cross-check (telnet) ----
    async def verify_playlist_via_telnet(self) -> dict[str, Any] | None:
        """Read /box/box.ini (live on-disk config) and return its rul*/wndnum.

        Used as an INDEPENDENT confirmation that a set_playlist actually persisted —
        per embedded-device-truth-verification, the on-disk file is a separate source
        from the HTTP 'succeed' string. Returns None on telnet failure (non-fatal).
        """
        async with self._telnet_lock:
            try:
                await self.telnet.connect()
                try:
                    return await self.telnet.read_box_ini()
                finally:
                    await self.telnet.close()
            except OSError as err:
                _LOGGER.debug("Telnet verify skipped: %s", err)
                return None


def _urlencode(s: str) -> str:
    """Minimal percent-encode (no external deps). Mirrors encodeURIComponent closely."""
    import urllib.parse

    return urllib.parse.quote(s, safe="")


def _parse_status(xml_text: str) -> dict[str, Any]:
    """Parse /get_status XML into a flat dict."""
    xml_text = _strip_chunk_artifacts(xml_text)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as err:
        raise UrayAPIError(f"Invalid status XML: {err}") from err

    def text_of(tag: str) -> str | None:
        el = root.find(tag)
        return el.text if el is not None else None

    streams = []
    for i in range(WINDOW_COUNT):
        s = root.find(f"s{i}")
        if s is None:
            continue
        ch: dict[str, Any] = {"index": i}
        for field in ("uri", "alive", "fps", "bps"):
            el = s.find(field)
            ch[field] = el.text if el is not None else None
        # normalize numeric fields
        if ch.get("alive") is not None:
            ch["alive"] = int(ch["alive"])
        if ch.get("fps") is not None:
            ch["fps"] = int(ch["fps"])
        if ch.get("bps") is not None:
            ch["bps"] = int(ch["bps"])
        streams.append(ch)

    return {
        "firmware_version": text_of("version"),
        "module": text_of("module"),
        "cpu_usage": _to_int(text_of("cpu_usage")),
        "mem_free": _to_int(text_of("mem_free")),
        "mem_total": _to_int(text_of("mem_total")),
        "runtime": text_of("runtime"),
        "net_status": text_of("net_status"),
        "vo": text_of("vo"),
        "wndnum": _to_int(text_of("wndnum")),
        "streams": streams,
    }


def _parse_playlist(xml_text: str) -> dict[str, Any]:
    """Parse /get_playlist XML into {wnd, channels:[...]}."""
    xml_text = _strip_chunk_artifacts(xml_text)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as err:
        raise UrayAPIError(f"Invalid playlist XML: {err}") from err

    wnd = root.get("wnd")
    channels = []
    for i in range(WINDOW_COUNT):
        node = root.find(f"uri{i}")
        if node is None:
            channels.append({"uri": "", "audio": 0, "pindex": 0, "cache": 10, "record": 0})
            continue
        channels.append(
            {
                "uri": node.text or "",
                "audio": int(node.get("audio", "0")),
                "pindex": int(node.get("pindex", "0")),
                "cache": int(node.get("cache", "10")),
                "record": int(node.get("record", "0")),
            }
        )
    return {"wnd": _to_int(wnd) or 4, "channels": channels}


def _to_int(v: str | None) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None
