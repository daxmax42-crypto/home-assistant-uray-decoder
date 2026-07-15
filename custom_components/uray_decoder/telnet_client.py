"""Clean-shell telnet client for the Uray UHCVD265-1-4K decoder.

CRITICAL: the Uray shell is CLEAN. Unlike the ISEEVY decoder, the Uray telnet session
does NOT auto-launch a console-grabbing app, does NOT flood /dev/console, and requires
NO CTRL-C x2 breakout. You log in with CRLF (root/unisheen) and commands return
immediately. Porting the ISEEVY shell-break + sentinel-verify dance here would be wrong
and is intentionally omitted. (See embedded-device-telnet-exploration §1.1 exception note
and the Uray troubleshooting manifesto: "Telnet root/unisheen, CRLF, clean shell".)

Used only as a GROUND-TRUTH cross-check (read /box/box.ini) — never for writes; the
HTTP /set_playlist API is the safe control surface and applies live, no reboot.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)


class UrayTelnetError(OSError):
    """Raised when the telnet session fails (subclass OSError so api layer maps it)."""


class UrayTelnetClient:
    """Minimal async raw-socket telnet for the Uray decoder (clean shell)."""

    def __init__(
        self,
        host: str,
        username: str = "root",
        password: str = "unisheen",
        port: int = 23,
        timeout: int = 15,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=self.timeout
        )
        # HiLinux rejects bare-LF logins -> send CRLF.
        await asyncio.sleep(0.5)
        await self._send_line(self.username)
        await asyncio.sleep(0.5)
        await self._send_line(self.password)
        await asyncio.sleep(1.0)
        # No CTRL-C break needed. The shell prompt ('~ # ') is the ready signal.
        # Flush any buffered login banner / telnet IAC negotiation bytes so run()
        # starts from a clean buffer (otherwise the first command read captures the
        # stale login banner instead of the command output).
        await self._flush()

    async def _flush(self) -> None:
        """Drain all pending bytes from the reader without blocking."""
        try:
            while True:
                data = await asyncio.wait_for(self._reader.read(4096), timeout=0.3)
                if not data:
                    break
        except (asyncio.TimeoutError, OSError):  # noqa: BLE001 - flush is best-effort
            return

    async def _send_line(self, text: str) -> None:
        if self._writer is None:
            return
        self._writer.write((text + "\r\n").encode())
        await self._writer.drain()

    async def _read_until(self, needle: bytes, timeout: float = 10.0) -> str:
        """Read until we see `needle` (e.g. b'~ # ') or timeout. Returns captured text."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        buf = bytearray()
        while loop.time() < deadline:
            try:
                chunk = await asyncio.wait_for(
                    self._reader.read(4096), timeout=deadline - loop.time()
                )
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            buf.extend(chunk)
            if needle in buf:
                break
        return buf.decode("latin-1", "replace")

    async def run(self, cmd: str, prompt: bytes = b"~ # ") -> str:
        """Run one command and return the captured output (echo + prompt stripped).

        Captures the raw buffer, keeps everything up to the LAST prompt we see (the one
        that follows the command output), then drops the echoed command line and any
        trailing prompt line. Tolerant of the prompt appearing mid-buffer.
        """
        await self._send_line(cmd)
        out = await self._read_until(prompt, timeout=10.0)
        # Keep only the segment before the final prompt occurrence.
        idx = out.rfind(prompt.decode("latin-1", "replace"))
        if idx != -1:
            out = out[:idx]
        lines = out.split("\n")
        # Drop a leading echoed command line.
        if lines and lines[0].strip() == cmd.strip():
            lines = lines[1:]
        # Drop any leading/trailing blank lines.
        while lines and not lines[0].strip():
            lines = lines[1:]
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        return "\n".join(lines).strip()

    async def read_box_ini(self) -> dict[str, Any]:
        """Read /box/box.ini (LIVE config) and return a flat dict of key=value."""
        raw = await self.run("cat /box/box.ini")
        result: dict[str, Any] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")) or "=" not in line:
                continue
            if not line[0].isalpha():
                continue
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
        return result

    async def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.write(b"exit\r\n")
                await self._writer.drain()
            except Exception:
                pass
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
        self._reader = None
        self._writer = None
