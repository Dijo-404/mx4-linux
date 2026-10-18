"""Exercise the real listener and Solaar key setters without accessing hidraw."""

import io
import json
import os
import queue
import signal
import struct
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from logitech_receiver import hidpp20

from mx4_linux import event_worker as worker
from mx4_linux.device_worker import _device_id


@pytest.fixture(autouse=True)
def private_reporting_state(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))


FEATURE = 0x1B04
INDEX = 9
TARGET = _device_id("/dev/hidraw-test", 255)


def notification(address=0, data=b"\0" * 8, number=255, index=INDEX):
    return SimpleNamespace(devnumber=number, sub_id=index, address=address, data=data)


class FakeDevice:
    def __init__(self):
        self.name, self.number, self.online = "MX Master 4", 255, True
        self.id = self.unitId = "a1b2c3d4"
        self.handle, self.receiver = 23, None
        self.features = {FEATURE: INDEX}
        # Persistent and analytics bits deliberately survive temporary changes.
        self.reporting = {80: (0, 80), 82: (0x104, 83), 416: (0x104, 416)}
        self.writes, self.fail_after_write = [], None
        self.ping = Mock(return_value=True)
        self.keys = [
            hidpp20.ReprogrammableKeyV4(self, i, cid, cid, 0x120, 0, 0, 0)
            for i, cid in enumerate(self.reporting)
        ]
        self.close = Mock()
        self.feature_request = Mock(side_effect=self._feature_request)

    def _feature_request(self, feature, function, *data):
        if feature == 3 and function == 0:
            return b'\0' + bytes.fromhex(self.id) + b'\0' * 8
        assert feature == FEATURE
        payload = bytes(data)
        cid = int.from_bytes(payload[:2])
        flags, mapping = self.reporting[cid]
        if function == 0x20:
            return struct.pack("!HBHB", cid, flags & 255, mapping, flags >> 8)
        assert function == 0x30
        cid, change, remap = struct.unpack("!HBH", payload)
        self.writes.append((cid, change, remap))
        for bit in (1, 4, 16, 64):
            if change & (bit << 1):
                flags = flags | bit if change & bit else flags & ~bit
        self.reporting[cid] = (flags, remap or mapping)
        if self.fail_after_write == len(self.writes):
            raise OSError("write acknowledgement lost")
        return payload


def setup_session():
    dev, commands, events = FakeDevice(), queue.Queue(), []
    api = SimpleNamespace(
        hidpp20=hidpp20,
        hidpp20_constants=SimpleNamespace(SupportedFeature=SimpleNamespace(REPROG_CONTROLS_V4=FEATURE)),
        configuration=SimpleNamespace(persister=Mock()),
        hidpp10=SimpleNamespace(set_configuration_pending_flags=Mock()),
        settings_templates=SimpleNamespace(SETTINGS=[], check_feature=Mock()),
    )
    info = SimpleNamespace(path="/dev/hidraw-test", isDevice=True)
    api.base = SimpleNamespace(
        receivers_and_devices=Mock(return_value=[info]),
        open_path=Mock(return_value=23), close=Mock(),
        read=Mock(), make_notification=Mock(side_effect=lambda *args: notification(args[2][1], args[2][2:], args[1], args[2][0])),
    )

    def factory(low_level, _info):
        low_level.open_path(info.path)
        dev.close.side_effect = lambda: low_level.close(dev.handle)
        return dev

    def read(*_args):
        commands.put({"op": "stop"})
        return None

    api.device = SimpleNamespace(create_device=Mock(side_effect=factory))
    api.receiver = SimpleNamespace(create_receiver=Mock())
    api.base.read.side_effect = read
    return dev, api, commands, events


def run(dev, api, commands, events, **config):
    return worker.run({"device_id": TARGET, **config}, api, commands, events.append)


def test_real_solaar_setters_restore_flags_and_keep_all_mappings():
    dev, api, commands, events = setup_session()
    before = dict(dev.reporting)
    assert run(dev, api, commands, events, controls=[82, 416])
    assert events == [
        {"type": "ready", "controls": [82, 416], "ring_button": 416, "raw_xy": True},
        {"type": "stopped", "restored": True},
    ]
    assert dev.reporting == before
    assert {cid for cid, _, _ in dev.writes} == {82, 416}
    assert all(remap == 0 for _, _, remap in dev.writes)
    assert dev.handle == 23 and type(dev.handle) is int
    dev.close.assert_called_once()
    api.base.close.assert_called_once_with(23)
    api.configuration.persister.assert_not_called()
    api.hidpp10.set_configuration_pending_flags.assert_not_called()


def test_listener_uses_separate_software_id_for_discovery_events_and_restore():
    dev, api, commands, events = setup_session()
    api.base.SOLAAR_SOFTWARE_ID = 0x0B
    original_request = dev._feature_request
    original_read = api.base.read.side_effect

    def request(*args):
        assert api.base.SOLAAR_SOFTWARE_ID == 0x0D
        return original_request(*args)

    def read(*args):
        assert api.base.SOLAAR_SOFTWARE_ID == 0x0D
        return original_read(*args)

    dev.feature_request.side_effect = request
    api.base.read.side_effect = read
    dev.ping.side_effect = lambda: api.base.SOLAAR_SOFTWARE_ID == 0x0D
    assert run(dev, api, commands, events)
    assert api.base.SOLAAR_SOFTWARE_ID == 0x0B
    assert dev.reporting[416] == (0x104, 416)


def test_preexisting_diversion_and_raw_motion_remain_enabled_without_writes():
    dev, api, commands, events = setup_session()
    dev.reporting[416] = (0x115, 416)
    assert run(dev, api, commands, events)
    assert dev.writes == []
    assert dev.reporting[416] == (0x115, 416)


@pytest.mark.parametrize("config", [
    {"controls": [80]}, {"controls": [81]}, {"controls": [416, 416]},
    {"controls": [True]}, {"controls": []}, {"controls": "416"},
    {"raw_xy": 1}, {"ring_button": True}, {"controls": [82]},
    {"device_id": "/dev/untrusted"},
])
def test_invalid_request_never_changes_any_device(config):
    dev, api, commands, events = setup_session()
    assert not run(dev, api, commands, events, **config)
    assert dev.writes == []
    assert events[0]["type"] == "error"
    assert events[-1] == {"type": "stopped", "restored": None}


@pytest.mark.parametrize("bad_flags", [0, 0x20, 0x1A0])
def test_all_control_capabilities_checked_before_first_write(bad_flags):
    dev, api, commands, events = setup_session()
    dev.keys[-1]._flags = bad_flags
    assert not run(dev, api, commands, events, controls=[82, 416])
    assert not dev.writes
    assert not any(event["type"] == "ready" for event in events)


def test_acknowledgement_failure_after_firmware_change_still_restores():
    dev, api, commands, events = setup_session()
    before = dict(dev.reporting)
    dev.fail_after_write = 2  # Raw XY changed but acknowledgement was lost.
    assert not run(dev, api, commands, events)
    assert dev.reporting == before
    assert events[-2]["type"] == "error"
    assert not any(event["type"] == "ready" for event in events)
    dev.close.assert_called_once()


def test_failed_live_snapshot_never_accepts_solaar_fallback_defaults():
    dev, api, commands, events = setup_session()
    dev.feature_request.side_effect = lambda feature, function, *args: dev._feature_request(feature, function, *args) if feature == 3 else None
    assert not run(dev, api, commands, events)
    assert dev.writes == []
    assert "No live reporting reply" in events[0]["message"]


def test_live_readback_required_before_ready_and_restored_after_timeout():
    dev, api, commands, events = setup_session()
    before = dict(dev.reporting)
    original = dev._feature_request
    reads = []

    def requests(feature, function, *data):
        if function == 0x20:
            reads.append(True)
            if len(reads) == 2:
                return None
        return original(feature, function, *data)

    dev.feature_request.side_effect = requests
    assert not run(dev, api, commands, events)
    assert dev.reporting == before
    assert not any(event["type"] == "ready" for event in events)


def test_button_edges_motion_filtering_and_release_on_stop():
    dev, api, commands, events = setup_session()
    packets = iter([
        (17, 255, bytes([INDEX, 0]) + struct.pack("!4H", 416, 82, 0, 0) + b"\0" * 8),
        (17, 255, bytes([INDEX, 0]) + struct.pack("!4H", 416, 82, 0, 0) + b"\0" * 8),
        (17, 1, bytes([INDEX, 0]) + b"\0" * 16),  # Another paired mouse.
        (17, 255, bytes([INDEX + 1, 0]) + b"\0" * 16),
        (17, 255, bytes([INDEX, 16]) + struct.pack("!hh", -314, 107) + b"\0" * 12),
        (17, 255, bytes([INDEX, 0]) + struct.pack("!4H", 82, 0, 0, 0) + b"\0" * 8),
        (17, 255, bytes([INDEX, 16]) + struct.pack("!hh", 1, 1) + b"\0" * 12),
    ])

    def read(*_args):
        try:
            return next(packets)
        except StopIteration:
            commands.put({"op": "stop"})

    api.base.read.side_effect = read
    assert run(dev, api, commands, events, controls=[82, 416])
    assert events[1:-1] == [
        {"type": "button", "control": 82, "pressed": True},
        {"type": "button", "control": 416, "pressed": True},
        {"type": "motion", "dx": -314, "dy": 107},
        {"type": "button", "control": 416, "pressed": False},
        {"type": "button", "control": 82, "pressed": False, "cancelled": True},
    ]


def test_decoder_ignores_malformed_data_and_non_notification_addresses():
    events = []
    decoder = worker._Decoder(255, INDEX, [416], 416, True, events.append)
    decoder.active = True
    decoder(notification(data=struct.pack("!4H", 416, 0, 0, 0)))
    for data in (b"", b"\0", b"\0" * 9, b"\0" * 18, b"\0" * 15 + b"\1", b"\0" * 10):
        decoder(notification(data=data))
    for data in (b"\0", b"\0" * 6, b"\0" * 15 + b"\1"):
        decoder(notification(address=16, data=data))
    decoder(notification(address=8))  # Solaar software-ID request reply.
    assert decoder.held == {416}
    assert events == [{"type": "button", "control": 416, "pressed": True}]


def test_cleanup_releases_are_cancelled_once_and_disable_later_notifications():
    events = []
    decoder = worker._Decoder(255, INDEX, [82, 416], 416, True, events.append)
    decoder.active = True
    decoder(notification(data=struct.pack("!4H", 416, 82, 0, 0)))
    decoder.release()
    decoder.release()
    decoder(notification(data=struct.pack("!4H", 416, 82, 0, 0)))
    assert not decoder.active and not decoder.held
    assert events[-2:] == [
        {"type": "button", "control": 82, "pressed": False, "cancelled": True},
        {"type": "button", "control": 416, "pressed": False, "cancelled": True},
    ]
    assert len(events) == 4


def test_haptic_request_notifications_use_same_decoder_and_are_not_swallowed():
    dev, api, commands, events = setup_session()
    setting = SimpleNamespace(choices=[0, 1])

    def play(value, save=True):
        assert value == 1 and save is False
        dev.handle.notifications_hook(notification(data=struct.pack("!4H", 416, 0, 0, 0)))
        dev.handle.notifications_hook(notification(address=16, data=struct.pack("!hh", 8, -5)))
        return value

    setting.write = Mock(side_effect=play)
    api.settings_templates.SETTINGS = [SimpleNamespace(name="haptic-play")]
    api.settings_templates.check_feature.return_value = setting
    commands.put({"op": "haptic", "waveform": 1})
    assert run(dev, api, commands, events)
    assert events[1:-1] == [
        {"type": "button", "control": 416, "pressed": True},
        {"type": "motion", "dx": 8, "dy": -5},
        {"type": "button", "control": 416, "pressed": False, "cancelled": True},
    ]


def test_eof_command_reader_and_malformed_command_are_handled():
    dev, api, commands, events = setup_session()
    worker._read_commands(io.BytesIO(b"{bad\n[]\n"), commands)
    assert run(dev, api, commands, events)
    assert [event["type"] for event in events] == ["ready", "error", "error", "stopped"]
    assert dev.reporting[416] == (0x104, 416)


def test_sigterm_uses_normal_restore_path_and_restores_signal_handlers():
    dev, api, commands, events = setup_session()
    stop = threading.Event()
    old_handler = signal.getsignal(signal.SIGTERM)

    def read(*_args):
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    api.base.read.side_effect = read
    with worker._stop_signals(stop):
        assert worker.run({"device_id": TARGET}, api, commands, events.append, stop)
    assert signal.getsignal(signal.SIGTERM) == old_handler
    assert dev.reporting[416] == (0x104, 416)
    assert events[-1] == {"type": "stopped", "restored": True}


def test_unplug_reports_error_and_attempts_all_restoration():
    dev, api, commands, events = setup_session()
    api.base.read.side_effect = OSError("receiver unplugged")
    assert not run(dev, api, commands, events)
    assert dev.reporting[416] == (0x104, 416)
    assert "unplugged" in events[-2]["message"]
    dev.close.assert_called_once()


def test_cleanup_failure_is_reported_and_other_controls_still_restore():
    dev, api, commands, events = setup_session()
    original = dev._feature_request

    def requests(feature, function, *data):
        # Firmware accepts enabling diversion, but refuses its restoration on
        # the ring. RawXY and the other selected control must still restore.
        if function == 0x30 and bytes(data) == struct.pack("!HBH", 416, 2, 0):
            raise OSError("firmware unavailable")
        return original(feature, function, *data)

    dev.feature_request.side_effect = requests
    assert not run(dev, api, commands, events, controls=[82, 416])
    assert dev.reporting[82] == (0x104, 83)
    assert not dev.reporting[416][0] & 16
    assert "Could not restore mouse reporting" in events[-2]["message"]
    assert events[-1] == {"type": "stopped", "restored": False}
    dev.close.assert_called_once()


def test_receiver_handle_is_retained_and_owns_child_notifications():
    dev, api, commands, events = setup_session()
    receiver = SimpleNamespace(handle=23)
    dev.receiver, dev.handle = receiver, None
    info = SimpleNamespace(path="/dev/hidraw-test", isDevice=False)
    api.base.receivers_and_devices.return_value = [info]

    class Receiver:
        handle = 23

        def count(self):
            return 1

        def __getitem__(self, number):
            return dev if number == 1 else None

        def close(self):
            api.base.close(self.handle)

    receiver = Receiver()
    dev.receiver, dev.number = receiver, 1

    def factory(low_level, _info):
        low_level.open_path(info.path)
        return receiver

    def read(handle, _timeout):
        assert handle is receiver.handle
        handle.notifications_hook(notification(number=1, data=struct.pack("!4H", 416, 0, 0, 0)))
        commands.put({"op": "stop"})

    api.receiver.create_receiver.side_effect = factory
    api.base.read.side_effect = read
    assert worker.run({"device_id": _device_id(info.path, 1)}, api, commands, events.append)
    assert receiver.handle == 23 and type(receiver.handle) is int
    assert dev.handle is None
    assert events[1:-1] == [
        {"type": "button", "control": 416, "pressed": True},
        {"type": "button", "control": 416, "pressed": False, "cancelled": True},
    ]
    api.base.close.assert_called_once_with(23)


def test_heartbeat_detects_wireless_mouse_loss_and_releases_held_control():
    dev, api, commands, events = setup_session()
    dev.ping.side_effect = [True, False]

    def read(*_args):
        dev.handle.notifications_hook(notification(data=struct.pack("!4H", 416, 0, 0, 0)))

    api.base.read.side_effect = read
    with patch.object(worker, "_HEARTBEAT_SECONDS", -1):
        assert not run(dev, api, commands, events)
    assert events[-3] == {"type": "button", "control": 416, "pressed": False, "cancelled": True}
    assert "disconnected" in events[-2]["message"]
    assert dev.reporting[416] == (0x104, 416)


def test_parent_output_closes_without_skipping_reporting_restoration():
    dev, api, commands, _ = setup_session()
    emit = Mock(side_effect=BrokenPipeError)
    assert worker.run({"device_id": TARGET}, api, commands, emit)
    assert dev.reporting[416] == (0x104, 416)


@pytest.mark.parametrize("raw", [b"{bad\n", b"[]\n", b"{}\n", b"x" * 65537 + b"\n"])
def test_bad_subprocess_configuration_returns_only_ndjson(raw):
    result = subprocess.run([sys.executable, "-m", "mx4_linux.event_worker"], input=raw, capture_output=True, check=True)
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["type"] for event in events] == ["error", "stopped"]


def test_fd_lines_preserves_commands_read_ahead_and_enforces_bounds():
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b'{"device_id":"x"}\n{"op":"stop"}\nabcdef')
        os.close(write_fd)
        write_fd = None
        reader = worker._FDLines(read_fd)
        assert reader.readline(100) == b'{"device_id":"x"}\n'
        assert reader.readline(100) == b'{"op":"stop"}\n'
        assert reader.readline(3) == b"abc"
        assert reader.readline(100) == b"def"
        assert reader.readline(100) == b""
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)


@pytest.mark.parametrize("raw", [b"[]\n", b"{}\n", b'{"device_id":"x","controls":[80]}\n'])
def test_invalid_initial_configuration_exits_with_parent_stdin_still_open(raw):
    process = subprocess.Popen([sys.executable, "-m", "mx4_linux.event_worker"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        process.stdin.write(raw)
        process.stdin.flush()
        assert process.wait(timeout=5) == 0
        events = [json.loads(line) for line in process.stdout.readlines()]
        assert [event["type"] for event in events] == ["error", "stopped"]
        assert b"Fatal Python error" not in process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def test_fd_command_reader_can_remain_blocked_during_normal_interpreter_exit():
    # Simulate valid configuration failing discovery while the parent retains
    # its stdin pipe. This helper never imports/opens real device discovery.
    program = """
import threading
import sys
from mx4_linux.event_worker import _FDLines, _read_commands
import queue
started = threading.Event()
class Reader(_FDLines):
    def readline(self, limit):
        started.set()
        return super().readline(limit)
threading.Thread(target=_read_commands, args=(Reader(sys.stdin.fileno()), queue.Queue()), daemon=True).start()
started.wait()
print('finished', flush=True)
"""
    process = subprocess.Popen([sys.executable, "-c", program], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.wait(timeout=5) == 0
        assert process.stdout.read() == b"finished\n"
        assert b"Fatal Python error" not in process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def test_valid_configuration_discovery_failure_exits_with_stdin_still_open():
    program = """
from mx4_linux import event_worker
def unavailable():
    raise RuntimeError('injected unavailable mouse; no hardware accessed')
event_worker._load_solaar = unavailable
event_worker.main()
"""
    process = subprocess.Popen([sys.executable, "-c", program], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        process.stdin.write(json.dumps({"device_id": TARGET}).encode() + b"\n")
        process.stdin.flush()
        assert process.wait(timeout=5) == 0
        events = [json.loads(line) for line in process.stdout.readlines()]
        assert [event["type"] for event in events] == ["error", "stopped"]
        assert "injected unavailable mouse" in events[0]["message"]
        assert b"Fatal Python error" not in process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def add_control(dev, cid):
    dev.reporting[cid] = (0x104, cid)
    dev.keys.append(hidpp20.ReprogrammableKeyV4(dev, len(dev.keys), cid, cid, 0x120, 0, 0, 0))


def test_profile_transition_restores_native_mapping_and_retains_original_on_reacquire():
    dev, api, commands, events = setup_session()
    add_control(dev, 83)
    before = dict(dev.reporting)
    phases = []
    def read(*_):
        phases.append(dict(dev.reporting))
        if len(phases) == 1:
            commands.put({'op': 'configure', 'config_id': 1, 'controls': [83, 416], 'ring_button': 416, 'raw_xy': True})
        elif len(phases) == 2:
            commands.put({'op': 'configure', 'config_id': 2, 'controls': [82, 416], 'ring_button': 416, 'raw_xy': True})
        else:
            commands.put({'op': 'stop'})
    api.base.read.side_effect = read
    assert run(dev, api, commands, events, controls=[82, 416])
    assert phases[0][82] == (0x105, 83) and phases[0][83] == before[83]
    assert phases[1][82] == before[82] and phases[1][83] == (0x105, 83)
    assert phases[2][82] == (0x105, 83) and phases[2][83] == before[83]
    assert dev.reporting == before
    assert [e['config_id'] for e in events if e['type'] == 'configured'] == [1, 2]


def test_profile_transition_defers_until_held_button_released_without_false_edges():
    dev, api, commands, events = setup_session()
    add_control(dev, 83)
    count = 0
    def read(*_):
        nonlocal count
        count += 1
        if count == 1:
            dev.handle.notifications_hook(notification(data=struct.pack('!4H', 82, 0, 0, 0)))
            commands.put({'op': 'configure', 'config_id': 1, 'controls': [83, 416], 'ring_button': 416, 'raw_xy': True})
        elif count == 2:
            assert dev.reporting[82][0] & 1
            assert not any(e['type'] == 'configured' for e in events)
            dev.handle.notifications_hook(notification(data=b'\0' * 8))
        else:
            assert not dev.reporting[82][0] & 1
            commands.put({'op': 'stop'})
    api.base.read.side_effect = read
    assert run(dev, api, commands, events, controls=[82, 416])
    assert [e for e in events if e['type'] == 'button'] == [
        {'type': 'button', 'control': 82, 'pressed': True},
        {'type': 'button', 'control': 82, 'pressed': False}]


@pytest.mark.parametrize('command', [
    {'op': 'configure', 'controls': [80], 'config_id': 1},
    {'op': 'configure', 'controls': [416], 'config_id': True},
    {'op': 'configure', 'controls': [416], 'config_id': 1, 'raw_xy': 1},
    {'op': 'configure', 'controls': [416], 'config_id': 1, 'device_id': 'other'},
])
def test_invalid_profile_transition_stops_and_restores(command):
    dev, api, commands, events = setup_session()
    before = dict(dev.reporting)
    commands.put(command)
    assert not run(dev, api, commands, events)
    assert dev.reporting == before
    assert events[-2]['type'] == 'error'


def test_failed_profile_transition_restores_old_and_newly_touched_controls():
    dev, api, commands, events = setup_session()
    add_control(dev, 83)
    before = dict(dev.reporting)
    def read(*_):
        commands.put({'op': 'configure', 'config_id': 1, 'controls': [83, 416], 'ring_button': 416, 'raw_xy': True})
        dev.fail_after_write = len(dev.writes) + 2
    api.base.read.side_effect = read
    assert not run(dev, api, commands, events, controls=[82, 416])
    assert dev.reporting == before
    assert not any(e['type'] == 'configured' for e in events)


def test_press_received_during_transition_snapshot_defers_before_any_write():
    dev, api, commands, events = setup_session()
    add_control(dev, 83)
    original = dev._feature_request
    trigger = False
    count = 0
    def request(feature, function, *data):
        nonlocal trigger
        if trigger and function == 0x20:
            trigger = False
            dev.handle.notifications_hook(notification(data=struct.pack('!4H', 82, 0, 0, 0)))
        return original(feature, function, *data)
    def read(*_):
        nonlocal count, trigger
        count += 1
        if count == 1:
            trigger = True
            commands.put({'op': 'configure', 'config_id': 1, 'controls': [83, 416], 'ring_button': 416, 'raw_xy': True})
        elif count == 2:
            assert dev.reporting[82][0] & 1
            assert not any(e['type'] == 'configured' for e in events)
            dev.handle.notifications_hook(notification(data=b'\0' * 8))
        else:
            commands.put({'op': 'stop'})
    dev.feature_request.side_effect = request
    api.base.read.side_effect = read
    assert run(dev, api, commands, events, controls=[82, 416])
    assert any(e['type'] == 'configured' for e in events)


def test_malformed_configure_requires_explicit_control_and_reporting_fields():
    dev, api, commands, events = setup_session()
    commands.put({'op': 'configure', 'config_id': 1})
    assert not run(dev, api, commands, events)
    assert dev.reporting[416] == (0x104, 416)


def test_journal_precedes_each_setter_and_clears_only_after_confirmed_stop(monkeypatch):
    from mx4_linux.reporting_state import Ownership
    dev, api, commands, events = setup_session()
    original = dev._feature_request
    def request(feature, function, *args):
        if feature == FEATURE and function == 0x30:
            # Both acquire and restore have a durable original snapshot first.
            record = Ownership('unit:a1b2c3d4').read()
            assert record and record['controls'][0]['flags'] == 0x104
        return original(feature, function, *args)
    dev.feature_request.side_effect = request
    assert run(dev, api, commands, events)
    assert events[-1]['restored'] is True
    assert Ownership('unit:a1b2c3d4').read() is None


def test_persistence_failure_before_setter_does_not_change_firmware(monkeypatch):
    monkeypatch.setattr('mx4_linux.reporting_state.atomic_json', Mock(side_effect=OSError('disk full')))
    dev, api, commands, events = setup_session()
    assert not run(dev, api, commands, events)
    assert dev.writes == []


def test_orphan_recovers_before_new_baseline_and_already_restored_needs_no_write():
    from mx4_linux.reporting_state import Ownership
    dev, api, commands, events = setup_session()
    with Ownership('unit:a1b2c3d4') as owner:
        owner.touch(416, 0x104, 416, 17)
    dev.reporting[416] = (0x115, 416)
    assert run(dev, api, commands, events)
    assert dev.reporting[416] == (0x104, 416)
    assert dev.writes[:2] == [(416, 32, 0), (416, 2, 0)]
    assert Ownership('unit:a1b2c3d4').read() is None
    before = list(dev.writes)
    with Ownership('unit:a1b2c3d4') as owner:
        owner.touch(416, 0x104, 416, 17)
        worker.recover_reporting(dev, api, owner)
        assert owner.read() is None
    assert before == dev.writes


def test_orphan_mapping_conflict_validated_before_any_recovery_write():
    from mx4_linux.reporting_state import Ownership
    dev, api, commands, events = setup_session()
    with Ownership('unit:a1b2c3d4') as owner:
        owner.touch(82, 0x104, 83, 1)
        owner.touch(416, 0x104, 416, 17)
    dev.reporting[82] = (0x105, 83)
    dev.reporting[416] = (0x115, 86)
    assert not run(dev, api, commands, events)
    assert dev.writes == []
    assert events[-1]['restored'] is False
    assert Ownership('unit:a1b2c3d4').read()


def test_reporting_lock_contention_never_writes():
    from mx4_linux.reporting_state import Ownership
    dev, api, commands, events = setup_session()
    with Ownership('unit:a1b2c3d4'):
        assert not run(dev, api, commands, events)
    assert not dev.writes
    assert events[-1]['restored'] is None


def test_fresh_identity_change_stops_before_setter_and_keeps_orphan():
    from mx4_linux.reporting_state import Ownership
    dev, api, commands, events = setup_session()
    original = dev._feature_request
    id_reads = 0
    def requests(feature, function, *args):
        nonlocal id_reads
        if feature == 3:
            id_reads += 1
            if id_reads > 2:
                return b'\0' + bytes.fromhex('d1e2f3a4') + b'\0' * 8
        return original(feature, function, *args)
    dev.feature_request.side_effect = requests
    assert not run(dev, api, commands, events)
    assert len(dev.writes) <= 1
    assert Ownership('unit:a1b2c3d4').read()


def test_restore_timeout_after_firmware_change_can_be_confirmed_live():
    from mx4_linux.reporting_state import Ownership
    dev, api, commands, events = setup_session()
    # Enabling writes two flags; the first restore write changes firmware then
    # raises. Its live original value still authorizes confirmed cleanup.
    dev.fail_after_write = 3
    assert run(dev, api, commands, events)
    assert events[-1]['restored'] is True
    assert dev.reporting[416] == (0x104, 416)
    assert Ownership('unit:a1b2c3d4').read() is None


def test_external_owned_bit_change_is_not_reasserted_by_reconfiguration():
    dev, api, commands, events = setup_session()
    changed_at = []
    def emit(event):
        events.append(event)
        if event['type'] == 'ready':
            flags, mapping = dev.reporting[416]
            dev.reporting[416] = (flags & ~16, mapping)
            changed_at.append(len(dev.writes))
            commands.put({'op': 'configure', 'config_id': 1, 'controls': [416], 'ring_button': 416, 'raw_xy': True})
    assert not worker.run({'device_id': TARGET}, api, commands, emit)
    assert not any(event['type'] == 'configured' for event in events)
    # Cleanup may restore the remaining diverted bit, but never re-enable RAW_XY.
    assert all(not (change & 16) for _cid, change, _mapping in dev.writes[changed_at[0]:])
