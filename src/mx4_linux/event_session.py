"""Nonblocking parent bridge for the isolated opt-in event worker."""

from collections import deque
import json
import logging
import subprocess
import sys
import threading
import time

from .event_worker import _validate


_LOG = logging.getLogger(__name__)
_MAX_LINE = 65536


class EventSession:
    def __init__(self, callback):
        self.callback = callback
        self.process = None
        self.device_id = None
        self.session_id = 0
        self.ready = False
        self.stopping = False
        self._lock = threading.RLock()
        self._feedback_time = 0
        self._config_id = 0
        self.restored = None

    @property
    def running(self):
        with self._lock:
            return self.process is not None and self.process.poll() is None

    def _current(self, process, session_id):
        return self.process is process and self.session_id == session_id

    def _emit(self, process, session_id, event):
        """Serialize callbacks with replacement so old readers cannot leak events.

        The token also lets a GUI reject an old event already queued by GLib.
        A callback failure must request restoration rather than abandon a worker.
        """
        with self._lock:
            if not self._current(process, session_id):
                return False
            try:
                self.callback({**event, "session_id": session_id})
                return True
            except Exception:
                _LOG.exception("Mouse event callback failed; stopping its listener")
                self._stop_process(process, session_id)
                return False

    def start(self, device_id, config, expected_stable_id=None):
        controls = sorted({config["ring_button"], *map(int, config["button_bindings"])})
        request = {"device_id": device_id, "controls": controls, "ring_button": config["ring_button"], "raw_xy": True}
        if expected_stable_id is not None:
            request["expected_stable_id"] = expected_stable_id
        _validate(request)
        with self._lock:
            if self.running:
                raise RuntimeError("Mouse integration is already running.")
            process = subprocess.Popen([sys.executable, "-m", "mx4_linux.event_worker"],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding="utf-8", bufsize=1)
            self.process = process
            self.device_id = device_id
            self.session_id += 1
            session_id = self.session_id
            self.ready, self.stopping = False, False
            self._feedback_time = 0
            self._config_id = 0
            self.restored = None
        errors = deque(maxlen=8)
        errors_lock = threading.Lock()

        def stderr():
            try:
                while line := process.stderr.readline(_MAX_LINE + 1):
                    with errors_lock:
                        errors.append(line.rstrip()[:500])
            except (OSError, ValueError, UnicodeError):
                pass
            finally:
                # Only this reader closes stderr; another thread closing it
                # during readline can deadlock or raise on its next iteration.
                process.stderr.close()

        stderr_thread = threading.Thread(target=stderr, daemon=True, name="mx4-event-stderr")
        stderr_thread.start()
        if not self._send(process, session_id, request):
            self._stop_process(process, session_id)

        def events():
            stopped_event = None
            diagnosed = False
            try:
                while line := process.stdout.readline(_MAX_LINE + 1):
                    try:
                        if len(line) > _MAX_LINE:
                            raise ValueError("Event is too large")
                        event = json.loads(line)
                        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                            raise ValueError("Invalid event")
                    except ValueError:
                        diagnosed = True
                        self._emit(process, session_id, {"type": "error", "message": "Invalid response from the mouse event worker."})
                        self._stop_process(process, session_id)
                        break
                    if event["type"] == "stopped":
                        # The worker reports restoration before exiting. Keep
                        # Stop active until its process and pipes actually exit.
                        stopped_event = event
                        with self._lock:
                            if self._current(process, session_id):
                                self.ready = False
                        continue
                    with self._lock:
                        if self._current(process, session_id) and event["type"] == "ready" and not self.stopping:
                            self.ready = True
                    if not self._emit(process, session_id, event):
                        break
            except (OSError, ValueError, UnicodeError) as exc:
                diagnosed = True
                self._emit(process, session_id, {"type": "error", "message": f"Cannot read mouse listener events: {exc}"})
                self._stop_process(process, session_id)
            finally:
                with self._lock:
                    expected_stop = self._current(process, session_id) and self.stopping
                # If stdout closes while the child is still alive, request the
                # same graceful restoration as a user stop before waiting.
                if process.poll() is None:
                    self._stop_process(process, session_id)
                try:
                    code = process.wait()
                finally:
                    with self._lock:
                        if process.stdin and not process.stdin.closed:
                            try:
                                process.stdin.close()
                            except (OSError, ValueError):
                                pass
                    process.stdout.close()
                    stderr_thread.join(timeout=1)
                with self._lock:
                    if self._current(process, session_id):
                        self.ready = self.stopping = False
                if stopped_event is None and not expected_stop and not diagnosed:
                    with errors_lock:
                        detail = " ".join(errors)
                    self._emit(process, session_id, {"type": "error", "message": "Mouse listener exited unexpectedly. " + detail})
                restored = stopped_event.get("restored") if stopped_event else None
                restored = restored if type(restored) is bool else None
                if code != 0:
                    restored = None
                with self._lock:
                    if self._current(process, session_id):
                        self.restored = restored
                self._emit(process, session_id, {**(stopped_event or {"type": "stopped"}),
                                                "restored": restored, "exit_code": code})

        threading.Thread(target=events, daemon=True, name="mx4-event-reader").start()

    def _send(self, process, session_id, request):
        with self._lock:
            if (not self._current(process, session_id) or process.poll() is not None
                    or not process.stdin or process.stdin.closed):
                return False
            try:
                process.stdin.write(json.dumps(request) + "\n")
                process.stdin.flush()
                return True
            except (BrokenPipeError, OSError, ValueError):
                return False

    def send(self, request):
        with self._lock:
            if self.process is None:
                return False
            return self._send(self.process, self.session_id, request)

    def configure(self, config):
        """Request an exact control set; the worker acknowledges its safe boundary."""
        controls = sorted({config["ring_button"], *map(int, config["button_bindings"])})
        request = {"controls": controls, "ring_button": config["ring_button"], "raw_xy": True}
        _validate({"device_id": "configure", **request})
        with self._lock:
            if not self.ready or self.stopping or not self.running:
                return None
            self._config_id += 1
            config_id = self._config_id
            if self._send(self.process, self.session_id, {"op": "configure", "config_id": config_id, **request}):
                return config_id
            self._stop_process(self.process, self.session_id)
            return None

    def feedback(self, waveform=0):
        with self._lock:
            now = time.monotonic()
            if self.ready and not self.stopping and now - self._feedback_time >= .075:
                self._feedback_time = now
                self.send({"op": "haptic", "waveform": waveform})

    def _stop_process(self, process, session_id):
        with self._lock:
            if not self._current(process, session_id) or process.poll() is not None or self.stopping:
                return
            self.stopping = True
            self.ready = False
            self._send(process, session_id, {"op": "stop"})
            if process.stdin and not process.stdin.closed:
                try:
                    process.stdin.close()  # EOF also requests restoration.
                except (OSError, ValueError):
                    pass

        def wait():
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()  # SIGTERM uses the restoration path.
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._emit(process, session_id, {"type": "error", "message":
                        "The mouse listener is still restoring device reporting after 30 seconds. "
                        "Reconnect the mouse or receiver if its buttons remain unavailable."})
                except (OSError, ProcessLookupError):
                    pass  # It exited between timeout and terminate.

        threading.Thread(target=wait, daemon=True, name="mx4-event-stop").start()

    def stop(self):
        with self._lock:
            if self.process is not None:
                self._stop_process(self.process, self.session_id)
