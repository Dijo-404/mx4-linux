"""Native 3D mouse artwork, image fallback and accessible surface callouts."""
from collections import OrderedDict
import json
from pathlib import Path
from time import monotonic
import weakref

from .gtk_support import Adw, Gdk, GLib, Gtk
from .mouse_motion import OrbitMotion, anchor_position, sample_pose
from .model_motion import DRAG_RADIANS_PER_PIXEL, ModelMotion, Showcase, quaternion_multiply
from .model_renderer import ModelRenderer, POSES
from gi.repository import Graphene, Pango

ASSETS = Path(__file__).parent / 'assets' / 'mouse'
ANCHORS = json.loads((ASSETS / 'surface-anchors.json').read_text())
TIMELINE = json.loads((ASSETS / 'manifest.json').read_text())['wheel_motion']['timeline']
ORBIT = json.loads((ASSETS / 'orbit.json').read_text())

# One bounded cache serves both the home card and the detail illustration. At
# 512px a camera texture is 1 MiB; original 768px wheel frames are 2.25 MiB.
# The byte limit includes both sizes and avoids a full decoded atlas per widget.
TEXTURES = OrderedDict()
TEXTURE_BUDGET = 16 * 1024 * 1024


def texture_for(path):
    if path in TEXTURES:
        TEXTURES.move_to_end(path)
        return TEXTURES[path]
    texture = Gdk.Texture.new_from_filename(path)
    TEXTURES[path] = texture
    while len(TEXTURES) > 1 and sum(t.get_width() * t.get_height() * 4 for t in TEXTURES.values()) > TEXTURE_BUDGET:
        TEXTURES.popitem(last=False)
    return texture

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


class BakedPicture(Gtk.Widget):
    """Interpolated native textures, with a pausable camera and no runtime 3D engine."""
    def __init__(self):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, can_target=False)
        self.texture = None
        self.next_texture = None
        self.blend = 0.0
        self.scene = None
        self.override = False
        self.motion = OrbitMotion()
        self.motion_enabled = True
        self.camera_hold = False
        self.ambient_tick_id = 0
        self.next_frame = 0.0
        self.on_pose_changed = None
        self.disposed = False
        self.settings = Gtk.Settings.get_default()
        reference = weakref.ref(self)
        self.settings_handler = self.settings.connect(
            'notify::gtk-enable-animations',
            lambda *_: reference() and reference()._sync_playback(),
        )
        self.connect('map', lambda *_: self._sync_playback())
        self.connect('unmap', lambda *_: self._stop_ambient())
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Silver MX Master 4 product illustration'])

    def set_filename(self, path):
        name = Path(path).name
        if name in ('overview.png', 'underside.png'):
            scene = Path(path).stem
            self.override = False
            if self.scene != scene:
                self.scene = scene
                self.motion.pose = 0.0
            self._update_pose()
            self._sync_playback()
        else:
            self.override = True
            self.texture = texture_for(path)
            self.next_texture = None
            self.queue_draw()

    def set_motion_enabled(self, enabled):
        """Pause at the visible pose; resume without advancing hidden elapsed time."""
        self.motion_enabled = bool(enabled)
        self._sync_playback()

    def steer(self, value):
        self.motion.steer(value)

    def hold_camera(self):
        self.camera_hold = True
        self._stop_ambient()
        return self.motion.pose

    def release_camera(self):
        self.camera_hold = False
        self._sync_playback()

    def set_camera_pose(self, pose):
        self.motion.pose = max(-1.0, min(1.0, pose))
        self._update_pose()

    def anchor(self, name):
        return anchor_position(ORBIT['views'][self.scene], self.motion.pose, name)

    def _update_pose(self):
        if self.scene and not self.override:
            first, second, self.blend = sample_pose(ORBIT['views'][self.scene], self.motion.pose)
            self.texture = texture_for(str(ASSETS / first['file']))
            self.next_texture = texture_for(str(ASSETS / second['file'])) if self.blend > .001 else None
        self.queue_draw()
        if self.on_pose_changed:
            self.on_pose_changed()

    def _sync_playback(self):
        active = (not self.disposed and self.scene and self.get_mapped() and self.motion_enabled
                  and not self.camera_hold and self.settings.get_property('gtk-enable-animations'))
        if active and not self.ambient_tick_id:
            self.motion.suspend()
            self.ambient_tick_id = self.add_tick_callback(self._ambient_tick)
        elif not active:
            self._stop_ambient()

    def _ambient_tick(self, _widget, clock):
        now = clock.get_frame_time() / 1_000_000
        if now + .0001 < self.next_frame:
            return GLib.SOURCE_CONTINUE
        self.next_frame += 1 / 60
        if self.next_frame <= now:
            self.next_frame = now + 1 / 60
        self.motion.advance(now)
        self._update_pose()
        return GLib.SOURCE_CONTINUE

    def _stop_ambient(self):
        if self.ambient_tick_id:
            self.remove_tick_callback(self.ambient_tick_id)
            self.ambient_tick_id = 0
        self.motion.suspend()
        self.next_frame = 0.0

    def dispose_art(self):
        self.disposed = True
        self._stop_ambient()
        self.on_pose_changed = None
        if self.settings_handler:
            self.settings.disconnect(self.settings_handler)
            self.settings_handler = 0
        self.texture = self.next_texture = None

    def do_measure(self, orientation, for_size):
        return 0, 1, -1, -1

    def do_snapshot(self, snapshot):
        if self.texture:
            bounds = Graphene.Rect().init(0, 0, self.get_width(), self.get_height())
            if self.next_texture:
                snapshot.push_cross_fade(self.blend)
                snapshot.append_texture(self.texture, bounds)
                snapshot.pop()
                snapshot.append_texture(self.next_texture, bounds)
                snapshot.pop()
            else:
                snapshot.append_texture(self.texture, bounds)


class ModelPicture(Gtk.Overlay):
    """On-demand 3D viewport with native gestures and a portable image fallback."""

    def __init__(self, *, interactive=False):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.fallback = BakedPicture()
        self.fallback.set_halign(Gtk.Align.FILL)
        self.fallback.set_valign(Gtk.Align.FILL)
        self.fallback.set_motion_enabled(False)
        self.set_child(self.fallback)
        self.renderer = ModelRenderer()
        self.renderer.set_hexpand(True)
        self.renderer.set_vexpand(True)
        self.renderer.set_can_target(False)
        self.add_overlay(self.renderer)
        self.set_measure_overlay(self.renderer, False)
        self.motion = ModelMotion(orientation=POSES['overview'])
        # Only the home card presents the product; page views stay still for their callouts.
        self.showcase = Showcase() if interactive else None
        self.drag_samples = []
        self.view_name = 'overview'
        self.motion_enabled = True
        self.interactive = interactive
        self.camera_hold = False
        self.ambient_tick_id = 0
        self.next_frame = 0.0
        self.on_pose_changed = None
        self.disposed = False
        self.ready = False
        self.ready_source = 0
        self.settings = Gtk.Settings.get_default()
        self.renderer.on_ready = self._ready
        self.renderer.on_failure = self._failed
        reference = weakref.ref(self)
        self.settings_handler = self.settings.connect(
            'notify::gtk-enable-animations', lambda *_: reference() and reference()._motion_changed())
        self.connect('map', lambda *_: self._present())
        self.connect('unmap', lambda *_: self._stop_ambient())
        self.add_css_class('mouse-model')
        self.set_focusable(interactive)
        self.update_property([Gtk.AccessibleProperty.LABEL], [
            'MX Master 4 3D model. Drag to rotate, scroll to zoom. Arrow keys rotate; Home resets.'
            if interactive else 'MX Master 4 product illustration'])
        if interactive:
            self.set_cursor_from_name('grab')
            self.set_tooltip_text('Drag in any direction to rotate · Scroll to zoom · Home to reset')
            drag = Gtk.GestureDrag(button=1)
            drag.connect('drag-begin', self._drag_begin)
            drag.connect('drag-update', self._drag_update)
            drag.connect('drag-end', self._drag_end)
            drag.connect('cancel', lambda *_: self._drag_end(None, 0, 0))
            self.add_controller(drag)
            scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
            scroll.connect('scroll', self._scroll)
            self.add_controller(scroll)
            keys = Gtk.EventControllerKey()
            keys.connect('key-pressed', self._key_pressed)
            self.add_controller(keys)
            hover = Gtk.EventControllerMotion()
            hover.connect('motion', self._hover)
            hover.connect('leave', lambda *_: self._hover(None, None, None))
            self.add_controller(hover)

    @property
    def scene(self):
        return self.fallback.scene

    @property
    def override(self):
        return self.fallback.override

    def _ready(self, *_):
        if self.disposed:
            return
        self.ready = True
        # GLArea reports readiness while GTK is snapshotting. Moving callouts
        # here would invalidate their allocation in the middle of that snapshot.
        if not self.ready_source:
            self.ready_source = GLib.idle_add(self._finish_ready)

    def _finish_ready(self):
        self.ready_source = 0
        if self.disposed or not self.ready:
            return GLib.SOURCE_REMOVE
        self.renderer.set_visible(True)
        self.fallback.set_opacity(0 if not self.camera_hold else 1)
        self._present()
        return GLib.SOURCE_REMOVE

    def _present(self):
        """Turn the home model into place whenever its card is shown."""
        if self.showcase and self.ready and self._animations():
            self.showcase.start_reveal()
        self._update_pose()
        self._sync_playback()

    def _bake(self):
        if self.showcase:
            self.motion.orientation = quaternion_multiply(self.showcase.bake(), self.motion.orientation)

    def _hover(self, _controller, x, y):
        if not self.showcase or not self.ready or self.motion.dragging or not self._animations():
            return
        if x is None:
            self.showcase.point(0, 0)
        else:
            self.showcase.point(x / max(1, self.get_width()) * 2 - 1, y / max(1, self.get_height()) * 2 - 1)
        self._sync_playback()

    def _failed(self, *_):
        self.ready = False
        self.motion.stop()
        self._stop_ambient()
        self.renderer.set_visible(False)
        self.fallback.set_opacity(1)
        self.set_cursor_from_name('default')
        if self.interactive:
            self.set_tooltip_text('3D view unavailable on this display. Product illustration shown.')
        self._update_pose()

    def set_filename(self, path):
        self.fallback.set_filename(path)

    def set_view(self, name):
        if name == self.view_name:
            return
        self.view_name = name
        self.motion.transition_to(POSES[name], animate=self._animations() and not self.renderer.failure,
                                  backward=name == 'underside')
        self._update_pose()
        self._sync_playback()

    def _animations(self):
        return self.motion_enabled and self.settings.get_property('gtk-enable-animations')

    def set_motion_enabled(self, enabled):
        self.motion_enabled = bool(enabled)
        self._sync_playback()

    def _motion_changed(self):
        if not self.settings.get_property('gtk-enable-animations'):
            if self.motion.active:
                self.motion.transition_to(POSES[self.view_name], animate=False)
            self.motion.stop()
            self._update_pose()
        self._sync_playback()

    def reset_view(self):
        self._bake()
        self.motion.reset(animate=self._animations())
        self._update_pose()
        self._sync_playback()

    def _drag_begin(self, gesture, _x, _y):
        if not self.ready:
            return
        self.grab_focus()
        if gesture:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._stop_ambient()
        self._bake()
        self.drag_samples = []
        self.motion.begin_drag()
        self.set_cursor_from_name('grabbing')

    def _drag_update(self, _gesture, dx, dy):
        if self.ready and self.motion.dragging:
            self.motion.drag(dx, dy)
            self.drag_samples = [*self.drag_samples[-5:], (monotonic(), dx, dy)]
            self._update_pose()

    def _release_rate(self):
        """Angular velocity over the last 100 ms of a drag, or None for a held release."""
        now = monotonic()
        recent = [sample for sample in self.drag_samples if now - sample[0] <= .1]
        self.drag_samples = []
        if len(recent) < 2 or now - recent[-1][0] > .05:
            return None
        span = recent[-1][0] - recent[0][0]
        if span < .012:
            return None
        scale = DRAG_RADIANS_PER_PIXEL / span
        return (recent[-1][1] - recent[0][1]) * scale, (recent[-1][2] - recent[0][2]) * scale

    def _drag_end(self, _gesture, _dx, _dy):
        released = self.motion.dragging
        self.motion.end_drag()
        self.set_cursor_from_name('grab' if self.ready else 'default')
        rate = self._release_rate() if released and self._animations() else None
        if rate:
            self.motion.fling(*rate)
        if released:
            self._sync_playback()

    def _scroll(self, _controller, _dx, dy):
        if not self.ready:
            return False
        self._bake()
        self.motion.scroll(dy)
        self._update_pose()
        return True

    def _key_pressed(self, _controller, key, _code, _state):
        if not self.ready or _state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK |
                                      Gdk.ModifierType.SUPER_MASK):
            return False
        directions = {Gdk.KEY_Left: (-.16, 0), Gdk.KEY_Right: (.16, 0),
                      Gdk.KEY_Up: (0, -.16), Gdk.KEY_Down: (0, .16)}
        if key in directions:
            self._bake()
            self.motion.rotate(*directions[key])
            self._update_pose()
        elif key in (Gdk.KEY_Home, Gdk.KEY_0):
            self.reset_view()
        elif key in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self._scroll(None, 0, -1)
        elif key in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self._scroll(None, 0, 1)
        else:
            return False
        return True

    def steer(self, _value):
        # Drag owns the home camera; page poses stay parked after transitioning.
        pass

    def hold_camera(self):
        self.camera_hold = True
        self._stop_ambient()
        self.renderer.set_opacity(0)
        self.fallback.set_opacity(1)
        return self.fallback.hold_camera()

    def release_camera(self):
        self.camera_hold = False
        self.renderer.set_opacity(1)
        self.fallback.set_opacity(0 if self.ready else 1)
        self._sync_playback()

    def set_camera_pose(self, pose):
        self.fallback.set_camera_pose(pose)
        if self.on_pose_changed:
            self.on_pose_changed()

    def anchor(self, name):
        if self.ready and not self.camera_hold:
            return self.renderer.project_anchor(name)
        return self.fallback.anchor(name)

    def anchor_visible(self, name):
        return not self.motion.active and (not self.ready or self.camera_hold or self.renderer.anchor_visible(name))

    def _update_pose(self):
        orientation, zoom = self.motion.orientation, self.motion.zoom
        if self.showcase:
            orientation = quaternion_multiply(self.showcase.offset(), orientation)
            zoom *= self.showcase.scale()
        self.renderer.set_orientation(orientation)
        self.renderer.set_zoom(zoom)
        if self.on_pose_changed:
            self.on_pose_changed()

    def _sync_playback(self):
        if self.showcase:
            sway = self.ready and not self.disposed and self._animations()
            if self.showcase.sway and not sway:
                self._bake()  # Pause and reduced motion freeze the visible pose.
            self.showcase.sway = sway
        active = (not self.disposed and self.get_mapped() and not self.camera_hold and self._animations()
                  and (self.motion.moving or bool(self.showcase and self.showcase.active)))
        if active and not self.ambient_tick_id:
            self.motion.suspend()
            if self.showcase:
                self.showcase.suspend()
            self.ambient_tick_id = self.add_tick_callback(self._tick)
        elif not active:
            self._stop_ambient()

    def _tick(self, _widget, clock):
        now = clock.get_frame_time() / 1_000_000
        if now + .0001 < self.next_frame:
            return GLib.SOURCE_CONTINUE
        active = self.motion.advance(now)
        if self.showcase and not self.motion.dragging:
            active = self.showcase.advance(now) or active
        # The slow sway alone looks identical at 30 fps and costs half the work.
        steady = self.showcase and self.showcase.steady and not self.motion.moving
        self.next_frame = now + (1 / 30 if steady else 1 / 60)
        self._update_pose()
        if not active:
            self.ambient_tick_id = 0
            self.next_frame = 0.0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _stop_ambient(self):
        if self.ambient_tick_id:
            self.remove_tick_callback(self.ambient_tick_id)
            self.ambient_tick_id = 0
        self.motion.suspend()
        self.motion.end_drag()
        if self.showcase:
            self.showcase.suspend()
        if self.interactive:
            self.set_cursor_from_name('grab' if self.ready else 'default')
        self.next_frame = 0.0

    def dispose_art(self):
        self.disposed = True
        self._stop_ambient()
        if self.ready_source:
            GLib.source_remove(self.ready_source)
            self.ready_source = 0
        self.on_pose_changed = None
        self.renderer.on_ready = self.renderer.on_failure = None
        self.renderer.dispose_art()
        self.fallback.dispose_art()
        if self.settings_handler:
            self.settings.disconnect(self.settings_handler)
            self.settings_handler = 0


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
        self.view_name = 'overview'
        self.host_channel = None
        self.online = None
        self.free_spin = False
        self.tick_id = 0
        self.started = 0
        self.pulse_source = 0
        self.pulse_started = 0
        self.wheel_pose = 0.0
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
        self.style_manager = Adw.StyleManager.get_default()
        reference = weakref.ref(self.lines)
        self.style_handler = self.style_manager.connect(
            'notify::dark', lambda *_: reference() and reference().queue_draw(),
        )
        self.add_overlay(self.lines)
        self.set_measure_overlay(self.lines, False)
        self.callout_layer = Gtk.Fixed()
        self.add_overlay(self.callout_layer)
        self.set_measure_overlay(self.callout_layer, False)
        self.connections = []
        self.picture.on_pose_changed = self._update_surface_positions
        self.settings_handler = self.settings.connect('notify::gtk-enable-animations', self.motion_changed)
        self.connect('unmap', self.stop)
        self.connect('notify::visible', lambda *_: self.stop() if not self.get_visible() else None)
        self._set_frame('overview.png')

    def set_motion_enabled(self, enabled):
        self.picture.set_motion_enabled(enabled)
        if not enabled:
            self.stop()

    def do_measure(self, orientation, for_size):
        return (280, 540, -1, -1) if orientation == Gtk.Orientation.HORIZONTAL else (250, 420, -1, -1)

    def _set_frame(self, name):
        if name != self.last_frame:
            self.picture.set_filename(str(ASSETS / name))
            self.last_frame = name

    def set_targets(self, specs, target=None, *, underside=False, view=None):
        view = view or ('underside' if underside else 'overview')
        if self.specs == specs and self.underside == underside and self.view_name == view:
            self.set_selected(target)
            return
        self.stop()
        self.underside = underside
        self.view_name = view
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
        self._set_frame('underside.png' if underside else 'overview.png')
        self.picture.set_view(view)
        self.set_selected(target)
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
            button.set_size_request(int(bw), -1)
            minimum_height, natural_height, _, _ = button.measure(Gtk.Orientation.VERTICAL, int(bw))
            bh = max(40, minimum_height, natural_height)
            button.set_size_request(int(bw), bh)
            # Offsets are bounded independently from the image, so all controls
            # remain reachable even in a narrow overlay or small window.
            bx = max(6, min(width - bw - 6, ax + offset[0]))
            by = max(6, min(height - bh - 6, ay + offset[1]))
            # Keep labels distinct when several source controls are close.
            # Native markers retain their exact registered coordinates.
            step = bh + 6
            for distance in (0, step, -step, 2 * step, -2 * step, 3 * step, -3 * step, 4 * step, -4 * step):
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
        self._update_surface_positions()
        self.lines.queue_draw()

    def _surface_position(self, name):
        x, y, size = self.art_bounds
        ax, ay = self.picture.anchor(name)
        return x + ax * size, y + ay * size

    def _update_surface_positions(self):
        if not hasattr(self, 'art_bounds') or not self.picture.scene:
            return
        # Labels stay parked for easy reading and clicking. Only their leaders
        # and the actual surface targets track the camera's registered pose.
        for key, marker in self.markers.items():
            marker.set_visible(self.picture.anchor_visible(self.specs[key][1]))
            ax, ay = self._surface_position(self.specs[key][1])
            self.callout_layer.move(marker, ax - 16, ay - 16)
        self.lines.queue_draw()

    def draw_connections(self, _area, cr, _width, _height):
        if self.picture.motion.active:
            return
        dark = Adw.StyleManager.get_default().get_dark()
        foreground = (.74, .80, .84, .66) if dark else (.23, .31, .36, .72)
        accent = (.47, .67, .87, .96) if dark else (0, .34, .70, 1)
        for key, _ax, _ay, bx, by in getattr(self, 'connections', []):
            if not self.picture.anchor_visible(self.specs[key][1]):
                continue
            ax, ay = self._surface_position(self.specs[key][1])
            selected = key == self.current_target
            cr.set_source_rgba(*(accent if selected else foreground))
            cr.set_line_width(1.6 if selected else 1.0)
            cr.move_to(ax, ay)
            midpoint = (ax + bx) / 2
            cr.curve_to(midpoint, ay, midpoint, by, bx, by)
            cr.stroke()
            cr.arc(ax, ay, 7 if selected else 6, 0, 6.284)
            cr.stroke()

        if self.underside:
            for channel in range(3):
                if not self.picture.anchor_visible(f'channel_{channel + 1}_indicator'):
                    continue
                ax, ay = self._surface_position(f'channel_{channel + 1}_indicator')
                cr.set_source_rgba(.13, .14, .14, 1)
                cr.arc(ax, ay, 4, 0, 6.284)
                cr.fill()
                if channel == self.host_channel:
                    cr.set_source_rgba(.47, .67, .87, 1)
                    cr.arc(ax, ay, 2.5, 0, 6.284)
                    cr.fill()
            # Cover the model's static green indicator when status is unknown.
            if not self.online and self.picture.anchor_visible('power'):
                ax, ay = self._surface_position('power')
                cr.set_source_rgba(.15, .16, .16, 1)
                cr.arc(ax, ay, 5, 0, 6.284)
                cr.fill()
        if self.pulse_source and not self.underside:
            ax, ay = self._surface_position('haptic_panel')
            elapsed = (monotonic() - self.pulse_started) / .8
            for delay in (0, .16, .32):
                progress = (elapsed - delay) / (1 - delay)
                if 0 <= progress < 1:
                    cr.set_source_rgba(*accent[:3], (1 - progress) * .65)
                    cr.set_line_width(2 - progress)
                    cr.arc(ax, ay, 8 + 65 * progress, 0, 6.284)
                    cr.stroke()

    def set_indicators(self, channel, online):
        self.host_channel = channel if type(channel) is int and channel in range(3) else None
        self.online = online is True
        self.lines.queue_draw()

    def pulse(self):
        """Finite visual panel ripple; independent of physical haptic playback."""
        if (not self.get_mapped() or self.underside or not self.picture.motion_enabled
                or not self.settings.get_property('gtk-enable-animations')):
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
        """Settle the camera, preview the real wheel, then restore its orbit pose."""
        if (self.underside or not self.get_mapped() or not self.picture.motion_enabled
                or not self.settings.get_property('gtk-enable-animations')):
            return
        self.stop()
        self.wheel_pose = self.picture.hold_camera()
        self.started = monotonic()
        self.tick_id = GLib.timeout_add(28, self.tick)

    def tick(self):
        elapsed = monotonic() - self.started
        if not self.get_mapped() or elapsed >= .73 or not self.settings.get_property('gtk-enable-animations'):
            self.tick_id = 0
            self._set_frame('underside.png' if self.underside else 'overview.png')
            self.picture.set_camera_pose(self.wheel_pose)
            self.picture.release_camera()
            return GLib.SOURCE_REMOVE
        if elapsed < .14:
            progress = elapsed / .14
            eased = progress * progress * (3 - 2 * progress)
            self.picture.set_camera_pose(self.wheel_pose * (1 - eased))
        elif elapsed < .59:
            self.picture.set_camera_pose(0)
            progress = (elapsed - .14) / .45
            self._set_frame(TIMELINE[min(len(TIMELINE) - 1, int(progress * len(TIMELINE)))])
        else:
            self._set_frame('overview.png')
            progress = (elapsed - .59) / .14
            eased = progress * progress * (3 - 2 * progress)
            self.picture.set_camera_pose(self.wheel_pose * eased)
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
        self.picture.release_camera()

    def dispose_art(self):
        self.stop()
        self.picture.dispose_art()
        if self.style_handler:
            self.style_manager.disconnect(self.style_handler)
            self.style_handler = 0
        if self.settings_handler:
            self.settings.disconnect(self.settings_handler)
            self.settings_handler = 0
