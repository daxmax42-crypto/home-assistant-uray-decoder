"""Config flow for the Uray UHCVD265-1-4K quad decoder.

Two connection tests: the decoder (/get_status) and optionally go2rtc (/api/streams).
Scenes are built as a schema list so the user can define presets (quad layouts / single
full-screen) with per-channel direct URIs or go2rtc stream names.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from .api import UrayClient, UrayAPIError, UrayConnectionError
from .const import (
    CONF_GO2RTC_PASSWORD,
    CONF_GO2RTC_URL,
    CONF_GO2RTC_USERNAME,
    CONF_RTSP_HOST,
    CONF_RTSP_PORT,
    CONF_SCENES,
    CONF_TELNET_PASSWORD,
    CONF_TELNET_USERNAME,
    DEFAULT_GO2RTC_URL,
    DEFAULT_PORT,
    DEFAULT_RTSP_PORT,
    DEFAULT_TELNET_PASSWORD,
    DEFAULT_TELNET_USERNAME,
    DEFAULT_USERNAME,
    DOMAIN,
    WINDOW_COUNT,
)
from .go2rtc_client import Go2RtcClient

_LOGGER = logging.getLogger(__name__)

# One channel sub-schema: either a direct URI or a go2rtc stream name.
_CHANNEL_SCHEMA = vol.Schema(
    {
        vol.Optional("uri"): str,
        vol.Optional("go2rtc_stream"): str,
        vol.Optional("go2rtc_credentials"): str,
        vol.Optional("audio", default=0): vol.All(int, vol.Range(min=0, max=1)),
        vol.Optional("pindex", default=0): int,
        vol.Optional("cache", default=10): vol.All(int, vol.Range(min=0, max=4000)),
        vol.Optional("record", default=0): vol.All(int, vol.Range(min=0, max=1)),
    }
)

_SCENE_SCHEMA = vol.Schema(
    {
        vol.Required("name"): str,
        vol.Required("wnd", default=4): vol.All(int, vol.In([1, 4])),
        vol.Optional("ch0"): _CHANNEL_SCHEMA,
        vol.Optional("ch1"): _CHANNEL_SCHEMA,
        vol.Optional("ch2"): _CHANNEL_SCHEMA,
        vol.Optional("ch3"): _CHANNEL_SCHEMA,
    }
)


class UrayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Uray decoder config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # Test decoder connection
            client = UrayClient(
                host=user_input[CONF_HOST],
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                port=user_input.get(CONF_PORT, DEFAULT_PORT),
                telnet_username=user_input.get(CONF_TELNET_USERNAME, DEFAULT_TELNET_USERNAME),
                telnet_password=user_input.get(CONF_TELNET_PASSWORD, DEFAULT_TELNET_PASSWORD),
            )
            try:
                await client.get_status()
            except UrayAuthError:
                errors["base"] = "auth_failed"
            except UrayConnectionError:
                errors["base"] = "cannot_connect"
            except UrayAPIError:
                errors["base"] = "unknown"
            else:
                # Validate go2rtc if provided
                if user_input.get(CONF_GO2RTC_URL):
                    go2rtc = Go2RtcClient(
                        base_url=user_input[CONF_GO2RTC_URL],
                        username=user_input.get(CONF_GO2RTC_USERNAME),
                        password=user_input.get(CONF_GO2RTC_PASSWORD),
                        rtsp_host=user_input.get(CONF_RTSP_HOST),
                        rtsp_port=int(user_input.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)),
                    )
                    if not await go2rtc.validate():
                        errors["base"] = "go2rtc_cannot_connect"
                if not errors:
                    # Flow to scene definition
                    self._user_data = user_input
                    return await self.async_step_scenes()
            finally:
                await client.close()

        data = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=data.get(CONF_HOST, "10.0.100.55")): str,
                vol.Required(CONF_USERNAME, default=data.get(CONF_USERNAME, DEFAULT_USERNAME)): str,
                vol.Required(CONF_PASSWORD, default=data.get(CONF_PASSWORD, "")): str,
                vol.Optional(CONF_PORT, default=data.get(CONF_PORT, DEFAULT_PORT)): int,
                vol.Optional(CONF_TELNET_USERNAME, default=DEFAULT_TELNET_USERNAME): str,
                vol.Optional(CONF_TELNET_PASSWORD, default=DEFAULT_TELNET_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors,
            description_placeholders={
                "host": "10.0.100.55",
                "note": "Telnet creds (root/unisheen) are only used for read-back verification; HTTP admin/admin drives control.",
            },
        )

    async def async_step_scenes(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            scenes_raw = user_input.get("scenes", [])
            scenes = []
            for sc in scenes_raw:
                # Normalize channel dict keys to ch0..ch3
                normalized = {"name": sc["name"], "wnd": sc["wnd"]}
                for i in range(WINDOW_COUNT):
                    key = f"ch{i}"
                    if sc.get(key):
                        normalized[key] = sc[key]
                scenes.append(normalized)
            # Persist config
            entry_data = {**self._user_data, CONF_SCENES: scenes}
            return self.async_create_entry(
                title=f"Uray Decoder ({self._user_data[CONF_HOST]})",
                data=entry_data,
            )
        schema = vol.Schema(
            {
                vol.Optional("scenes", default=[]): vol.All(
                    [vol.Any(_SCENE_SCHEMA, vol.Schema({}))],
                    vol.Length(max=20),
                ),
            }
        )
        return self.async_show_form(
            step_id="scenes",
            data_schema=schema,
            description_placeholders={
                "hint": "Define scene presets. Each scene: name, wnd (1=single full-screen, 4=quad), and ch0..ch3 with a direct uri or a go2rtc_stream name. Leave scenes empty to skip voice scenes and use the 4 channel selects only.",
            },
        )
