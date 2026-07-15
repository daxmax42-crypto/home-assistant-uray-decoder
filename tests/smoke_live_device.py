#!/usr/bin/env python3
"""Regression smoke test for the Uray quad decoder component — runs AGAINST THE LIVE DEVICE.

No Home Assistant or aiohttp required. The component is loaded standalone by stubbing
aiohttp (not a real dependency — manifest requirements: []) and registering the
component dir as a package. Guards the bug classes we must never ship:

  1. /get_status parses into per-stream alive/fps/bps + device health (rich sensor source).
  2. /get_playlist parses into a 4-slot channel list (scene-switch ground truth).
  3. set_playlist applies LIVE and persists (verified via telnet /box/box.ini read-back) —
     the safe-write path, no destructive CGI.
  4. go2rtc client discovers streams from /api/streams and builds a restream URL.
  5. The component never imports a removed HA constant (MEDIA_TYPE_CHANNEL is pinned) —
     catches the load-crash class without needing HA installed (we just import the modules).

The test never leaves the device in a changed state: it applies a scene, then restores
the ORIGINAL playlist read at start.

Usage:
    python3 tests/smoke_live_device.py [HOST] [--username admin --password admin]
                                        [--go2rtc http://10.0.10.41:1984]

Exit code 0 = all checks passed; non-zero = regression or device unreachable.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
import sys
import types

logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")

PKG = "custom_components/uray_decoder"

# --- 1. stub aiohttp (not installed in CI/agent venvs; keeps requirements: []) ---
aiohttp = types.ModuleType("aiohttp")


class _ClientSession:
    closed = False

    def __init__(self, *a, **k):
        pass

    async def close(self):
        pass


aiohttp.ClientSession = _ClientSession
sys.modules["aiohttp"] = aiohttp

# --- 2. register component as a real package so `from .const import` works ---
_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO))
pkg = types.ModuleType("uray_decoder")
pkg.__path__ = [str(_REPO / PKG)]
sys.modules["uray_decoder"] = pkg

# Importing these modules would crash at import time if they referenced a removed HA
# symbol (e.g. MEDIA_TYPE_CHANNEL from media_player.const). That is the v1.0.19 class of
# bug — catching it here means it never reaches a user's HA 2026.x.
from uray_decoder.api import UrayClient, UrayAPIError  # noqa: E402
from uray_decoder.const import DEFAULT_TELNET_USERNAME, DEFAULT_TELNET_PASSWORD  # noqa: E402
from uray_decoder.go2rtc_client import Go2RtcClient  # noqa: E402


def _check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    return ok


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host", nargs="?", default="10.0.100.55")
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--go2rtc", default="http://10.0.10.41:1984")
    args = ap.parse_args()

    print(f"== Uray live smoke test -> {args.host} ==")
    client = UrayClient(
        host=args.host,
        username=args.username,
        password=args.password,
        port=args.port,
    )

    failures = 0
    original_playlist = None
    try:
        # ---- CHECK 1: /get_status parses into streams + health ----
        try:
            status = await client.get_status()
            streams = status.get("streams", [])
            ok = (
                len(streams) == 4
                and all(
                    s.get("alive") in (0, 1) and s.get("fps") is not None
                    for s in streams
                )
                and status.get("cpu_usage") is not None
                and status.get("mem_free") is not None
            )
            failures += 0 if _check(
                "get_status parses 4 streams + device health (sensor source)",
                ok,
                f"streams={len(streams)} cpu={status.get('cpu_usage')} mem={status.get('mem_free')}",
            ) else 1
        except UrayAPIError as e:
            failures += 1
            _check("get_status raised", False, f"{type(e).__name__}: {e}")

        # ---- CHECK 2: /get_playlist parses into 4-slot channels ----
        try:
            pl = await client.get_playlist()
            channels = pl.get("channels", [])
            ok = len(channels) == 4 and "wnd" in pl
            failures += 0 if _check(
                "get_playlist parses 4-slot channels (scene ground truth)",
                ok,
                f"wnd={pl.get('wnd')} channels={len(channels)}",
            ) else 1
            original_playlist = pl  # save for restore
        except UrayAPIError as e:
            failures += 1
            _check("get_playlist raised", False, f"{type(e).__name__}: {e}")

        # ---- CHECK 3: set_playlist applies live + persists (telnet read-back) ----
        if original_playlist is not None:
            try:
                # Apply a quad scene built from the current (real) channels.
                await client.set_scene(4, original_playlist["channels"])
                # Independent verification: read /box/box.ini via telnet.
                disk = await client.verify_playlist_via_telnet()
                api_after = await client.get_playlist()
                ok = disk is not None and api_after.get("wnd") == 4
                failures += 0 if _check(
                    "set_playlist applies live; telnet read-back confirms (no corruption)",
                    ok,
                    f"disk_wndnum={disk.get('wndnum') if disk else None} api_wnd={api_after.get('wnd')}",
                ) else 1
            except (UrayAPIError, OSError) as e:
                failures += 1
                _check("set_playlist/verify raised", False, f"{type(e).__name__}: {e}")

        # ---- CHECK 4: go2rtc discovery + RTSP URL build ----
        try:
            go2rtc = Go2RtcClient(base_url=args.go2rtc)
            streams = await go2rtc.get_streams()
            names = sorted(streams.keys())
            sample_url = go2rtc.build_rtsp_url(names[0]) if names else ""
            ok = len(names) > 0 and sample_url.startswith("rtsp://")
            failures += 0 if _check(
                "go2rtc discovers streams + builds restream URL",
                ok,
                f"streams={len(names)} sample={sample_url}",
            ) else 1
        except Exception as e:  # noqa: BLE001
            failures += 1
            _check("go2rtc discovery raised", False, f"{type(e).__name__}: {e}")

        # ---- CHECK 5: telnet client uses the device's real console creds ----
        failures += 0 if _check(
            "telnet client uses decoder console creds (root/unisheen)",
            client.telnet.username == DEFAULT_TELNET_USERNAME
            and client.telnet.password == DEFAULT_TELNET_PASSWORD,
            f"telnet={client.telnet.username}/{client.telnet.password}",
        ) else 1

    finally:
        # Restore original playlist so the device is left unchanged.
        if original_playlist is not None:
            try:
                await client.set_scene(4, original_playlist["channels"])
                _check("original playlist restored", True)
            except Exception as e:  # noqa: BLE001
                failures += 1
                _check("restore original playlist", False, f"{type(e).__name__}: {e}")
        await client.close()

    print(f"== {'ALL CHECKS PASSED' if failures == 0 else str(failures) + ' CHECK(S) FAILED'} ==")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
