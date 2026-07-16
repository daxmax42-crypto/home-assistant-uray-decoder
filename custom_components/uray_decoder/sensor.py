"""Sensor platform: Uray decoder stream-health + device-health."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfInformation, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_FIRMWARE_VERSION,
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
    entities: list[Any] = [
        UrayFirmwareSensor(coordinator, entry),
        UrayCPUSensor(coordinator, entry),
        UrayMemFreeSensor(coordinator, entry),
        UrayVideoOutputSensor(coordinator, entry),
        UrayLayoutSensor(coordinator, entry),
        UrayActiveSceneSensor(coordinator, entry),
        UrayNetStatusSensor(coordinator, entry),
        UrayVerifyMatchSensor(coordinator, entry),
        UrayUptimeSensor(coordinator, entry),
    ]
    for i in range(WINDOW_COUNT):
        entities.append(UrayStreamAliveSensor(coordinator, entry, i))
        entities.append(UrayStreamFpsSensor(coordinator, entry, i))
        entities.append(UrayStreamBpsSensor(coordinator, entry, i))
    async_add_entities(entities)


class _Base(CoordinatorEntity, SensorEntity):
    def __init__(self, c: UrayDataUpdateCoordinator, e: ConfigEntry, key: str, name: str) -> None:
        super().__init__(c)
        self._entry = e
        self._key = key
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

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None


class UrayFirmwareSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "firmware_version", "Firmware Version")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:chip"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(ATTR_FIRMWARE_VERSION)


class UrayCPUSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "cpu_usage", "CPU Usage")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_native_unit_of_measurement = UnitOfPower.PERCENTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:cpu-64-bit"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("cpu_usage")


class UrayMemFreeSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "mem_free", "Memory Free")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_native_unit_of_measurement = UnitOfInformation.KIBIBYTES
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:memory"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("mem_free")


class UrayVideoOutputSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "video_output", "Video Output")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:monitor"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("vo")


class UrayLayoutSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "window_layout", "Window Layout")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:grid"

    @property
    def native_value(self):
        wnd = (self.coordinator.data or {}).get("wndnum")
        if wnd == 1:
            return "Single"
        if wnd == 4:
            return "Quad"
        return wnd


class UrayActiveSceneSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "active_scene", "Active Scene")
        self._attr_icon = "mdi:movie-open"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("active_scene")


class UrayNetStatusSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "net_status", "Network Status")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = ["Online", "Offline"]

    @property
    def native_value(self):
        v = (self.coordinator.data or {}).get("net_status")
        return "Online" if v == 1 else "Offline"


class UrayVerifyMatchSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "verify_match", "API/Disk Config Match")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = ["Match", "Mismatch", "Unknown"]
        self._attr_icon = "mdi:check-network"

    @property
    def native_value(self):
        v = getattr(self.coordinator, "_last_verify_match", None)
        if v is None:
            return "Unknown"
        return "Match" if v else "Mismatch"


class UrayStreamAliveSensor(_Base):
    def __init__(self, c, e, idx: int):
        super().__init__(c, e, f"stream{idx}_alive", f"Stream {idx} Alive")
        self._idx = idx
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = ["Alive", "Dead"]
        self._attr_icon = "mdi:video-wireless"

    @property
    def native_value(self):
        d = self.coordinator.data or {}
        streams = d.get("streams", [])
        if self._idx < len(streams):
            return "Alive" if streams[self._idx].get("alive") == 1 else "Dead"
        return None

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        streams = d.get("streams", [])
        if self._idx < len(streams):
            return {"uri": streams[self._idx].get("uri")}
        return {}


class UrayStreamFpsSensor(_Base):
    def __init__(self, c, e, idx: int):
        super().__init__(c, e, f"stream{idx}_fps", f"Stream {idx} FPS")
        self._idx = idx
        self._attr_native_unit_of_measurement = "fps"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        d = self.coordinator.data or {}
        streams = d.get("streams", [])
        if self._idx < len(streams):
            return streams[self._idx].get("fps")
        return None


class UrayStreamBpsSensor(_Base):
    def __init__(self, c, e, idx: int):
        super().__init__(c, e, f"stream{idx}_bps", f"Stream {idx} Bitrate")
        self._idx = idx
        self._attr_native_unit_of_measurement = "kbps"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        d = self.coordinator.data or {}
        streams = d.get("streams", [])
        if self._idx < len(streams):
            return streams[self._idx].get("bps")
        return None


class UrayUptimeSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "days_since_boot", "Days Since Boot")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_native_unit_of_measurement = "d"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:clock-outline"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("days_since_boot")


class UrayRebootBinarySensor:
    """Moved to binary_sensor.py — see note there. HA 2026.x rejects a SensorEntity +
    BinarySensorEntity mix in the sensor platform (aborts the whole platform)."""
