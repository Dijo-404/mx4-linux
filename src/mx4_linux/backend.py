"""Keep Solaar's GTK 3 imports and HID handles outside the GTK 4 UI."""

import json
import subprocess
import sys


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

    def write(self, device_id, setting, value, key=None):
        payload = {"op": "write", "device_id": device_id, "setting": setting, "value": value}
        if key is not None:
            payload["key"] = key
        return self.request(payload)


class DemoBackend:
    """An explicitly selected preview, with no physical device access."""

    def __init__(self):
        self.device = {
            "id": "demo", "name": "MX Master 4 · Demo", "online": True,
            "path": "simulated", "number": 1, "battery": "80% (simulated)",
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
                 "kind": "choice", "value": None, "choices": [{"value": 0, "label": "SHARP STATE CHANGE"}, {"value": 5, "label": "HAPPY ALERT"}]},
                {"name": "reprogrammable-keys", "label": "Button assignments", "description": "Choose a built-in mouse function.",
                 "kind": "map_choice", "value": {"83": 83},
                 "keys": [{"value": 83, "label": "Back button", "choices": [{"value": 83, "label": "Back"}, {"value": 86, "label": "Forward"}]}]},
            ],
        }

    def scan(self):
        return {"ok": True, "devices": [self.device], "diagnostics": []}

    def write(self, device_id, setting, value, key=None):
        if device_id != "demo":
            raise BackendError("Unknown demo device.")
        item = next(s for s in self.device["settings"] if s["name"] == setting)
        if key is None:
            item["value"] = value
        else:
            item["value"][str(key)] = value
        return {"ok": True, "value": value}
