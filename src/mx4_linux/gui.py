"""GTK 4 control panel. Device work is serialized off the UI thread."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from copy import deepcopy

from .gtk_support import Adw, Gdk, Gio, GLib, Gtk

from .actions import assignment_scope, set_button_assignment
from .backend import Backend, DemoBackend
from .profiles import ProfileStore, RememberedSettingsStore, apply_profile, replay_remembered
from .reporting_state import valid_identity


class ControlWindow(Adw.ApplicationWindow):
    def __init__(self, app, demo=False):
        super().__init__(application=app, title="MX4 Linux", default_width=1100, default_height=800)
        self.demo = demo
        self.backend = DemoBackend() if demo else Backend()
        self.store = ProfileStore()
        self.remembered = RememberedSettingsStore()
        self.hardware_cancelled = threading.Event()
        self.operation_epoch = 0
        self._observed_online = set()
        self.battery_source = 0
        self.replay_suppressed_identity = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mx4-device")
        self.busy = False
        self.pending_hardware = None
        self._hardware_resume = None
        self.devices = []
        self.selected_id = None
        self._selected_explicitly = False
        self.current = None
        self.setting_widgets = {}
        from .desktop import DesktopController
        self.desktop_status = self.desktop_toggle = self.cancel_action = None
        self.desktop = DesktopController(self)
        self.connect("close-request", self.on_close)
        self.closed = False
        self.quitting = False
        self._quit_dialog = None
        self.device_refresh_source = 0
        self.device_monitor = None
        try:
            self.device_monitor = Gio.File.new_for_path("/dev").monitor_directory(Gio.FileMonitorFlags.NONE, None)
            self.device_monitor.connect("changed", self.on_device_change)
        except GLib.Error:
            pass  # Manual refresh remains available if monitoring is unavailable.

        self.route = "home"
        self.route_history = []
        self.selected_target = None
        from .reference_shell import ReferenceShell
        self.shell = ReferenceShell(self)
        toolbar = self.shell.toolbar
        self.stack = self.shell.stack
        self.controls = self.shell.controls
        self.profile_controls = self.shell.profile_controls
        self.actions_box = self.shell.actions_box
        # Artwork is installed with the first successful device read.
        from .mouse_view import MouseView
        self.wheel = MouseView(self.select_target)
        self.shell.art = self.wheel
        self.wheel.set_visible(False)
        self.refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Read device settings")
        self.refresh.connect("clicked", lambda *_: self.reload())
        self.shell.header.pack_end(self.refresh)
        self.add_desktop_controls()
        self.integration_warning = Adw.Banner(button_label="Review integration", revealed=False)
        self.integration_warning.connect("button-clicked", lambda *_: self.navigate("actions"))
        toolbar.add_top_bar(self.integration_warning)

        status_bar = Gtk.Box(spacing=12, margin_start=18, margin_end=18,
                             margin_top=8, margin_bottom=8)
        status_bar.add_css_class("mx4-status")
        self.spinner = Gtk.Spinner(visible=False, valign=Gtk.Align.CENTER)
        status_bar.append(self.spinner)
        self.status = Gtk.Label(label="Reading connected devices…", xalign=0,
                                 wrap=True, selectable=True, hexpand=True)
        # Selectable for copying errors, but never a focus stop that highlights itself.
        self.status.set_focusable(False)
        self.status.add_css_class("dim-label")
        status_bar.append(self.status)
        self.cancel_device = Gtk.Button(label="Cancel operation", visible=False, valign=Gtk.Align.CENTER)
        self.cancel_device.connect("clicked", lambda *_: self.cancel_hardware())
        status_bar.append(self.cancel_device)
        toolbar.add_bottom_bar(status_bar)
        self.toasts = Adw.ToastOverlay(child=toolbar)
        self.set_content(self.toasts)
        keys = Gtk.EventControllerKey()
        # Native dropdowns and text editors get the first chance to use Escape.
        keys.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)
        self.reload()
        self.battery_source = GLib.timeout_add_seconds(30, self.refresh_battery)

    def on_close(self, *_):
        if self.closed:
            return False
        if self.quitting:
            return True
        keep_running = ((self.desktop.config or {}).get("background", True)
                        and (self.desktop.requested_enabled or self.desktop.session.running
                             or self.desktop.starting))
        if keep_running:
            # Keep the same application window registered: GTK retains the
            # process, its listener and unsaved controls while it is hidden.
            self.set_visible(False)
        else:
            self.request_quit()
        return True

    def request_quit(self, *_):
        """Explicit Quit waits for reporting restoration before ending GTK."""
        if self.closed or self.quitting:
            return
        editor = self.desktop.editor
        if editor is not None and editor.get_visible():
            editor.close()
            if editor.get_visible():
                # Reuse the editor's existing unsaved-changes decision; a
                # cancelled Quit must not remain armed for a later close.
                dialog = editor.close_dialog
                if dialog is not None and dialog is not self._quit_dialog:
                    self._quit_dialog = dialog
                    def answered(_dialog, response):
                        self._quit_dialog = None
                        if response == "discard":
                            self.request_quit()
                    dialog.connect_after("response", answered)
                editor.present()
                return
        self.quitting = True
        self.set_sensitive(False)
        self._hardware_resume = None
        self.operation_epoch += 1
        self.hardware_cancelled.set()
        self.desktop.wait_stopped(self._finish_quit)

    def _finish_quit(self, _restored):
        application = self.get_application()
        self.shutdown()
        self.destroy()
        if application is not None:
            application.quit()

    def shutdown(self):
        """Release resources once, including application shutdown paths."""
        if self.closed:
            return
        self.closed = True
        self._hardware_resume = None
        self.operation_epoch += 1
        self.hardware_cancelled.set()
        if self.battery_source:
            GLib.source_remove(self.battery_source)
            self.battery_source = 0
        self.wheel.stop()
        self.shell.close()
        self.desktop.close()
        if self.device_refresh_source:
            GLib.source_remove(self.device_refresh_source)
            self.device_refresh_source = 0
        if self.device_monitor:
            self.device_monitor.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def on_device_change(self, _monitor, file, _other_file, event):
        if self.demo or self.closed or not (file.get_basename() or "").startswith("hidraw"):
            return
        if event not in (Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.DELETED,
                         Gio.FileMonitorEvent.ATTRIBUTE_CHANGED, Gio.FileMonitorEvent.MOVED_IN,
                         Gio.FileMonitorEvent.MOVED_OUT, Gio.FileMonitorEvent.RENAMED):
            return  # HID++ query writes must not trigger another discovery scan.
        if not self.device_refresh_source:
            self.device_refresh_source = GLib.timeout_add(750, self.refresh_after_device_change)

    def refresh_after_device_change(self):
        if self.closed:
            self.device_refresh_source = 0
            return GLib.SOURCE_REMOVE
        if self.busy:
            return GLib.SOURCE_CONTINUE
        self.device_refresh_source = 0
        self.reload()
        return GLib.SOURCE_REMOVE

    def job(self, work, done, message, failed=None, *, device_bound=True):
        """Serialize work; only local-file callbacks may ignore device epochs."""
        if self.busy or self.pending_hardware or self.quitting:
            return
        self.busy = True
        epoch = self.operation_epoch
        self.hardware_cancelled.clear()
        self.cancel_device.set_visible(device_bound)
        self.controls.set_sensitive(False)
        self.profile_controls.set_sensitive(False)
        self.refresh.set_sensitive(False)
        self.spinner.set_visible(True)
        self.spinner.start()
        if message is not None:
            self.status.set_text(message)
        self.update_desktop_status()
        future = self.executor.submit(work)

        def finish():
            if self.closed:
                return GLib.SOURCE_REMOVE
            self.busy = False
            self.cancel_device.set_visible(False)
            self.controls.set_sensitive(True)
            self.profile_controls.set_sensitive(True)
            self.refresh.set_sensitive(True)
            self.spinner.stop()
            self.spinner.set_visible(False)
            stale = epoch != self.operation_epoch
            if stale:
                self.current = None
                self.selected_target = None
                self.clear_controls()
                self.wheel.set_visible(False)
                self.add_profiles()
            self.update_desktop_status()
            if stale and device_bound:
                # Always consume completion, including confirmed partial counts,
                # while refusing callbacks that could apply an old readback.
                try:
                    result = future.result()
                except Exception as exc:
                    detail = str(exc).strip()[:1200] or type(exc).__name__
                    message = f"Previous device operation: {detail}"
                else:
                    count = result if type(result) is int else result.get("changes") if isinstance(result, dict) else None
                    progress = f" with {count} confirmed changes" if type(count) is int and 0 <= count <= 65535 else ""
                    message = f"Previous device operation finished{progress} before cancellation."
                self.status.set_text(message + " Refresh to read current settings.")
                return GLib.SOURCE_REMOVE
            try:
                result = future.result()
            except Exception as exc:
                if failed:
                    failed(str(exc))
                else:
                    self.status.set_text(str(exc))
            else:
                done(result)
            return GLib.SOURCE_REMOVE

        future.add_done_callback(lambda _: GLib.idle_add(finish))

    def reload(self, notice=None):
        def loaded(result):
            self.devices = result["devices"]
            self.render(result)
            online = {(d.get("stable_id"), d["id"]) for d in self.devices if d.get("online") and valid_identity(d.get("stable_id"))}
            new_connection = bool(self.current and (self.current.get("stable_id"), self.current["id"]) in online - self._observed_online)
            self._observed_online = online
            if new_connection and not self.demo and not self.desktop.session.running and not self.desktop._reconnect_busy:
                epoch = self.operation_epoch
                GLib.idle_add(lambda: self.recover_current(replay=self.current and self.current.get("stable_id") != self.replay_suppressed_identity, automatic=True) if not self.closed and epoch == self.operation_epoch else GLib.SOURCE_REMOVE)
            if notice:
                self.status.set_text(notice)
            resume, self._hardware_resume = self._hardware_resume, None
            if resume:
                resume(not result.get("diagnostics"))
        def failed(error):
            resume, self._hardware_resume = self._hardware_resume, None
            if resume:
                resume(False)
            self.status.set_text(error)
        self.job(self.backend.scan, loaded, "Reading connected devices… Wake the mouse if it is asleep.", failed=failed)

    def clear_controls(self):
        self.setting_widgets.clear()
        self.shell.inspector = None
        self.shell.targets = {}
        while child := self.controls.get_first_child():
            self.controls.remove(child)

    def navigate(self, route):
        from .reference_shell import TITLES
        if route not in TITLES:
            raise ValueError("Unknown page")
        if route == "configuration":
            self.configuration_return = self.route if self.route in ("application", "settings") else "application"
        if route == "home":
            self.route_history.clear()
        elif route != self.route:
            self.route_history.append((self.route, self.selected_target))
        self.route = route
        self.selected_target = None
        self.shell.update_route()
        if route in ("ring", "smart-actions"):
            self.desktop.configure(page="ring" if route == "ring" else "actions")

    def select_target(self, target):
        if not self.current or not self.current.get("online") or target not in self.shell.targets:
            return
        self.selected_target = target
        self.shell.update_route()
        self.shell.inspector_stack.child_focus(Gtk.DirectionType.TAB_FORWARD)

    def close_inspector(self):
        self.selected_target = None
        if self.route == "settings":
            self.go_back()
        else:
            self.shell.update_route()

    def go_back(self):
        if self.selected_target:
            self.close_inspector()
        elif self.route_history:
            self.route, target = self.route_history.pop()
            self.selected_target = target if target in self.shell.targets and self.current and self.current.get("online") else None
            self.shell.update_route()
        elif self.route == "configuration":
            self.navigate(getattr(self, "configuration_return", "application"))
        else:
            self.navigate("home")

    def _key_pressed(self, _controller, keyval, _keycode, state):
        if keyval != Gdk.KEY_Escape or state & Gtk.accelerator_get_default_mod_mask():
            return False
        # A dialog owns its own dismissal and any unsaved-changes decision.
        if getattr(self, "get_visible_dialog", lambda: None)() is not None:
            return False
        if any(window.get_visible() and window.get_modal() and window.get_transient_for() is self
               for window in Gtk.Window.get_toplevels()):
            return False
        focus = self.get_focus()
        while focus is not None:
            if isinstance(focus, Gtk.Popover):
                return False
            focus = focus.get_parent()
        if self.route == "home" and not self.selected_target:
            return False
        self.go_back()
        return True

    def add_device(self):
        self.navigate("home")
        self.status.set_text("Pair in your desktop's Bluetooth settings or connect a Logi Bolt receiver, then wake the mouse. Checking connected devices…")
        self.reload()

    def open_support(self):
        launcher = Gtk.UriLauncher.new("https://github.com/Dijo-404/mx4-linux#readme")
        launcher.launch(self, None, None, None)

    def save_local_preference(self, name, value, subkey=None):
        if self.desktop.config is None:
            self.status.set_text(self.desktop.error or "Local action configuration is unavailable.")
            return
        if self.busy or self.pending_hardware or self.closed or self.quitting:
            self.shell.sync_local_preferences()
            return
        def mutate(document):
            if subkey is None:
                document[name] = value
            else:
                document.setdefault(name, {})[subkey] = value
        def saved(_result):
            self.local_config_finished("Local preference saved.")
        def failed(error):
            self.local_config_finished(error)
        self.job(lambda: self.desktop.store.update(mutate), saved, "Saving local preference…",
                 failed=failed, device_bound=False)

    def save_button_assignment(self, cid, action_id, app_id=None):
        if self.desktop.config is None:
            self.status.set_text(self.desktop.error or "Local action configuration is unavailable.")
            return
        if self.busy or self.pending_hardware or self.closed:
            return
        self.desktop.stop()
        def mutate(document):
            set_button_assignment(document, cid, action_id, app_id)
        def saved(_result):
            self.local_config_finished("Local assignment saved. Enable mouse integration to use it.")
        def failed(error):
            self.local_config_finished(error)
            if self.current and self.current.get("online"):
                self.render_workspace()
        self.job(lambda: self.desktop.store.update(mutate), saved, "Saving local assignment…",
                 failed=failed, device_bound=False)

    def local_config_finished(self, message):
        # Atomic replacement may already have committed even when completion
        # reports an error. Reconcile from disk, independently of device epochs.
        self.desktop.reconcile_config()
        self.shell.sync_local_preferences()
        if self.desktop.config is not None:
            self.shell.apply_appearance(self.desktop.config.get("appearance", "dark"))
        self.shell.update_context()
        self.shell.update_readback()
        self.update_desktop_status()
        self.status.set_text(self.desktop.error or message)

    def desktop_config_changed(self):
        self.shell.update_context()
        if self.current and self.current.get("online"):
            self.render_workspace()

    def render_workspace(self):
        self.clear_controls()
        self.shell.binding_widgets = {}
        self.shell.build_workspace()
        if self.selected_target not in self.shell.targets:
            self.selected_target = None
        self.shell.update_route()
        self.update_wheel()

    def render(self, result):
        self.clear_controls()
        self.current = None
        self.wheel.set_visible(False)
        if not self.devices:
            self.selected_target = None
            self.shell.inspector = None
            self.shell.targets = {}
            denied = any(d.get("permission_denied") for d in result.get("diagnostics", []))
            diagnostics = "\n".join(f"{d.get('path', '')}: {d['message']}" for d in result.get("diagnostics", []))
            self.controls.append(self.shell.build_connect_page("denied" if denied else "missing", diagnostics))
            self.status.set_text(diagnostics or "No accessible MX Master 4 found.")
        else:
            selected = next((d for d in self.devices if d["id"] == self.selected_id), None)
            if selected is None:
                self._selected_explicitly = False
            self.current = (selected if selected and (selected.get("online") or self._selected_explicitly)
                            else next((d for d in self.devices if d.get("online")), selected or self.devices[0]))
            self.selected_id = self.current["id"]
            if not self.current.get("online"):
                self.selected_target = None
                self.shell.inspector = None
                self.shell.targets = {}
                self.controls.append(self.shell.build_connect_page("offline"))
                self.status.set_text("Mouse is asleep or offline. Wake it, then check again.")
            else:
                self.render_workspace()
                self.wheel.set_visible(True)
                diagnostic_text = " · ".join(d["message"] for d in result.get("diagnostics", []))
                self.status.set_text("Preview · all changes are simulated" if self.demo else f"{len(self.current['settings'])} settings reported. Changes apply after confirmed readback." + (f" {diagnostic_text}" if diagnostic_text else ""))
        self.shell.update_devices(result)
        self.shell.update_home()
        self.shell.update_context()
        self.shell.update_route()
        self.add_profiles()
        self.update_desktop_status()

    def add_desktop_controls(self):
        group = Adw.PreferencesGroup(title="Actions Ring and Smart Actions",
                                     description="Start with a preview. Enable mouse integration when you're ready to use the haptic panel.")
        self.actions_box.append(group)
        self.desktop_status = Adw.ActionRow(use_markup=False, title="Mouse integration", subtitle=self.desktop.message)
        self.desktop_status.set_subtitle_lines(5)
        self.desktop_toggle = Gtk.Button(valign=Gtk.Align.CENTER)
        self.desktop_toggle.connect("clicked", lambda *_: self.desktop.stop() if self.desktop.session.running else self.desktop.start())
        self.desktop_status.add_suffix(self.desktop_toggle)
        group.add(self.desktop_status)
        ring_row = Adw.ActionRow(use_markup=False, title="Actions Ring", subtitle="Hold the haptic panel, move and release, or click then select. Escape closes the ring.")
        preview = Gtk.Button(label="Try the ring", valign=Gtk.Align.CENTER,
                              tooltip_text="Explore the ring without running actions")
        preview.connect("clicked", lambda *_: self.desktop.open_ring(preview=True))
        ring_row.add_suffix(preview)
        group.add(ring_row)
        edit_row = Adw.ActionRow(use_markup=False, title="Customize actions", subtitle="Assign ring positions and mouse buttons; create routines with shortcuts, applications, text and delays.")
        edit_row.set_subtitle_lines(3)
        edit = Gtk.Button(label="Edit actions", valign=Gtk.Align.CENTER)
        edit.add_css_class("suggested-action")
        edit.connect("clicked", lambda *_: self.desktop.configure(page="actions" if self.route == "smart-actions" else "ring"))
        edit_row.add_suffix(edit)
        group.add(edit_row)
        cancel_row = Adw.ActionRow(use_markup=False, title="Running routine", subtitle="Cancel stops subsequent steps and releases any held shortcut keys.")
        self.cancel_action = Gtk.Button(label="Stop routine", valign=Gtk.Align.CENTER)
        self.cancel_action.connect("clicked", lambda *_: self.desktop.cancelled.set())
        cancel_row.add_suffix(self.cancel_action)
        group.add(cancel_row)
        import os
        from .actions import _native_wtype
        if not _native_wtype() and not os.access("/dev/uinput", os.R_OK | os.W_OK):
            group.add(Adw.ActionRow(use_markup=False, title="Set up keyboard input",
                                    subtitle="Install wtype for native Wayland shortcuts and text. See the installation guide for the optional input-access fallback."))
        self.update_desktop_status()

    def update_desktop_status(self):
        if hasattr(self, "shell"):
            self.shell.update_home()
        if hasattr(self, "integration_warning"):
            uncertain = (self.desktop.session.process is not None and not self.desktop.session.running
                         and not self.desktop.session.stopping and self.desktop.restoration_confirmed is not True)
            blocked = bool(self.desktop.error or self.desktop.reacquire_blocked or uncertain)
            self.integration_warning.set_title(f"[!]  {self.desktop.message}")
            self.integration_warning.set_revealed(blocked)
        if self.desktop_status:
            self.desktop_status.set_subtitle(self.desktop.message)
        if self.desktop_toggle:
            active = self.desktop.session.running
            self.desktop_toggle.set_label("Disable" if active else "Enable")
            self.desktop_toggle.set_sensitive(not self.demo and not self.desktop.session.stopping
                                                and (active or (not self.busy and not self.pending_hardware and bool(self.current and self.current.get("online")))))
        if self.cancel_action:
            self.cancel_action.set_sensitive(self.desktop.action_busy)
        editor = getattr(self.desktop, "editor", None)
        if editor is not None and editor.get_visible():
            editor.update_integration_status()

    def select_device(self, chooser, _param, result):
        if self.closed or self.busy or self.pending_hardware:
            return
        index = chooser.get_selected()
        if index < len(self.devices) and self.devices[index]["id"] != self.selected_id:
            self._selected_explicitly = True
            self.operation_epoch += 1
            self.hardware_cancelled.set()
            self.desktop.stop()
            self.selected_id = self.devices[index]["id"]
            self.selected_target = None
            self.render(result)

    def add_setting(self, group, setting):
        from .reference_shell import choice_row
        if setting["kind"] == "map_choice":
            for key in setting.get("keys", []):
                value = (setting.get("value") or {}).get(str(key["value"]))
                if value is None:
                    value = (setting.get("value") or {}).get(key["value"])
                widget = self.choice(setting, key["choices"], value, key["value"])
                widget.update_property([Gtk.AccessibleProperty.LABEL], [key["label"]])
                group.add(choice_row(key["label"], "Built-in mouse function", widget))
            return
        value = setting.get("value")
        row = Adw.ActionRow(use_markup=False, title=setting["label"], subtitle=setting.get("description", ""))
        row.set_subtitle_lines(3)
        kind = setting["kind"]
        if kind == "toggle":
            widget = Gtk.Switch(active=bool(value), valign=Gtk.Align.CENTER)
            widget.set_sensitive(value is not None)
            handler = widget.connect("notify::active", lambda switch, _p: self.change(setting, switch.get_active()))
            self.setting_widgets[(setting["name"], None)] = (widget, handler, widget.set_active)
            row.set_activatable_widget(widget)
        elif kind == "range":
            # Inspector width is about a third of the window. Stack range
            # labels above their editor instead of squeezing text beside it.
            row = Adw.PreferencesRow()
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                              margin_start=14, margin_end=14, margin_top=12, margin_bottom=12)
            heading = Gtk.Label(label=setting["label"], xalign=0, wrap=True)
            heading.add_css_class("heading")
            content.append(heading)
            if setting.get("description"):
                detail = Gtk.Label(label=setting["description"], xalign=0, wrap=True)
                detail.add_css_class("dim-label")
                content.append(detail)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, setting["minimum"], setting["maximum"], 1)
            scale.set_draw_value(False)
            scale.set_round_digits(0)
            scale.set_value(value if value is not None else setting["minimum"])
            scale.set_sensitive(value is not None)
            scale.update_property([Gtk.AccessibleProperty.LABEL], [setting["label"] + " draft value"])
            content.append(scale)
            box = Gtk.Box(spacing=8)
            widget = Gtk.SpinButton.new_with_range(setting["minimum"], setting["maximum"], 1)
            widget.set_value(value if value is not None else setting["minimum"])
            widget.set_sensitive(value is not None)
            widget.update_property([Gtk.AccessibleProperty.LABEL], [setting["label"]])
            scale.connect("value-changed", lambda slider: widget.set_value(slider.get_value()))
            widget.connect("value-changed", lambda spin: scale.set_value(spin.get_value()))
            confirmed = Gtk.Label(label=f"Current: {value}" if value is not None else "Current value unavailable",
                                  xalign=0, wrap=True, hexpand=True)
            confirmed.add_css_class("dim-label")
            def update(confirmed_value):
                widget.set_value(confirmed_value)
                confirmed.set_text(f"Current: {confirmed_value}")
            self.setting_widgets[(setting["name"], None)] = (widget, None, update)
            apply_button = Gtk.Button(label="Apply", sensitive=value is not None, valign=Gtk.Align.CENTER)
            apply_button.set_tooltip_text(f"Apply {setting['label'].lower()}")
            apply_button.connect("clicked", lambda *_, spin=widget: self.change(setting, spin.get_value_as_int()))
            content.append(confirmed)
            widget.set_hexpand(True)
            box.append(widget)
            box.append(apply_button)
            content.append(box)
            row.set_child(content)
            group.add(row)
            return
        elif kind == "choice":
            widget = self.choice(setting, setting["choices"], value)
            widget.update_property([Gtk.AccessibleProperty.LABEL], [setting["label"]])
            group.add(choice_row(setting["label"], setting.get("description", ""), widget))
            return
        else:
            return
        widget.update_property([Gtk.AccessibleProperty.LABEL], [setting["label"]])
        row.add_suffix(widget)
        group.add(row)

    def choice(self, setting, choices, value, key=None):
        labels = [c["label"] for c in choices]
        if setting["name"] == "reprogrammable-keys":
            names = {82: "Middle button", 83: "Back", 86: "Forward", 195: "Gesture button", 196: "Shift wheel mode", 416: "Haptic panel"}
            labels = [names.get(c["value"], c["label"]) if c["label"] == str(c["value"]) else c["label"] for c in choices]
        from .reference_shell import bounded_dropdown
        dropdown = bounded_dropdown(labels, accessible_name=setting["label"], max_chars=16)
        dropdown.set_enable_search(True)
        dropdown.set_valign(Gtk.Align.CENTER)
        dropdown.set_hexpand(True)
        dropdown.set_selected(next((i for i, c in enumerate(choices) if c["value"] == value), Gtk.INVALID_LIST_POSITION))
        if setting["name"] == "haptic-play":
            box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
            play = Gtk.Button(label="Play", valign=Gtk.Align.CENTER)
            if choices and dropdown.get_selected() == Gtk.INVALID_LIST_POSITION:
                dropdown.set_selected(0)
            play.set_sensitive(bool(choices))
            play.connect("clicked", lambda *_: self.change(setting, choices[dropdown.get_selected()]["value"]))
            box.append(dropdown)
            box.append(play)
            return box
        dropdown.set_sensitive(value is not None)
        def changed(widget, _param):
            index = widget.get_selected()
            if index < len(choices):
                self.change(setting, choices[index]["value"], key)
        handler = dropdown.connect("notify::selected", changed)
        def select(confirmed):
            dropdown.set_selected(next((i for i, item in enumerate(choices) if item["value"] == confirmed), Gtk.INVALID_LIST_POSITION))
        self.setting_widgets[(setting["name"], key)] = (dropdown, handler, select)
        return dropdown

    def change(self, setting, value, key=None):
        if self.busy:
            return
        if not self.demo and (not self.current or not valid_identity(self.current.get("stable_id"))):
            self.status.set_text("Wake and refresh this mouse to verify its identity before changing settings.")
            return
        previous = setting.get("value")
        if key is not None:
            previous = (previous or {}).get(str(key), (previous or {}).get(key))
        mapping = setting["name"] == "reprogrammable-keys"
        app_id = self.shell.context_app
        local = self.shell.effective_config() if mapping else {}
        masked = mapping and (key == local.get("ring_button") or str(key) in local.get("button_bindings", {}))
        if setting["name"] != "haptic-play" and previous == value and not masked:
            return
        device = deepcopy(self.current)
        device_id = device["id"]
        epoch = self.operation_epoch
        text = "Simulated change." if self.demo else f"Applied {setting['label']}."
        def applied(response):
            if epoch != self.operation_epoch or not self.current or self.current["id"] != device_id or self.current.get("stable_id") != device.get("stable_id"):
                return
            if response.get("refresh_required"):
                self.refresh_after_write(text)
                return
            confirmed = {setting["name"]: response["value"], **response.get("values", {})}
            for name, result in confirmed.items():
                item = next((s for s in self.current["settings"] if s["name"] == name), None)
                if item is None or name == "haptic-play":
                    continue
                if key is not None and name == setting["name"]:
                    item["value"][str(key)] = result
                else:
                    item["value"] = result
                self.sync_setting(item, key if name == setting["name"] else None)
            self.update_wheel()
            self.shell.update_readback()
            if self.demo and setting["name"] != "haptic-play":
                try:
                    self.remembered.update_confirmed(device["stable_id"], confirmed, key=key, demo=True)
                except Exception as exc:
                    self.status.set_text(f"{text} Remembered settings were not updated: {exc}")
                    return
            self.status.set_text(text)
            self.toasts.add_toast(Adw.Toast(title=text, timeout=2))
        def failed(error):
            self.refresh_after_write(error)
        def reconcile_local():
            if not self.closed:
                self.local_config_finished("Local assignment cleared before applying the mouse function.")
            return GLib.SOURCE_REMOVE
        def work():
            if self.closed or epoch != self.operation_epoch or self.hardware_cancelled.is_set():
                raise RuntimeError("Device change cancelled")
            cleared = False
            if mapping:
                def clear(document):
                    nonlocal cleared
                    target = assignment_scope(document, app_id)
                    if key == target.get("ring_button", document["ring_button"]) or str(key) in target['button_bindings']:
                        set_button_assignment(document, key, None, app_id)
                        cleared = True
                try:
                    self.desktop.store.update(clear)
                except Exception as exc:
                    raise RuntimeError(f"Local assignment save was not confirmed; mouse mapping was not changed: {exc}") from exc
                finally:
                    GLib.idle_add(reconcile_local)
                if self.closed or epoch != self.operation_epoch or self.hardware_cancelled.is_set():
                    raise RuntimeError("Local assignment cleared; mouse mapping cancelled." if cleared else "Mouse mapping cancelled.")
            try:
                if self.demo:
                    return self.backend.write(device_id, setting["name"], value, key)
                return self.backend.write(device_id, setting["name"], value, key, expected_stable_id=device.get("stable_id"))
            except Exception as exc:
                if cleared:
                    raise RuntimeError(f"Local assignment cleared; mouse mapping was not confirmed: {exc}") from exc
                raise
        if setting["name"] == "reprogrammable-keys":
            self.hardware_job(work, applied, f"Applying {setting['label']}…", failed=failed)
        else:
            self.job(work, applied, f"Applying {setting['label']}…", failed=failed)

    def set_hardware_wait(self, token):
        self.pending_hardware = token
        active = bool(token) or self.busy
        self.cancel_device.set_visible(active)
        self.controls.set_sensitive(not active)
        self.profile_controls.set_sensitive(not active)
        self.refresh.set_sensitive(not active)
        self.update_desktop_status()

    def cancel_hardware(self):
        self._hardware_resume = None
        self.set_hardware_wait(None)
        self.hardware_cancelled.set()
        self.operation_epoch += 1
        self.desktop.stop()
        self.desktop._reconnect_busy = False
        self.status.set_text("Cancelling device operation after the current request…")

    def hardware_job(self, work, done, message, failed=None, resume=True):
        """Pause temporary reporting and await confirmed child cleanup before mapping/profile changes."""
        if self.busy or self.pending_hardware or self.closed or self.quitting:
            return
        wait_token = object()
        self.set_hardware_wait(wait_token)
        generation = self.desktop._intent_generation
        epoch = self.operation_epoch
        wanted = self.desktop.requested_enabled and resume
        identity = self.current.get("stable_id") if self.current else None
        self.desktop.hardware_paused = True
        def stopped(restored):
            if self.pending_hardware is wait_token:
                self.set_hardware_wait(None)
            if self.closed or epoch != self.operation_epoch or generation != self.desktop._intent_generation:
                return
            if restored is not True:
                self.desktop.hardware_paused = False
                self.status.set_text("Button restoration was not confirmed. Recover this mouse before changing its mappings or computer.")
                return
            def resume_after_readback(confirmed):
                if epoch != self.operation_epoch or generation != self.desktop._intent_generation:
                    return
                self.desktop.hardware_paused = False
                if (wanted and confirmed and not self.busy and not self.closed
                        and self.desktop.requested_enabled and self.current and self.current.get("online")
                        and self.current.get("stable_id") == identity):
                    self.desktop.start(automatic=True)
            def completed(result):
                # The completion may request a fresh scan after a whole profile.
                # Keep the pause until that matching readback resolves.
                self._hardware_resume = resume_after_readback
                done(result)
                if not self.busy and self._hardware_resume is resume_after_readback:
                    self._hardware_resume = None
                    resume_after_readback(True)
            def failure(error):
                self._hardware_resume = None
                self.desktop.hardware_paused = False
                self.desktop.stop()
                (failed or self.status.set_text)(error)
            self.job(work, completed, message, failed=failure)
        self.desktop.wait_stopped(stopped, preserve_intent=True)

    def refresh_battery(self):
        if self.closed:
            return GLib.SOURCE_REMOVE
        if self.busy or self.pending_hardware or not self.current or not self.current.get("online") or not valid_identity(self.current.get("stable_id")):
            return GLib.SOURCE_CONTINUE
        identity, device_id = self.current["stable_id"], self.current["id"]
        def loaded(result):
            result = result if isinstance(result, dict) else {}
            if self.current and self.current.get("stable_id") == identity and self.current["id"] == device_id:
                for name in ("battery", "battery_percent", "battery_observed_at"):
                    self.current[name] = result.get(name)
                self.shell.update_battery()
        def failed(_message):
            loaded({})
        # The battery labels already display the result. Periodic reads must not
        # replace the last operation notice with a status that never completes.
        self.job(lambda: self.backend.battery(device_id, identity), loaded, None, failed=failed)
        return GLib.SOURCE_CONTINUE

    def recover_current(self, replay=False, automatic=False):
        if automatic and self.desktop.reacquire_blocked:
            self.status.set_text("Another application changed button reporting. Use Recover or enable integration explicitly to continue.")
            return GLib.SOURCE_REMOVE
        if (automatic and self.desktop.session.process is not None
                and self.desktop.restoration_confirmed is not True):
            self.status.set_text("Previous reporting restoration was not confirmed. Use Recover for this mouse before resuming integration.")
            return GLib.SOURCE_REMOVE
        if self.closed or self.busy or self.desktop.starting or self.desktop.session.running or self.desktop.session.stopping or not self.current:
            return GLib.SOURCE_REMOVE
        device = deepcopy(self.current)
        identity = device.get("stable_id")
        if not device.get("online") or not valid_identity(identity):
            self.status.set_text("Wake and refresh this mouse before recovery.")
            return GLib.SOURCE_REMOVE
        epoch = self.operation_epoch
        def cancelled():
            return self.closed or epoch != self.operation_epoch or self.hardware_cancelled.is_set()
        def work():
            if cancelled():
                raise RuntimeError("Recovery cancelled")
            result = self.backend.recover(device["id"], identity)
            if replay and not cancelled():
                result["changes"] = replay_remembered(self.backend, device, store=self.remembered,
                                                      demo=self.demo, cancelled=cancelled)
            return result
        def loaded(result):
            if cancelled():
                return
            self.desktop.restoration_confirmed = True
            self.desktop.reacquire_blocked = False
            self.status.set_text("Previous button reporting restored." + (f" Remembered settings applied · {result['changes']} changes." if result.get('changes') else ""))
            if result.get("changes"):
                self.reload()
        self.job(work, loaded, "Recovering this mouse's previous button reporting…")
        return GLib.SOURCE_REMOVE

    def remember_current(self, enabled):
        if self.busy or not self.current:
            return
        device = deepcopy(self.current)
        if enabled:
            self.replay_suppressed_identity = None
        self.job(lambda: self.remembered.set_enabled(device, enabled, demo=self.demo),
                 lambda _result: self.status.set_text("Settings will be remembered for this mouse." if enabled else "Remembered settings disabled."),
                 "Updating remembered settings…", failed=lambda error: self.reload(error))

    def set_resume_integration(self, enabled):
        if self.desktop.config is None:
            self.status.set_text(self.desktop.error or "Local action configuration is unavailable.")
            return
        if self.closed or self.busy:
            return
        document = deepcopy(self.desktop.config)
        document["resume_integration"] = bool(enabled)
        def saved(_result):
            self.desktop.config["resume_integration"] = bool(enabled)
            if not enabled:
                self.desktop._cancel_reconnect()
            self.status.set_text("Reconnect preference saved.")
        self.job(lambda: self.desktop.store.save(document), saved, "Saving reconnect preference…")

    def request_host_switch(self, index):
        if self.closed or self.busy or self.pending_hardware or not self.current:
            return
        device = deepcopy(self.current)
        epoch = self.operation_epoch
        wait_token = object()
        self.set_hardware_wait(wait_token)
        self.replay_suppressed_identity = device.get("stable_id")
        self.desktop.stop()  # Explicit switching suppresses automatic resume and replay.
        generation = self.desktop._intent_generation
        def stopped(restored):
            if self.pending_hardware is wait_token:
                self.set_hardware_wait(None)
            if (self.closed or epoch != self.operation_epoch or generation != self.desktop._intent_generation
                    or not self.current or self.current.get("stable_id") != device.get("stable_id")):
                return
            if restored is not True:
                self.status.set_text("Button restoration was not confirmed. Recover this mouse before switching computers.")
                return
            def work():
                if self.closed or epoch != self.operation_epoch or self.hardware_cancelled.is_set():
                    raise RuntimeError("Computer switch cancelled")
                return self.backend.switch_host(device["id"], index, device.get("stable_id"))
            self.job(work, lambda result: self.status.set_text(result["message"]), "Requesting computer switch…")
        self.desktop.wait_stopped(stopped, preserve_intent=True)

    def add_device_policies(self, parent=None, host_parent=None):
        parent = parent or self.controls
        host_parent = host_parent or parent
        group = Adw.PreferencesGroup(title="Remember and reconnect")
        parent.append(group)
        valid = valid_identity(self.current.get("stable_id"))
        try:
            remembered = self.remembered.get(self.current, demo=self.demo) is not None
        except Exception as exc:
            remembered = False
            group.add(Adw.ActionRow(use_markup=False, title="Remembered settings unavailable", subtitle=str(exc)))
        row = Adw.SwitchRow(title="Remember settings for this mouse", subtitle="Reapply confirmed hardware settings when the same mouse reconnects.", active=remembered, sensitive=valid)
        row.connect("notify::active", lambda switch, _p: self.remember_current(switch.get_active()))
        group.add(row)
        resume = Adw.SwitchRow(title="Resume integration on reconnect", subtitle="Resume only for this mouse after a confirmed recovery.", active=(self.desktop.config or {}).get("resume_integration", False), sensitive=not self.demo and valid and self.desktop.config is not None)
        resume.connect("notify::active", lambda switch, _p: self.set_resume_integration(switch.get_active()))
        group.add(resume)
        row = Adw.ActionRow(use_markup=False, title="Restore previous button reporting", subtitle="Recover this app's temporary reporting after a disconnect or unexpected exit.")
        button = Gtk.Button(label="Recover", valign=Gtk.Align.CENTER, sensitive=valid)
        button.connect("clicked", lambda *_: self.recover_current())
        row.add_suffix(button)
        group.add(row)
        hosts = self.current.get("easy_switch", {})
        host_group = Adw.PreferencesGroup(title="Easy-Switch", description="Switch to a computer already paired with this mouse.")
        host_parent.append(host_group)
        if not hosts.get("supported"):
            host_group.add(Adw.ActionRow(use_markup=False, title="Computer channels unavailable", subtitle="Wake and refresh the mouse to read supported channels."))
        for host in hosts.get("hosts", []):
            current = host["index"] == hosts.get("current")
            row = Adw.ActionRow(use_markup=False, title=f"{host['index'] + 1} · {host['name']}",
                                subtitle="Current computer" if current else "Paired computer" if host.get("paired") else "Pairing unavailable" if host.get("paired") is None else "Not paired")
            button = Gtk.Button(label="Current" if current else "Switch", valign=Gtk.Align.CENTER,
                                sensitive=valid and not current and host.get("paired") is True)
            button.connect("clicked", lambda *_, index=host["index"]: self.request_host_switch(index))
            row.add_suffix(button)
            host_group.add(row)

    def sync_setting(self, setting, key=None):
        entry = self.setting_widgets.get((setting["name"], key))
        if entry is None:
            return
        widget, handler, update = entry
        value = setting.get("value")
        if key is not None:
            value = (value or {}).get(str(key), (value or {}).get(key))
        if value is not None:
            if handler is not None:
                widget.handler_block(handler)
            try:
                update(value)
            finally:
                if handler is not None:
                    widget.handler_unblock(handler)

    def update_wheel(self):
        mode = next((s.get("value") for s in self.current["settings"] if s["name"] == "scroll-ratchet"), None)
        self.wheel.configure(mode == 1)

    def refresh_after_write(self, notice):
        # A timed-out reply or partial profile can follow physical changes.
        self.current = None
        self.clear_controls()
        self.wheel.set_visible(False)
        self.add_profiles()
        self.reload(notice)

    def add_profiles(self):
        from .reference_shell import choice_row
        while child := self.profile_controls.get_first_child():
            self.profile_controls.remove(child)
        if not self.current or not self.current.get("online"):
            notice = Gtk.Box(spacing=12)
            notice.add_css_class("mx4-prompt")
            text = Gtk.Label(label="Connect and wake your mouse to save or apply profiles.", xalign=0, wrap=True, hexpand=True)
            text.add_css_class("mx4-prompt-text")
            notice.append(text)
            retry = Gtk.Button(label="Check again", valign=Gtk.Align.CENTER)
            retry.add_css_class("mx4-secondary")
            retry.connect("clicked", lambda *_: self.reload())
            notice.append(retry)
            self.profile_controls.append(notice)
            return
        group = Adw.PreferencesGroup(title="Profiles", description="Save current settings and apply them manually. Haptic playback is excluded.")
        self.profile_controls.append(group)
        save_controls = Gtk.Box(spacing=8)
        entry = Gtk.Entry(placeholder_text="Profile name", max_length=64, valign=Gtk.Align.CENTER, hexpand=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], ["Profile name"])
        save_button = Gtk.Button(label="Save", valign=Gtk.Align.CENTER, sensitive=False)
        entry.connect("changed", lambda *_: save_button.set_sensitive(bool(entry.get_text().strip())))
        save_controls.append(entry)
        save_controls.append(save_button)
        group.add(choice_row("Save current settings", "", save_controls))
        def save(*_):
            name, device = entry.get_text(), self.current
            def saved(_result):
                self.status.set_text("Profile saved.")
                self.add_profiles()
                self.toasts.add_toast(Adw.Toast(title="Profile saved.", timeout=2))
            self.job(lambda: self.store.save(name, device, demo=self.demo), saved, "Saving profile…")
        save_button.connect("clicked", save)
        entry.connect("activate", lambda *_: save() if entry.get_text().strip() else None)
        try:
            profiles = {n: p for n, p in self.store.read().items() if p.get("demo", False) == self.demo}
        except Exception as exc:
            group.add(Adw.ActionRow(title="Profiles unavailable", subtitle=str(exc)))
            return
        if not profiles:
            return
        names = sorted(profiles)
        load_controls = Gtk.Box(spacing=8)
        from .reference_shell import bounded_dropdown
        chooser = bounded_dropdown(names, accessible_name="Saved mouse profile", max_chars=22)
        chooser.set_valign(Gtk.Align.CENTER)
        chooser.set_hexpand(True)
        apply_button = Gtk.Button(label="Apply", valign=Gtk.Align.CENTER)
        def apply(*_):
            profile = profiles[names[chooser.get_selected()]]
            device = self.current
            self.hardware_job(lambda: apply_profile(self.backend, device, profile, demo=self.demo, cancelled=lambda: self.closed or self.hardware_cancelled.is_set()),
                     lambda count: self.refresh_after_write(f"Profile applied · {count} changes."), "Applying profile…",
                     failed=self.refresh_after_write)
        apply_button.connect("clicked", apply)
        load_controls.append(chooser)
        load_controls.append(apply_button)
        group.add(choice_row("Apply a saved profile", "", load_controls))


def _control_window(application, *, demo=False):
    window = next((w for w in application.get_windows()
                   if isinstance(w, ControlWindow) and not w.closed), None)
    return window if window is not None else ControlWindow(application, demo=demo)


def run(demo=False, show_ring=False, preview_ring=False):
    app = Adw.Application(application_id="io.github.mx4linux.Control" + (".Demo" if demo else ""),
                          flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    def startup(_application):
        icons = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        icons.add_search_path(str(Path(__file__).parent / "icons"))
        Gtk.Window.set_default_icon_name("io.github.mx4linux.Control")
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: _control_window(app, demo=demo).request_quit())
        app.add_action(quit_action)
        app.set_accels_for_action("app.quit", ["<Primary>q"])
    app.connect("startup", startup)
    app.add_main_option("ring", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Open Actions Ring", None)
    app.add_main_option("preview-ring", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Preview without executing actions", None)
    def activate(application):
        window = _control_window(application, demo=demo)
        window.present()
    def command_line(application, command):
        window = _control_window(application, demo=demo)
        if window.quitting:
            return 0
        options = command.get_options_dict()
        if options.contains("ring"):
            window.desktop.open_ring(preview=options.contains("preview-ring"))
        else:
            if not window.busy and not (window.desktop.requested_enabled or window.desktop.session.running):
                window.reload()
            window.present()
        return 0
    def shutdown(application):
        for window in application.get_windows():
            if isinstance(window, ControlWindow):
                window.shutdown()
    app.connect("activate", activate)
    app.connect("command-line", command_line)
    app.connect("shutdown", shutdown)
    argv = ["mx4-linux"]
    if show_ring:
        argv.append("--ring")
    if preview_ring:
        argv.append("--preview-ring")
    return app.run(argv)
