# Uray Quad Decoder

Home Assistant custom integration for the **Uray UHCVD265-1-4K** quad video decoder (model XD3/XD3S, firmware 1.53.1).

It polls the decoder's `/get_status` XML for live stream-health and device-health
metrics, switches quad/single-view **scenes** via the safe `/set_playlist` HTTP API,
and exposes an **Alexa/Google/Assist**-compatible voice surface (a `media_player` with
scene titles as sources). Optional **go2rtc** integration dynamically discovers camera
streams and offers them as per-channel select options.

## Features

- **Stream-health sensors** (per window 0–3): alive, FPS, bitrate — plus device-health
  (CPU, free memory, video output, window layout, firmware, network status).
- **Scene presets**: define quad layouts / single full-screen presets in the config
  flow; switch them from the UI, automations, or voice.
- **Alexa voice control** via a `media_player` entity:
  - *"Alexa, change the channel to Classroom Quad"* → by name
  - *"Alexa, play channel 2"* → by number
- **Per-channel selects**: pick a go2rtc stream (or direct RTSP URI) for each of the 4 windows.
- **First-load seeding**: the active scene is read from the decoder's true current
  playlist (`/get_playlist`), not a hardcoded constant.
- **Safe writes**: all changes go through the live HTTP API; an independent telnet
  read-back (`/box/box.ini`) confirms the change actually persisted. No destructive
  CGI is ever called.
- **Local-only / no cloud**: `iot_class: local_polling`, no third-party dependencies
  (`requirements: []`).

## Device access

| Surface | Credentials |
|---------|-------------|
| HTTP API (:8080) | `admin` / `admin` |
| Telnet (:23, read-back only) | `root` / `unisheen` |

> The telnet shell on this device is **clean** (no console flood, no app-grab). Telnet
> is used only as a ground-truth cross-check for writes — control goes through HTTP.

## Installation (HACS)

1. Add this repo as a **Custom Repository** (category: Integration) in HACS.
2. Install **Uray Quad Decoder**.
3. Restart Home Assistant.
4. **Settings → Devices & Services → Add Integration → Uray Quad Decoder**.
5. Enter the decoder host (`10.0.100.55`) and HTTP credentials, optionally a go2rtc URL
   (`http://10.0.10.41:1984`) and scene presets.

## go2rtc

If you run [go2rtc](https://github.com/AlexxIT/go2rtc) (e.g. on `10.0.10.41:1984`),
the integration discovers its streams from `/api/streams` and builds restream URLs of
the form `rtsp://<host>:<port>/<stream_name>`. Each channel select then offers the live
go2rtc stream names; selecting one pushes the restream URL into the decoder.

> Note: the decoder's firmware-managed default URLs use `viewer:0p3nd00r` directly at
> the camera IPs. The optional go2rtc restream path uses the go2rtc host/RTSP port and
> may require different credentials depending on your go2rtc config.

## Services

- `uray_decoder.apply_scene` — switch to a named scene preset.
- `uray_decoder.set_channel` — set one channel (1–4) to a direct RTSP URI or a go2rtc
  stream name, keeping the other three from the current quad baseline.

## Known device quirks (handled by the integration)

- **Single-view trap**: `set_playlist` in single-view (`wnd=1`) returns empty `uri0..3`.
  The integration always caches and pushes a full 4-slot quad baseline, never clobbering
  the playlist with empties.
- **Black-window after rebuild** (Reolink sub-stream pacing on two cameras): the
  `fps` sensors stay accurate; ignore the ~60s settle and alarm only if `fps=0` persists.

## Development

Regression smoke test (runs against the live device, no HA/aiohttp needed):

```bash
python3 tests/smoke_live_device.py 10.0.100.55 --username admin --password admin
```

## License

MIT
