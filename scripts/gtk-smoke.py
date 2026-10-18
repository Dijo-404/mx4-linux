#!/usr/bin/env python3
"""Exercise native demo widgets; use dbus-run-session and xvfb-run in CI."""

import os
from pathlib import Path
import tempfile
from time import monotonic, sleep


def main():
    with tempfile.TemporaryDirectory(prefix="mx4-gtk-smoke-") as directory:
        os.environ["XDG_CONFIG_HOME"] = directory
        os.environ["XDG_STATE_HOME"] = str(Path(directory) / "state")
        from mx4_linux.actions import DesktopConfigStore, default_config
        from mx4_linux.desktop_editor import present_desktop_editor
        from mx4_linux.gtk_support import Adw, Gdk, Gio, GLib, Gtk
        from mx4_linux.gui import ControlWindow
        from mx4_linux.ring import ActionsRing, CENTER_X, CENTER_Y
        from mx4_linux.ring_model import button_center

        context = GLib.MainContext.default()

        def wait_until(condition, timeout=4):
            deadline = monotonic() + timeout
            while not condition():
                while context.pending():
                    context.iteration(False)
                if monotonic() > deadline:
                    raise AssertionError("GTK smoke check timed out")
                sleep(0.01)

        app = Adw.Application(application_id="io.github.mx4linux.Smoke", flags=Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        isolated = default_config()
        for action in isolated["actions"].values():
            action["steps"] = [{"type": "command", "argv": ["/usr/bin/true"]}]
        DesktopConfigStore().save(isolated)
        window = ControlWindow(app, demo=True)
        ring = None
        editor = None
        settings = Gtk.Settings.get_default()
        animations = settings.get_property("gtk-enable-animations")
        try:
            window.present()
            wait_until(lambda: window.current is not None and not window.busy)
            assert window.route == "home"
            window.navigate("buttons")
            window.select_target("button:83")
            assert window.selected_target == "button:83"
            window.go_back()
            assert window.selected_target is None
            window.navigate("point")
            wait_until(window.wheel.get_mapped)
            assert window.wheel.get_visible()
            settings.set_property("gtk-enable-animations", True)
            window.wheel.spin(1)
            assert window.wheel.tick_id
            wait_until(lambda: window.wheel.tick_id == 0)
            window.navigate("haptics")
            window.wheel.pulse()
            assert window.wheel.pulse_source
            window.navigate("home")
            wait_until(lambda: window.wheel.pulse_source == 0)
            window.navigate("point")
            wait_until(window.wheel.get_mapped)

            selected = []
            ring = ActionsRing(app, lambda action, preview: selected.append((action, preview)), lambda: None)
            config = default_config()
            ring.show_ring(config, preview=True)
            wait_until(lambda: ring.tick_id == 0)
            ring.key_pressed(None, Gdk.KEY_Right, 0, 0)
            ring.key_pressed(None, Gdk.KEY_Return, 0, 0)
            assert selected == [(config["ring"][0], True)]
            assert ring.tick_id == 0 and not ring.get_visible()

            config["ring_groups"] = {"2": ["copy", "paste"]}
            ring.show_ring(config, preview=True)
            wait_until(lambda: ring.tick_id == 0)
            dx, dy = button_center(2)
            ring.pointer_motion(None, CENTER_X + dx * ring.spread, CENTER_Y + dy * ring.spread)
            assert ring.group_slot == 2 and len(selected) == 1
            wait_until(lambda: ring.tick_id == 0)
            assert len(ring.child_positions) == 2
            ring.pointer_motion(None, *ring.child_positions[1])
            assert ring.child_selected == 1
            ring.pointer_click(None, 1, *ring.child_positions[1])
            assert selected == [(config["ring"][0], True), ("paste", True)]
            assert ring.tick_id == 0 and not ring.get_visible()

            settings.set_property("gtk-enable-animations", False)
            ring.show_ring(config, preview=True)
            ring.pointer_motion(None, CENTER_X + dx, CENTER_Y + dy)
            assert ring.tick_id == 0 and ring.group_slot == 2
            assert not any(transition.active() for transition in
                           (ring.reveal, ring.group_reveal, ring.label_reveal))
            ring.dismiss()
            window.wheel.spin(1)
            assert window.wheel.tick_id == 0

            settings.set_property("gtk-enable-animations", True)
            window.wheel.spin(1)
            window.wheel.set_visible(False)
            wait_until(lambda: window.wheel.tick_id == 0)
            editor = present_desktop_editor(window, DesktopConfigStore(Path(directory) / "desktop.json"), lambda: None, cid=83)
            wait_until(editor.get_mapped)
            assert len(editor.ring_choices) == 8
            assert not editor.dirty
            assert editor.shortcuts_expander.get_expanded()
            print("GTK smoke passed: demo settings, wheel, ring groups, reduced motion, editor, cleanup.")
        finally:
            settings.set_property("gtk-enable-animations", animations)
            if editor is not None:
                editor.destroy()
            if ring is not None:
                ring.hide_ring()
                ring.destroy()
            window.close()
            app.quit()


if __name__ == "__main__":
    main()
