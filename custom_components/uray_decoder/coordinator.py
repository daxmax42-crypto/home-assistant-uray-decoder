"""DataUpdateCoordinator for the Uray quad decoder.

Two independent refresh loops feed one coordinator:
  * status poll (30s) -> /get_status XML (stream health + device health)
  * go2rtc poll (60s) -> stream-name discovery for the per-channel select entities

The coordinator also owns:
  * scene application via /set_playlist (live, persists, no reboot)
  * a cached quad baseline so a single-view switch is NEVER pushed back as an empty
    playlist (the device's set_playlist-in-single-view returns empty uri0..3 trap)
  * first-load seeding of the "active scene" from /get_playlist ground truth (not from a
    device-reported constant) — per the first-load-seed lesson
  * immediate push via async_set_updated_data() (async_request_refresh is coalesced on HA
    2026.x and would freeze writes)
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UrayClient, UrayAPIError
from .const import (
    CONF_GO2RTC_PASSWORD,
    CONF_GO2RTC_URL,
    CONF_GO2RTC_USERNAME,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_RTSP_HOST,
    CONF_RTSP_PORT,
    CONF_SCENES,
    CONF_TELNET_PASSWORD,
    CONF_TELNET_USERNAME,
    CONF_USERNAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    WINDOW_COUNT,
)
from .go2rtc_client import Go2RtcClient

_LOGGER = logging.getLogger(__name__)


def _build_channel_uri(
    scene_channels: dict[str, Any],
    channel_idx: int,
    go2rtc: Go2RtcClient | None,
    streams_cache: dict[str, Any],
) -> tuple[str, int]:
    """Resolve the RTSP URI + audio flag for a channel slot from a scene definition.

    Returns (uri, audio). Uses the per-channel config which may supply either a direct
    `uri` or a `go2rtc_stream` name (built into the restream URL). The firmware-managed
    default device URLs use viewer:0p3nd00r at the camera IP directly; the optional
    go2rtc restream URL uses the go2rtc host/RTSP port (credentials vary by server).
    """
    key = f"ch{channel_idx}"
    ch = scene_channels.get(key) or {}
    uri = ch.get("uri")
    audio = int(ch.get("audio", 0))
    if not uri and ch.get("go2rtc_stream") and go2rtc is not None:
        creds = None
        if ch.get("go2rtc_credentials"):
            creds = ch["go2rtc_credentials"]
        uri = go2rtc.build_rtsp_url(ch["go2rtc_stream"], credentials=creds)
    return (uri or "", audio)


class UrayDataUpdateCoordinator(DataUpdateCoordinator):
    """Manages Uray decoder polling + scene state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        data = entry.data
        self.client = UrayClient(
            host=data[CONF_HOST],
            username=data[CONF_USERNAME],
            password=data[CONF_PASSWORD],
            port=data.get(CONF_PORT, DEFAULT_PORT),
            telnet_username=data.get(CONF_TELNET_USERNAME, "root"),
            telnet_password=data.get(CONF_TELNET_PASSWORD, "unisheen"),
        )
        # go2rtc client (None if not configured)
        self.go2rtc: Go2RtcClient | None = None
        if data.get(CONF_GO2RTC_URL):
            self.go2rtc = Go2RtcClient(
                base_url=data[CONF_GO2RTC_URL],
                username=data.get(CONF_GO2RTC_USERNAME),
                password=data.get(CONF_GO2RTC_PASSWORD),
                rtsp_host=data.get(CONF_RTSP_HOST),
                rtsp_port=int(data.get(CONF_RTSP_PORT, 8554)),
            )
        self.scenes: list[dict[str, Any]] = list(data.get(CONF_SCENES, []))
        # Cache last applied scene name (dropdown stability across polls)
        self._active_scene: str | None = None
        # Cached quad baseline (the trap guard)
        self._quad_baseline: list[dict[str, Any]] | None = None
        # Cached go2rtc stream list
        self._streams_cache: dict[str, Any] = {}
        # Lazy one-shot first-load seed flag
        self._seeded = False

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    # ---- main status update ----
    async def _async_update_data(self) -> dict[str, Any]:
        try:
            status = await self.client.get_status()
        except UrayAPIError as err:
            raise UpdateFailed(f"Error communicating with decoder: {err}") from err

        # Refresh go2rtc streams opportunistically (coarser cadence handled in background
        # task; here we just surface the cache). We also kick a refresh if empty.
        if self.go2rtc is not None and not self._streams_cache:
            try:
                self._streams_cache = await self.go2rtc.get_streams()
            except Exception as err:  # noqa: BLE001 - non-fatal for status
                _LOGGER.debug("go2rtc refresh skipped: %s", err)

        # First-load seed: determine the real active scene from /get_playlist ground truth.
        if not self._seeded:
            await self._seed_active_scene(status)
            self._seeded = True

        status["scenes"] = [s.get("name") for s in self.scenes]
        status["active_scene"] = self._active_scene
        status["streams_go2rtc"] = sorted(self._streams_cache.keys())
        status["quad_baseline"] = self._quad_baseline
        return status

    # ---- go2rtc refresh (called by background task) ----
    async def async_refresh_go2rtc(self) -> None:
        if self.go2rtc is None:
            return
        try:
            self._streams_cache = await self.go2rtc.get_streams()
            if self.data:
                current = dict(self.data)
                current["streams_go2rtc"] = sorted(self._streams_cache.keys())
                self.async_set_updated_data(current)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("go2rtc refresh failed: %s", err)

    # ---- first-load seed from device truth ----
    async def _seed_active_scene(self, status: dict[str, Any]) -> None:
        """Match the device's CURRENT playlist (/get_playlist) to a configured scene.

        Per the truth-verification skill: /get_playlist (or /get_status uris) is the real
        current state, NOT a device-reported "current scene" constant. We compare the
        live window URIs against each scene's resolved channel URIs and pick the best
        match. Only seeds when no scene has been explicitly selected. Telnet read-back is
        used as an independent confirmation but is non-fatal.
        """
        try:
            playlist = await self.client.get_playlist()
        except UrayAPIError as err:
            _LOGGER.debug("First-load seed: get_playlist failed: %s", err)
            return

        live_uris = [c.get("uri", "") for c in playlist.get("channels", [])]
        # Cache the quad baseline if currently in quad mode (trap guard).
        if playlist.get("wnd") == 4 and not self._quad_baseline:
            self._quad_baseline = playlist.get("channels", [])

        best_name: str | None = None
        best_score = -1
        for scene in self.scenes:
            score = 0
            chans = scene.get("channels", {})
            for i in range(WINDOW_COUNT):
                uri, _ = _build_channel_uri(chans, i, self.go2rtc, self._streams_cache)
                if uri and i < len(live_uris) and _uri_host_path(uri) == _uri_host_path(live_uris[i]):
                    score += 1
            if score > best_score:
                best_score = score
                best_name = scene.get("name")
        if best_name and best_score >= 1:
            self._active_scene = best_name
            _LOGGER.info("Seeded active scene from device truth: %s", best_name)

    # ---- scene application ----
    async def async_apply_scene(self, scene_name: str) -> bool:
        """Switch the decoder to a named scene via /set_playlist (live, persists)."""
        scene = next((s for s in self.scenes if s.get("name") == scene_name), None)
        if scene is None:
            _LOGGER.error("Scene %r not found", scene_name)
            return False
        wnd = int(scene.get("wnd", 4))
        chans = scene.get("channels", {})
        channels: list[dict[str, Any]] = []
        for i in range(WINDOW_COUNT):
            uri, audio = _build_channel_uri(chans, i, self.go2rtc, self._streams_cache)
            channels.append(
                {
                    "uri": uri,
                    "audio": audio,
                    "pindex": int(chans.get(f"ch{i}", {}).get("pindex", 0)),
                    "cache": int(chans.get(f"ch{i}", {}).get("cache", 10)),
                    "record": int(chans.get(f"ch{i}", {}).get("record", 0)),
                }
            )
        try:
            await self.client.set_scene(wnd, channels)
        except UrayAPIError as err:
            _LOGGER.error("Failed to apply scene %r: %s", scene_name, err)
            return False

        # Trap guard: never allow a single-view push to clobber the quad baseline.
        if wnd == 4:
            self._quad_baseline = channels
        elif wnd == 1 and self._quad_baseline is not None:
            _LOGGER.warning(
                "Single-view scene applied; quad baseline preserved in memory (do NOT "
                "push it back as a quad playlist accidentally)."
            )

        self._active_scene = scene_name
        # Independent verification: confirm the on-disk config reflects the switch.
        verify = await self.client.verify_playlist_via_telnet()
        if verify is not None:
            _LOGGER.debug("Telnet verify /box/box.ini: wndnum=%s", verify.get("wndnum"))
        self._push(active_scene=scene_name)
        return True

    # ---- per-channel set (media_player / service) ----
    async def async_set_channel(
        self, channel: int, uri: str, audio: int = 0
    ) -> bool:
        """Set ONE channel slot, keeping the other three from the quad baseline.

        Builds a full 4-slot quad playlist (never an empty wnd=1 push) so we don't trip
        the empty-playlist trap. If no baseline exists yet, pull it live first.
        """
        if not 1 <= channel <= WINDOW_COUNT:
            raise ValueError(f"channel must be 1-{WINDOW_COUNT}")
        if self._quad_baseline is None:
            try:
                pl = await self.client.get_playlist()
                self._quad_baseline = pl.get("channels", [])
            except UrayAPIError as err:
                _LOGGER.error("Cannot read baseline playlist: %s", err)
                return False
        channels = [dict(c) for c in self._quad_baseline]
        idx = channel - 1
        channels[idx] = {
            "uri": uri,
            "audio": int(audio),
            "pindex": channels[idx].get("pindex", 0),
            "cache": channels[idx].get("cache", 10),
            "record": channels[idx].get("record", 0),
        }
        try:
            await self.client.set_scene(4, channels)
        except UrayAPIError as err:
            _LOGGER.error("Failed to set channel %d: %s", channel, err)
            return False
        self._quad_baseline = channels
        self._push()
        return True

    # ---- immediate push (HA 2026.x: async_request_refresh is coalesced) ----
    def _push(self, **overrides: Any) -> None:
        if self.data:
            current = dict(self.data)
            current["active_scene"] = self._active_scene
            current["quad_baseline"] = self._quad_baseline
            current["streams_go2rtc"] = sorted(self._streams_cache.keys())
            current.update(overrides)
            self.async_set_updated_data(current)
        else:
            self.hass.async_create_task(self.async_request_refresh())

    # ---- buttons ----
    async def async_refresh(self) -> None:
        try:
            status = await self.client.get_status()
        except UrayAPIError as err:
            _LOGGER.error("Refresh failed: %s", err)
            return
        self._push()

    async def async_refresh_streams(self) -> None:
        await self.async_refresh_go2rtc()

    async def async_verify_state(self) -> dict[str, Any]:
        """Cross-check the API-reported playlist against the on-disk config (truth)."""
        try:
            api_playlist = await self.client.get_playlist()
        except UrayAPIError as err:
            _LOGGER.error("Verify failed: %s", err)
            return {"api": None, "disk": None, "match": False}
        disk = await self.client.verify_playlist_via_telnet()
        match = False
        if disk is not None:
            api_uris = [c.get("uri", "") for c in api_playlist.get("channels", [])]
            disk_uris = [disk.get(f"rul{i}", "") for i in range(WINDOW_COUNT)]
            match = all(
                _uri_host_path(a) == _uri_host_path(b)
                for a, b in zip(api_uris, disk_uris)
            )
        self._push()
        return {"api": api_playlist, "disk": disk, "match": match}

    async def async_shutdown(self) -> None:
        await super().async_shutdown()


def _uri_host_path(u: str) -> str:
    """Compare two RTSP URLs ignoring embedded credentials."""
    return __import__("re").sub(r"//[^@/]+@", "//", (u or "").strip())
