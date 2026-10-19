"""Accessible ring assignment preview with finite native motion."""

import math
from time import monotonic

from .gtk_support import Gdk, Gtk
from gi.repository import GObject, Graphene, Gsk, Pango
from .motion import Transition
from .ring_icons import action_icon
from .ring_model import RingGeometry

# The editor diagram keeps its roomier labeled layout; the popup ring is compact.
ORBIT_RADIUS, BUTTON_RADIUS, CENTER_RADIUS = 116, 26, 16


def button_center(index):
    return ORBIT_RADIUS * math.sin(index * math.pi / 4), -ORBIT_RADIUS * math.cos(index * math.pi / 4)


class RingPreview(Gtk.Box):
    """Show the overlay's eight positions without executing assigned actions."""

    # One provider per display, shared by all recreated editor/popup instances.
    _style_providers = {}

    def __init__(self, config, on_selected=None):
        super().__init__(hexpand=True)
        self.set_layout_manager(None)
        self._positions = {}
        self.on_selected = on_selected
        self.selected = None
        self.tick_id = 0
        self.reveal = Transition(1, duration=.24)
        self.selection_reveal = Transition(1, duration=.16)
        self.settings = Gtk.Settings.get_default()
        self._settings_handler = 0
        self._preview_scale = 1
        self.add_css_class("mx4-ring-preview")
        self.update_property([Gtk.AccessibleProperty.LABEL], [
            "Actions Ring assignment preview. Arrow keys or numbers 1 through 8 select; Enter or Space edits the assignment."])
        display = Gdk.Display.get_default()
        if display not in self._style_providers:
            css = Gtk.CssProvider()
            css.load_from_string("""
                .mx4-ring-preview {
                    font-family: "Berkeley Mono", "JetBrains Mono", "IBM Plex Mono", "Adwaita Mono", monospace;
                }
                .mx4-ring-preview button.preview-ring-action {
                    background: #201d1d; color: #fdfcfc;
                    border: 1px solid #787474;
                    border-radius: 9999px; padding: 0;
                    box-shadow: none;
                    min-width: 0; min-height: 0;
                    transition: background-color 120ms ease-out, color 120ms ease-out, border-color 140ms ease-out;
                }
                .mx4-ring-preview button.preview-ring-action:hover {
                    background: #424040; border-color: #aaa5a5;
                }
                .mx4-ring-preview button.preview-ring-action > image {
                    transform: scale(1);
                    transition: transform 160ms cubic-bezier(.2,.8,.2,1);
                }
                .mx4-ring-preview button.preview-ring-action:hover > image {
                    transform: scale(1.12);
                }
                .mx4-ring-preview button.preview-ring-action.selected {
                    background: #dce7f3; color: #243d59; border-color: #7f9ebc;
                }
                .mx4-ring-preview button.preview-ring-action:focus-visible {
                    outline: 2px solid #6b8aab; outline-offset: 3px;
                }
                .mx4-ring-preview label.preview-ring-label {
                    background: #201d1d; color: #fdfcfc; border: 1px solid #646262;
                    border-radius: 4px; padding: 6px 8px; font-size: 12px;
                    transition: border-color 140ms ease-out;
                }
                .mx4-ring-preview label.preview-ring-label.selected {
                    border-color: #6b8aab;
                }
                .mx4-ring-preview label.preview-ring-center {
                    background: #302c2c; color: #d9d5d5;
                    border-radius: 9999px;
                }
                .mx4-light .mx4-ring-preview label.preview-ring-label {
                    background: #fdfcfc; color: #201d1d; border-color: alpha(#0f0000, .12);
                }
                .mx4-light .mx4-ring-preview label.preview-ring-label.selected {
                    border-color: #6b8aab;
                }
                .mx4-light .mx4-ring-preview label.preview-ring-center {
                    background: #f1eeee; color: #646262;
                }
                .mx4-ring-preview.reduced-motion button, .mx4-ring-preview.reduced-motion label,
                .mx4-ring-preview.reduced-motion image {
                    transition: none;
                }
                .mx4-ring-preview.reduced-motion button.preview-ring-action > image {
                    transform: none;
                }
            """)
            Gtk.StyleContext.add_provider_for_display(display, css,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self._style_providers[display] = css
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_can_target(False)
        self.canvas.update_state([Gtk.AccessibleState.HIDDEN], [True])
        self.canvas.set_draw_func(self.draw)
        self.put(self.canvas, 0, 0)
        self.buttons, self.icons, self.labels = [], [], []
        # Labels sit outside the circles, preserving the overlay's clockwise order.
        for index in range(8):
            x, y = button_center(index)
            button = Gtk.Button(width_request=BUTTON_RADIUS * 2, height_request=BUTTON_RADIUS * 2)
            button.add_css_class("preview-ring-action")
            icon = Gtk.Image(pixel_size=24)
            button.set_child(icon)
            button.connect("clicked", self._clicked, index)
            self.put(button, 320 + x - BUTTON_RADIUS, 190 + y - BUTTON_RADIUS)
            label = Gtk.Label(width_request=140, wrap=True, wrap_mode=Pango.WrapMode.WORD,
                              max_width_chars=1, lines=2, ellipsize=Pango.EllipsizeMode.END,
                              justify=Gtk.Justification.CENTER)
            label.add_css_class("preview-ring-label")
            label.set_can_target(False)
            self.put(label, 0, 0)
            self.buttons.append(button)
            self.icons.append(icon)
            self.labels.append(label)
        self.center = center = Gtk.Label(label="×", width_request=CENTER_RADIUS * 2, height_request=CENTER_RADIUS * 2)
        center.add_css_class("preview-ring-center")
        center.set_can_target(False)
        center.update_state([Gtk.AccessibleState.HIDDEN], [True])
        self.put(center, 320 - CENTER_RADIUS, 190 - CENTER_RADIUS)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.key_pressed)
        self.add_controller(keys)
        self.connect("map", self.mapped)
        self.connect("unmap", self.unmapped)
        self.motion_preference_changed()
        self.update_config(config)

    def update_config(self, config):
        self.config = config
        self.geometry = RingGeometry(config.get("ring_scale", 100))
        self.layout_preview(self.get_width() or 620)
        groups = config.get("ring_groups", {})
        for index, action_id in enumerate(config["ring"]):
            action = config["actions"][action_id]
            label = action.get("label", action_id)
            grouped = str(index) in groups
            self.icons[index].set_from_icon_name(action_icon(action, grouped=grouped))
            self.labels[index].set_text(label)
            description = f"{index + 1}. {label}" + (". Action group" if grouped else "")
            self.buttons[index].set_tooltip_text(description)
            self.buttons[index].update_property([Gtk.AccessibleProperty.LABEL], [description])
        self.queue_resize()

    def select(self, index):
        if type(index) is not int or not 0 <= index < 8:
            return
        if self.selected != index:
            self.selection_reveal.move(0, animate=False)
            self.selection_reveal.move(1, animate=self.animations_enabled() and self.get_mapped())
            self.animate()
        self.selected = index
        for position, (button, label) in enumerate(zip(self.buttons, self.labels)):
            for widget in (button, label):
                if position == index:
                    widget.add_css_class("selected")
                else:
                    widget.remove_css_class("selected")
            # Native button semantics announce the currently edited assignment.
            pressed = GObject.Value(GObject.TYPE_INT, int(position == index))
            button.update_state([Gtk.AccessibleState.PRESSED], [pressed])
        self.canvas.queue_draw()

    def _clicked(self, _button, index):
        self.select(index)
        if self.on_selected:
            self.on_selected(index)

    def key_pressed(self, _controller, keyval, _keycode, modifiers):
        if modifiers & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        if Gdk.KEY_1 <= keyval <= Gdk.KEY_8:
            index = keyval - Gdk.KEY_1
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Up):
            direction = 1 if keyval in (Gdk.KEY_Right, Gdk.KEY_Down) else -1
            root = self.get_root()
            focus = root.get_focus() if root else None
            focused = next((i for i, button in enumerate(self.buttons) if button is focus), self.selected)
            index = ((focused if focused is not None else -direction) + direction) % 8
        elif keyval in (Gdk.KEY_Home, Gdk.KEY_End):
            index = 0 if keyval == Gdk.KEY_Home else 7
        else:
            return False
        self.buttons[index].grab_focus()
        # Selection stays inside the preview. Native activation (Enter/Space
        # or a click) alone moves focus to the editor's assignment control.
        self.select(index)
        return True

    def animations_enabled(self):
        return self.settings.get_property("gtk-enable-animations")

    def mapped(self, *_):
        if not self._settings_handler:
            self._settings_handler = self.settings.connect("notify::gtk-enable-animations", self.motion_preference_changed)
        self.motion_preference_changed()
        self.reveal.move(0, animate=False)
        self.reveal.move(1, animate=self.animations_enabled())
        self.apply_frame(monotonic())
        self.animate()

    def unmapped(self, *_):
        self.stop_animation()
        if self._settings_handler:
            self.settings.disconnect(self._settings_handler)
            self._settings_handler = 0

    def animate(self):
        if self.get_mapped() and self.animations_enabled() and not self.tick_id:
            self.tick_id = self.add_tick_callback(self.tick)

    def stop_animation(self):
        if self.tick_id:
            self.remove_tick_callback(self.tick_id)
            self.tick_id = 0

    def motion_preference_changed(self, *_):
        if self.animations_enabled():
            self.remove_css_class("reduced-motion")
        else:
            self.add_css_class("reduced-motion")
            self.stop_animation()
            for transition in (self.reveal, self.selection_reveal):
                transition.move(transition.target, animate=False)
            self.apply_frame(monotonic())

    def apply_frame(self, now):
        progress = self.reveal.value(now)
        for index, (button, label) in enumerate(zip(self.buttons, self.labels)):
            opacity = max(0, min(1, (progress - .025 * index) / (1 - .025 * index)))
            button.set_opacity(opacity)
            label.set_opacity(opacity)
        self.canvas.queue_draw()

    def tick(self, _widget, _clock):
        now = monotonic()
        self.apply_frame(now)
        active = self.get_mapped() and any(t.active(now) for t in (self.reveal, self.selection_reveal))
        if not active:
            self.tick_id = 0
        return active

    def draw(self, _area, cr, width, height):
        scale = self._preview_scale
        cx, cy = width / 2, 190 * scale
        cr.set_line_width(1)
        cr.set_source_rgba(.49, .47, .47, .25)
        cr.arc(cx, cy, 116 * scale, 0, math.tau)
        cr.stroke()
        if self.selected is not None:
            x, y = button_center(self.selected)
            progress = self.selection_reveal.value()
            cr.set_source_rgba(.42, .54, .67, .35 + .35 * progress)
            cr.set_line_width(1.5)
            cr.arc(cx + x * scale, cy + y * scale,
                   (BUTTON_RADIUS + 4 + 6 * (1 - progress)) * scale, 0, math.tau)
            cr.stroke()

    def layout_preview(self, width):
        # Fit the entire scaled scene; keep text readable rather than scaling fonts.
        saved_scale = self.geometry.scale
        fit = min(1, max(1, width) / (640 * saved_scale))
        scale = saved_scale * fit
        self._preview_scale = scale
        cx, cy = width / 2, 190 * scale
        self._preview_height = round(390 * scale)
        self.canvas.set_size_request(width, self._preview_height)
        label_positions = [(0, -190), (196, -102), (226, -18), (196, 96),
                           (0, 156), (-196, 96), (-226, -18), (-196, -102)]
        for index, (button, icon, label) in enumerate(zip(self.buttons, self.icons, self.labels)):
            x, y = button_center(index)
            diameter = round(BUTTON_RADIUS * 2 * scale)
            button.set_size_request(diameter, diameter)
            icon.set_pixel_size(round(24 * scale))
            self.move(button, cx + x * scale - diameter / 2, cy + y * scale - diameter / 2)
            # Keep names readable in compact editors; only the radial geometry
            # scales. Reserve room at the two widest label anchors.
            label_width = round(min(140, max(40, width - 452 * scale - 8)))
            label.set_size_request(label_width, -1)
            lx, ly = label_positions[index]
            self.move(label, cx + lx * scale - label_width / 2, cy + ly * scale)
        diameter = round(CENTER_RADIUS * 2 * scale)
        self.center.set_size_request(diameter, diameter)
        self.move(self.center, cx - diameter / 2, cy - diameter / 2)

    def put(self, child, x, y):
        self.append(child)
        self._positions[child] = (x, y)

    def move(self, child, x, y):
        self._positions[child] = (x, y)

    def get_child_position(self, child):
        return self._positions[child]

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, 620, -1, -1
        return self._preview_height, self._preview_height, -1, -1

    def do_size_allocate(self, width, height, baseline):
        self.layout_preview(width)
        for child, (x, y) in self._positions.items():
            cw = child.measure(Gtk.Orientation.HORIZONTAL, -1).natural
            ch = child.measure(Gtk.Orientation.VERTICAL, cw).natural
            child.allocate(cw, ch, -1, Gsk.Transform().translate(Graphene.Point().init(x, y)))

    def do_snapshot(self, snapshot):
        for child in self._positions:
            self.snapshot_child(child, snapshot)
