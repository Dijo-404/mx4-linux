"""GTK 4 control panel. Device work is serialized off the UI thread."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from copy import deepcopy

from .gtk_support import Adw, Gdk, Gio, GLib, Gtk

from .backend import Backend, DemoBackend
from .profiles import ProfileStore, RememberedSettingsStore, apply_profile, replay_remembered
from .reporting_state import valid_identity


class ControlWindow(Adw.ApplicationWindow):
    def __init__(self, app, demo=False):
        super().__init__(application=app, title="MX4 Linux", default_width=820, default_height=820)
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
        self.devices = []
        self.selected_id = None
        self.current = None
        self.setting_widgets = {}
        from .desktop import DesktopController
        self.desktop_status = self.desktop_toggle = self.cancel_action = None
        self.desktop = DesktopController(self)
        self.connect("close-request", self.on_close)
        self.closed = False
        self.device_refresh_source = 0
        self.device_monitor = None
        try:
            self.device_monitor = Gio.File.new_for_path("/dev").monitor_directory(Gio.FileMonitorFlags.NONE, None)
            self.device_monitor.connect("changed", self.on_device_change)
        except GLib.Error:
            pass  # Manual refresh remains available if monitoring is unavailable.

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        title = Adw.WindowTitle(title="MX4 Linux", subtitle="Preview · no hardware access" if demo else "MX Master 4 controls")
        header.set_title_widget(title)
        self.refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Read device settings")
        self.refresh.connect("clicked", lambda *_: self.reload())
        header.pack_end(self.refresh)
        toolbar.add_top_bar(header)
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=150)
        switcher = Gtk.StackSwitcher(stack=self.stack, halign=Gtk.Align.CENTER,
                                     margin_top=6, margin_bottom=6)
        toolbar.add_top_bar(switcher)

        mouse_page = self.add_page("mouse", "Mouse", "Your mouse, your way",
                                   "Changes in this preview are simulated." if demo
                                   else "Fine-tune your pointer, scrolling and haptic feedback.")
        from .wheel import WheelPreview
        self.wheel = WheelPreview()
        self.wheel.set_visible(False)
        mouse_page.append(self.wheel)
        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        mouse_page.append(self.controls)
        self.actions_box = self.add_page("actions", "Actions", "A shortcut to your next move",
                                         "Choose an action from the ring, or build a routine that does more in one press.")
        self.profiles_box = self.add_page("profiles", "Profiles", "A setup for every task",
                                          "Save your mouse settings, then switch between them when you need to.")
        self.profile_controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.profiles_box.append(self.profile_controls)
        self.add_desktop_controls()

        status_bar = Gtk.Box(spacing=12, margin_start=24, margin_end=24,
                             margin_top=12, margin_bottom=12)
        self.spinner = Gtk.Spinner(visible=False, valign=Gtk.Align.CENTER)
        status_bar.append(self.spinner)
        self.status = Gtk.Label(label="Reading connected devices…", xalign=0,
                                 wrap=True, selectable=True, hexpand=True)
        self.status.add_css_class("dim-label")
        status_bar.append(self.status)
        self.cancel_device = Gtk.Button(label="Cancel operation", visible=False, valign=Gtk.Align.CENTER)
        self.cancel_device.connect("clicked", lambda *_: self.cancel_hardware())
        status_bar.append(self.cancel_device)
        toolbar.add_bottom_bar(status_bar)
        toolbar.set_content(self.stack)
        self.toasts = Adw.ToastOverlay(child=toolbar)
        self.set_content(self.toasts)
        self.reload()
        self.battery_source = GLib.timeout_add_seconds(30, self.refresh_battery)

    def add_page(self, name, title, heading, description):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                       margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        label = Gtk.Label(label=heading, xalign=0, wrap=True)
        label.add_css_class("title-1")
        intro.append(label)
        detail = Gtk.Label(label=description, xalign=0, wrap=True)
        detail.add_css_class("dim-label")
        intro.append(detail)
        page.append(intro)
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=620, child=page)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=clamp)
        self.stack.add_titled(scroll, name, title)
        return page

    def on_close(self, *_):
        self.closed = True
        self.operation_epoch += 1
        self.hardware_cancelled.set()
        if self.battery_source:
            GLib.source_remove(self.battery_source)
            self.battery_source = 0
        self.wheel.stop()
        self.desktop.close()
        if self.device_refresh_source:
            GLib.source_remove(self.device_refresh_source)
            self.device_refresh_source = 0
        if self.device_monitor:
            self.device_monitor.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
        return False

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

    def job(self, work, done, message, failed=None):
        if self.busy or self.pending_hardware:
            return
        self.busy = True
        epoch = self.operation_epoch
        self.hardware_cancelled.clear()
        self.cancel_device.set_visible(True)
        self.controls.set_sensitive(False)
        self.profile_controls.set_sensitive(False)
        self.refresh.set_sensitive(False)
        self.spinner.set_visible(True)
        self.spinner.start()
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
            self.update_desktop_status()
            if epoch != self.operation_epoch:
                self.current = None
                self.clear_controls()
                self.add_profiles()
                self.status.set_text("Device operation cancelled; refresh to read current settings.")
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
                GLib.idle_add(lambda: self.recover_current(replay=self.current and self.current.get("stable_id") != self.replay_suppressed_identity) if not self.closed and epoch == self.operation_epoch else GLib.SOURCE_REMOVE)
            if notice:
                self.status.set_text(notice)
        self.job(self.backend.scan, loaded, "Reading connected devices… Wake the mouse if it is asleep.")

    def clear_controls(self):
        self.setting_widgets.clear()
        while child := self.controls.get_first_child():
            self.controls.remove(child)

    def render(self, result):
        self.clear_controls()
        self.current = None
        self.wheel.set_visible(False)
        if not self.devices:
            denied = any(d.get("permission_denied") for d in result.get("diagnostics", []))
            title = "Allow access to your mouse" if denied else "Connect an MX Master 4"
            detail = "Apply device access with ./scripts/install-udev-rule.sh, then click Check again." if denied else "Connect with a Logi Bolt receiver or Bluetooth, wake the mouse, then refresh."
            empty = Adw.StatusPage(title=title, description=detail, icon_name="input-mouse-symbolic")
            retry = Gtk.Button(label="Check again", halign=Gtk.Align.CENTER)
            retry.add_css_class("suggested-action")
            retry.connect("clicked", lambda *_: self.reload())
            empty.set_child(retry)
            self.controls.append(empty)
            diagnostics = "\n".join(f"{d.get('path', '')}: {d['message']}" for d in result.get("diagnostics", []))
            self.status.set_text(diagnostics or "No accessible MX Master 4 found.")
            self.add_profiles()
            self.update_desktop_status()
            return

        self.current = next((d for d in self.devices if d["id"] == self.selected_id), self.devices[0])
        self.selected_id = self.current["id"]
        device_group = Adw.PreferencesGroup(title="Device")
        chooser = Gtk.DropDown.new_from_strings([d["name"] for d in self.devices])
        chooser.set_selected(self.devices.index(self.current))
        chooser.set_valign(Gtk.Align.CENTER)
        row = Adw.ActionRow(use_markup=False, title=self.current["name"], subtitle=self.current.get("battery") or "Battery unavailable")
        row.add_prefix(Gtk.Image(icon_name="input-mouse-symbolic", pixel_size=32))
        if len(self.devices) > 1:
            row.add_suffix(chooser)
            chooser.connect("notify::selected", self.select_device, result)
        device_group.add(row)
        self.controls.append(device_group)
        if not self.current.get("online"):
            self.status.set_text("Mouse is asleep or offline. Wake it and refresh.")
            self.add_profiles()
            self.update_desktop_status()
            return

        self.wheel.set_visible(True)
        self.update_wheel()

        groups = {}
        names = [("Pointer and scrolling", ("dpi", "smart-shift", "scroll-", "hires-", "thumb-")),
                 ("Haptic feedback", ("haptic-", "force-sensing")), ("Buttons", ("reprogrammable-keys",))]
        for setting in self.current["settings"]:
            group_name = next((title for title, prefixes in names if setting["name"].startswith(prefixes)), "Device settings")
            if group_name not in groups:
                group = Adw.PreferencesGroup(title=group_name)
                groups[group_name] = group
                self.controls.append(group)
            self.add_setting(groups[group_name], setting)
        if not self.current["settings"]:
            self.controls.append(Gtk.Label(label="The mouse did not report any supported settings. Wake it and refresh.", wrap=True))

        self.add_device_policies()
        self.add_profiles()
        self.update_desktop_status()
        diagnostic_text = " · ".join(d["message"] for d in result.get("diagnostics", []))
        if self.demo:
            self.status.set_text("Demo mode · all changes are simulated")
        else:
            self.status.set_text(f"{len(self.current['settings'])} settings reported by your mouse. Changes apply immediately." + (f" {diagnostic_text}" if diagnostic_text else ""))

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
        edit.connect("clicked", lambda *_: self.desktop.configure())
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
        if self.desktop_status:
            self.desktop_status.set_subtitle(self.desktop.message)
        if self.desktop_toggle:
            active = self.desktop.session.running
            self.desktop_toggle.set_label("Disable" if active else "Enable")
            self.desktop_toggle.set_sensitive(not self.demo and not self.desktop.session.stopping
                                                and (active or (not self.busy and not self.pending_hardware and bool(self.current and self.current.get("online")))))
        if self.cancel_action:
            self.cancel_action.set_sensitive(self.desktop.action_busy)

    def select_device(self, chooser, _param, result):
        index = chooser.get_selected()
        if index < len(self.devices):
            self.operation_epoch += 1
            self.hardware_cancelled.set()
            self.desktop.stop()
            self.selected_id = self.devices[index]["id"]
            self.render(result)

    def add_setting(self, group, setting):
        if setting["kind"] == "map_choice":
            for key in setting.get("keys", []):
                value = (setting.get("value") or {}).get(str(key["value"]))
                if value is None:
                    value = (setting.get("value") or {}).get(key["value"])
                row = Adw.ActionRow(use_markup=False, title=key["label"], subtitle="Built-in mouse function")
                widget = self.choice(setting, key["choices"], value, key["value"])
                widget.update_property([Gtk.AccessibleProperty.LABEL], [key["label"]])
                row.add_suffix(widget)
                group.add(row)
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
            box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
            widget = Gtk.SpinButton.new_with_range(setting["minimum"], setting["maximum"], 1)
            widget.set_value(value if value is not None else setting["minimum"])
            widget.set_sensitive(value is not None)
            widget.update_property([Gtk.AccessibleProperty.LABEL], [setting["label"]])
            self.setting_widgets[(setting["name"], None)] = (widget, None, widget.set_value)
            apply_button = Gtk.Button(label="Apply", sensitive=value is not None)
            apply_button.set_tooltip_text(f"Apply {setting['label'].lower()}")
            apply_button.connect("clicked", lambda *_, spin=widget: self.change(setting, spin.get_value_as_int()))
            box.append(widget)
            box.append(apply_button)
            widget = box
        elif kind == "choice":
            widget = self.choice(setting, setting["choices"], value)
        else:
            return
        widget.update_property([Gtk.AccessibleProperty.LABEL], [setting["label"]])
        row.add_suffix(widget)
        group.add(row)

    def choice(self, setting, choices, value, key=None):
        dropdown = Gtk.DropDown.new_from_strings([c["label"] for c in choices])
        dropdown.set_enable_search(True)
        dropdown.set_valign(Gtk.Align.CENTER)
        dropdown.set_selected(next((i for i, c in enumerate(choices) if c["value"] == value), Gtk.INVALID_LIST_POSITION))
        if setting["name"] == "haptic-play":
            box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
            play = Gtk.Button(label="Play")
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
        previous = setting.get("value")
        if key is not None:
            previous = (previous or {}).get(str(key), (previous or {}).get(key))
        if setting["name"] != "haptic-play" and previous == value:
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
        def work():
            if self.closed or epoch != self.operation_epoch or self.hardware_cancelled.is_set():
                raise RuntimeError("Device change cancelled")
            if self.demo:
                return self.backend.write(device_id, setting["name"], value, key)
            return self.backend.write(device_id, setting["name"], value, key, expected_stable_id=device.get("stable_id"))
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
        self.set_hardware_wait(None)
        self.hardware_cancelled.set()
        self.operation_epoch += 1
        self.desktop.stop()
        self.desktop._reconnect_busy = False
        self.status.set_text("Cancelling device operation after the current request…")

    def hardware_job(self, work, done, message, failed=None, resume=True):
        """Pause temporary reporting and await confirmed child cleanup before mapping/profile changes."""
        if self.busy or self.pending_hardware or self.closed:
            return
        wait_token = object()
        self.set_hardware_wait(wait_token)
        generation = self.desktop._intent_generation
        epoch = self.operation_epoch
        wanted = self.desktop.requested_enabled and resume
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
            def completed(result):
                self.desktop.hardware_paused = False
                done(result)
                if (wanted and not self.busy and not self.closed and epoch == self.operation_epoch
                        and generation == self.desktop._intent_generation and self.desktop.requested_enabled):
                    self.desktop.start(automatic=True)
            def failure(error):
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
            if self.current and self.current.get("stable_id") == identity and self.current["id"] == device_id:
                for name in ("battery", "battery_percent", "battery_observed_at"):
                    self.current[name] = result.get(name)
        def failed(_message):
            loaded({})
        self.job(lambda: self.backend.battery(device_id, identity), loaded, "Reading battery…", failed=failed)
        return GLib.SOURCE_CONTINUE

    def recover_current(self, replay=False):
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

    def add_device_policies(self):
        group = Adw.PreferencesGroup(title="Remember and reconnect")
        self.controls.append(group)
        valid = valid_identity(self.current.get("stable_id"))
        try:
            remembered = self.remembered.get(self.current, demo=self.demo) is not None
        except Exception as exc:
            remembered = False
            group.add(Adw.ActionRow(use_markup=False, title="Remembered settings unavailable", subtitle=str(exc)))
        row = Adw.SwitchRow(title="Remember settings for this mouse", subtitle="Reapply confirmed hardware settings when the same mouse reconnects.", active=remembered, sensitive=valid)
        row.connect("notify::active", lambda switch, _p: self.remember_current(switch.get_active()))
        group.add(row)
        resume = Adw.SwitchRow(title="Resume integration on reconnect", subtitle="Resume only for this mouse after a confirmed recovery.", active=self.desktop.config.get("resume_integration", False), sensitive=not self.demo and valid)
        resume.connect("notify::active", lambda switch, _p: self.set_resume_integration(switch.get_active()))
        group.add(resume)
        row = Adw.ActionRow(use_markup=False, title="Restore previous button reporting", subtitle="Recover this app's temporary reporting after a disconnect or unexpected exit.")
        button = Gtk.Button(label="Recover", valign=Gtk.Align.CENTER, sensitive=valid)
        button.connect("clicked", lambda *_: self.recover_current())
        row.add_suffix(button)
        group.add(row)
        hosts = self.current.get("easy_switch", {})
        host_group = Adw.PreferencesGroup(title="Easy-Switch", description="Switch to a computer already paired with this mouse.")
        self.controls.append(host_group)
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
        while child := self.profile_controls.get_first_child():
            self.profile_controls.remove(child)
        if not self.current or not self.current.get("online"):
            self.profile_controls.append(Adw.StatusPage(title="Connect your mouse to use profiles",
                                                       description="Profiles save settings from a connected, awake mouse.",
                                                       icon_name="input-mouse-symbolic"))
            return
        group = Adw.PreferencesGroup(title="Profiles", description="Save current settings and apply them manually. Haptic playback is excluded.")
        self.profile_controls.append(group)
        save_row = Adw.ActionRow(title="Save current settings")
        entry = Gtk.Entry(placeholder_text="Profile name", max_length=64, valign=Gtk.Align.CENTER)
        entry.update_property([Gtk.AccessibleProperty.LABEL], ["Profile name"])
        save_button = Gtk.Button(label="Save", valign=Gtk.Align.CENTER, sensitive=False)
        entry.connect("changed", lambda *_: save_button.set_sensitive(bool(entry.get_text().strip())))
        save_row.add_suffix(entry)
        save_row.add_suffix(save_button)
        group.add(save_row)
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
        load_row = Adw.ActionRow(title="Apply a saved profile")
        chooser = Gtk.DropDown.new_from_strings(names)
        chooser.set_valign(Gtk.Align.CENTER)
        apply_button = Gtk.Button(label="Apply", valign=Gtk.Align.CENTER)
        def apply(*_):
            profile = profiles[names[chooser.get_selected()]]
            device = self.current
            self.hardware_job(lambda: apply_profile(self.backend, device, profile, demo=self.demo, cancelled=lambda: self.closed or self.hardware_cancelled.is_set()),
                     lambda count: self.refresh_after_write(f"Profile applied · {count} changes."), "Applying profile…",
                     failed=self.refresh_after_write)
        apply_button.connect("clicked", apply)
        load_row.add_suffix(chooser)
        load_row.add_suffix(apply_button)
        group.add(load_row)


def run(demo=False, show_ring=False, preview_ring=False):
    app = Adw.Application(application_id="io.github.mx4linux.Control" + (".Demo" if demo else ""),
                          flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    def startup(_application):
        icons = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        icons.add_search_path(str(Path(__file__).parent / "icons"))
        Gtk.Window.set_default_icon_name("io.github.mx4linux.Control")
    app.connect("startup", startup)
    app.add_main_option("ring", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Open Actions Ring", None)
    app.add_main_option("preview-ring", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Preview without executing actions", None)
    def control_window(application):
        window = next((w for w in application.get_windows() if isinstance(w, ControlWindow)), None)
        if not window:
            window = ControlWindow(application, demo=demo)
        return window
    def activate(application):
        window = control_window(application)
        window.present()
    def command_line(application, command):
        window = control_window(application)
        options = command.get_options_dict()
        if options.contains("ring"):
            window.desktop.open_ring(preview=options.contains("preview-ring"))
        else:
            if not window.busy:
                window.reload()
            window.present()
        return 0
    app.connect("activate", activate)
    app.connect("command-line", command_line)
    argv = ["mx4-linux"]
    if show_ring:
        argv.append("--ring")
    if preview_ring:
        argv.append("--preview-ring")
    return app.run(argv)
