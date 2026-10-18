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

    def start(self):
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
                    or self.window.current["id"] != device_id):
                self.notify("The selected mouse changed; refresh and enable integration again.")
                return GLib.SOURCE_REMOVE
            try:
                if revision == self._context_revision:
                    self.context = future.result()
                self.effective_config = resolve_config(document, self.context.get("app_id") if self.context else None)
                self._pending_configs.clear()
                self._context_revision += 1
                self._active_revision = self._context_revision
                self.session.start(device_id, self.effective_config)
                session_id = self.session.session_id
                self.observer = NiriFocusObserver(lambda event: self.queue_event(
                    {**event, "context_token": token, "session_id": session_id}))
                self.observer.start()
                self.notify("Enabling haptic panel and configured button shortcuts…")
            except (RuntimeError, OSError) as exc:
                self.notify(str(exc))
            return GLib.SOURCE_REMOVE
        future.add_done_callback(lambda _: GLib.idle_add(begin))

    def stop(self):
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
                not context or context.get("app_id", "").startswith("io.github.mx4linux")):
            return  # The owned overlay must not replace its original application.
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
        if kind in ("context", "context-unavailable"):
            self._context_event(event)
        elif kind == "configured":
            update = self._pending_configs.pop(event.get("config_id"), None)
            if update is not None and self.session.ready and not self.session.stopping:
                self.effective_config, self._active_revision = update
                self._pending_configs = {key: value for key, value in self._pending_configs.items()
                                         if key > event["config_id"]}
        elif kind == "ready":
            self.error = None
            self.notify("Enabled · hold the haptic panel, move, then release. A short click opens the ring.")
            if self.context_available is False:
                self.notify(self.message + " Application profiles unavailable; General assignments remain active.")
            self._configure_context()
        elif kind == "error":
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
            self.notify(self.error or "Mouse integration disabled. Previous button behavior restored.")
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
                if (self.target and self.target.get("app_id", "").startswith("io.github.mx4linux")
                        and self.context):
                    self.target = deepcopy(self.context)
                self._ring_config = resolve_config(document, self.target.get("app_id") if self.target else None)
                if self.ring is None:
                    from .ring import ActionsRing
                    self.ring = ActionsRing(self.window.get_application(), self.selected, self.dismissed, self.hovered, self.adjusted)
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

    def dismissed(self):
        self._cancel_adjustments()
        self._dispatch_token += 1

    def selected(self, action_id, preview):
        target = deepcopy(self.target)
        config = deepcopy(self._ring_config or self.effective_config or self.config)
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
        if self._adjustment_running:
            return GLib.SOURCE_REMOVE
        if pending is None:
            self._adjustment_active = False
            yielded = self._adjustment_yield
            self._adjustment_yield = None
            if yielded and yielded == (self.ring, self._open_token, token):
                self.ring.resume_keyboard()
            return GLib.SOURCE_REMOVE
        direction = "increase" if pending["count"] > 0 else "decrease"
        pending["count"] += -1 if pending["count"] > 0 else 1
        if not pending["count"]:
            self._adjustment_pending = None
        self._adjustment_running = True
        self.execute(pending["action"], target=pending["target"], config=pending["config"],
                     adjustment=direction, completed=lambda success: self._adjustment_finished(token, success))
        return GLib.SOURCE_REMOVE

    def _adjustment_finished(self, token, success):
        self._adjustment_running = False
        if not success:
            self._adjustment_pending = None
        if token == self._adjustment_token:
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
        self.notify("Previewing action…" if preview else "Running Smart Action…")
        def run():
            if self.closed or dispatch_token != self._dispatch_token or self.cancelled.is_set():
                raise RuntimeError("Action cancelled before dispatch.")
            runner = DesktopActions(snapshot, dry_run=preview)
            try:
                action = snapshot["actions"][action_id]
                if not preview:
                    context = restore_focus(target["window_id"]) if target else focused_context()
                    steps = action["adjustment"][adjustment] if adjustment else action["steps"]
                    if any(step["type"] in ("shortcut", "text") for step in steps):
                        if (not context or context["app_id"].startswith("io.github.mx4linux")
                                or (adjustment and not target)):
                            raise RuntimeError("Open the ring from another application using the haptic panel or mx4-linux ring before sending shortcuts or text.")
                if self.cancelled.is_set() or dispatch_token != self._dispatch_token:
                    raise RuntimeError("Action cancelled before execution.")
                kwargs = {"adjustment": adjustment} if adjustment else {}
                return runner.execute(action_id, cancelled=self.cancelled.is_set, **kwargs)
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
