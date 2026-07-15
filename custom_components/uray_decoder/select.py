"""Select platform: scene preset + per-channel stream selection.

NOTE on voice: Amazon Alexa does NOT support HA `select` entities (shows "Unsupported").
Voice control of scenes goes through media_player.py (source_list = scene titles).
These selects drive the same coordinator methods so HA UI / Google / Assist work, and
the media_player mirrors the scene list for Alexa.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_HOST,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    SW_VERSION_PLACEHOLDER,
    WINDOW_COUNT,
)
from .coordinator import UrayDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: UrayDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [UraySceneSelect(coordinator, entry)]
    for i in range(WINDOW_COUNT):
        entities.append(UrayChannelSelect(coordinator, entry, i))
    async_add_entities(entities)


class _Base(CoordinatorEntity, SelectEntity):
    def __init__(self, c: UrayDataUpdateCoordinator, e: ConfigEntry, key: str, name: str) -> None:
        super().__init__(c)
        self._entry = e
        self._attr_unique_id = f"{e.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, e.entry_id)},
            name=f"Uray Decoder ({e.data[CONF_HOST]})",
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=SW_VERSION_PLACEHOLDER,
            configuration_url=f"http://{e.data[CONF_HOST]}:{e.data.get('port', 8080)}",
        )


class UraySceneSelect(_Base):
    """Choose a named scene preset (quad layout / single full-screen)."""

    def __init__(self, c, e):
        super().__init__(c, e, "scene", "Scene")
        self._attr_icon = "mdi:movie-open"

    @property
    def options(self) -> list[str]:
        return [s.get("name") for s in self.coordinator.scenes if s.get("name")]

    @property
    def current_option(self) -> str | None:
        return (self.coordinator.data or {}).get("active_scene")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"wnd": (self.coordinator.data or {}).get("wndnum")}

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_apply_scene(option)


class UrayChannelSelect(_Base):
    """Per-channel select populated from go2rtc stream names (or current device URI)."""

    def __init__(self, c: UrayDataUpdateCoordinator, e: ConfigEntry, idx: int) -> None:
        super().__init__(c, e, f"channel_{idx}", f"Channel {idx + 1}")
        self._idx = idx
        self._attr_icon = "mdi:video-input-component"
        self._current_uri: str | None = None

    @property
    def options(self) -> list[str]:
        names = sorted((self.coordinator.data or {}).get("streams_go2rtc", []))
        # Always include the live device URI as an option if present (so it shows even
        # if it is not in go2rtc's list).
        live = self._live_uri()
        if live and live not in names:
            names = [live] + names
        return names

    def _live_uri(self) -> str | None:
        d = self.coordinator.data or {}
        streams = d.get("streams", [])
        if self._idx < len(streams):
            return streams[self._idx].get("uri")
        return None

    @property
    def current_option(self) -> str | None:
        # Show the current URI if it is a go2rtc stream name, else the raw URI.
        live = self._live_uri()
        if live is None:
            return None
        names = sorted((self.coordinator.data or {}).get("streams_go2rtc", []))
        for n in names:
            if self.coordinator.go2rtc and self._uri_eq(
                live, self.coordinator.go2rtc.build_rtsp_url(n)
            ):
                return n
        return live

    @staticmethod
    def _uri_eq(a: str, b: str) -> bool:
        import re

        return re.sub(r"//[^@/]+@", "//", a) == re.sub(r"//[^@/]+@", "//", b)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "channel": self._idx + 1,
            "rtsp_url": self._live_uri(),
            "source": "go2rtc" if self.coordinator.go2rtc else "device",
        }

    async def async_select_option(self, option: str) -> None:
        # Resolve option -> URI. If option is a go2rtc stream name, build the restream URL.
        uri = option
        go2rtc_names = sorted((self.coordinator.data or {}).get("streams_go2rtc", []))
        if self.coordinator.go2rtc is not None and option in go2rtc_names:
            uri = self.coordinator.go2rtc.build_rtsp_url(option)
        # audio: keep current channel audio from baseline if known
        audio = 0
        if self.coordinator._quad_baseline and self._idx < len(self.coordinator._quad_baseline):
            audio = int(self.coordinator._quad_baseline[self._idx].get("audio", 0))
        await self.coordinator.async_set_channel(self._idx + 1, uri, audio)
