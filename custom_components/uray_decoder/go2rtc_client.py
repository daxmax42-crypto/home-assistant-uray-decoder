"""Lightweight, pure client for a go2rtc REST API.

No Uray knowledge lives here. Discovers streams from /api/streams and builds the RTSP
restream URL the decoder should consume. Keeping this client pure lets it be unit-tested
and reused for Chromecast/WebRTC later (same stream names).

go2rtc API shape (observed live at 10.0.10.41:1984):
    GET /api/streams -> { "<stream_name>": {"producers":[{"url": "rtsp://.../..."}], "consumers": null}, ... }
The keys are the stream names; producers[0].url is the original camera source.
The decoder consumes the restream: rtsp://<rtsp_host>:<rtsp_port>/<stream_name>
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .const import DEFAULT_GO2RTC_URL, DEFAULT_RTSP_PORT


class Go2RtcClient:
    """Tiny HTTP client for go2rtc stream discovery + RTSP restream URL building."""

    def __init__(
        self,
        base_url: str = DEFAULT_GO2RTC_URL,
        username: str | None = None,
        password: str | None = None,
        rtsp_host: str | None = None,
        rtsp_port: int = DEFAULT_RTSP_PORT,
        http_get=None,
    ) -> None:
        """http_get, if supplied, is an async callable(url, auth) -> (status, text) used
        for testing without a real network (and so the component avoids importing aiohttp
        directly here). The coordinator wires the real raw-socket getter when None.
        """
        self.base_url = base_url.rstrip("/")
        self.rtsp_port = rtsp_port
        parsed = urlparse(self.base_url)
        self.rtsp_host = rtsp_host or parsed.hostname or "10.0.10.41"
        self._auth = None
        if username:
            self._auth = (username, password or "")
        self._http_get = http_get

    async def get_streams(self) -> dict[str, Any]:
        """Return {stream_name: info} from /api/streams."""
        url = f"{self.base_url}/api/streams"
        if self._http_get is not None:
            status, text = await self._http_get(url, self._auth)
        else:
            status, text = await _real_get(url, self._auth)
        if status != 200:
            raise Go2RtcError(f"go2rtc returned HTTP {status}")
        import json

        try:
            data = json.loads(text)
        except json.JSONDecodeError as err:
            raise Go2RtcError(f"Invalid go2rtc JSON: {err}") from err
        return data

    async def validate(self) -> bool:
        try:
            await self.get_streams()
            return True
        except Exception:
            return False

    def stream_names(self) -> list[str]:
        """Convenience: list cached stream names (caller supplies the cache)."""
        return []

    def build_rtsp_url(self, stream_name: str, credentials: str | None = None) -> str:
        """Build the restream RTSP URL for the decoder.

        credentials: optional "user:pass" to embed (the go2rtc restream may require auth
        depending on server config). Omit for open restreams.
        """
        authority = self.rtsp_host
        if credentials:
            authority = f"{credentials}@{authority}"
        return f"rtsp://{authority}:{self.rtsp_port}/{stream_name}"

    def producer_url(self, stream_name: str, streams: dict[str, Any]) -> str | None:
        """Return the producer (source camera) URL for a stream name, if known."""
        info = streams.get(stream_name)
        if not info:
            return None
        producers = info.get("producers") or []
        if producers and isinstance(producers[0], dict):
            return producers[0].get("url")
        return None


class Go2RtcError(Exception):
    """go2rtc client error."""


async def _real_get(url: str, auth) -> tuple[int, str]:
    """Real HTTP GET via the shared raw-socket helper (no aiohttp dependency).

    auth is a (user, pass) tuple or None. The coordinator passes a function that uses
    the component's lenient raw-socket client so we keep requirements: [].
    """
    # Imported lazily to avoid a hard dependency at import time in test stubs.
    from .go2rtc_transport import raw_get_json

    return await raw_get_json(url, auth)
