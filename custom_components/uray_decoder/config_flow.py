"""Config flow for the Uray UHCVD265-1-4K quad decoder.

Two steps:
  1. Device + optional go2rtc connection (all flat, standard voluptuous fields).
  2. Optional scene presets, entered as a JSON block (validated manually — avoids the
     nested list-of-dict voluptuous schema that HA's config flow cannot render and which
     surfaces as a generic "Unknown error" in the UI).

If the connection test raises anything unexpected we surface a clear base error rather
than letting it bubble as "Unknown error".
"""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from .api import UrayClient, UrayAPIError, UrayAuthError, UrayConnectionError
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
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default="10.0.100.55"): str,
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD, default=""): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_TELNET_USERNAME, default=DEFAULT_TELNET_USERNAME): str,
        vol.Optional(CONF_TELNET_PASSWORD, default=DEFAULT_TELNET_PASSWORD): str,
        vol.Optional(CONF_GO2RTC_URL, default=DEFAULT_GO2RTC_URL): str,
        vol.Optional(CONF_GO2RTC_USERNAME): str,
        vol.Optional(CONF_GO2RTC_PASSWORD): str,
        vol.Optional(CONF_RTSP_HOST): str,
        vol.Optional(CONF_RTSP_PORT, default=DEFAULT_RTSP_PORT): int,
    }
)


class UrayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Uray decoder config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
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
            except UrayAPIError as err:
                # HTTP-level error that isn't auth/connection -> report it clearly.
                errors["base"] = "device_error"
                _LOGGER.error("Uray device error during setup: %s", err)
            except Exception:  # noqa: BLE001 - never surface as bare "Unknown"
                _LOGGER.exception("Unexpected error during Uray setup")
                errors["base"] = "unknown"
            else:
                # Validate go2rtc only if a URL was provided.
                if user_input.get(CONF_GO2RTC_URL):
                    from .go2rtc_client import Go2RtcClient

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
                    self._user_data = user_input
                    return await self.async_step_scenes()
            finally:
                await client.close()

        return self.async_show_form(
            step_id="user",
            data_schema=USER_SCHEMA,
            errors=errors,
        )

    async def async_step_scenes(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            raw = (user_input.get("scenes_json") or "").strip()
            scenes: list[dict[str, Any]] = []
            if raw:
                try:
                    parsed = json.loads(raw)
                    if not isinstance(parsed, list):
                        raise ValueError("scenes must be a JSON list")
                    scenes = parsed
                except Exception as err:  # noqa: BLE001
                    errors["base"] = "scenes_invalid"
                    _LOGGER.error("Invalid scenes JSON: %s", err)
            if not errors:
                entry_data = {**self._user_data, CONF_SCENES: scenes}
                return self.async_create_entry(
                    title=f"Uray Decoder ({self._user_data[CONF_HOST]})",
                    data=entry_data,
                )

        # Example shown to the user so they can copy/edit.
        example = json.dumps(
            [
                {
                    "name": "Quad All",
                    "wnd": 4,
                    "ch0": {"uri": "rtsp://viewer:0p3nd00r@10.0.100.242:554//h264Preview_01_sub", "audio": 0},
                    "ch1": {"uri": "rtsp://viewer:0p3nd00r@10.0.100.240:554/Preview_02_sub", "audio": 1},
                    "ch2": {"uri": "rtsp://viewer:0p3nd00r@10.0.100.117:554//h264Preview_01_sub", "audio": 0},
                    "ch3": {"uri": "rtsp://viewer:0p3nd00r@10.0.100.251:554//h264Preview_01_sub", "audio": 0},
                },
                {
                    "name": "Ch0 Full",
                    "wnd": 1,
                    "ch0": {"go2rtc_stream": "barn_trackmix_north_sub", "audio": 0},
                },
            ],
            indent=2,
        )
        schema = vol.Schema(
            {
                vol.Optional("scenes_json", default=""): str,
            }
        )
        return self.async_show_form(
            step_id="scenes",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "example": example,
                "hint": "Optional. Paste a JSON list of scene presets (leave blank for none). "
                "Each scene: name, wnd (1=single, 4=quad), and ch0..ch3 with a direct 'uri' "
                "or a 'go2rtc_stream' name. The 4 channel selects work without scenes.",
            },
        )
