"""Keep Solaar's GTK 3 imports and HID handles outside the GTK 4 UI."""

import json
import subprocess
import sys
import time
from copy import deepcopy


class BackendError(RuntimeError):
    pass


class Backend:
    def request(self, payload):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "mx4_linux.device_worker"],
                input=json.dumps(payload), text=True, capture_output=True, timeout=90,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendError("The mouse did not respond within 90 seconds. Wake it and try again.") from exc
        try:
            response = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            detail = result.stderr.strip()[-1500:]
            raise BackendError(f"Device worker failed. {detail or 'No response received.'}") from exc
        if not isinstance(response, dict) or not response.get("ok"):
            message = response.get("error", "Device request failed.") if isinstance(response, dict) else "Invalid device response."
            raise BackendError(str(message))
        if result.returncode:
            raise BackendError("Device worker exited unexpectedly.")
        return response

    def scan(self):
        return self.request({"op": "scan"})

    def write(self, device_id, setting, value, key=None, expected_stable_id=None, remember=True):
        payload = {"op": "write", "device_id": device_id, "setting": setting, "value": value}
        if key is not None:
            payload["key"] = key
        if expected_stable_id is not None:
            payload["expected_stable_id"] = expected_stable_id
        response = self.request(payload)
        if remember and expected_stable_id and setting != "haptic-play" and not response.get("refresh_required"):
            from .profiles import RememberedSettingsStore
            confirmed = {setting: response["value"], **response.get("values", {})}
            RememberedSettingsStore().update_confirmed(expected_stable_id, confirmed, key=key)
        return response

    def battery(self, device_id, expected_stable_id):
        return self.request({"op": "battery", "device_id": device_id, "expected_stable_id": expected_stable_id})

    def recover(self, device_id, expected_stable_id):
        return self.request({"op": "recover", "device_id": device_id, "expected_stable_id": expected_stable_id})

    def switch_host(self, device_id, index, expected_stable_id):
        return self.request({"op": "switch_host", "device_id": device_id, "index": index,
                             "expected_stable_id": expected_stable_id})


class DemoBackend:
    """An explicitly selected preview, with no physical device access."""

    def __init__(self):
        self.device = {
            "id": "demo", "name": "MX Master 4 · Demo", "online": True,
            "path": "simulated", "number": 1, "battery": "80% (simulated)",
            "stable_id": "demo:mx-master-4", "identity_source": "demo",
            "battery_percent": 80, "battery_observed_at": time.time(),
            "easy_switch": {"supported": True, "count": 3, "current": 0,
                            "hosts": [{"index": i, "name": f"Computer {i + 1} (demo)", "paired": i < 2} for i in range(3)]},
            "settings": [
                {"name": "dpi", "label": "Pointer resolution", "description": "Dots per inch.",
                 "kind": "choice", "value": 1000,
                 "choices": [{"value": v, "label": str(v)} for v in [400, 800, 1000, 1600, 3200, 8000]]},
                {"name": "smart-shift", "label": "SmartShift threshold", "description": "Wheel speed that switches to free spin.",
                 "kind": "range", "value": 20, "minimum": 0, "maximum": 50},
                {"name": "scroll-ratchet", "label": "Wheel mode", "description": "Ratchet or free spin.",
                 "kind": "choice", "value": 2, "choices": [{"value": 1, "label": "Free spin"}, {"value": 2, "label": "Ratchet"}]},
                {"name": "hires-smooth-invert", "label": "Reverse vertical scrolling", "description": "Reverse the wheel direction.",
                 "kind": "toggle", "value": False},
                {"name": "thumb-scroll-invert", "label": "Reverse thumb wheel", "description": "Reverse horizontal scrolling.",
                 "kind": "toggle", "value": False},
                {"name": "haptic-level", "label": "Haptic strength", "description": "Set to zero to turn haptics off.",
                 "kind": "range", "value": 50, "minimum": 0, "maximum": 100},
                {"name": "haptic-play", "label": "Play a haptic waveform", "description": "Send a single haptic effect to the mouse.",
                 "kind": "choice", "value": None, "choices": [{"value": i, "label": f"Waveform {i}"} for i in [*range(15), 27]]},
                {"name": "scroll-ratchet-torque", "label": "Wheel ratchet strength", "description": "Resistance of the wheel detents.",
                 "kind": "range", "value": 50, "minimum": 1, "maximum": 100},
                {"name": "hires-smooth-resolution", "label": "High-resolution scrolling", "description": "Report finer vertical scroll steps.",
                 "kind": "toggle", "value": True},
                {"name": "force-sensing", "label": "Haptic panel force", "description": "Raw supported pressure threshold.",
                 "kind": "range", "value": 4515, "minimum": 4515, "default": 5644, "maximum": 7507},
                {"name": "reprogrammable-keys", "label": "Button assignments", "description": "Choose a built-in mouse function.",
                 "kind": "map_choice", "value": {str(i): i for i in (82, 83, 86, 195, 196, 416)},
                 "keys": [{"value": i, "label": {82: "Middle button", 83: "Back button", 86: "Forward button", 195: "Gesture button", 196: "Wheel mode button", 416: "Haptic panel"}[i],
                           "choices": [{"value": j, "label": str(j)} for j in (82, 83, 86, 195, 196, 416)]} for i in (82, 83, 86, 195, 196, 416)]},
            ],
        }

    def scan(self):
        return {"ok": True, "devices": [deepcopy(self.device)], "diagnostics": []}

    def write(self, device_id, setting, value, key=None, expected_stable_id=None, remember=True):
        if expected_stable_id is not None and expected_stable_id != self.device["stable_id"]:
            raise BackendError("Demo mouse identity changed.")
        if device_id != "demo":
            raise BackendError("Unknown demo device.")
        item = next(s for s in self.device["settings"] if s["name"] == setting)
        if key is None:
            item["value"] = value
        else:
            item["value"][str(key)] = value
        return {"ok": True, "value": value}

    def battery(self, device_id, expected_stable_id):
        if device_id != "demo" or expected_stable_id != self.device["stable_id"]:
            raise BackendError("Unknown demo mouse")
        return {"ok": True, "battery": "80% (simulated)", "battery_percent": 80,
                "battery_observed_at": time.time(), "stable_id": expected_stable_id}

    def recover(self, device_id, expected_stable_id):
        if device_id != "demo" or expected_stable_id != self.device["stable_id"]:
            raise BackendError("Unknown demo mouse")
        return {"ok": True, "restored": True}

    def switch_host(self, device_id, index, expected_stable_id):
        if device_id != "demo" or expected_stable_id != self.device["stable_id"] or type(index) is not int or index not in (0, 1):
            raise BackendError("Unknown or unpaired demo channel")
        return {"ok": True, "requested": index != self.device["easy_switch"]["current"],
                "message": "Simulated switch requested; no physical mouse was changed."}
