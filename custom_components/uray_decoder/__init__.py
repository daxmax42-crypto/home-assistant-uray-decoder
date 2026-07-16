"""Uray UHCVD265-1-4K Quad Video Decoder integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import UrayAuthError, UrayConnectionError
from .const import DOMAIN
from .coordinator import UrayDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Uray decoder from a config entry."""
    coordinator = UrayDataUpdateCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except UrayAuthError:
        _LOGGER.error("Authentication failed for %s", entry.data["host"])
        return False
    except UrayConnectionError as err:
        raise ConfigEntryNotReady(f"Cannot connect to {entry.data['host']}: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Background go2rtc refresh (coarser cadence than status poll)
    async def _go2rtc_loop() -> None:
        while True:
            await coordinator.async_refresh_go2rtc()
            await asyncio.sleep(60)

    import asyncio

    task = asyncio.create_task(_go2rtc_loop())
    coordinator._go2rtc_task = task

    # Services
    async def async_apply_scene(call) -> None:
        name = call.data.get("scene")
        if name:
            await coordinator.async_apply_scene(name)

    async def async_set_channel(call) -> None:
        channel = int(call.data["channel"])
        uri = call.data.get("uri") or call.data.get("stream_name")
        audio = int(call.data.get("audio", 0))
        if uri:
            await coordinator.async_set_channel(channel, uri, audio)

    hass.services.async_register(DOMAIN, "apply_scene", async_apply_scene, schema=None)
    hass.services.async_register(DOMAIN, "set_channel", async_set_channel, schema=None)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: UrayDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
    if getattr(coordinator, "_go2rtc_task", None):
        coordinator._go2rtc_task.cancel()
    await coordinator.async_shutdown()
    hass.services.async_remove(DOMAIN, "apply_scene")
    hass.services.async_remove(DOMAIN, "set_channel")
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
