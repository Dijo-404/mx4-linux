"""Interactive scroll-wheel illustration; no HID access or idle render loop."""

import math
from time import monotonic

from .gtk_support import Gtk
from .motion import WheelMotion


class WheelPreview(Gtk.Box):
    def __init__(self):
        super().__init__(spacing=20)
        self.add_css_class("card")
        self.set_margin_bottom(4)
        self.motion = WheelMotion()
        self.free_spin = False
        self.tick_id = 0
        self.settings = Gtk.Settings.get_default()
        self.canvas = Gtk.DrawingArea(content_width=128, content_height=156)
        self.canvas.set_draw_func(self.draw)
        self.canvas.update_property([Gtk.AccessibleProperty.LABEL], ["Interactive scroll wheel preview"])
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.scroll)
        self.canvas.add_controller(scroll)
        self.append(self.canvas)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                          valign=Gtk.Align.CENTER, hexpand=True,
                          margin_top=16, margin_bottom=16, margin_end=16)
        title = Gtk.Label(label="Make every scroll feel right", xalign=0, wrap=True)
        title.add_css_class("title-3")
        content.append(title)
        self.detail = Gtk.Label(xalign=0, wrap=True)
        self.detail.add_css_class("dim-label")
        content.append(self.detail)
        self.try_button = Gtk.Button(label="Try the wheel", halign=Gtk.Align.START,
                                     tooltip_text="Animate a preview without changing mouse settings")
        self.try_button.add_css_class("pill")
        self.try_button.connect("clicked", lambda *_: self.spin(2))
        content.append(self.try_button)
        self.append(content)
        self.configure(False)
        self.connect("unmap", self.stop)
        self.settings.connect("notify::gtk-enable-animations", self.motion_changed)
        self.motion_changed()

    def configure(self, free_spin):
        self.free_spin = free_spin
        self.detail.set_text("Free spin · coast through long pages." if free_spin
                             else "Ratchet · precise steps, one line at a time.")

    def motion_changed(self, *_):
        enabled = self.settings.get_property("gtk-enable-animations")
        self.try_button.set_sensitive(enabled)
        if not enabled:
            self.stop()

    def spin(self, amount):
        if not self.settings.get_property("gtk-enable-animations"):
            return
        self.motion.advance(monotonic(), free_spin=self.free_spin)
        self.motion.impulse(amount)
        if not self.tick_id:
            self.tick_id = self.canvas.add_tick_callback(self.tick)

    def scroll(self, _controller, _dx, dy):
        if not self.settings.get_property("gtk-enable-animations"):
            return False
        self.spin(dy)
        return True

    def tick(self, _widget, _clock):
        active = self.motion.advance(monotonic(), free_spin=self.free_spin)
        self.canvas.queue_draw()
        if not active:
            self.tick_id = 0
        return active

    def stop(self, *_):
        if self.tick_id:
            self.canvas.remove_tick_callback(self.tick_id)
            self.tick_id = 0
        self.motion.stop()

    def draw(self, _area, cr, width, height):
        cx, cy = width / 2, height / 2
        color = self.get_color()
        # Shading and projected grooves suggest depth without a 3D renderer.
        cr.set_source_rgba(color.red, color.green, color.blue, 0.07)
        cr.arc(cx, cy, 48, 0, math.tau)
        cr.fill()
        cr.save()
        cr.translate(cx, cy)
        cr.scale(0.56, 1)
        cr.arc(0, 0, 44, 0, math.tau)
        cr.set_source_rgb(0.12, 0.14, 0.18)
        cr.fill_preserve()
        cr.set_source_rgba(color.red, color.green, color.blue, 0.25)
        cr.set_line_width(2)
        cr.stroke_preserve()
        cr.clip()
        cr.set_line_width(2)
        for index in range(18):
            angle = index * math.tau / 18 + self.motion.position
            depth = math.cos(angle)
            if depth < 0:
                continue
            y = math.sin(angle) * 42
            cr.move_to(-34, y)
            cr.line_to(34, y)
            cr.set_source_rgba(0.74, 0.79, 0.88, 0.25 + 0.65 * depth)
            cr.stroke()
        cr.restore()
