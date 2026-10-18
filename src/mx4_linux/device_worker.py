"""One-request HID++ worker, isolated from the desktop UI and Solaar's config.

The wire protocol is one JSON request on stdin and one JSON response on stdout.
Discovery reads capabilities from the attached device; it never applies settings.
"""

from __future__ import annotations

import contextlib
import errno
import importlib
import json
import logging
import os
import re
import struct
import sys
from types import SimpleNamespace


ALLOWED_SETTINGS = (
    "dpi",
    "smart-shift",
    "scroll-ratchet",
    "scroll-ratchet-torque",
    "hires-smooth-invert",
    "hires-smooth-resolution",
    "thumb-scroll-invert",
    "reprogrammable-keys",
    "haptic-level",
    "haptic-play",
    "force-sensing",
)
# Wheel/button diversion requires a continuously running notification consumer.
# Do not expose thumb-scroll-mode, hires-scroll-mode, or divert-keys here.
_KIND_NAMES = {1: "toggle", 2: "choice", 4: "range", 10: "map_choice"}
_MAX_REQUEST_BYTES = 65536


def _load_solaar():
    modules = (
        "base", "device", "receiver", "settings_templates", "hidpp10",
        "hidpp20_constants",
    )
    api = SimpleNamespace(**{
        name: importlib.import_module(f"logitech_receiver.{name}") for name in modules
    })
    api.configuration = importlib.import_module("solaar.configuration")
    return api


@contextlib.contextmanager
def _isolated_solaar(api, software_id=0x0C):
    """Prevent Solaar startup writes, persistence, and leaked factory handles.

    Solaar's Receiver constructor normally writes configuration-pending flags;
    that is unnecessary for synchronous requests. Its persister is also global
    application state and must never be touched by this separate application.
    Use our own HID++ software ID so concurrent Solaar and listener requests
    cannot mistake each other's feature replies for their own. These changes
    are process-local and restored for test/embedded callers.
    """
    old_persister = api.configuration.persister
    old_pending = api.hidpp10.set_configuration_pending_flags
    old_open, old_close = api.base.open_path, api.base.close
    old_find_paired = getattr(api.base, "find_paired_node", None)
    has_software_id = hasattr(api.base, "SOLAAR_SOFTWARE_ID")
    old_software_id = getattr(api.base, "SOLAAR_SOFTWARE_ID", None)
    handles = {}

    def tracked_open(path):
        handle = old_open(path)
        # hidapi's Linux backend retries EACCES and then returns None, losing
        # errno. Recover the useful permission diagnostic without changing it.
        if not handle and os.path.exists(path) and not os.access(path, os.R_OK | os.W_OK):
            raise PermissionError(errno.EACCES, "Read/write access to HID device is required", str(path))
        if handle:
            handles[handle] = handle
        return handle

    def tracked_close(handle, *args, **kwargs):
        try:
            return old_close(handle, *args, **kwargs)
        finally:
            handles.pop(handle, None)

    api.configuration.persister = lambda _device: None
    api.hidpp10.set_configuration_pending_flags = lambda *_args, **_kwargs: True
    api.base.open_path, api.base.close = tracked_open, tracked_close
    if old_find_paired is not None:
        # The receiver handle works while a paired udev node appears. Solaar's
        # one-second busy wait otherwise repeats for every request and scan.
        api.base.find_paired_node = lambda path, number, timeout: old_find_paired(path, number, min(timeout, .01))
    if has_software_id:
        api.base.SOLAAR_SOFTWARE_ID = software_id
    try:
        yield
    finally:
        for handle in tuple(handles):
            try:
                old_close(handle)
            except Exception:
                logging.getLogger(__name__).exception("Closing HID handle")
        api.base.open_path, api.base.close = old_open, old_close
        if old_find_paired is not None:
            api.base.find_paired_node = old_find_paired
        api.configuration.persister = old_persister
        api.hidpp10.set_configuration_pending_flags = old_pending
        if has_software_id:
            api.base.SOLAAR_SOFTWARE_ID = old_software_id


def _diagnostic(path, error):
    return {
        "path": str(path),
        "message": str(error) or error.__class__.__name__,
        "permission_denied": isinstance(error, PermissionError)
        or getattr(error, "errno", None) in (errno.EACCES, errno.EPERM),
    }


def _device_id(path, number):
    # An opaque connection identifier; never use user input as an open path.
    return json.dumps([str(path), int(number)], separators=(",", ":"))


def _is_mx4(name):
    normalized = re.sub(r"[\s_-]+", " ", str(name)).strip().casefold()
    return re.fullmatch(r"(?:logitech |logi )?mx master 4(?: for (?:mac|business))?", normalized) is not None


def _choice_items(choices):
    return [{"value": int(choice), "label": str(choice)} for choice in choices]


def _json_value(value):
    if value is None or type(value) in (str, bool, float):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    raise ValueError(f"Unsupported setting value type: {type(value).__name__}")


def _read_live(setting, dev, api):
    """Read the device, without trusting Solaar's optimistic write caches."""
    if setting.name == "reprogrammable-keys":
        # Solaar's mapped_to caches values before the write acknowledgement and
        # _getCidReporting falls back to a default on errors. Require a real,
        # correctly addressed reply instead of accepting that fallback.
        feature = api.hidpp20_constants.SupportedFeature.REPROG_CONTROLS_V4
        for control in setting.choices:
            control_id = int(control)
            reply = dev.feature_request(feature, 0x20, *struct.pack("!H", control_id))
            if not reply or len(reply) < 5:
                raise RuntimeError(f"No live mapping reply for control {control_id}")
            returned_id, flags, mapped_to = struct.unpack("!HBH", reply[:5])
            if returned_id != control_id:
                raise RuntimeError(f"Mapping reply identifies another control: {returned_id}")
            key = dev.keys[dev.keys.index(control)]
            key._mapped_to = mapped_to or control_id
            key._mapping_flags = flags | ((reply[5] << 8) if len(reply) > 5 else 0)
    # Setting.read(cached=False) can retain a previous _value when a transport
    # read fails. Clear it so a timeout cannot confirm our requested value.
    setting._value = {} if setting.name == "force-sensing" else None
    value = setting.read(cached=False)
    # Solaar presents a single force button as RANGE, but its newer Settings
    # implementation still returns a map. Preserve its internal map for writes
    # while exposing the scalar expected by a range widget.
    if setting.name == "force-sensing" and setting.kind == 4:
        if not isinstance(value, dict) or len(value) != 1:
            raise RuntimeError("Force sensing did not return one live button value")
        return next(iter(value.values()))
    return value


def _describe_setting(setting, dev, api):
    kind = _KIND_NAMES.get(setting.kind)
    if kind is None:
        return None
    value = _read_live(setting, dev, api)
    if setting.name == "force-sensing":
        button = next(iter(setting._device_object.values()))
        if button is None or not button.changeable:
            return None
    if value is None and setting.name != "haptic-play":
        raise RuntimeError(f"{setting.name}: no live value received")
    result = {
        "name": setting.name,
        "label": str(setting.label),
        "description": str(setting.description),
        "kind": kind,
        "value": _json_value(value),
    }
    if kind == "range":
        minimum, maximum = setting.range
        result.update(minimum=int(minimum), maximum=int(maximum))
    elif kind == "choice":
        result["choices"] = _choice_items(setting.choices)
    elif kind == "map_choice":
        current = value or {}
        result["keys"] = [
            {"value": int(key), "label": str(key), "choices": _choice_items(choices)}
            for key, choices in setting.choices.items()
            if int(key) in current or str(int(key)) in current
        ]
        if not result["keys"]:
            return None
    return result


def _discover_settings(dev, api, path, diagnostics, names=ALLOWED_SETTINGS):
    """Only build reviewed templates: broad discovery can add event handlers."""
    found = []
    templates = api.settings_templates.SETTINGS
    for name in names:
        for template in templates:
            if template.name != name or not template.feature:
                continue
            try:
                setting = api.settings_templates.check_feature(dev, template)
                if setting is not None:
                    found.append(setting)
                    break
            except Exception as error:
                diagnostics.append(_diagnostic(path, RuntimeError(f"{name}: {error}")))
    # Tunable torque's writer accesses device.settings to preserve wheel mode.
    # Avoid invoking Solaar's unrelated, full setting discovery at that point.
    dev._settings = found
    dev._feature_settings_checked = True
    return found


def _children(owner, info, diagnostics):
    if info.isDevice:
        yield owner
        return
    try:
        expected = owner.count()
    except Exception as error:
        diagnostics.append(_diagnostic(info.path, error))
        expected = None
    found = 0
    # Solaar also probes slot 7 on receivers whose documented maximum is 6.
    for number in range(1, 8):
        if isinstance(expected, int) and found >= expected:
            return
        try:
            dev = owner[number]
            if dev is not None:
                found += 1
                yield dev
        except Exception as error:
            diagnostics.append(_diagnostic(info.path, error))


def _devices(api, diagnostics, target_id=None):
    """Yield devices while owning their receiver/direct-device handle."""
    if target_id is not None:
        try:
            target_path, target_number = json.loads(target_id)
            if not isinstance(target_path, str) or type(target_number) is not int:
                return
        except (ValueError, TypeError):
            return
    for info in api.base.receivers_and_devices():
        if target_id is not None and str(info.path) != target_path:
            continue
        owner = None
        try:
            factory = api.device.create_device if info.isDevice else api.receiver.create_receiver
            owner = factory(api.base, info)
            if owner is None:
                diagnostics.append(_diagnostic(info.path, RuntimeError("Unable to open HID device")))
                continue
            # Open only enumerated paths; a selection must never become an
            # arbitrary user-supplied path or probe unrelated receiver slots.
            if target_id is None:
                children = _children(owner, info, diagnostics)
            elif info.isDevice:
                children = (owner,) if owner.number == target_number else ()
            else:
                children = (owner[target_number],) if 1 <= target_number <= 7 else ()
            for dev in children:
                if dev is None:
                    continue
                try:
                    dev.ping()
                    if _is_mx4(dev.name):
                        yield dev, info.path
                except Exception as error:
                    diagnostics.append(_diagnostic(info.path, error))
        except Exception as error:
            diagnostics.append(_diagnostic(info.path, error))
        finally:
            if owner is not None:
                try:
                    owner.close()
                except Exception as error:
                    diagnostics.append(_diagnostic(info.path, error))


def _scan(api):
    devices, diagnostics = [], []
    with contextlib.closing(_devices(api, diagnostics)) as discovered:
        for dev, path in discovered:
            result = {
                "id": _device_id(path, dev.number),
                "name": str(dev.name),
                "online": bool(dev.online),
                "path": str(path),
                "number": int(dev.number),
                "battery": None,
                "settings": [],
            }
            if dev.online:
                try:
                    battery = dev.battery()
                    if battery is not None:
                        result["battery"] = battery.to_str() or None
                except Exception as error:
                    diagnostics.append(_diagnostic(path, RuntimeError(f"Battery: {error}")))
                for setting in _discover_settings(dev, api, path, diagnostics):
                    try:
                        descriptor = _describe_setting(setting, dev, api)
                        if descriptor is not None:
                            result["settings"].append(descriptor)
                    except Exception as error:
                        diagnostics.append(_diagnostic(path, RuntimeError(f"{setting.name}: {error}")))
            devices.append(result)
    return {"ok": True, "devices": devices, "diagnostics": diagnostics}


def _validate_value(descriptor, value, key):
    kind = descriptor["kind"]
    if kind == "toggle":
        if type(value) is not bool:
            raise ValueError("Toggle value must be a JSON boolean")
    elif type(value) is not int:
        raise ValueError("Setting value must be a JSON integer")
    elif kind == "range":
        if not descriptor["minimum"] <= value <= descriptor["maximum"]:
            raise ValueError(f"Value must be between {descriptor['minimum']} and {descriptor['maximum']}")
    elif kind == "choice":
        if value not in [choice["value"] for choice in descriptor["choices"]]:
            raise ValueError("Value is not one of the device's supported choices")
    elif kind == "map_choice":
        if type(key) is not int:
            raise ValueError("Mapping key must be a JSON integer")
        selected = next((item for item in descriptor["keys"] if item["value"] == key), None)
        if selected is None:
            raise ValueError("Mapping key is not supported by this device")
        if value not in [choice["value"] for choice in selected["choices"]]:
            raise ValueError("Value is not supported for this mapping key")
    if kind != "map_choice" and key is not None:
        raise ValueError("This setting does not accept a mapping key")


def _write(request, api):
    target_id = request.get("device_id")
    name = request.get("setting")
    if not isinstance(target_id, str) or not target_id or len(target_id) > 1024:
        raise ValueError("A valid device_id is required")
    if name not in ALLOWED_SETTINGS:
        raise ValueError("Setting is not supported by this application")
    if "value" not in request:
        raise ValueError("A setting value is required")
    diagnostics = []
    with contextlib.closing(_devices(api, diagnostics, target_id)) as discovered:
        for dev, path in discovered:
            if _device_id(path, dev.number) != target_id:
                continue
            if not dev.online:
                raise RuntimeError("Device is offline; wake it and scan again")
            names = [name]
            if name in ("smart-shift", "scroll-ratchet"):
                names.append("scroll-ratchet" if name == "smart-shift" else "smart-shift")
            elif name == "scroll-ratchet-torque":
                names.append("scroll-ratchet")
            settings = _discover_settings(dev, api, path, diagnostics, names)
            refresh_required = bool(diagnostics)
            setting = next((item for item in settings if item.name == name), None)
            if setting is None:
                raise ValueError("Setting is not supported by the connected device")
            descriptor = _describe_setting(setting, dev, api)
            if descriptor is None:
                raise ValueError("Setting is not editable on the connected device")
            value, key = request["value"], request.get("key")
            _validate_value(descriptor, value, key)
            if descriptor["kind"] == "map_choice":
                written = setting.write_key_value(key, value, save=False)
            else:
                written = setting.write(value, save=False)
            if written is None:
                raise RuntimeError("Device did not acknowledge the setting write")
            if name == "haptic-play":
                return {"ok": True, "value": value}
            actual = _read_live(setting, dev, api)
            if descriptor["kind"] == "map_choice" and isinstance(actual, dict):
                actual = actual.get(key, actual.get(str(key)))
            if actual is None or type(value) is bool and type(actual) is not bool or actual != value:
                raise RuntimeError(f"Device readback did not confirm the change (received {_json_value(actual)!r})")
            response = {"ok": True, "value": _json_value(actual)}
            values = {}
            for related in settings:
                if related is not setting:
                    try:
                        value = _read_live(related, dev, api)
                        if value is not None:
                            values[related.name] = _json_value(value)
                        else:
                            refresh_required = True
                    except Exception:
                        refresh_required = True
                        logging.getLogger(__name__).debug("Reading related setting %s", related.name, exc_info=True)
            if values:
                response["values"] = values
            if refresh_required:
                response["refresh_required"] = True
            return response
    message = "Device is unavailable; scan again"
    if diagnostics:
        message += ": " + "; ".join(item["message"] for item in diagnostics)
    raise RuntimeError(message)


def handle_request(request, api=None):
    """Execute a request. Optional api injection keeps tests independent of HID."""
    try:
        if not isinstance(request, dict):
            raise ValueError("Request must be a JSON object")
        if request.get("op") not in ("scan", "write"):
            raise ValueError("Unknown operation; use scan or write")
        # Solaar and optional native dependencies may print during imports.
        with contextlib.redirect_stdout(sys.stderr):
            api = api or _load_solaar()
            with _isolated_solaar(api):
                return _scan(api) if request["op"] == "scan" else _write(request, api)
    except Exception as error:
        return {"ok": False, "error": str(error) or error.__class__.__name__}


def main():
    try:
        raw = sys.stdin.buffer.readline(_MAX_REQUEST_BYTES + 1)
        if len(raw) > _MAX_REQUEST_BYTES:
            raise ValueError("Request is too large")
        request = json.loads(raw)
        response = handle_request(request)
    except Exception as error:
        response = {"ok": False, "error": str(error) or error.__class__.__name__}
    sys.stdout.write(json.dumps(response, ensure_ascii=True, allow_nan=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
