"""Coordinate the overlay, Smart Actions, and opt-in hardware events."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import math
import threading
import time

from .actions import DesktopActions, DesktopConfigStore, focused_context, resolve_config, restore_focus
from .context import NiriFocusObserver
from .event_session import EventSession
from .gtk_support import GLib
from .reporting_state import valid_identity


class DesktopController:
    def __init__(self, window):
        self.window = window
        self.store = DesktopConfigStore()
        self.config = None
        self.actions = None
        self.error = None
        self.message = "Enable mouse integration to use the haptic panel."
        self.closed = False
        self.action_busy = False
        self.cancelled = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mx4-actions")
        self._event_lock = threading.Lock()
        self._event_queue = deque()
        self._events_pending = False
        self.session = EventSession(self.queue_event)
        self.ring = None
        self.target = None
        self.opening = False
        self.starting = False
        self._open_token = 0
        self._dispatch_token = 0
        self._pending_released = False
        self._pending_motion = [0, 0]
        self._opening_motion = deque()
        self.context = None
        self.context_available = None
        self.observer = None
        self._context_token = self._context_revision = self._active_revision = 0
        self.effective_config = None
        self._pending_configs = {}
        self._ring_config = None
        self._adjustment_pending = None
        self._adjustment_active = self._adjustment_running = False
        self._adjustment_token = 0
        self._adjustment_yield = None
        self._selection_pending = None
        self._ring_observer = None
        self._ring_context_token = 0
        self._ring_focus_lost = threading.Event()
        self.requested_enabled = False
        self.hardware_paused = False
        self.target_identity = None
        self._intent_generation = 0
        self.reconnect_source = 0
        self.reconnect_attempt = 0
        self._reconnect_busy = False
        self._stop_waiters = []
        self.restoration_confirmed = None
        self.reacquire_blocked = False
        self.reload_config()

    def queue_event(self, event):
        """Batch UI dispatch without changing the radius-clamped motion path."""
        with self._event_lock:
            if self.closed:
                return
            self._event_queue.append(dict(event))
            if self._events_pending:
                return
            self._events_pending = True
        GLib.idle_add(self.flush_events)

    def flush_events(self):
        with self._event_lock:
            events = tuple(self._event_queue)
            self._event_queue.clear()
            self._events_pending = False
        for event in events:
            self.event(event)
        return GLib.SOURCE_REMOVE

    def reload_config(self):
        self._stop_ring_context()
        self._cancel_adjustments()
        self._dispatch_token += 1
        if self.ring and self.ring.state.visible:
            self.ring.dismiss()
        try:
            self.config = self.store.read()
            if self.actions:
                self.actions.config = deepcopy(self.config)
            else:
                self.actions = DesktopActions(self.config)
            if not self.session.running:
                self.effective_config = resolve_config(self.config, self.context.get("app_id") if self.context else None)
            self.error = None
        except RuntimeError as exc:
            self.error = str(exc)

    def notify(self, message):
        self.message = message
        if not self.closed:
            self.window.update_desktop_status()

    def start(self, automatic=False):
        if self.window.demo:
            self.notify("Preview mode does not access mouse buttons. Use Preview ring.")
            return
        if self.window.busy:
            self.notify("Wait for the device operation to finish.")
            return
        if self.starting or self.session.running:
            self.notify("Mouse integration is already starting or running.")
            return
        self.reload_config()
        if self.error:
            self.notify(self.error)
            return
        if not self.window.current or not self.window.current.get("online"):
            self.notify("Wake and refresh the mouse before enabling integration.")
            return
        identity = self.window.current.get("stable_id")
        if not valid_identity(identity) or identity.startswith("demo:"):
            self.notify("Wake and refresh this mouse to read its identity before enabling integration.")
            return
        if automatic and (self.reacquire_blocked or not self.requested_enabled or identity != self.target_identity):
            return
        if not automatic:
            self.reacquire_blocked = False
            self.window.replay_suppressed_identity = None
            self._cancel_reconnect()
            self._intent_generation += 1
            self.requested_enabled = True
            self.target_identity = identity
            self.reconnect_attempt = 0
        self.starting = True
        self._context_token += 1
        token, revision = self._context_token, self._context_revision
        device_id, document = self.window.current["id"], deepcopy(self.config)
        self.notify("Reading active application before enabling mouse integration…")
        future = self.executor.submit(focused_context)
        def begin():
            if self.closed or token != self._context_token:
                return GLib.SOURCE_REMOVE
            self.starting = False
            if (self.window.busy or not self.window.current or not self.window.current.get("online")
                    or self.window.current["id"] != device_id or self.window.current.get("stable_id") != identity):
                self.notify("The selected mouse changed; refresh and enable integration again.")
                return GLib.SOURCE_REMOVE
            try:
                if revision == self._context_revision:
                    self.context = future.result()
                self.effective_config = resolve_config(document, self.context.get("app_id") if self.context else None)
                self._pending_configs.clear()
                self._context_revision += 1
                self._active_revision = self._context_revision
                self.restoration_confirmed = None
                self.session.start(device_id, self.effective_config, expected_stable_id=identity)
                session_id = self.session.session_id
                self.observer = NiriFocusObserver(lambda event: self.queue_event(
                    {**event, "context_token": token, "session_id": session_id}))
                self.observer.start()
                self.notify("Enabling haptic panel and configured button shortcuts…")
            except (RuntimeError, OSError) as exc:
                self.notify(str(exc))
            return GLib.SOURCE_REMOVE
        future.add_done_callback(lambda _: GLib.idle_add(begin))

    def _cancel_reconnect(self):
        if getattr(self, "reconnect_source", 0):
            GLib.source_remove(self.reconnect_source)
            self.reconnect_source = 0

    def stop(self, preserve_intent=False):
        self.cancelled.set()
        if not preserve_intent:
            self.hardware_paused = False
            self.requested_enabled = False
            self._intent_generation = getattr(self, "_intent_generation", 0) + 1
            self._cancel_reconnect()
        self._stop_ring_context()
        self._cancel_adjustments()
        self._open_token += 1
        self._dispatch_token += 1
        self.opening = False
        self._stop_context()
        self._pending_motion = [0, 0]
        self._opening_motion.clear()
        if self.ring:
            self.ring.dismiss()
        if self.session.running:
            self.session.stop()
            self.notify("Restoring the mouse's previous button behavior…")

    def _stop_context(self):
        self.starting = False
        self._context_token += 1
        self._context_revision += 1
        self._pending_configs.clear()
        if self.observer:
            self.observer.stop()
            self.observer = None

    def _configure_context(self):
        if not self.config or not self.session.ready or self.session.stopping:
            return
        resolved = resolve_config(self.config, self.context.get("app_id") if self.context else None)
        config_id = self.session.configure(resolved)
        if config_id is not None:
            self._pending_configs = {config_id: (resolved, self._context_revision)}

    def _context_event(self, event):
        if event.get("context_token") != self._context_token:
            return
        context = event.get("context")
        if event["type"] == "context-unavailable":
            self.context_available = False
            context = None
            self.notify(event.get("message", "Application profiles unavailable; General assignments remain active."))
        elif (self.opening or (self.ring and self.ring.state.visible)) and (
                not context or self._is_ring_context(context)):
            return  # Only this popup may preserve its original application.
        else:
            self.context_available = True
        if context == self.context and event["type"] != "context-unavailable":
            return
        if (self.ring and self.ring.state.visible and context != self.target):
            self._cancel_adjustments()
            self.ring.dismiss()
        self.context = deepcopy(context)
        self._context_revision += 1
        self._configure_context()

    def event(self, event):
        if self.closed:
            return GLib.SOURCE_REMOVE
        session_id = getattr(self.session, "session_id", None)
        if event.get("session_id", session_id) != session_id:
            return GLib.SOURCE_REMOVE
        kind = event.get("type")
        if kind == "ring-context":
            if event.get("ring_context_token") == getattr(self, "_ring_context_token", 0):
                if self._ring_focus_lost.is_set() and self.ring and self.ring.state.visible:
                    self.ring.dismiss()
        elif kind in ("context", "context-unavailable"):
            self._context_event(event)
        elif kind == "configured":
            update = self._pending_configs.pop(event.get("config_id"), None)
            if update is not None and self.session.ready and not self.session.stopping:
                self.effective_config, self._active_revision = update
                self._pending_configs = {key: value for key, value in self._pending_configs.items()
                                         if key > event["config_id"]}
        elif kind == "ready":
            self._cancel_reconnect()
            self.reconnect_attempt = 0
            self.error = None
            self.notify("Enabled · hold the haptic panel, move, then release. A short click opens the ring.")
            if self.context_available is False:
                self.notify(self.message + " Application profiles unavailable; General assignments remain active.")
            self._configure_context()
        elif kind == "error":
            if event.get("reason") == "ownership-conflict":
                self.reacquire_blocked = True
                self._cancel_reconnect()
            self._cancel_adjustments()
            self._open_token += 1
            self._dispatch_token += 1
            self.opening = False
            self._pending_motion = [0, 0]
            self._opening_motion.clear()
            self._stop_context()
            if self.session.running:
                self.session.stop()
            if self.ring:
                self.ring.dismiss()
            self.error = event.get("message", "Mouse integration failed.")
            self.notify(self.error)
        elif kind == "warning":
            self.notify(event.get("message", "Mouse feedback is unavailable."))
        elif kind == "stopped":
            self._cancel_adjustments()
            self._open_token += 1
            self._dispatch_token += 1
            self.opening = False
            self._stop_context()
            if self.ring:
                self.ring.dismiss()
            outcome = event.get("restored")
            self.restoration_confirmed = outcome if type(outcome) is bool else None
            if outcome is True:
                self.notify(self.error or "Mouse integration disabled. Previous button behavior restored.")
            else:
                self.notify("Mouse integration stopped; previous button behavior was not confirmed. Wake/reconnect this mouse and retry recovery.")
            waiters = getattr(self, "_stop_waiters", [])
            self._stop_waiters = []
            for callback in waiters:
                callback(self.restoration_confirmed)
            if self.restoration_confirmed is not True:
                self._cancel_reconnect()
            elif getattr(self, "requested_enabled", False):
                self._schedule_reconnect()
        elif kind == "button" and event.get("cancelled"):
            self._cancel_adjustments()
            self._open_token += 1
            self._dispatch_token += 1
            self.opening = False
            self._pending_motion = [0, 0]
            self._opening_motion.clear()
            if self.ring:
                self.ring.dismiss()
        elif kind == "button" and self.session.ready and not self.session.stopping:
            cid, pressed = event.get("control"), event.get("pressed")
            active = self.effective_config or self.config
            if cid == active["ring_button"]:
                if pressed:
                    if self.opening or (self.ring and self.ring.state.visible):
                        self._open_token += 1
                        self.opening = False
                        if self.ring:
                            self.ring.dismiss()
                    else:
                        self.open_ring(held=True)
                elif self.opening:
                    self._pending_released = True
                elif self.ring and self.ring.state.visible:
                    self.ring.release_button()
            elif (pressed and self._active_revision == self._context_revision
                  and str(cid) in active["button_bindings"]):
                self.execute(active["button_bindings"][str(cid)], target=deepcopy(self.context), config=active)
        elif kind == "motion" and self.session.ready and not self.session.stopping:
            dx, dy = event.get("dx"), event.get("dy")
            if type(dx) is int and type(dy) is int:
                if self.opening:
                    self._pending_motion[0] += dx
                    self._pending_motion[1] += dy
                    self._opening_motion.append((dx, dy))
                elif self.ring and self.ring.state.visible:
                    self.ring.motion(dx, dy)
        return GLib.SOURCE_REMOVE

    def wait_stopped(self, callback, preserve_intent=False):
        """Asynchronously wait for actual child exit and its restoration outcome."""
        self.stop(preserve_intent=preserve_intent)
        if self.session.running or self.session.stopping:
            self._stop_waiters.append(callback)
            def timeout():
                if callback in self._stop_waiters:
                    self._stop_waiters.remove(callback)
                    callback(None)
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(31000, timeout)
        else:
            # With no listener, the worker's clean-journal/lock check remains authoritative.
            callback(True if self.restoration_confirmed is True or not getattr(self.session, "process", None)
                     else getattr(self.session, "restored", None))

    def _schedule_reconnect(self):
        if (self.closed or not getattr(self, "requested_enabled", False)
                or self.restoration_confirmed is not True or self.reacquire_blocked
                or not self.config.get("resume_integration", False)
                or getattr(self, "hardware_paused", False) or self.reconnect_source or self._reconnect_busy):
            return
        delays = (1, 2, 5, 10, 20, 30)
        if self.reconnect_attempt >= len(delays):
            self.notify("Mouse unavailable; wake this mouse and enable integration to retry.")
            return
        generation = self._intent_generation
        self.reconnect_source = GLib.timeout_add(delays[self.reconnect_attempt] * 1000,
                                                 lambda: self._reconnect(generation))

    def _reconnect(self, generation):
        self.reconnect_source = 0
        if (self.closed or generation != self._intent_generation or not self.requested_enabled
                or self.restoration_confirmed is not True or self.reacquire_blocked):
            return GLib.SOURCE_REMOVE
        if self.session.running and not self.session.stopping:
            return GLib.SOURCE_REMOVE
        if self.window.busy or self.session.stopping or self.starting or self.hardware_paused:
            self.reconnect_source = GLib.timeout_add(500, lambda: self._reconnect(generation))
            return GLib.SOURCE_REMOVE
        self.reconnect_attempt += 1
        self._reconnect_busy = True
        identity = self.target_identity
        def cancelled():
            return (self.closed or generation != self._intent_generation or not self.requested_enabled
                    or self.restoration_confirmed is not True or self.reacquire_blocked)
        def work():
            from .profiles import replay_remembered
            result = self.window.backend.scan()
            device = next((d for d in result["devices"] if d.get("online") and d.get("stable_id") == identity), None)
            if device is None or cancelled():
                raise RuntimeError("The same mouse is not awake yet")
            self.window.backend.recover(device["id"], identity)
            if cancelled():
                raise RuntimeError("Reconnect cancelled")
            replay_remembered(self.window.backend, device, cancelled=cancelled)
            if cancelled():
                raise RuntimeError("Reconnect cancelled")
            return self.window.backend.scan()
        def loaded(result):
            self._reconnect_busy = False
            if cancelled():
                return
            device = next((d for d in result["devices"] if d.get("online") and d.get("stable_id") == identity), None)
            if device is None:
                self._schedule_reconnect()
                return
            self.window.selected_id = device["id"]
            self.window.devices = result["devices"]
            self.window.render(result)
            self.restoration_confirmed = True
            self.start(automatic=True)
        def failed(message):
            self._reconnect_busy = False
            if not cancelled():
                self.notify(message)
                self._schedule_reconnect()
        self.window.job(work, loaded, "Looking for this mouse and recovering previous button reporting…", failed=failed)
        return GLib.SOURCE_REMOVE

    def open_ring(self, preview=False, held=False):
        if self.closed or self.action_busy:
            self.notify("A Smart Action is running; wait or cancel it first.")
            return
        if self.ring and self.ring.state.visible:
            self.ring.dismiss()
            return
        if self.opening:
            return
        self.reload_config()
        if self.error:
            self.notify(self.error)
            return
        self.opening = True
        self._open_token += 1
        self._dispatch_token += 1
        token = self._open_token
        self._pending_released = False
        self._pending_motion = [0, 0]
        self._opening_motion.clear()
        document = deepcopy(self.config)
        future = self.executor.submit(focused_context)
        def show():
            if self.closed or token != self._open_token:
                return GLib.SOURCE_REMOVE
            self.opening = False
            try:
                self.target = future.result()
                if self._is_ring_context(self.target) and self.context:
                    self.target = deepcopy(self.context)
                self._ring_config = resolve_config(document, self.target.get("app_id") if self.target else None)
                if self.ring is None:
                    from .ring import ActionsRing
                    self.ring = ActionsRing(self.window.get_application(), self.selected, self.dismissed, self.hovered, self.adjusted)
                self._watch_ring_context()
                self.ring.show_ring(self._ring_config, held=held, preview=preview or self.window.demo)
                self.feedback("open", config=self._ring_config, preview=preview or self.window.demo)
                if held:
                    for dx, dy in self._opening_motion:
                        self.ring.motion(dx, dy)
                    self._opening_motion.clear()
                    if self._pending_released:
                        self.ring.release_button()
            except Exception as exc:
                self.notify(f"Cannot show Actions Ring: {exc}")
            return GLib.SOURCE_REMOVE
        future.add_done_callback(lambda _: GLib.idle_add(show))

    def hovered(self, _index):
        if self.ring:
            self.feedback("hover", config=self._ring_config, preview=self.ring.preview)

    def feedback(self, source, *, config=None, preview=False):
        config = config or self.effective_config or self.config
        current = self.window.current
        if (self.closed or preview or self.window.demo or not current or not current.get("online")
                or not self.session.ready or self.session.stopping
                or current.get("id") != self.session.device_id or not config.get("haptic_feedback", True)
                or not config.get("haptic_events", {}).get(source, source == "hover")):
            return
        if config.get("haptic_power_save"):
            percent, observed = current.get("battery_percent"), current.get("battery_observed_at")
            if (type(percent) in (int, float) and math.isfinite(percent) and 0 <= percent <= 10
                    and type(observed) in (int, float) and math.isfinite(observed)
                    and 0 <= time.time() - observed <= 60):
                return
        self.session.feedback(config.get("haptic_waveforms", {}).get(source, 0))

    def _stop_ring_context(self):
        self._ring_context_token = getattr(self, "_ring_context_token", 0) + 1
        observer = getattr(self, "_ring_observer", None)
        self._ring_observer = None
        if observer:
            observer.stop()

    def _is_ring_context(self, context, identity=None):
        if identity is None:
            identity = getattr(self, "_ring_identity", None)
            if identity is None:
                ring = self.ring
                app_id = (getattr(ring, "context_app_id", None) if ring
                          and not getattr(ring, "overlay_supported", True) else None)
                self._ring_identity = identity = {"app_id": app_id, "window_id": None}
        if (not context or not identity["app_id"] or context.get("app_id") != identity["app_id"]
                or type(context.get("window_id")) is not int):
            return False
        if identity["window_id"] is None:
            identity["window_id"] = context["window_id"]
        return context["window_id"] == identity["window_id"]

    def _watch_ring_context(self):
        self._stop_ring_context()
        self._ring_focus_lost = threading.Event()
        ring = self.ring
        app_id = (getattr(ring, "context_app_id", None) if ring
                  and not getattr(ring, "overlay_supported", True) else None)
        self._ring_identity = identity = {"app_id": app_id, "window_id": None}
        if self.observer is not None:
            return  # Integration already owns an observer; action boundaries also verify focus.
        token, target, lost = self._ring_context_token, deepcopy(self.target), self._ring_focus_lost
        def receive(event):
            context = event.get("context")
            if (event.get("type") == "context" and context and context != target
                    and not self._is_ring_context(context, identity)):
                lost.set()  # Cancel worker delays even before GTK drains the event queue.
            self.queue_event({"type": "ring-context", "ring_context_token": token})
        self._ring_observer = NiriFocusObserver(receive)
        self._ring_observer.start()

    def dismissed(self):
        self._stop_ring_context()
        self._cancel_adjustments()
        self._dispatch_token += 1

    def selected(self, action_id, preview):
        target = deepcopy(self.target)
        config = deepcopy(self._ring_config or self.effective_config or self.config)
        self._stop_ring_context()
        self._cancel_adjustments()
        self.feedback("select", config=config, preview=preview)
        token = self._dispatch_token
        self._selection_pending = (token, action_id, target, preview, config)
        self._selection_ready = False
        def dispatch():
            if not self.closed and token == self._dispatch_token:
                self._selection_ready = True
                self._dispatch_selection()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(80, dispatch)  # Allow the layer surface to unmap first.

    def _dispatch_selection(self):
        selection = getattr(self, "_selection_pending", None)
        if (selection is None or not self._selection_ready
                or getattr(self, "_adjustment_running", False)):
            return
        self._selection_pending = None
        token, action_id, target, preview, config = selection
        if not self.closed and token == self._dispatch_token:
            self.execute(action_id, target=target, preview=preview, config=config)

    def _cancel_adjustments(self):
        self._adjustment_token = getattr(self, "_adjustment_token", 0) + 1
        self._adjustment_pending = None
        self._selection_pending = None
        self._adjustment_active = False
        self._adjustment_resuming = False
        self._adjustment_yield = None
        if getattr(self, "_adjustment_running", False):
            self.cancelled.set()

    def adjusted(self, action_id, direction, preview=False):
        """Keep one running future and at most four coalesced pending wheel steps."""
        if (self.closed or preview or self.window.demo or self.opening or self.error
                or not self.ring or not self.ring.state.visible or direction not in ("increase", "decrease")
                or (self.action_busy and not getattr(self, "_adjustment_running", False))):
            return False
        config = self._ring_config or self.effective_config or self.config
        if not config.get("actions", {}).get(action_id, {}).get("adjustment"):
            return False
        delta = 1 if direction == "increase" else -1
        pending = getattr(self, "_adjustment_pending", None)
        key = (action_id, self.target, self._open_token, self._dispatch_token, self.session.session_id)
        if pending is not None and pending["key"] == key:
            pending["count"] = max(-4, min(4, pending["count"] + delta))
            if not pending["count"]:
                self._adjustment_pending = None
        else:
            self._adjustment_pending = {"key": deepcopy(key), "action": action_id, "target": deepcopy(self.target),
                "config": deepcopy(config), "count": delta}
        if not getattr(self, "_adjustment_active", False):
            self._adjustment_active = True
            token = self._adjustment_token
            self._adjustment_yield = (self.ring, self._open_token, token)
            self.ring.yield_keyboard()
            GLib.timeout_add(80, lambda: self._drain_adjustment(token))
        return True

    def _drain_adjustment(self, token):
        if token != self._adjustment_token or not self._adjustment_active:
            return GLib.SOURCE_REMOVE
        pending = self._adjustment_pending
        if (self.closed or not self.ring or not self.ring.state.visible
                or (pending and pending["key"][2:] != (self._open_token, self._dispatch_token, self.session.session_id))):
            self._cancel_adjustments()
            return GLib.SOURCE_REMOVE
        if self._adjustment_running or getattr(self, "_adjustment_resuming", False):
            return GLib.SOURCE_REMOVE
        if pending is None:
            self._adjustment_active = False
            yielded = self._adjustment_yield
            self._adjustment_yield = None
            if yielded and yielded == (self.ring, self._open_token, token):
                self._resume_adjustment_keyboard(token)
            return GLib.SOURCE_REMOVE
        direction = "increase" if pending["count"] > 0 else "decrease"
        pending["count"] += -1 if pending["count"] > 0 else 1
        if not pending["count"]:
            self._adjustment_pending = None
        self._adjustment_running = True
        self.execute(pending["action"], target=pending["target"], config=pending["config"],
                     adjustment=direction, completed=lambda success: self._adjustment_finished(token, success))
        return GLib.SOURCE_REMOVE

    def _resume_adjustment_keyboard(self, token):
        if getattr(self.ring, "overlay_supported", True):
            self.ring.resume_keyboard()
            return
        identity = getattr(self, "_ring_identity", None)
        if not identity or identity["window_id"] is None:
            self.ring.dismiss()
            return
        ring, target, lost = self.ring, deepcopy(self.target), self._ring_focus_lost
        self._adjustment_active = self._adjustment_resuming = True
        def focus_popup():
            if self.closed or token != self._adjustment_token or lost.is_set():
                return False
            if focused_context() != target:
                lost.set()
                return False
            if token != self._adjustment_token or lost.is_set():
                return False
            return self._is_ring_context(restore_focus(identity["window_id"]), identity)
        future = self.executor.submit(focus_popup)
        def focused():
            if self.closed or token != self._adjustment_token:
                return GLib.SOURCE_REMOVE
            self._adjustment_active = self._adjustment_resuming = False
            try:
                restored = future.result()
            except (RuntimeError, OSError):
                restored = False
            if self.ring is ring and ring.state.visible:
                if not restored or lost.is_set():
                    ring.dismiss()
                elif self._adjustment_pending:
                    self._adjustment_active = True
                    self._adjustment_yield = (ring, self._open_token, token)
                    self._drain_adjustment(token)
                else:
                    ring.resume_keyboard()
            return GLib.SOURCE_REMOVE
        future.add_done_callback(lambda _: GLib.idle_add(focused))

    def _adjustment_finished(self, token, success):
        self._adjustment_running = False
        if not success:
            self._adjustment_pending = None
        if token == self._adjustment_token:
            if getattr(self, "_ring_focus_lost", None) and self._ring_focus_lost.is_set():
                self.ring.dismiss()
            else:
                self._drain_adjustment(token)
        self._dispatch_selection()

    def execute(self, action_id, target=None, preview=False, config=None, adjustment=None, completed=None):
        if self.closed:
            return
        if (self.action_busy or self.opening
                or (adjustment is None and getattr(self, "_adjustment_active", False))):
            self.notify("A Smart Action is already running; this trigger was skipped.")
            return
        if self.error or not self.actions:
            self.notify(self.error or "Desktop configuration is unavailable.")
            return
        self.action_busy = True
        snapshot = deepcopy(config or self.effective_config or self.config)
        session_id, dispatch_token = self.session.session_id, self._dispatch_token
        self.cancelled.clear()
        focus_lost = getattr(self, "_ring_focus_lost", threading.Event())
        popup_identity = getattr(self, "_ring_identity", {"app_id": None, "window_id": None})
        self.notify("Previewing action…" if preview else "Running Smart Action…")
        def run():
            if self.closed or dispatch_token != self._dispatch_token or self.cancelled.is_set():
                raise RuntimeError("Action cancelled before dispatch.")
            runner = DesktopActions(snapshot, dry_run=preview)
            try:
                action = snapshot["actions"][action_id]
                steps = action["adjustment"][adjustment] if adjustment else action["steps"]
                sends_input = any(step["type"] in ("shortcut", "text") for step in steps)
                if not preview:
                    if adjustment and target:
                        before = focused_context()
                        if focus_lost.is_set() or (before != target and not self._is_ring_context(before, popup_identity)):
                            focus_lost.set()
                            raise RuntimeError("The original window lost focus; the adjustment was cancelled.")
                    context = restore_focus(target["window_id"]) if target else focused_context()
                    if sends_input:
                        if (not context or context["app_id"].startswith("io.github.mx4linux")
                                or (adjustment and not target)):
                            raise RuntimeError("Open the ring from another application using the haptic panel or mx4-linux ring before sending shortcuts or text.")
                if self.cancelled.is_set() or dispatch_token != self._dispatch_token:
                    raise RuntimeError("Action cancelled before execution.")
                def cancelled():
                    if self.cancelled.is_set() or (adjustment and focus_lost.is_set()):
                        return True
                    # This runs on the action worker at every step/delay/input boundary.
                    if adjustment and target and focused_context() != target:
                        focus_lost.set()
                        return True
                    return False
                kwargs = {"adjustment": adjustment} if adjustment else {}
                return runner.execute(action_id, cancelled=cancelled, **kwargs)
            finally:
                runner.close()
        future = self.executor.submit(run)
        def finished():
            self.action_busy = False
            if self.closed:
                if completed:
                    completed(False)
                return GLib.SOURCE_REMOVE
            success = False
            try:
                result = future.result()
                success = bool(result.get("ok", True) and not result.get("skipped") and not self.cancelled.is_set())
                if result.get("skipped"):
                    self.notify(result["reason"])
                elif preview:
                    self.notify("Preview · " + result["label"] + " · " + json.dumps(result["steps"], ensure_ascii=False))
                else:
                    self.notify(f"Completed {result['label']} · {result['completed']} steps.")
                    if (result.get("ok", True) and not self.cancelled.is_set() and session_id == self.session.session_id
                            and dispatch_token == self._dispatch_token):
                        self.feedback("complete", config=snapshot)
            except Exception as exc:
                self.notify(str(exc))
            finally:
                if completed:
                    completed(success)
            return GLib.SOURCE_REMOVE
        future.add_done_callback(lambda _: GLib.idle_add(finished))

    def configure(self):
        if self.action_busy:
            self.notify("Wait for or cancel the running routine before editing.")
            return
        self.stop()
        try:
            from .desktop_editor import present_desktop_editor
            present_desktop_editor(self.window, self.store, self.saved)
        except (RuntimeError, OSError) as exc:
            self.notify(str(exc))

    def saved(self):
        self.reload_config()
        self.notify(self.error or "Configuration saved. Enable mouse integration to use the new assignments.")

    def close(self):
        self.requested_enabled = False
        self._intent_generation = getattr(self, "_intent_generation", 0) + 1
        self._cancel_reconnect()
        self._stop_waiters = []
        self._stop_ring_context()
        self._cancel_adjustments()
        self.closed = True
        with self._event_lock:
            self._event_queue.clear()
        self._open_token += 1
        self._dispatch_token += 1
        self._stop_context()
        self.cancelled.set()
        if self.ring:
            self.ring.hide_ring()
            self.ring.destroy()
        self.session.stop()
        if self.actions:
            self.executor.submit(self.actions.close)
        self.executor.shutdown(wait=False, cancel_futures=False)
