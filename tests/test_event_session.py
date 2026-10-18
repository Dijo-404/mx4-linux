"""Exercise parent-process races without opening a physical mouse or HID node."""

import json
import queue
import subprocess
import sys
import threading
import time

import pytest

from mx4_linux.actions import default_config
from mx4_linux.event_session import EventSession
import mx4_linux.event_session as bridge


def wait_for(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        threading.Event().wait(.002)
    assert predicate(), "condition did not become true"


class Input:
    def __init__(self):
        self.closed = False
        self.lines = []
        self.failure = None
        self.close_count = 0

    def write(self, line):
        if self.failure:
            raise self.failure
        if self.closed:
            raise ValueError("closed input")
        self.lines.append(line)

    def flush(self):
        if self.closed:
            raise ValueError("closed input")

    def close(self):
        self.closed = True
        self.close_count += 1


class Output:
    def __init__(self):
        self.queue = queue.Queue()
        self.closed = False
        self.close_count = 0
        self.close_thread = None

    def readline(self, _size):
        value = self.queue.get(timeout=3)
        if isinstance(value, Exception):
            raise value
        return value

    def put(self, value):
        self.queue.put(json.dumps(value) + "\n" if isinstance(value, dict) else value)

    def close(self):
        self.closed = True
        self.close_count += 1
        self.close_thread = threading.current_thread().name


class Process:
    def __init__(self):
        self.stdin, self.stdout, self.stderr = Input(), Output(), Output()
        self.returncode = None
        self.exited = threading.Event()
        self.reader_waiting = threading.Event()
        self.cleanup_release = threading.Event()
        self.cleanup_release.set()
        self.terminated = 0
        self.wait_timeouts = []
        self.force_timeouts = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if timeout is None:
            self.reader_waiting.set()
            assert self.cleanup_release.wait(3)
        elif self.force_timeouts:
            raise subprocess.TimeoutExpired("fake worker", timeout)
        if not self.exited.wait(timeout if timeout is not None else 3):
            raise subprocess.TimeoutExpired("fake worker", timeout)
        return self.returncode

    def terminate(self):
        self.terminated += 1

    def exit(self, code=0):
        if not self.exited.is_set():
            self.returncode = code
            self.exited.set()
            self.stdout.put("")
            self.stderr.put("")


@pytest.fixture
def processes(monkeypatch):
    created = []
    def popen(*_args, **_kwargs):
        process = Process()
        created.append(process)
        return process
    monkeypatch.setattr(bridge.subprocess, "Popen", popen)
    yield created
    for process in created:
        process.cleanup_release.set()
        process.exit()
    wait_for(lambda: all(p.stdout.closed and p.stderr.closed for p in created))


def test_initial_request_and_running_guard(processes):
    events = []
    session = EventSession(events.append)
    config = default_config()
    config["button_bindings"] = {"83": "copy", "86": "paste"}
    session.start("selected-id", config)
    process = processes[0]
    assert json.loads(process.stdin.lines[0]) == {
        "device_id": "selected-id", "controls": [83, 86, 416], "ring_button": 416, "raw_xy": True}
    assert session.running and not session.ready
    with pytest.raises(RuntimeError, match="already running"):
        session.start("another", config)
    process.stdout.put({"type": "ready"})
    wait_for(lambda: session.ready)
    assert events == [{"type": "ready", "session_id": 1}]


def test_stopped_is_delivered_once_only_after_process_exit_and_cleanup(processes):
    events = []
    session = EventSession(events.append)
    session.start("fake", default_config())
    process = processes[0]
    process.stdout.put({"type": "ready"})
    wait_for(lambda: session.ready)
    session.stop()
    process.stdout.put({"type": "stopped"})
    wait_for(lambda: process.stdout.queue.empty())
    assert session.stopping and session.running
    assert not any(e["type"] == "stopped" for e in events)
    process.exit()
    wait_for(lambda: any(e["type"] == "stopped" for e in events))
    assert not session.running and not session.stopping and not session.ready
    assert process.stdin.closed and process.stdout.closed and process.stderr.closed
    assert [e["type"] for e in events] == ["ready", "stopped"]
    assert events[-1] == {"type": "stopped", "exit_code": 0, "session_id": 1}
    assert process.stderr.close_count == 1
    assert process.stderr.close_thread == "mx4-event-stderr"


def test_old_cleanup_cannot_clear_replacement_ready_or_emit_stale_callbacks(processes):
    events = []
    session = EventSession(events.append)
    session.start("old", default_config())
    old = processes[0]
    old.cleanup_release.clear()
    old.stdout.put({"type": "ready"})
    wait_for(lambda: session.ready)
    old.exit()
    assert old.reader_waiting.wait(1)
    session.start("new", default_config())
    new = processes[1]
    new.stdout.put({"type": "ready"})
    wait_for(lambda: session.ready)
    assert session.session_id == 2
    old.cleanup_release.set()
    wait_for(lambda: old.stdout.closed)
    assert session.process is new and session.running and session.ready
    assert events == [{"type": "ready", "session_id": 1}, {"type": "ready", "session_id": 2}]
    assert not new.stdin.closed
    assert not session._emit(old, 1, {"type": "error", "message": "stale"})
    session._stop_process(old, 1)
    assert session.ready and not session.stopping and not new.stdin.closed


@pytest.mark.parametrize("payload", ["{bad\n", "[]\n", '{"type":12}\n', "x" * 65537])
def test_malformed_stdout_requests_restoration_and_still_closes_pipes(processes, payload):
    events = []
    session = EventSession(events.append)
    session.start("fake", default_config())
    process = processes[0]
    process.stdout.put(payload)
    wait_for(lambda: session.stopping)
    assert {"op": "stop"} in [json.loads(line) for line in process.stdin.lines]
    assert process.stdin.closed
    process.exit()
    wait_for(lambda: any(e["type"] == "stopped" for e in events))
    assert [e["type"] for e in events] == ["error", "stopped"]
    assert "Invalid response" in events[0]["message"]
    assert process.stdout.closed and process.stderr.closed


def test_callback_exception_stops_listener_and_cleans_all_pipes(processes):
    seen = []
    def callback(event):
        seen.append(event)
        raise RuntimeError("UI callback failed")
    session = EventSession(callback)
    session.start("fake", default_config())
    process = processes[0]
    process.stdout.put({"type": "ready"})
    wait_for(lambda: session.stopping)
    assert process.stdin.closed
    assert json.loads(process.stdin.lines[-1]) == {"op": "stop"}
    process.exit()
    wait_for(lambda: process.stdout.closed and process.stderr.closed)
    wait_for(lambda: any(e["type"] == "stopped" for e in seen))
    assert not session.ready and not session.stopping


def test_unexpected_exit_reports_stderr_and_actual_exit_code(processes):
    events = []
    session = EventSession(events.append)
    session.start("fake", default_config())
    process = processes[0]
    process.stderr.put("receiver vanished\n")
    process.exit(7)
    wait_for(lambda: any(e["type"] == "stopped" for e in events))
    assert [e["type"] for e in events] == ["error", "stopped"]
    assert "receiver vanished" in events[0]["message"]
    assert events[-1]["exit_code"] == 7


@pytest.mark.parametrize("failure", [BrokenPipeError("closed"), OSError("gone"), ValueError("closed stream")])
def test_send_and_stop_handle_closed_pipe_races(processes, failure):
    session = EventSession(lambda _event: None)
    assert session.send({"op": "stop"}) is False
    session.start("fake", default_config())
    process = processes[0]
    process.stdin.failure = failure
    assert session.send({"op": "haptic", "waveform": 0}) is False
    session.stop()
    assert process.stdin.closed and session.stopping
    session.stop()  # Repeated stop does not touch an already closed input.
    assert process.stdin.close_count == 1
    assert session.send({"op": "haptic", "waveform": 0}) is False


def test_stop_captures_old_process_without_stopping_replacement(processes):
    session = EventSession(lambda _event: None)
    session.start("old", default_config())
    old = processes[0]
    session.stop()
    old.exit()
    session.start("new", default_config())
    new = processes[1]
    wait_for(lambda: old.stdout.closed)
    assert not new.stdin.closed and new.terminated == 0
    assert session.running and not session.stopping


def test_stop_timeout_uses_sigterm_then_reports_without_sigkill(processes):
    events = []
    session = EventSession(events.append)
    session.start("fake", default_config())
    process = processes[0]
    process.force_timeouts = True
    session.stop()
    wait_for(lambda: any("30 seconds" in e.get("message", "") for e in events))
    assert process.terminated == 1
    assert process.wait_timeouts[:2] == [20, 10]
    assert session.running and session.stopping
    assert not any(e["type"] == "stopped" for e in events)


def test_feedback_throttle_and_stop_prevent_more_haptics(processes):
    session = EventSession(lambda _event: None)
    session.start("fake", default_config())
    process = processes[0]
    process.stdout.put({"type": "ready"})
    wait_for(lambda: session.ready)
    session.feedback(1)
    session.feedback(2)
    assert [json.loads(line) for line in process.stdin.lines][1:] == [{"op": "haptic", "waveform": 1}]
    session.stop()
    session.feedback(3)
    assert len(process.stdin.lines) == 3


def test_read_error_requests_restoration(processes):
    events = []
    session = EventSession(events.append)
    session.start("fake", default_config())
    process = processes[0]
    process.stdout.put(OSError("stream failed"))
    wait_for(lambda: session.stopping)
    process.exit()
    wait_for(lambda: any(e["type"] == "stopped" for e in events))
    assert "Cannot read" in events[0]["message"]
    assert process.stdin.closed and process.stdout.closed


def test_real_protocol_process_stops_and_reaps_without_any_mouse_access(monkeypatch):
    original_popen = subprocess.Popen
    script = """
import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'type': 'ready'}), flush=True)
for line in sys.stdin:
    if json.loads(line).get('op') == 'stop':
        break
print(json.dumps({'type': 'stopped'}), flush=True)
"""
    monkeypatch.setattr(bridge.subprocess, "Popen", lambda _argv, **kwargs:
                        original_popen([sys.executable, "-c", script], **kwargs))
    events = []
    session = EventSession(events.append)
    session.start("simulated device only", default_config())
    process = session.process
    try:
        wait_for(lambda: session.ready)
        session.stop()
        wait_for(lambda: any(event["type"] == "stopped" for event in events))
        assert [event["type"] for event in events] == ["ready", "stopped"]
        assert events[-1]["exit_code"] == 0
        assert not session.ready and not session.running and not session.stopping
        assert process.stdin.closed and process.stdout.closed and process.stderr.closed
    finally:
        session.stop()
        process.wait(timeout=3)


def test_configure_sends_only_validated_current_controls_and_stops_on_broken_pipe(processes):
    session = EventSession(lambda _: None)
    assert session.configure(default_config()) is None
    session.start('fake', default_config())
    process = processes[0]
    process.stdout.put({'type': 'ready'})
    wait_for(lambda: session.ready)
    config = default_config()
    config['button_bindings'] = {'82': 'copy'}
    assert session.configure(config) == 1
    assert json.loads(process.stdin.lines[-1]) == {'op': 'configure', 'config_id': 1,
        'controls': [82, 416], 'ring_button': 416, 'raw_xy': True}
    config['button_bindings'] = {'80': 'copy'}
    with pytest.raises(ValueError):
        session.configure(config)
    process.stdin.failure = BrokenPipeError()
    config['button_bindings'] = {}
    assert session.configure(config) is None
    assert session.stopping
    assert session.configure(config) is None
