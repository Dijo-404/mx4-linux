"""GTK 4 control panel. Device work is serialized off the UI thread."""

from concurrent.futures import ThreadPoolExecutor

from .gtk_support import Adw, Gio, GLib, Gtk

from .backend import Backend, DemoBackend
from .profiles import ProfileStore, apply_profile


class ControlWindow(Adw.ApplicationWindow):
    def __init__(self, app, demo=False):
        super().__init__(application=app, title="MX4 Linux", default_width=820, default_height=820)
        self.demo = demo
        self.backend = DemoBackend() if demo else Backend()
        self.store = ProfileStore()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mx4-device")
        self.busy = False
        self.devices = []
        self.selected_id = None
        self.current = None
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

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=620)
        clamp.set_child(page)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(clamp)
        toolbar.set_content(scroll)
        self.set_content(toolbar)

        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        heading = Gtk.Label(label="Your mouse, on Linux", xalign=0)
        heading.add_css_class("title-1")
        intro.append(heading)
        text = "Changes in this preview are simulated." if demo else "Tune scrolling, assign buttons and control haptic feedback."
        intro.append(Gtk.Label(label=text, xalign=0, wrap=True))
        page.append(intro)

        self.status = Gtk.Label(label="Reading connected devices…", xalign=0, wrap=True, selectable=True)
        self.status.add_css_class("dim-label")
        page.append(self.status)
        self.spinner = Gtk.Spinner(halign=Gtk.Align.START)
        page.append(self.spinner)
        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        page.append(self.controls)
        self.reload()

    def on_close(self, *_):
        self.closed = True
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
        if self.busy:
            return
        self.busy = True
        self.controls.set_sensitive(False)
        self.refresh.set_sensitive(False)
        self.spinner.set_visible(True)
        self.spinner.start()
        self.status.set_text(message)
        future = self.executor.submit(work)

        def finish():
            if self.closed:
                return GLib.SOURCE_REMOVE
            self.busy = False
            self.controls.set_sensitive(True)
            self.refresh.set_sensitive(True)
            self.spinner.stop()
            self.spinner.set_visible(False)
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
            if notice:
                self.status.set_text(notice)
        self.job(self.backend.scan, loaded, "Reading connected devices… Wake the mouse if it is asleep.")

    def clear_controls(self):
        while child := self.controls.get_first_child():
            self.controls.remove(child)

    def render(self, result):
        self.clear_controls()
        self.current = None
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
            self.add_desktop_controls()
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
            self.add_desktop_controls()
            return

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

        self.add_profiles()
        self.add_desktop_controls()
        diagnostic_text = " · ".join(d["message"] for d in result.get("diagnostics", []))
        if self.demo:
            self.status.set_text("Demo mode · all changes are simulated")
        else:
            self.status.set_text(f"{len(self.current['settings'])} settings reported by your mouse. Changes apply immediately." + (f" {diagnostic_text}" if diagnostic_text else ""))

    def add_desktop_controls(self):
        group = Adw.PreferencesGroup(title="Actions Ring and Smart Actions",
                                     description="Eight ring positions, shortcuts and multi-step routines. Flow is planned for later.")
        self.controls.append(group)
        self.desktop_status = Adw.ActionRow(use_markup=False, title="Mouse integration", subtitle=self.desktop.message)
        self.desktop_status.set_subtitle_lines(5)
        self.desktop_toggle = Gtk.Button(valign=Gtk.Align.CENTER)
        self.desktop_toggle.connect("clicked", lambda *_: self.desktop.stop() if self.desktop.session.running else self.desktop.start())
        self.desktop_status.add_suffix(self.desktop_toggle)
        group.add(self.desktop_status)
        ring_row = Adw.ActionRow(use_markup=False, title="Actions Ring", subtitle="Hold the haptic panel, move and release, or click then select. Escape closes the ring.")
        preview = Gtk.Button(label="Preview ring", valign=Gtk.Align.CENTER)
        preview.connect("clicked", lambda *_: self.desktop.open_ring(preview=True))
        ring_row.add_suffix(preview)
        group.add(ring_row)
        edit_row = Adw.ActionRow(use_markup=False, title="Customize actions", subtitle="Assign ring positions and mouse buttons; create routines with shortcuts, applications, text and delays.")
        edit_row.set_subtitle_lines(3)
        edit = Gtk.Button(label="Configure", valign=Gtk.Align.CENTER)
        edit.connect("clicked", lambda *_: self.desktop.configure())
        edit_row.add_suffix(edit)
        group.add(edit_row)
        cancel_row = Adw.ActionRow(use_markup=False, title="Running routine", subtitle="Cancel stops subsequent steps and releases any held shortcut keys.")
        self.cancel_action = Gtk.Button(label="Cancel", valign=Gtk.Align.CENTER)
        self.cancel_action.connect("clicked", lambda *_: self.desktop.cancelled.set())
        cancel_row.add_suffix(self.cancel_action)
        group.add(cancel_row)
        import os
        from .actions import _native_wtype
        if not _native_wtype() and not os.access("/dev/uinput", os.R_OK | os.W_OK):
            group.add(Adw.ActionRow(use_markup=False, title="Enable keyboard shortcuts", subtitle="Run ./scripts/install-input-access.sh once. Desktop actions and app launches work without it."))
        self.update_desktop_status()

    def update_desktop_status(self):
        if self.desktop_status:
            self.desktop_status.set_subtitle(self.desktop.message)
        if self.desktop_toggle:
            active = self.desktop.session.running
            self.desktop_toggle.set_label("Disable" if active else "Enable")
            self.desktop_toggle.set_sensitive(not self.demo and not self.desktop.session.stopping)
        if self.cancel_action:
            self.cancel_action.set_sensitive(self.desktop.action_busy)

    def select_device(self, chooser, _param, result):
        index = chooser.get_selected()
        if index < len(self.devices):
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
            widget.connect("notify::active", lambda switch, _p: self.change(setting, switch.get_active()))
        elif kind == "range":
            box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
            widget = Gtk.SpinButton.new_with_range(setting["minimum"], setting["maximum"], 1)
            widget.set_value(value if value is not None else setting["minimum"])
            widget.set_sensitive(value is not None)
            apply_button = Gtk.Button(label="Apply", sensitive=value is not None)
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
        dropdown.connect("notify::selected", changed)
        return dropdown

    def change(self, setting, value, key=None):
        device_id = self.current["id"]
        text = "Simulated change." if self.demo else f"Applied {setting['label']}."
        self.job(lambda: self.backend.write(device_id, setting["name"], value, key),
                 lambda _: self.reload(text), f"Applying {setting['label']}…", failed=lambda error: self.reload(error))

    def add_profiles(self):
        group = Adw.PreferencesGroup(title="Profiles", description="Save current settings and apply them manually. Haptic playback is excluded.")
        self.controls.append(group)
        save_row = Adw.ActionRow(title="Save current settings")
        entry = Gtk.Entry(placeholder_text="Profile name", max_length=64, valign=Gtk.Align.CENTER)
        save_button = Gtk.Button(label="Save", valign=Gtk.Align.CENTER)
        save_row.add_suffix(entry)
        save_row.add_suffix(save_button)
        group.add(save_row)
        def save(*_):
            try:
                self.store.save(entry.get_text(), self.current, demo=self.demo)
                self.status.set_text("Profile saved.")
                self.reload("Profile saved.")
            except Exception as exc:
                self.status.set_text(str(exc))
        save_button.connect("clicked", save)
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
            self.job(lambda: apply_profile(self.backend, device, profile, demo=self.demo, cancelled=lambda: self.closed),
                     lambda count: self.reload(f"Profile applied · {count} changes."), "Applying profile…",
                     failed=lambda error: self.reload(error))
        apply_button.connect("clicked", apply)
        load_row.add_suffix(chooser)
        load_row.add_suffix(apply_button)
        group.add(load_row)


def run(demo=False, show_ring=False, preview_ring=False):
    app = Adw.Application(application_id="io.github.mx4linux.Control" + (".Demo" if demo else ""),
                          flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
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
