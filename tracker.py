#!/usr/bin/env python3
"""skwd-tracker: dwl-ipc subscriber.

Connects to the running dwl/mango/noir compositor via the
`zdwl_ipc_manager_v2` protocol, listens for per-output `active` events
(focus changes), and writes the focused monitor's name to
$XDG_CACHE_HOME/skwd-wall/active-monitor.

This is the compositor-IPC half of skwd-wall's `monitor: "auto"` feature:
no cursor edge-strips, no polling — the compositor pushes focus changes
the moment they happen.

Designed for dwl-class compositors that ship the `dwl_ipc_unstable_v2`
protocol (vendored XML at protocols/dwl-ipc-unstable-v2.xml). On other
compositors (Hyprland, Sway, niri) this binary does nothing useful and
should be replaced with their respective IPC clients.
"""

from __future__ import annotations

import os
import sys

from pywayland.client import Display
from pywayland.protocol.wayland import WlOutput

# Generated at build time by pywayland-scanner from protocols/dwl-ipc-unstable-v2.xml
from dwl_ipc_unstable_v2 import ZdwlIpcManagerV2, ZdwlIpcOutputV2  # noqa: F401


def _state_path() -> str:
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(cache, "skwd-wall", "active-monitor")


class Tracker:
    def __init__(self) -> None:
        # registry_name (int) → entry dict
        self.outputs: dict[int, dict] = {}
        self.manager: ZdwlIpcManagerV2 | None = None
        self.last_written = ""
        self.path = _state_path()

    # ── State write ──
    def write_monitor(self, name: str) -> None:
        if not name or name == self.last_written:
            return
        self.last_written = name
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            f.write(name)
        os.rename(tmp, self.path)
        print(f"[skwd-tracker] active monitor → {name}", flush=True)

    # ── Registry handling ──
    def on_global(self, registry, name, interface, version):
        if interface == "zdwl_ipc_manager_v2":
            self.manager = registry.bind(name, ZdwlIpcManagerV2, min(version, 3))
            # Late bind: attach IPC outputs for outputs that arrived first.
            for entry in self.outputs.values():
                if entry["ipc"] is None:
                    self._attach_ipc(entry)
        elif interface == "wl_output":
            wl_output = registry.bind(name, WlOutput, min(version, 4))
            entry = {"wl_output": wl_output, "name": None, "ipc": None, "active": False}
            self.outputs[name] = entry
            wl_output.dispatcher["name"] = lambda obj, n: self._on_output_name(entry, n)
            if self.manager is not None:
                self._attach_ipc(entry)

    def on_global_remove(self, registry, name):
        entry = self.outputs.pop(name, None)
        if entry and entry["ipc"] is not None:
            try:
                entry["ipc"].destroy()
            except Exception:
                pass

    def _attach_ipc(self, entry) -> None:
        ipc = self.manager.get_output(entry["wl_output"])
        ipc.dispatcher["active"] = lambda obj, active: self._on_active(entry, active)
        entry["ipc"] = ipc

    def _on_output_name(self, entry, name):
        entry["name"] = name
        if entry["active"]:
            self.write_monitor(name)

    def _on_active(self, entry, active):
        entry["active"] = bool(active)
        if active and entry["name"]:
            self.write_monitor(entry["name"])

    # ── Main loop ──
    def run(self) -> None:
        display = Display()
        display.connect()
        registry = display.get_registry()
        registry.dispatcher["global"] = self.on_global
        registry.dispatcher["global_remove"] = self.on_global_remove
        # First roundtrip: get globals + bind them
        display.roundtrip()
        # Second roundtrip: receive name + active events
        display.roundtrip()
        # Steady-state event loop
        while display.dispatch(block=True) != -1:
            pass


def main() -> int:
    try:
        Tracker().run()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
