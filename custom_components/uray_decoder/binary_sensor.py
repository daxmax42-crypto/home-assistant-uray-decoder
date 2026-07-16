"""Binary sensor platform: Uray decoder reboot detection.

Separate from sensor.py because HA 2026.x rejects an entity that is simultaneously a
SensorEntity and a BinarySensorEntity when added via the sensor platform forward-setup.
Mixing the two bases aborts the ENTIRE sensor platform (no sensor entities get created).
Keeping binary sensors in their own platform file is the correct pattern.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    async_add_entities([UrayRebootBinarySensor(coordinator, entry)])


class UrayRebootBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """True when the decoder's uptime day counter dropped (i.e. it rebooted)."""

    def __init__(self, c: UrayDataUpdateCoordinator, e: ConfigEntry) -> None:
        super().__init__(c)
        self._entry = e
        self._attr_unique_id = f"{e.entry_id}_reboot_detected"
        self._attr_name = "Reboot Detected"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        self._attr_icon = "mdi:restart-alert"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, e.entry_id)},
            name=f"Uray Decoder ({e.data[CONF_HOST]})",
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=SW_VERSION_PLACEHOLDER,
            configuration_url=f"http://{e.data[CONF_HOST]}:{e.data.get('port', 8080)}",
        )

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("reboot_detected"))

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None
