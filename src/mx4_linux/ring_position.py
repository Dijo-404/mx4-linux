"""Locate a Wayland pointer without compositor-specific IPC or cursor warping."""

from .gtk_support import Gdk, GLib, Gtk, Layer


def ring_origin(pointer, center, radius, monitor_size):
    """Translate the ring layout, keeping its visible faces inside the output.

    All values are GTK logical coordinates. In particular, an output's device
    scale must not be applied a second time on mixed-DPI desktops. Transparent
    layout padding must not push the ring hundreds of pixels from the pointer.
    The full-output input surface stays at (0, 0); only its children move.
    """
    return tuple(round(limit / 2 if limit < radius * 2 else max(radius, min(p, limit - radius))) - c
                 for p, c, limit in zip(pointer, center, monitor_size))


class PointerLocator:
    """Briefly map transparent, keyboard-free layers to obtain pointer enter.

    Wayland does not expose other clients' pointer coordinates. A full-output
    surface receives the current coordinates even when the pointer is still.
    Probe every output so keyboard focus cannot select the wrong monitor.
    """

    def __init__(self, found, timeout_ms=100):
        self.found = found
        self.timeout_ms = timeout_ms
        self.windows = []
        self.timeout = 0
        self.active = False

    def locate(self):
        self.active = True
        try:
            display = Gdk.Display.get_default()
            for monitor in display.get_monitors():
                # Keep resize enabled so opposite anchors can stretch this
                # probe. A fixed-size GTK window otherwise remains 200×200.
                window = Gtk.Window(decorated=False)
                self.windows.append(window)
                window.add_css_class("mx4-ring")
                Layer.init_for_window(window)
                Layer.set_namespace(window, "mx4-ring-pointer")
                Layer.set_monitor(window, monitor)
                Layer.set_layer(window, Layer.Layer.OVERLAY)
                Layer.set_keyboard_mode(window, Layer.KeyboardMode.NONE)
                # -1 includes panel-exclusive areas: coordinates start at (0, 0)
                # of the output rather than the compositor's reduced work area.
                Layer.set_exclusive_zone(window, -1)
                for edge in (Layer.Edge.LEFT, Layer.Edge.RIGHT, Layer.Edge.TOP, Layer.Edge.BOTTOM):
                    Layer.set_anchor(window, edge, True)
                area = Gtk.DrawingArea()
                area.set_draw_func(self._draw)
                window.set_child(area)
                motion = Gtk.EventControllerMotion()
                motion.connect("enter", self._entered, monitor)
                window.add_controller(motion)
                window.present()
            self.timeout = GLib.timeout_add(self.timeout_ms, self._expired)
        except Exception:
            # A removed output or failed map must never strand an invisible
            # input surface on the desktop, including before timeout creation.
            self.cancel()
            self.found(None, None, None)

    @staticmethod
    def _draw(_area, cr, _width, _height):
        # An empty transparent widget may not commit a buffer, so it cannot
        # receive enter. A transparent Cairo node produces an invisible buffer.
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()

    def _entered(self, _controller, x, y, monitor):
        if self.active:
            callback = self.found
            self.cancel()
            callback(monitor, x, y)

    def _expired(self):
        self.timeout = 0
        if self.active:
            callback = self.found
            self.cancel()
            callback(None, None, None)
        return False

    def cancel(self):
        self.active = False
        if self.timeout:
            GLib.source_remove(self.timeout)
            self.timeout = 0
        windows, self.windows = self.windows, []
        for window in windows:
            window.destroy()
