"""Centered Wayland Actions Ring, operated by pointer, keyboard or raw XY."""

import math

from .gtk_support import Adw, Gdk, Gtk, Layer
from .ring_model import RingState


class ActionsRing(Gtk.Window):
    def __init__(self, application, selected, dismissed, hovered=None):
        super().__init__(application=application, title="MX4 Actions Ring", decorated=False,
                         resizable=False, default_width=460, default_height=500)
        self.state = RingState()
        self.on_selected, self.on_dismissed, self.on_hover = selected, dismissed, hovered
        self.config = None
        self.preview = False
        self.overlay_supported = bool(Layer and Layer.is_supported())
        if self.overlay_supported:
            Layer.init_for_window(self)
            Layer.set_namespace(self, "mx4-actions-ring")
            Layer.set_layer(self, Layer.Layer.OVERLAY)
            Layer.set_keyboard_mode(self, Layer.KeyboardMode.EXCLUSIVE)
            Layer.set_exclusive_zone(self, 0)
        self.add_css_class("mx4-ring")
        css = Gtk.CssProvider()
        css.load_from_string("window.mx4-ring { background: transparent; box-shadow: none; }")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.canvas = Gtk.DrawingArea(content_width=460, content_height=500, focusable=True)
        self.canvas.set_draw_func(self.draw)
        self.set_child(self.canvas)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.pointer_motion)
        self.canvas.add_controller(motion)
        click = Gtk.GestureClick(button=1)
        click.connect("released", self.pointer_click)
        self.canvas.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.key_pressed)
        self.add_controller(keys)
        self.connect("close-request", lambda *_: self.dismiss() or True)

    def show_ring(self, config, held=False, preview=False):
        self.config, self.preview = config, preview
        self.state.open(held)
        self.canvas.queue_draw()
        self.present()
        self.canvas.grab_focus()

    def hide_ring(self):
        self.state.cancel()
        self.set_visible(False)
        Gdk.Display.get_default().flush()

    def dismiss(self):
        if self.state.visible:
            self.hide_ring()
            self.on_dismissed()
        return False

    def commit(self, index):
        chosen = self.state.choose(index)
        if chosen is not None:
            self.hide_ring()
            self.on_selected(self.config["ring"][chosen], self.preview)

    def release_button(self):
        index = self.state.release()
        if index is not None:
            self.hide_ring()
            self.on_selected(self.config["ring"][index], self.preview)
        else:
            self.canvas.queue_draw()

    def motion(self, dx, dy):
        before = self.state.selected
        self.state.motion(dx, dy)
        self.changed(before)

    def changed(self, before):
        if before != self.state.selected and self.state.selected is not None and self.on_hover:
            self.on_hover(self.state.selected)
        self.canvas.queue_draw()

    def pointer_motion(self, _controller, x, y):
        if self.state.held:
            return  # Raw XY remains relative to the ring center while held.
        before = self.state.selected
        self.state.point(x - self.canvas.get_width() / 2, y - 225)
        self.changed(before)

    def pointer_click(self, _gesture, _count, x, y):
        if self.state.held:
            return
        self.state.point(x - self.canvas.get_width() / 2, y - 225)
        if self.state.selected is None:
            self.dismiss()
        else:
            self.commit(self.state.selected)

    def key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape:
            self.dismiss()
        elif Gdk.KEY_1 <= keyval <= Gdk.KEY_8:
            self.commit(keyval - Gdk.KEY_1)
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_space) and self.state.selected is not None:
            self.commit(self.state.selected)
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Up):
            before = self.state.selected
            direction = 1 if keyval in (Gdk.KEY_Right, Gdk.KEY_Down) else -1
            self.state.selected = ((before if before is not None else -direction) + direction) % 8
            self.changed(before)
        else:
            return False
        return True

    @staticmethod
    def label(cr, text, x, y, size=12, bold=False, width=104):
        # Pango handles user labels, Unicode and wrapping without markup.
        from gi.repository import Pango, PangoCairo
        layout = PangoCairo.create_layout(cr)
        description = Pango.FontDescription.from_string(f"Sans {'Bold ' if bold else ''}{size}")
        layout.set_font_description(description)
        layout.set_text(text, -1)
        layout.set_width(width * Pango.SCALE)
        layout.set_alignment(Pango.Alignment.CENTER)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_height(-2)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        _w, height = layout.get_pixel_size()
        cr.move_to(x - width / 2, y - height / 2)
        PangoCairo.show_layout(cr, layout)

    def draw(self, _area, cr, width, height):
        if not self.config:
            return
        cx, cy = width / 2, 225
        # Opaque ring segments remain legible over every desktop wallpaper.
        for index in range(8):
            angle = index * math.pi / 4 - math.pi / 2
            a, b = angle - math.pi / 8 + .025, angle + math.pi / 8 - .025
            cr.new_path()
            cr.arc(cx, cy, 205, a, b)
            cr.arc_negative(cx, cy, 76, b, a)
            cr.close_path()
            if self.state.selected == index:
                cr.set_source_rgba(.22, .45, .92, .99)
            else:
                cr.set_source_rgba(.13, .14, .17, .98)
            cr.fill_preserve()
            cr.set_source_rgba(.4, .43, .52, .4)
            cr.set_line_width(1)
            cr.stroke()
            x, y = cx + 143 * math.cos(angle), cy + 143 * math.sin(angle)
            cr.set_source_rgb(.64, .70, .85)
            self.label(cr, str(index + 1), x, y - 26, 10)
            cr.set_source_rgb(1, 1, 1)
            action = self.config["actions"][self.config["ring"][index]]
            self.label(cr, action["label"], x, y + 3, 11, True)
        cr.arc(cx, cy, 71, 0, 2 * math.pi)
        cr.set_source_rgba(.09, .10, .12, .98)
        cr.fill()
        cr.set_source_rgb(.95, .96, 1)
        self.label(cr, "Preview" if self.preview else "Actions Ring", cx, cy - 8, 12, True)
        cr.set_source_rgb(.65, .68, .75)
        self.label(cr, "Esc to close", cx, cy + 20, 9)
        cr.set_source_rgb(.83, .85, .91)
        self.label(cr, "Hold, move, release" if self.state.held else "Click an action · keys 1–8", cx, 455, 10, width=420)
        if self.state.held:
            cr.arc(cx + self.state.x, cy + self.state.y, 4, 0, 2 * math.pi)
            cr.set_source_rgb(1, 1, 1)
            cr.fill()
