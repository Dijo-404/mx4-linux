"""Coordinate the overlay, Smart Actions, and opt-in hardware events."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading

from .actions import DesktopActions, DesktopConfigStore, focused_context, restore_focus
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
        self.session = EventSession(lambda event: GLib.idle_add(self.event, event))
        self.ring = None
        self.target = None
        self.opening = False
        self._open_token = 0
        self._dispatch_token = 0
        self._pending_released = False
        self._pending_motion = [0, 0]
        self.reload_config()

    def reload_config(self):
        try:
            self.config = self.store.read()
            if self.actions:
                self.actions.config = deepcopy(self.config)
            else:
                self.actions = DesktopActions(self.config)
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
        self.reload_config()
        if self.error:
            self.notify(self.error)
            return
        if not self.window.current or not self.window.current.get("online"):
            self.notify("Wake and refresh the mouse before enabling integration.")
            return
        try:
            self.session.start(self.window.current["id"], self.config)
            self.notify("Enabling haptic panel and configured button shortcuts…")
        except (RuntimeError, OSError) as exc:
            self.notify(str(exc))

    def stop(self):
        self._open_token += 1
        self._dispatch_token += 1
        self.opening = False
        self._pending_motion = [0, 0]
        if self.ring:
            self.ring.dismiss()
        if self.session.running:
            self.session.stop()
            self.notify("Restoring the mouse's previous button behavior…")

    def event(self, event):
        if self.closed:
            return GLib.SOURCE_REMOVE
        session_id = getattr(self.session, "session_id", None)
        if event.get("session_id", session_id) != session_id:
            return GLib.SOURCE_REMOVE
        kind = event.get("type")
        if kind == "ready":
            self.error = None
            self.notify("Enabled · hold the haptic panel, move, then release. A short click opens the ring.")
        elif kind == "error":
            self._open_token += 1
            self._dispatch_token += 1
            self.opening = False
            self._pending_motion = [0, 0]
            if self.ring:
                self.ring.dismiss()
            self.error = event.get("message", "Mouse integration failed.")
            self.notify(self.error)
        elif kind == "stopped":
            self._open_token += 1
            self._dispatch_token += 1
            self.opening = False
            if self.ring:
                self.ring.dismiss()
            self.notify(self.error or "Mouse integration disabled. Previous button behavior restored.")
        elif kind == "button" and event.get("cancelled"):
            self._open_token += 1
            self._dispatch_token += 1
            self.opening = False
            self._pending_motion = [0, 0]
            if self.ring:
                self.ring.dismiss()
        elif kind == "button" and self.session.ready and not self.session.stopping:
            cid, pressed = event.get("control"), event.get("pressed")
            if cid == self.config["ring_button"]:
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
            elif pressed and str(cid) in self.config["button_bindings"]:
                self.execute(self.config["button_bindings"][str(cid)])
        elif kind == "motion" and self.session.ready and not self.session.stopping:
            dx, dy = event.get("dx"), event.get("dy")
            if type(dx) is int and type(dy) is int:
                if self.opening:
                    self._pending_motion[0] += dx
                    self._pending_motion[1] += dy
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
        future = self.executor.submit(focused_context)
        def show():
            if self.closed or token != self._open_token:
                return GLib.SOURCE_REMOVE
            self.opening = False
            try:
                self.target = future.result()
                if self.ring is None:
                    from .ring import ActionsRing
                    self.ring = ActionsRing(self.window.get_application(), self.selected, self.dismissed, self.hovered)
                self.ring.show_ring(self.config, held=held, preview=preview or self.window.demo)
                if held:
                    self.ring.motion(*self._pending_motion)
                    if self._pending_released:
                        self.ring.release_button()
            except Exception as exc:
                self.notify(f"Cannot show Actions Ring: {exc}")
            return GLib.SOURCE_REMOVE
        future.add_done_callback(lambda _: GLib.idle_add(show))

    def hovered(self, _index):
        if self.config["haptic_feedback"] and self.ring and not self.ring.preview:
            self.session.feedback(0)

    def dismissed(self):
        # The compositor normally returns focus after a layer surface unmaps.
        # Explicit restoration is done before running any selected action.
        pass

    def selected(self, action_id, preview):
        target = self.target
        token = self._dispatch_token
        def dispatch():
            if not self.closed and token == self._dispatch_token:
                self.execute(action_id, target=target, preview=preview)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(80, dispatch)  # Allow the layer surface to unmap first.

    def execute(self, action_id, target=None, preview=False):
        if self.closed:
            return
        if self.action_busy or self.opening:
            self.notify("A Smart Action is already running; this trigger was skipped.")
            return
        if self.error or not self.actions:
            self.notify(self.error or "Desktop configuration is unavailable.")
            return
        self.action_busy = True
        self.cancelled.clear()
        self.notify("Previewing action…" if preview else "Running Smart Action…")
        def run():
            runner = DesktopActions(self.config, dry_run=True) if preview else self.actions
            action = self.config["actions"][action_id]
            if not preview:
                context = restore_focus(target["window_id"]) if target else focused_context()
                if any(step["type"] in ("shortcut", "text") for step in action["steps"]):
                    if not context or context["app_id"].startswith("io.github.mx4linux"):
                        raise RuntimeError("Open the ring from another application using the haptic panel or mx4-linux ring before sending shortcuts or text.")
            return runner.execute(action_id, cancelled=self.cancelled.is_set)
        future = self.executor.submit(run)
        def finished():
            self.action_busy = False
            if self.closed:
                return GLib.SOURCE_REMOVE
            try:
                result = future.result()
                if result.get("skipped"):
                    self.notify(result["reason"])
                elif preview:
                    self.notify("Preview · " + result["label"] + " · " + json.dumps(result["steps"], ensure_ascii=False))
                else:
                    self.notify(f"Completed {result['label']} · {result['completed']} steps.")
            except Exception as exc:
                self.notify(str(exc))
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
        self.closed = True
        self._open_token += 1
        self._dispatch_token += 1
        self.cancelled.set()
        if self.ring:
            self.ring.hide_ring()
            self.ring.destroy()
        self.session.stop()
        if self.actions:
            self.executor.submit(self.actions.close)
        self.executor.shutdown(wait=False, cancel_futures=False)
