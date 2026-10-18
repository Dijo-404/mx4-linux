"""Opt-in, long-lived HID++ event stream for Actions Ring and Smart Actions.

One JSON configuration line is followed by optional ``stop`` / ``haptic``
commands on stdin. Stdout is flushed NDJSON; Solaar output goes to stderr.
Every HID operation stays on this process's main thread. Temporary reporting
flags are read from hardware and restored before the owning handles close.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import logging
import os
import queue
import signal
import struct
import sys
import threading
import time

from .device_worker import _device_id, _devices, _isolated_solaar, _load_solaar


ALLOWED_CONTROLS = frozenset((82, 83, 86, 195, 196, 416))
_MAX_LINE = 65536
_POLL_SECONDS = 0.05
_HEARTBEAT_SECONDS = 5.0
_LOG = logging.getLogger(__name__)


class _StopRequested(Exception):
    pass


class _FDLines:
    """Bounded lines without Python buffered-stdio locks in a daemon thread.

    A parent may leave stdin open while configuration/discovery fails. A
    daemon blocked on ``sys.stdin.buffer.readline`` then holds its lock during
    interpreter shutdown and can abort Python. ``os.read`` has no such lock.
    The same reader owns the initial line and subsequent commands so bytes
    read beyond a newline are retained when ownership moves to the thread.
    """

    def __init__(self, fd):
        self.fd, self.buffer, self.eof = fd, bytearray(), False

    def readline(self, limit=_MAX_LINE + 1):
        if limit <= 0:
            return b""
        while True:
            newline = self.buffer.find(b"\n", 0, limit)
            if newline >= 0:
                count = newline + 1
            elif len(self.buffer) >= limit:
                count = limit
            elif self.eof:
                count = len(self.buffer)
            else:
                chunk = os.read(self.fd, min(4096, limit - len(self.buffer)))
                if chunk:
                    self.buffer.extend(chunk)
                else:
                    self.eof = True
                continue
            result = bytes(self.buffer[:count])
            del self.buffer[:count]
            return result


class _NotificationHandle(int):
    """Preserve notifications encountered inside synchronous Solaar requests.

Solaar requests drain the input queue before writing and while awaiting a
reply. Its supported ``notifications_hook`` handle interface lets those reads
use the same decoder as the event loop. An int subclass retains its normal
handle ownership/closing behavior and does not open another hidraw handle.
"""

    def __new__(cls, handle, hook):
        result = super().__new__(cls, handle)
        result.notifications_hook = hook
        return result


def _validate(request):
    if not isinstance(request, dict):
        raise ValueError("Configuration must be a JSON object")
    target = request.get("device_id")
    if not isinstance(target, str) or not target or len(target) > 1024:
        raise ValueError("A valid device_id from discovery is required")
    controls = request.get("controls", [416])
    if not isinstance(controls, list) or not controls or len(controls) > len(ALLOWED_CONTROLS):
        raise ValueError("Select at least one supported mouse control")
    if any(type(cid) is not int or cid not in ALLOWED_CONTROLS for cid in controls):
        raise ValueError("Only middle, back, forward, gesture, shift, and haptic controls can be diverted")
    if len(set(controls)) != len(controls):
        raise ValueError("Controls must not contain duplicates")
    ring = request.get("ring_button", 416)
    raw_xy = request.get("raw_xy", True)
    if type(ring) is not int or ring not in ALLOWED_CONTROLS:
        raise ValueError("Invalid ring control")
    if type(raw_xy) is not bool:
        raise ValueError("raw_xy must be a JSON boolean")
    if raw_xy and ring not in controls:
        raise ValueError("The ring control must be selected for raw motion reporting")
    return target, controls, ring, raw_xy


class _Decoder:
    def __init__(self, number, feature_index, controls, ring, raw_xy, emit):
        self.number, self.feature_index = number, feature_index
        self.controls, self.ring, self.raw_xy = set(controls), ring, raw_xy
        self.emit, self.held, self.active = emit, set(), False

    def __call__(self, notification):
        if not self.active or notification.devnumber != self.number or notification.sub_id != self.feature_index:
            return
        data = notification.data
        if not isinstance(data, bytes):
            return
        if notification.address == 0x00:
            # Long HID reports include zero padding after the four CID slots.
            if not data or len(data) % 2 or len(data) > 16:
                return
            if len(data) > 8 and (len(data) != 16 or any(data[8:])):
                return
            payload = data[:8]
            pressed = set(struct.unpack(f"!{len(payload) // 2}H", payload)) & self.controls
            for cid in sorted(self.held - pressed):
                self.emit({"type": "button", "control": cid, "pressed": False})
            for cid in sorted(pressed - self.held):
                self.emit({"type": "button", "control": cid, "pressed": True})
            self.held = pressed
        elif notification.address == 0x10 and self.raw_xy and self.ring in self.held:
            if len(data) not in (4, 16) or any(data[4:]):
                return
            dx, dy = struct.unpack("!hh", data[:4])
            if dx or dy:
                self.emit({"type": "motion", "dx": dx, "dy": dy})

    def release(self):
        self.active = False
        for cid in sorted(self.held):
            self.emit({"type": "button", "control": cid, "pressed": False, "cancelled": True})
        self.held.clear()


def _reporting(dev, api, key):
    """Require an addressed, live reply; Solaar's fallback defaults are unsafe."""
    cid = int(key._cid)
    reply = dev.feature_request(api.hidpp20_constants.SupportedFeature.REPROG_CONTROLS_V4, 0x20, *struct.pack("!H", cid))
    if not reply or len(reply) < 5:
        raise RuntimeError(f"No live reporting reply for control {cid}")
    returned, low_flags, mapped_to = struct.unpack("!HBH", reply[:5])
    if returned != cid:
        raise RuntimeError(f"Reporting reply identifies another control: {returned}")
    flags = low_flags | (reply[5] << 8 if len(reply) > 5 else 0)
    key._mapping_flags, key._mapped_to = flags, mapped_to or cid
    return flags, mapped_to or cid


def _selected_keys(dev, api, controls, ring, raw_xy):
    key_flag = api.hidpp20.KeyFlag
    result = []
    # Validate and snapshot ALL requested controls before making any writes.
    for cid in controls:
        key = next((key for key in dev.keys if int(key._cid) == cid), None)
        if key is None or key_flag.DIVERTABLE not in key.flags or key_flag.VIRTUAL in key.flags:
            raise ValueError(f"Control {cid} does not support temporary diversion")
        if raw_xy and cid == ring and key_flag.RAW_XY not in key.flags:
            raise ValueError(f"Control {cid} does not support raw motion reporting")
        flags, mapped_to = _reporting(dev, api, key)
        result.append((key, flags, mapped_to))
    return result


def _play_haptic(dev, api, waveform, cached):
    if type(waveform) is not int or not 0 <= waveform <= 255:
        raise ValueError("waveform must be an integer between 0 and 255")
    if not cached:
        for template in api.settings_templates.SETTINGS:
            if template.name == "haptic-play":
                setting = api.settings_templates.check_feature(dev, template)
                if setting is not None:
                    cached.append(setting)
                    break
    if not cached:
        raise ValueError("Haptic playback is not supported by this device")
    setting = cached[0]
    if waveform not in [int(choice) for choice in setting.choices]:
        raise ValueError("This waveform is not supported by the device")
    if setting.write(waveform, save=False) is None:
        raise RuntimeError("Device did not acknowledge haptic playback")


def _listen(dev, api, controls, ring, raw_xy, commands, emit, stop_event):
    feature = api.hidpp20_constants.SupportedFeature.REPROG_CONTROLS_V4
    index = dev.features[feature] if dev.features is not None else None
    if type(index) is not int or not 0 < index < 256:
        raise ValueError("Device does not expose REPROG_CONTROLS_V4 notifications")
    decoder = _Decoder(dev.number, index, controls, ring, raw_xy, emit)
    owner = dev if dev.handle else dev.receiver
    if owner is None or not owner.handle:
        raise RuntimeError("Device has no available HID handle")
    original = owner.handle
    handle = _NotificationHandle(original, decoder)
    owner.handle = handle
    touched = []
    diversion = api.hidpp20.MappingFlag.DIVERTED.value
    raw = api.hidpp20.MappingFlag.RAW_XY_DIVERTED.value
    haptic = []
    try:
        snapshots = _selected_keys(dev, api, controls, ring, raw_xy)
        for key, flags, mapped_to in snapshots:
            if stop_event.is_set():
                raise _StopRequested()
            # Record BEFORE each write: a timeout can occur after firmware has
            # already accepted it, and must still trigger restoration.
            changed = 0
            touched.append([key, flags, changed])
            if not flags & diversion:
                touched[-1][2] |= diversion
                key.set_diverted(True)
            if raw_xy and int(key._cid) == ring and not flags & raw:
                touched[-1][2] |= raw
                key.set_rawXY_reporting(True)
            actual_flags, actual_mapping = _reporting(dev, api, key)
            required = diversion | (raw if raw_xy and int(key._cid) == ring else 0)
            if actual_flags & required != required or actual_mapping != mapped_to:
                raise RuntimeError(f"Device did not confirm reporting configuration for control {key._cid}")
        if stop_event.is_set():
            raise _StopRequested()
        emit({"type": "ready", "controls": controls, "ring_button": ring, "raw_xy": raw_xy})
        decoder.active = True
        heartbeat = time.monotonic() + _HEARTBEAT_SECONDS
        # Feature notifications are delivered by diversion itself. Receiver
        # notification flags govern unrelated connection/legacy events, so no
        # receiver register or persistent configuration changes are necessary.
        while not stop_event.is_set():
            for _ in range(32):
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    break
                if not isinstance(command, dict):
                    emit({"type": "error", "message": "Command must be a JSON object"})
                elif command.get("op") == "stop":
                    stop_event.set()
                    break
                elif command.get("op") == "haptic":
                    try:
                        _play_haptic(dev, api, command.get("waveform"), haptic)
                    except ValueError as error:
                        emit({"type": "error", "message": str(error)})
                else:
                    emit({"type": "error", "message": command.get("message", "Unknown command; use haptic or stop")})
            if stop_event.is_set():
                break
            reply = api.base.read(handle, _POLL_SECONDS)
            if reply:
                if len(reply) == 3 and isinstance(reply[2], bytes) and len(reply[2]) >= 2:
                    notification = api.base.make_notification(*reply)
                    if notification is not None:
                        decoder(notification)
            if time.monotonic() >= heartbeat:
                if not dev.ping():
                    raise RuntimeError("Mouse disconnected or is no longer responding")
                heartbeat = time.monotonic() + _HEARTBEAT_SECONDS
    finally:
        decoder.release()
        failures = []
        for key, flags, changed in reversed(touched):
            for bit, setter in ((raw, key.set_rawXY_reporting), (diversion, key.set_diverted)):
                if changed & bit:
                    try:
                        setter(bool(flags & bit))
                    except Exception as error:
                        failures.append(f"control {key._cid}: {error}")
            if changed:
                try:
                    actual, _ = _reporting(dev, api, key)
                    if actual & changed != flags & changed:
                        raise RuntimeError("Reporting restoration was not confirmed")
                except Exception as error:
                    failures.append(f"control {key._cid}: {error}")
        owner.handle = original
        if failures:
            raise RuntimeError("Could not restore mouse reporting: " + "; ".join(failures))


def run(request, api=None, commands=None, emit=None, stop_event=None):
    """Run one opt-in session; dependencies can be injected for hardware-free tests."""
    commands = commands if commands is not None else queue.Queue()
    stop_event = stop_event if stop_event is not None else threading.Event()
    emit = emit if emit is not None else lambda event: None

    def send(event):
        try:
            emit(event)
        except (BrokenPipeError, OSError):
            # The parent disappeared. Finish restoration with stdout closed.
            stop_event.set()

    try:
        target, controls, ring, raw_xy = _validate(request)
        with contextlib.redirect_stdout(sys.stderr):
            api = api or _load_solaar()
            if not hasattr(api, "hidpp20"):
                api.hidpp20 = importlib.import_module("logitech_receiver.hidpp20")
            with _isolated_solaar(api, software_id=0x0D):
                diagnostics = []
                with contextlib.closing(_devices(api, diagnostics)) as devices:
                    for dev, path in devices:
                        if _device_id(path, dev.number) != target:
                            continue
                        if not dev.online:
                            raise RuntimeError("Mouse is offline; wake it and scan again")
                        _listen(dev, api, controls, ring, raw_xy, commands, send, stop_event)
                        return True
                detail = "; ".join(item["message"] for item in diagnostics)
                raise RuntimeError("Selected mouse is unavailable; scan again" + (": " + detail if detail else ""))
    except _StopRequested:
        return True
    except Exception as error:
        _LOG.debug("Event listener stopped", exc_info=True)
        send({"type": "error", "message": str(error) or type(error).__name__})
        return False
    finally:
        send({"type": "stopped"})


def _read_commands(stream, commands):
    try:
        while True:
            line = stream.readline(_MAX_LINE + 1)
            if not line:
                break
            if len(line) > _MAX_LINE:
                commands.put({"op": "_error", "message": "Command is too large"})
                break
            try:
                commands.put(json.loads(line))
            except (ValueError, UnicodeError):
                commands.put({"op": "_error", "message": "Invalid JSON command"})
    finally:
        commands.put({"op": "stop"})


@contextlib.contextmanager
def _stop_signals(stop_event):
    previous = {}
    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, lambda *_args: stop_event.set())
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def main():
    output, stream = sys.stdout, _FDLines(sys.stdin.fileno())

    def emit(event):
        output.write(json.dumps(event, ensure_ascii=True, allow_nan=False) + "\n")
        output.flush()

    commands, stop_event = queue.Queue(), threading.Event()
    with _stop_signals(stop_event):
        try:
            line = stream.readline(_MAX_LINE + 1)
            if len(line) > _MAX_LINE:
                raise ValueError("Configuration is too large")
            request = json.loads(line)
            _validate(request)
        except (ValueError, UnicodeError) as error:
            emit({"type": "error", "message": str(error)})
            emit({"type": "stopped"})
            return
        threading.Thread(target=_read_commands, args=(stream, commands), daemon=True, name="mx4-event-commands").start()
        run(request, commands=commands, emit=emit, stop_event=stop_event)


if __name__ == "__main__":
    main()
