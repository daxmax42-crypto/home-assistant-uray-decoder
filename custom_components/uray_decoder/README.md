# Uray Quad Decoder

Home Assistant integration for the **Uray UHCVD265-1-4K** quad video decoder (model XD3/XD3S, firmware 1.53.1).

## Features

- **Stream-health + device-health sensors** from `/get_status`: per-window alive/FPS/bitrate, plus CPU, free memory, video output, window layout, firmware, network status.
- **Scene presets** (quad / single full-screen) switched via the safe `/set_playlist` HTTP API.
- **Alexa / Google / Assist voice control** via a `media_player` entity — scene titles are sources:
  - *"Alexa, change the channel to Classroom Quad"* (by name)
  - *"Alexa, play channel 2"* (by number)
- **Optional go2rtc integration**: dynamically discovers camera streams from `/api/streams` and offers them as per-channel select options (`rtsp://<host>:<port>/<name>`).
- **First-load seeding** of the active scene from the decoder's true current playlist (`/get_playlist`), not a hardcoded constant.
- **Independent telnet read-back** (`/box/box.ini`) confirms every write persisted — no destructive CGI is ever called.
- Local-only, no cloud, no third-party dependencies (`requirements: []`, `iot_class: local_polling`).

## Device access

| Surface | Credentials |
|---------|-------------|
| HTTP API (:8080) | `admin` / `admin` |
| Telnet (:23, read-back only) | `root` / `unisheen` |

## Installation (HACS)

1. Add this repo as a **Custom Repository** (category: Integration) in HACS.
2. Install **Uray Quad Decoder**.
3. Restart Home Assistant.
4. **Settings → Devices & Services → Add Integration → Uray Quad Decoder**.
5. Enter the decoder host (`10.0.100.55`) and HTTP credentials, optionally a go2rtc URL (`http://10.0.10.41:1984`) and scene presets (JSON).

## Services

- `uray_decoder.apply_scene` — switch to a named scene preset.
- `uray_decoder.set_channel` — set one channel (1–4) to a direct RTSP URI or a go2rtc stream name.

## License

MIT
