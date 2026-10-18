"""Native, finite mouse artwork and registered accessible callouts.

The pictures are baked from the user-supplied static model. Only the small
main-wheel preview is authored motion; it never reads or writes a device.
"""
import json
from pathlib import Path
from time import monotonic

from .gtk_support import Gdk, GLib, Gtk
from gi.repository import Graphene, Pango

ASSETS = Path(__file__).parent / 'assets' / 'mouse'
ANCHORS = json.loads((ASSETS / 'surface-anchors.json').read_text())
TIMELINE = json.loads((ASSETS / 'manifest.json').read_text())['wheel_motion']['timeline']

# Source-model geometry is visual registration, independently mapped to the
# reviewed CID allowlist. The thumb wheel is deliberately a settings target.
BUTTONS = {
    82: ('Middle button', 'main_wheel', (20, -20)),
    83: ('Back', 'back_button', (-10, 54)),
    86: ('Forward', 'forward_button', (-130, -8)),
    195: ('Gesture button', 'extra_thumb_button', (-145, -42)),
    196: ('Shift wheel mode', 'wheel_mode', (20, 10)),
    416: ('Haptic panel', 'haptic_panel', (-150, 35)),
}


class ModelPicture(Gtk.Widget):
    """A texture with a bounded natural size, instead of its 768px source size."""
    def __init__(self):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, can_target=False)
        self.texture = None
        self.cache = {}
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Silver MX Master 4 product illustration'])

    def set_filename(self, path):
        if path not in self.cache:
            self.cache[path] = Gdk.Texture.new_from_filename(path)
        self.texture = self.cache[path]
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        return 0, 1, -1, -1

    def do_snapshot(self, snapshot):
        if self.texture:
            snapshot.append_texture(self.texture, Graphene.Rect().init(0, 0, self.get_width(), self.get_height()))


class MouseView(Gtk.Overlay):
    """Responsive artwork canvas; callout buttons retain native keyboard focus."""
    def __init__(self, selected):
        super().__init__(hexpand=True, vexpand=True)
        self.selected = selected
        self.callouts = {}
        self.markers = {}
        self.specs = {}
        self.current_target = None
        self.underside = False
        self.host_channel = None
        self.online = None
        self.free_spin = False
        self.tick_id = 0
        self.started = 0
        self.pulse_source = 0
        self.pulse_started = 0
        self.last_frame = None
        self.settings = Gtk.Settings.get_default()
        self.picture = ModelPicture()
        self.picture.set_halign(Gtk.Align.CENTER)
        self.picture.set_valign(Gtk.Align.CENTER)
        self.picture.set_margin_top(52)
        self.space = Gtk.DrawingArea(content_width=280, content_height=250, hexpand=True, vexpand=True)
        self.space.connect('resize', self.allocate_art)
        self.set_child(self.space)
        self.add_overlay(self.picture)
        self.set_measure_overlay(self.picture, False)
        self.lines = Gtk.DrawingArea(can_target=False)
        self.lines.set_draw_func(self.draw_connections)
        self.add_overlay(self.lines)
        self.set_measure_overlay(self.lines, False)
        self.callout_layer = Gtk.Fixed()
        self.add_overlay(self.callout_layer)
        self.set_measure_overlay(self.callout_layer, False)
        self.connections = []
        self.settings_handler = self.settings.connect('notify::gtk-enable-animations', self.motion_changed)
        self.connect('unmap', self.stop)
        self.connect('notify::visible', lambda *_: self.stop() if not self.get_visible() else None)
        self._set_frame('overview.png')

    def do_measure(self, orientation, for_size):
        return (280, 540, -1, -1) if orientation == Gtk.Orientation.HORIZONTAL else (250, 420, -1, -1)

    def _set_frame(self, name):
        if name != self.last_frame:
            self.picture.set_filename(str(ASSETS / name))
            self.last_frame = name

    def set_targets(self, specs, target=None, *, underside=False):
        if self.specs == specs and self.underside == underside:
            self.set_selected(target)
            return
        self.stop()
        self.underside = underside
        self.current_target = target
        self.specs = specs
        for button in [*self.callouts.values(), *self.markers.values()]:
            self.callout_layer.remove(button)
        self.callouts = {}
        self.markers = {}
        for key, (label, _anchor, _offset) in specs.items():
            button = Gtk.ToggleButton(label=label, tooltip_text=f'Configure {label}')
            button.add_css_class('mouse-callout')
            button.set_can_shrink(True)
            text = button.get_child()
            text.set_max_width_chars(24)
            text.set_ellipsize(Pango.EllipsizeMode.END)
            text.set_single_line_mode(True)
            button.connect('clicked', lambda *_, key=key: self.selected(key))
            self.callout_layer.put(button, 0, 0)
            self.callouts[key] = button
            marker = Gtk.ToggleButton(tooltip_text=f'Configure {label}')
            marker.add_css_class('surface-marker')
            marker.update_property([Gtk.AccessibleProperty.LABEL], [f'{label} surface control'])
            marker.connect('clicked', lambda *_, key=key: self.selected(key))
            marker.set_size_request(32, 32)
            self.callout_layer.put(marker, 0, 0)
            self.markers[key] = marker
        self.set_selected(target)
        self._set_frame('underside.png' if underside else 'overview.png')
        self.allocate_art(self.lines, self.get_width(), self.get_height())

    def set_selected(self, target):
        self.current_target = target
        for key, button in self.callouts.items():
            if key == target:
                button.add_css_class('selected')
                button.set_tooltip_text(f'Selected: {button.get_label()}')
                button.set_label("✓ " + self.specs[key][0])
                button.set_active(True)
            else:
                button.remove_css_class('selected')
                button.set_label(self.specs[key][0])
                button.set_active(False)
        self.picture.set_margin_start(40 if target else 0)
        self.picture.set_margin_end(0 if target else 50)
        self.allocate_art(self.space, self.get_width(), self.get_height())
        self.lines.queue_draw()

    def allocate_art(self, _area, width, height):
        if width <= 0 or height <= 0:
            return
        size = max(200, int(min(width * .92, height * .90, 490)))
        shift = 20 if self.current_target else -25
        self.art_bounds = ((width - size) / 2 + shift, (height - size) / 2 + 26, size)
        x, y, _ = self.art_bounds
        self.picture.set_size_request(size, size)
        self.connections = []
        anchors = ANCHORS['underside' if self.underside else 'overview']
        placed = []
        for key, button in self.callouts.items():
            _label, anchor, offset = self.specs[key]
            ax, ay = anchors[anchor]['normalized']
            ax, ay = x + ax * size, y + ay * size
            button.set_size_request(-1, -1)
            _minimum, natural, _, _ = button.measure(Gtk.Orientation.HORIZONTAL, -1)
            bw = min(max(72, natural), min(190, width * .42))
            bh = 34
            button.set_size_request(int(bw), bh)
            # Offsets are bounded independently from the image, so all controls
            # remain reachable even in a narrow overlay or small window.
            bx = max(6, min(width - bw - 6, ax + offset[0]))
            by = max(6, min(height - bh - 6, ay + offset[1]))
            # Keep labels distinct when several source controls are close.
            # Native markers retain their exact registered coordinates.
            for distance in (0, 38, -38, 76, -76, 114, -114, 152, -152):
                candidate = max(6, min(height - bh - 6, by + distance))
                if all(bx + bw + 4 <= px or px + pw + 4 <= bx or candidate + bh + 4 <= py or py + ph + 4 <= candidate
                       for px, py, pw, ph in placed):
                    by = candidate
                    break
            placed.append((bx, by, bw, bh))
            self.callout_layer.move(button, bx, by)
            self.callout_layer.move(self.markers[key], ax - 16, ay - 16)
            self.markers[key].set_active(key == self.current_target)
            self.connections.append((key, ax, ay, bx + bw / 2, by + bh / 2))
        self.callout_bounds = placed
        self.lines.queue_draw()

    def draw_connections(self, _area, cr, _width, _height):
        for key, ax, ay, bx, by in getattr(self, 'connections', []):
            selected = key == self.current_target
            cr.set_source_rgba(0, .92, .82, .95) if selected else cr.set_source_rgba(1, 1, 1, .95)
            cr.set_line_width(1.5)
            cr.move_to(ax, ay)
            cr.line_to(bx, by)
            cr.stroke()
            cr.arc(ax, ay, 7 if selected else 6, 0, 6.284)
            cr.stroke()

        if self.underside:
            x, y, size = self.art_bounds
            for channel in range(3):
                ax, ay = ANCHORS['underside'][f'channel_{channel + 1}_indicator']['normalized']
                cr.set_source_rgba(.13, .14, .14, 1)
                cr.arc(x + ax * size, y + ay * size, 4, 0, 6.284)
                cr.fill()
                if channel == self.host_channel:
                    cr.set_source_rgba(0, .92, .82, 1)
                    cr.arc(x + ax * size, y + ay * size, 2.5, 0, 6.284)
                    cr.fill()
            # Cover the model's static green indicator when status is unknown.
            if not self.online:
                ax, ay = ANCHORS['underside']['power']['normalized']
                cr.set_source_rgba(.15, .16, .16, 1)
                cr.arc(x + ax * size, y + ay * size, 5, 0, 6.284)
                cr.fill()
        if self.pulse_source and not self.underside:
            x, y, size = self.art_bounds
            ax, ay = ANCHORS['overview']['haptic_panel']['normalized']
            elapsed = (monotonic() - self.pulse_started) / .8
            for delay in (0, .16, .32):
                progress = (elapsed - delay) / (1 - delay)
                if 0 <= progress < 1:
                    cr.set_source_rgba(0, .92, .82, (1 - progress) * .65)
                    cr.set_line_width(2 - progress)
                    cr.arc(x + ax * size, y + ay * size, 8 + 65 * progress, 0, 6.284)
                    cr.stroke()

    def set_indicators(self, channel, online):
        self.host_channel = channel if type(channel) is int and channel in range(3) else None
        self.online = online is True
        self.lines.queue_draw()

    def pulse(self):
        """Finite visual panel ripple; independent of physical haptic playback."""
        if not self.get_mapped() or self.underside or not self.settings.get_property('gtk-enable-animations'):
            return
        self.stop()
        self.pulse_started = monotonic()
        self.pulse_source = GLib.timeout_add(30, self.pulse_tick)

    def pulse_tick(self):
        if not self.get_mapped() or monotonic() - self.pulse_started >= .8 or not self.settings.get_property('gtk-enable-animations'):
            self.pulse_source = 0
            self.lines.queue_draw()
            return GLib.SOURCE_REMOVE
        self.lines.queue_draw()
        return GLib.SOURCE_CONTINUE

    def configure(self, free_spin):
        self.free_spin = free_spin

    def motion_changed(self, *_):
        if not self.settings.get_property('gtk-enable-animations'):
            self.stop()

    def spin(self, _amount=1):
        """One 450ms, bounded 12-degree wheel movement; never an idle loop."""
        if self.underside or not self.get_mapped() or not self.settings.get_property('gtk-enable-animations'):
            return
        self.stop()
        self.started = monotonic()
        self.tick_id = GLib.timeout_add(28, self.tick)

    def tick(self):
        progress = (monotonic() - self.started) / .45
        if not self.get_mapped() or progress >= 1 or not self.settings.get_property('gtk-enable-animations'):
            self.tick_id = 0
            self._set_frame('underside.png' if self.underside else 'overview.png')
            return GLib.SOURCE_REMOVE
        self._set_frame(TIMELINE[min(len(TIMELINE) - 1, int(progress * len(TIMELINE)))])
        return GLib.SOURCE_CONTINUE

    def stop(self, *_):
        if self.pulse_source:
            GLib.source_remove(self.pulse_source)
            self.pulse_source = 0
            self.lines.queue_draw()
        if self.tick_id:
            GLib.source_remove(self.tick_id)
            self.tick_id = 0
        self._set_frame('underside.png' if self.underside else 'overview.png')

    def dispose_art(self):
        self.stop()
        if self.settings_handler:
            self.settings.disconnect(self.settings_handler)
            self.settings_handler = 0
