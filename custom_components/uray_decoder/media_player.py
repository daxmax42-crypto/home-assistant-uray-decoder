"""Media player platform: Alexa/Google/Assist voice control for Uray scenes.

WHY: Amazon Alexa does NOT support HA `select` entities (entity page shows "Unsupported").
It DOES support `media_player` with a channel/input source list. So this entity is the
Alexa-compatible voice surface for switching decoder scenes:

  * "Alexa, change the channel to Classroom Quad" -> select_source(title)   (by NAME)
  * "Alexa, play channel 2"                    -> play_media(channel=2)    (by NUMBER)

Google Assistant and Assist can use this media_player OR the scene `select`.
The decoder has no real play/pause/HDMI-volume semantics, so we implement only the
channel/input surface (source_list, select_source, play_media).

IMPORTANT: pin MEDIA_TYPE_CHANNEL locally. The imported constant was REMOVED from
media_player.const in newer HA core (caused a load-time ImportError on 2026.x that took
down the WHOLE integration). Never import removed HA internals here.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
)
from homeassistant.components.media_player.const import MediaPlayerEntityFeature

# Channel media type — pinned literal (removed from media_player.const on 2026.x).
MEDIA_TYPE_CHANNEL = "channel"
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, DOMAIN, MANUFACTURER, MODEL, SW_VERSION_PLACEHOLDER
from .coordinator import UrayDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

_SUPPORT = MediaPlayerEntityFeature.SELECT_SOURCE | MediaPlayerEntityFeature.PLAY_MEDIA


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: UrayDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([UrayMediaPlayer(coordinator, entry)])


class UrayMediaPlayer(CoordinatorEntity, MediaPlayerEntity):
    """Media player exposing decoder scenes as Alexa-compatible sources."""

    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = _SUPPORT

    def __init__(self, coordinator: UrayDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_media_player"
        self._attr_name = "Decoder"
        self._attr_icon = "mdi:television"
        self._attr_state = "on"  # a decoder is always on
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Uray Decoder ({entry.data[CONF_HOST]})",
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=SW_VERSION_PLACEHOLDER,
            configuration_url=f"http://{entry.data[CONF_HOST]}:{entry.data.get('port', 8080)}",
        )

    @property
    def source_list(self) -> list[str] | None:
        if not self.coordinator.data:
            return None
        return [s.get("name") for s in self.coordinator.scenes if s.get("name")]

    @property
    def source(self) -> str | None:
        return (self.coordinator.data or {}).get("active_scene")

    async def async_select_source(self, source: str) -> None:
        """Change scene by NAME (voice: "change the channel to <name>")."""
        if not await self.coordinator.async_apply_scene(source):
            _LOGGER.error("Failed to select scene %r", source)

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        """Change scene by NUMBER or NAME (voice: "play channel 3" / scene name).

        Voice hubs send media_type inconsistently, so don't gate on it: try the id as a
        1-based scene number first (validated against the live scene list), then fall
        back to matching the name. Mirrors the ISEEVY reference's hardening.
        """
        media_id = str(media_id).strip()
        scenes = self.coordinator.scenes
        try:
            idx = int(media_id)
            if 1 <= idx <= len(scenes):
                name = scenes[idx - 1].get("name")
                if name and await self.coordinator.async_apply_scene(name):
                    return
        except ValueError:
            pass
        # Fallback: treat media_id as a scene name (case-insensitive).
        for sc in scenes:
            if sc.get("name", "").lower() == media_id.lower():
                if await self.coordinator.async_apply_scene(sc["name"]):
                    return
                break
        _LOGGER.error("Failed to play media %r (type=%r)", media_id, media_type)
