"""Constants for the Uray UHCVD265-1-4K Quad Video Decoder integration."""

DOMAIN = "uray_decoder"

# --- Device config keys (config flow) ---
CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_PORT = "port"
CONF_TELNET_USERNAME = "telnet_username"
CONF_TELNET_PASSWORD = "telnet_password"
CONF_SCAN_INTERVAL = "scan_interval"

# --- go2rtc config keys ---
CONF_GO2RTC_URL = "go2rtc_url"
CONF_GO2RTC_USERNAME = "go2rtc_username"
CONF_GO2RTC_PASSWORD = "go2rtc_password"
CONF_RTSP_HOST = "rtsp_host"
CONF_RTSP_PORT = "rtsp_port"

# --- Scene (preset) config key ---
CONF_SCENES = "scenes"  # list of dicts: {name, wnd, ch0..ch3}

# --- Defaults ---
DEFAULT_PORT = 8080
DEFAULT_USERNAME = "admin"
# Uray telnet shell is CLEAN (no app-grab, no CTRL-C breakout, no console flood).
# The console creds differ from the web UI creds — keep them separate (ISEEVY lesson:
# reusing web creds for telnet lands in a dead context). Default to the device's known
# backdoor; user can override in the flow.
DEFAULT_TELNET_USERNAME = "root"
DEFAULT_TELNET_PASSWORD = "unisheen"
DEFAULT_SCAN_INTERVAL = 30  # seconds
DEFAULT_GO2RTC_URL = "http://10.0.10.41:1984"
DEFAULT_RTSP_PORT = 8554

# --- Uray HTTP API endpoints (served by box.d3_uni; no CGI binaries on this device) ---
ENDPOINT_GET_STATUS = "/get_status"
ENDPOINT_GET_PLAYLIST = "/get_playlist"
ENDPOINT_SET_PLAYLIST = "/set_playlist"

# --- Module / device identity (do NOT change — device is what it is) ---
MANUFACTURER = "Uray"
MODEL = "UHCVD265-1-4K (XD3/XD3S)"
# SW_VERSION is the DEVICE FIRMWARE string from /get_status, not the component version.
# Never bump this for releases — it is set from the live status each poll.
SW_VERSION_PLACEHOLDER = "1.53.1"

# Number of windows/stream slots in the quad decoder.
WINDOW_COUNT = 4

# Attribute keys
ATTR_FIRMWARE_VERSION = "firmware_version"
ATTR_MAC_ADDRESS = "mac_address"
ATTR_WINDOW_LAYOUT = "window_layout"
ATTR_SCENE = "scene"
ATTR_STREAMS = "streams"
ATTR_CHANNEL = "channel"
ATTR_RTSP_URL = "rtsp_url"
ATTR_SOURCE = "source"
ATTR_SOURCE_TYPE = "source_type"
ATTR_GO2RTC = "go2rtc"
ATTR_ALIVE = "alive"
ATTR_FPS = "fps"
ATTR_BPS = "bps"
