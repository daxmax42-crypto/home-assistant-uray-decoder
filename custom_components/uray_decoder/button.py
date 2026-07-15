"""Button platform: refresh + verify controls."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, DOMAIN, MANUFACTURER, MODEL, SW_VERSION_PLACEHOLDER
from .coordinator import UrayDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: UrayDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            UrayRefreshButton(coordinator, entry),
            UrayRefreshStreamsButton(coordinator, entry),
            UrayVerifyButton(coordinator, entry),
        ]
    )


class _Base(CoordinatorEntity, ButtonEntity):
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


class UrayRefreshButton(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "refresh", "Refresh Status")
        self._attr_icon = "mdi:refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()


class UrayRefreshStreamsButton(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "refresh_streams", "Refresh go2rtc Streams")
        self._attr_icon = "mdi:playlist-edit"

    async def async_press(self) -> None:
        await self.coordinator.async_refresh_streams()


class UrayVerifyButton(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "verify", "Verify API vs Disk")
        self._attr_icon = "mdi:check-network"

    async def async_press(self) -> None:
        result = await self.coordinator.async_verify_state()
        # Stash match result so the verify-match sensor reflects it.
        self.coordinator._last_verify_match = result.get("match")
