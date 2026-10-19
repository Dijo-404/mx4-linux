"""Compact native Actions Ring: white icon faces that burst from the center."""

from collections import namedtuple
import math
from time import monotonic
from uuid import uuid4

import cairo

from .gtk_support import Gdk, GLib, Gtk, Layer
import gi
from gi.repository import GObject, Graphene, Pango

try:
    gi.require_version("GdkWayland", "4.0")
    from gi.repository import GdkWayland
except (ImportError, ValueError):
    GdkWayland = None
from .motion import Transition
from .ring_icons import action_icon
from .ring_model import BUTTON_RADIUS, CENTER_RADIUS, HorizontalDrag, RingGeometry, RingState, button_at, button_center


WIDTH, HEIGHT = 780, 440
CENTER_X, CENTER_Y = 390, 220
CHILD_RADIUS = 20
# Group children fan along an outer arc, centered on their parent's angle.
CHILD_ORBIT, CHILD_SPACING = 160, math.radians(21)
DOT_RADIUS, ICON_SIZE, LABEL_GAP = 11, 20, 8

# Motion is paint only. Faces travel between the center and slots that are
# already their hit regions, so a quick click never chases a moving target.
OPEN_DURATION, EXIT_DURATION = .11, .12
GROUP_GROW, GROUP_STAGGER, GROUP_FADE = .12, .025, .09
GROUP_DURATION = GROUP_GROW + 2.5 * GROUP_STAGGER
LABEL_DELAY, LABEL_FADE = .12, .11

FacePose = namedtuple("FacePose", "scale shift", defaults=(1.0, (0.0, 0.0)))


def _point(x, y):
    return Graphene.Point().init(x, y)


def _unit(value):
    return min(1.0, max(0.0, value))


def _ease(value):
    return 1 - (1 - _unit(value)) ** 3


class RingStage(Gtk.Fixed):
    """Paints each child scaled and shifted about its fixed center; picking stays put."""

    def __init__(self):
        super().__init__()
        self.poses = {}

    def do_snapshot(self, snapshot):
        child = self.get_first_child()
        while child is not None:
            pose = self.poses.get(child, FacePose())
            ok, bounds = child.compute_bounds(self) if pose != FacePose() else (False, None)
            if not ok:
                self.snapshot_child(child, snapshot)
            else:
                x = bounds.get_x() + bounds.get_width() / 2
                y = bounds.get_y() + bounds.get_height() / 2
                snapshot.save()
                snapshot.translate(_point(x + pose.shift[0], y + pose.shift[1]))
                snapshot.scale(max(pose.scale, .01), max(pose.scale, .01))
                snapshot.translate(_point(-x, -y))
                self.snapshot_child(child, snapshot)
                snapshot.restore()
            child = child.get_next_sibling()


class ActionsRing(Gtk.Window):
    # One provider per display, shared by all recreated editor/popup instances.
    _style_providers = {}

    def __init__(self, application, selected, dismissed, hovered=None, adjusted=None):
        super().__init__(application=application, title="MX4 Actions Ring", decorated=False,
                         resizable=False, default_width=WIDTH, default_height=HEIGHT)
        self.context_app_id = "io.github.mx4linux.Ring.r" + uuid4().hex
        self.state = RingState()
        self.geometry = RingGeometry()
        self.on_adjusted = adjusted
        self._primary_start = self._drag = self._press_target = None
        self._suppress_native = False
        self._pointer_token = 0
        self._adjusted_in_hold = False
        self._keyboard_yielded = False
        self._saved_focus = None
        self.on_selected, self.on_dismissed, self.on_hover = selected, dismissed, hovered
        self.config = None
        self.preview = False
        self.tick_id = 0
        self.reveal = Transition(1, duration=OPEN_DURATION)
        self.exit = Transition(0, duration=EXIT_DURATION)
        self.group_reveal = Transition(0, duration=GROUP_DURATION)
        self.group_fade = Transition(1, duration=GROUP_FADE)
        self.label_reveal = Transition(0, duration=LABEL_FADE)
        self._exiting = self._presented = False
        self._fading = []  # Child faces of a closing group, shrinking in place.
        self._label_target = None
        self._callout_side = (0, 0)
        self.spread = 1.0
        self.group_slot = self.child_selected = None
        self.children = []
        self.child_positions = []
        self.settings = Gtk.Settings.get_default()
        self._settings_handler = 0
        display = Gdk.Display.get_default()
        self.overlay_supported = bool(Layer and display.__gtype__.name == "GdkWaylandDisplay" and Layer.is_supported())
        if self.overlay_supported:
            Layer.init_for_window(self)
            Layer.set_namespace(self, "mx4-actions-ring")
            Layer.set_layer(self, Layer.Layer.OVERLAY)
            Layer.set_keyboard_mode(self, Layer.KeyboardMode.EXCLUSIVE)
            Layer.set_exclusive_zone(self, 0)
        self.connect_after("map", self.identify_popup)
        self.add_css_class("mx4-ring")
        if display not in self._style_providers:
            css = Gtk.CssProvider()
            css.load_from_string("""
                window.mx4-ring {
                    background: transparent; box-shadow: none;
                    font-family: "Berkeley Mono", "JetBrains Mono", "IBM Plex Mono", "Adwaita Mono", monospace;
                }
                /* White faces with dark icons; the hovered face turns dark.
                   Colors are the only state change, so circles never move. */
                .mx4-ring button.ring-action {
                    background: #fbfbfc; color: #26262e;
                    border: none; border-radius: 9999px; padding: 0;
                    box-shadow: 0 1px 3px rgba(0,0,0,.32);
                    min-width: 0; min-height: 0;
                    transition: background-color 90ms ease-out, color 90ms ease-out;
                }
                .mx4-ring button.ring-action.selected { background: rgba(26,26,32,.94); color: #ffffff; }
                .mx4-ring button.ring-action.pressed,
                .mx4-ring button.ring-action.chosen { background: #b9c6f4; color: #23232b; }
                .mx4-ring button.ring-close {
                    background: none; border: none; box-shadow: none; padding: 0;
                    min-width: 0; min-height: 0; border-radius: 9999px;
                }
                /* Red while releasing or clicking would choose nothing. */
                .mx4-ring .ring-dot {
                    background: #b3c1f5; border-radius: 9999px;
                    box-shadow: 0 1px 2px rgba(0,0,0,.3);
                    transition: background-color 120ms ease-out;
                }
                .mx4-ring button.ring-close.cancel .ring-dot { background: #e8486b; }
                .mx4-ring button:focus-visible { outline: 2px solid #8cabca; outline-offset: 2px; }
                .mx4-ring.reduced-motion button, .mx4-ring.reduced-motion .ring-dot { transition: none; }
                .mx4-ring .ring-callout {
                    background: #fbfbfc; color: #2e2e36; border-radius: 9999px;
                    padding: 4px 11px; box-shadow: 0 1px 3px rgba(0,0,0,.3);
                }
            """)
            Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self._style_providers[display] = css
        self.scale_css = Gtk.CssProvider()
        self.canvas = Gtk.DrawingArea(content_width=WIDTH, content_height=HEIGHT, focusable=True)
        self.canvas.update_property([Gtk.AccessibleProperty.LABEL],
                                    ["Actions Ring. Arrow keys select, Enter opens or runs, keys 1 through 8 choose, Escape goes back or closes. Scroll or drag horizontally on adjustable actions."])
        self.surface = Gtk.Overlay(child=self.canvas)
        self.labels = RingStage()
        self.surface.add_overlay(self.labels)
        self.buttons, self.icons = [], []
        for index in range(8):
            button, icon = self.make_button(BUTTON_RADIUS)
            button.connect("clicked", lambda _button, i=index: self.native_commit(i))
            focus = Gtk.EventControllerFocus()
            focus.connect("enter", lambda _controller, i=index: self.focus_action(i))
            button.add_controller(focus)
            self.labels.put(button, 0, 0)
            self.buttons.append(button)
            self.icons.append(icon)
        self.child_buttons, self.child_icons = [], []
        for index in range(6):
            button, icon = self.make_button(CHILD_RADIUS)
            button.add_css_class("child-action")
            button.set_visible(False)
            button.connect("clicked", lambda _button, i=index: self.native_commit(i, child=True))
            focus = Gtk.EventControllerFocus()
            focus.connect("enter", lambda _controller, i=index: self.focus_child(i))
            button.add_controller(focus)
            self.labels.put(button, 0, 0)
            self.child_buttons.append(button)
            self.child_icons.append(icon)
        self.close_button = Gtk.Button(width_request=CENTER_RADIUS * 2, height_request=CENTER_RADIUS * 2)
        self.close_button.add_css_class("ring-close")
        self.close_button.update_property([Gtk.AccessibleProperty.LABEL], ["Close Actions Ring"])
        self.close_button.connect("clicked", lambda *_: self.dismiss())
        self.dot = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                           width_request=DOT_RADIUS * 2, height_request=DOT_RADIUS * 2)
        self.dot.add_css_class("ring-dot")
        self.dot.set_can_target(False)
        self.close_button.set_child(self.dot)
        self.labels.put(self.close_button, CENTER_X - CENTER_RADIUS, CENTER_Y - CENTER_RADIUS)
        # Opacity alone hides the pill: a hidden widget measures 0 x 0, and the
        # label is measured to sit on its face's outward side.
        self.callout = Gtk.Box(valign=Gtk.Align.CENTER, opacity=0)
        self.callout.add_css_class("ring-callout")
        self.callout.set_can_target(False)
        self.callout_label = Gtk.Label(single_line_mode=True, ellipsize=Pango.EllipsizeMode.END, max_width_chars=26)
        self.callout.append(self.callout_label)
        self.labels.put(self.callout, 0, 0)
        for widget in (self.callout, self.callout_label):
            widget.get_style_context().add_provider(self.scale_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.set_child(self.surface)
        motion = Gtk.EventControllerMotion()
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", self.pointer_motion)
        motion.connect("leave", self.pointer_leave)
        self.surface.add_controller(motion)
        scroll = Gtk.EventControllerScroll(flags=(Gtk.EventControllerScrollFlags.VERTICAL
                    | Gtk.EventControllerScrollFlags.HORIZONTAL | Gtk.EventControllerScrollFlags.DISCRETE))
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self.scroll)
        self.surface.add_controller(scroll)
        click = Gtk.GestureClick(button=1)
        # Native buttons have square allocations. Claim pointer clicks here so
        # only the actual circular faces can select an action.
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self.pointer_press)
        click.connect("released", self.pointer_click)
        click.connect("cancel", lambda *_: self.cancel_pointer())
        self.surface.add_controller(click)
        drag = Gtk.GestureDrag(button=1)
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", lambda gesture, x, y: self.pointer_press(gesture, 1, x, y))
        drag.connect("drag-update", self.pointer_drag)
        drag.connect("drag-end", self.pointer_drag_end)
        drag.connect("cancel", lambda *_: self.cancel_pointer())
        self.surface.add_controller(drag)
        drag.group(click)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.key_pressed)
        self.add_controller(keys)
        self.connect("close-request", lambda *_: self.dismiss() or True)
        self.connect("map", self.mapped)
        self.connect("unmap", self.unmapped)
        self.motion_preference_changed()

    def mapped(self, *_):
        if not self._settings_handler:
            self._settings_handler = self.settings.connect("notify::gtk-enable-animations", self.motion_preference_changed)
        self.motion_preference_changed()

    def unmapped(self, *_):
        self.cancel_pointer()
        self.stop_animation()
        if self._settings_handler:
            self.settings.disconnect(self._settings_handler)
            self._settings_handler = 0

    @staticmethod
    def make_button(radius):
        button = Gtk.Button(width_request=radius * 2, height_request=radius * 2)
        button.add_css_class("ring-action")
        icon = Gtk.Image(pixel_size=ICON_SIZE)
        button.set_child(icon)
        return button, icon

    def groups(self):
        return self.config.get("ring_groups", {}) if self.config else {}

    def show_ring(self, config, held=False, preview=False):
        if self._exiting:
            self.cancel_exit()
        self.motion_preference_changed()
        geometry = RingGeometry(config.get("ring_scale", 100))
        state = RingState(config.get("ring_motion_gain", .45), config.get("ring_deadzone", 24))
        self.cancel_pointer()
        self._suppress_native = False
        self.geometry, self.state = geometry, state
        self.config, self.preview = config, preview
        self._adjusted_in_hold = False
        self._keyboard_yielded = False
        if self.overlay_supported:
            Layer.set_keyboard_mode(self, Layer.KeyboardMode.EXCLUSIVE)
        self.set_default_size(geometry.size(WIDTH), geometry.size(HEIGHT))
        self.canvas.set_content_width(geometry.size(WIDTH))
        self.canvas.set_content_height(geometry.size(HEIGHT))
        for button, icon in zip(self.buttons + self.child_buttons, self.icons + self.child_icons):
            radius = CHILD_RADIUS if button in self.child_buttons else BUTTON_RADIUS
            button.set_size_request(geometry.size(radius * 2), geometry.size(radius * 2))
            icon.set_pixel_size(geometry.size(ICON_SIZE))
        self.close_button.set_size_request(geometry.size(CENTER_RADIUS * 2), geometry.size(CENTER_RADIUS * 2))
        self.dot.set_size_request(geometry.size(DOT_RADIUS * 2), geometry.size(DOT_RADIUS * 2))
        scale = geometry.scale
        self.scale_css.load_from_string(f"""
            .ring-callout {{ padding: {4 * scale}px {11 * scale}px; }}
            .ring-callout label {{ font-size: {12 * scale}px; }}
        """)
        self.position_faces()
        self.close_group()
        self.state.open(held)
        self._label_target = None
        self.label_reveal.move(0, animate=False)
        for index, action_id in enumerate(config["ring"]):
            action = config["actions"][action_id]
            grouped = str(index) in self.groups()
            self.icons[index].set_from_icon_name(action_icon(action, grouped=grouped))
            text = f"{index + 1}. {action['label']}" + (". Open action group" if grouped else self.adjustment_hint(action))
            self.buttons[index].set_tooltip_text(text)
            self.buttons[index].update_property([Gtk.AccessibleProperty.LABEL], [text])
            if grouped:
                self.expanded_state(self.buttons[index], False)
            else:
                self.buttons[index].reset_state(Gtk.AccessibleState.EXPANDED)
        self.start_entrance()
        self.update_labels()
        self.present()
        self._presented = True
        self.canvas.grab_focus()
        self.animate()

    def start_entrance(self):
        """All faces burst together from the center to their slots."""
        now = monotonic()
        self.reveal.move(0, animate=False, now=now)
        self.reveal.move(1, animate=self.animations_enabled(), now=now)
        self.apply_frame(now)

    def hide_ring(self, animate=False):
        """End the ring now. With ``animate`` the faces collapse into the center
        afterwards, with keyboard and pointer input already released."""
        self.cancel_pointer()
        self._adjusted_in_hold = False
        self.state.cancel()
        if animate and self._presented and not self._exiting and self.animations_enabled():
            self.begin_exit()
        elif not (animate and self._exiting):
            self.finish_exit()

    def begin_exit(self):
        self._exiting = True
        if self.overlay_supported:
            Layer.set_keyboard_mode(self, Layer.KeyboardMode.NONE)
        self.set_input_passthrough(True)
        now = monotonic()
        self.exit.move(0, animate=False, now=now)
        self.exit.move(1, now=now)
        self._label_target = None
        self.label_reveal.move(0, now=now)
        self.apply_frame(now)
        self.animate()
        Gdk.Display.get_default().flush()

    def finish_exit(self):
        self.cancel_exit()
        self.close_group()
        self.stop_animation()
        self._presented = False
        self.set_visible(False)
        Gdk.Display.get_default().flush()

    def cancel_exit(self):
        self._exiting = False
        self.exit.move(0, animate=False)
        self.set_input_passthrough(False)
        self.surface.set_opacity(1)
        for button in self.buttons + self.child_buttons:
            button.remove_css_class("chosen")

    def set_input_passthrough(self, passthrough):
        surface = self.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region() if passthrough else None)

    def dismiss(self):
        if self.state.visible:
            self.hide_ring(animate=True)
            self.on_dismissed()
        return False

    def commit(self, index):
        if not self.state.visible or type(index) is not int or not 0 <= index < 8:
            return
        if str(index) in self.groups():
            self.state.held = False
            self.state.selected = index
            self.open_group(index, focus=True)
            return
        chosen = self.state.choose(index)
        if chosen is not None:
            self.buttons[chosen].add_css_class("chosen")
            self.hide_ring(animate=True)
            self.on_selected(self.config["ring"][chosen], self.preview)

    def release_button(self):
        if self.state.visible and self.state.held and self._adjusted_in_hold:
            self.state.held = False
            self._adjusted_in_hold = False
            self.update_labels()
            return
        index = self.state.selected
        if self.state.visible and self.state.held and index is not None and str(index) in self.groups():
            self.commit(index)
            return
        index = self.state.release()
        if index is not None:
            self.buttons[index].add_css_class("chosen")
            self.hide_ring(animate=True)
            self.on_selected(self.config["ring"][index], self.preview)
        else:
            self.update_labels()

    def motion(self, dx, dy):
        before = self.state.selected
        self.state.motion(dx, dy)
        self.changed(before)

    def changed(self, before):
        if before != self.state.selected:
            self.child_selected = None
            self.update_labels()
            if self.state.selected is not None and self.on_hover:
                self.on_hover(self.state.selected)

    def focus_action(self, index):
        if self._primary_start is not None:
            return
        if self.state.visible and not self.state.held:
            if self.group_slot is not None and index != self.group_slot:
                self.close_group(animate=True)
            before = self.state.selected
            self.state.selected = index
            self.changed(before)

    def open_group(self, index, focus=False):
        children = self.groups().get(str(index))
        if not children or not self.state.visible:
            return
        if self.group_slot != index:
            self.cancel_pointer()
            self.clear_fading()
            if self.group_slot is not None:
                self.expanded_state(self.buttons[self.group_slot], False)
            self.group_slot, self.children, self.child_selected = index, list(children), None
            self.child_positions = self.child_layout(index, len(children))
            self.expanded_state(self.buttons[index], True)
            for i, button in enumerate(self.child_buttons):
                button.set_visible(i < len(children))
                if i < len(children):
                    x, y = self.child_positions[i]
                    self._move(button, x - CHILD_RADIUS, y - CHILD_RADIUS)
                    action = self.config["actions"][children[i]]
                    self.child_icons[i].set_from_icon_name(action_icon(action))
                    label = f"{i + 1}. {action['label']}" + self.adjustment_hint(action)
                    button.set_tooltip_text(label)
                    button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            self.group_reveal.move(0, animate=False)
            self.group_reveal.move(1, animate=self.animations_enabled())
            self.apply_frame(monotonic())
            self.animate()
        if focus:
            self.child_selected = 0
            self.child_buttons[0].grab_focus()
        self.update_labels()

    def child_layout(self, slot, count):
        """Children fan clockwise along an outer arc centered on their parent."""
        def positions(angle):
            angles = (angle + (i - (count - 1) / 2) * CHILD_SPACING for i in range(count))
            return [(CENTER_X + CHILD_ORBIT * math.sin(a), CENTER_Y - CHILD_ORBIT * math.cos(a)) for a in angles]

        return positions(slot * math.pi / 4)

    def child_growth(self, index, now):
        """Children grow from dots in place, the one in the parent's direction first."""
        if not self.group_reveal.active(now):
            return self.group_reveal.target
        delay = GROUP_STAGGER * abs(index - (len(self.children) - 1) / 2)
        return _ease((now - self.group_reveal.started - delay) / GROUP_GROW)

    def close_group(self, animate=False):
        self.cancel_pointer()
        if self.group_slot is not None:
            self.expanded_state(self.buttons[self.group_slot], False)
        self.clear_fading()
        if animate and self.group_slot is not None and self.state.visible and self.animations_enabled():
            # Hit testing forgets these children now; only their paint lingers.
            self._fading = self.child_buttons[:len(self.children)]
            for button in self._fading:
                button.set_can_target(False)
            self.group_fade.move(0, animate=False)
            self.group_fade.move(1)
        self.group_slot = self.child_selected = None
        self.children = []
        self.child_positions = []
        self.group_reveal.move(0, animate=False)
        for button in getattr(self, "child_buttons", []):
            if button not in self._fading:
                button.set_visible(False)
        self.animate()

    def clear_fading(self):
        for button in self._fading:
            button.set_visible(False)
            button.set_can_target(True)
        self._fading = []

    def focus_child(self, index):
        if self._primary_start is not None:
            return
        if self.state.visible and self.group_slot is not None and index < len(self.children):
            before = self.child_selected
            self.child_selected = index
            self.update_labels()
            if before != index and self.on_hover:
                self.on_hover(index)

    def commit_child(self, index):
        if (self.state.visible and not self.state.held and self.group_slot is not None
                and type(index) is int and 0 <= index < len(self.children)):
            action_id = self.children[index]
            self.child_buttons[index].add_css_class("chosen")
            self.hide_ring(animate=True)
            self.on_selected(action_id, self.preview)

    def update_labels(self):
        for index, button in enumerate(self.buttons):
            self.selected_style(button, index == self.state.selected or index == self.group_slot)
        for index, button in enumerate(self.child_buttons):
            self.selected_style(button, index == self.child_selected)
        selected, child = self.state.selected, self.child_selected
        if selected is None and child is None and self.group_slot is None:
            self.close_button.add_css_class("cancel")
        else:
            self.close_button.remove_css_class("cancel")
        # An open group's children take the parent's outward side, so the
        # parent shows no label; a hovered child or root face does.
        if child is not None and self.group_slot is not None and child < len(self.child_positions):
            target = (self.children[child], *self.child_positions[child], CHILD_RADIUS)
        elif selected is not None and self.group_slot is None:
            dx, dy = button_center(selected)
            target = (self.config["ring"][selected], CENTER_X + dx, CENTER_Y + dy, BUTTON_RADIUS)
        else:
            target = None
        animate, now = self.animations_enabled(), monotonic()
        if target != self._label_target:
            self._label_target = target
            if target is None:
                self.label_reveal.move(0, animate=animate, now=now)
            else:
                self.callout_label.set_text(self.config["actions"][target[0]]["label"])
                self.place_callout(*target[1:])
                self.label_reveal.move(0, animate=False, now=now)
                # Like a tooltip, the label waits briefly so a sweep stays quiet.
                self.label_reveal.move(1, animate=animate, now=now + LABEL_DELAY if animate else now)
            self.apply_frame(now)
        self.animate()

    def place_callout(self, x, y, radius):
        """Put the pill on the outward side of its face, inside the popup."""
        natural = self.callout.measure(Gtk.Orientation.HORIZONTAL, -1).natural
        width = natural / self.geometry.scale
        height = self.callout.measure(Gtk.Orientation.VERTICAL, natural).natural / self.geometry.scale
        if abs(x - CENTER_X) < 1:
            above = y < CENTER_Y
            left = x - width / 2
            top = y - radius - LABEL_GAP - height if above else y + radius + LABEL_GAP
            self._callout_side = (0, -1 if above else 1)
        elif x > CENTER_X:
            left, top, self._callout_side = x + radius + LABEL_GAP, y - height / 2, (1, 0)
        else:
            left, top, self._callout_side = x - radius - LABEL_GAP - width, y - height / 2, (-1, 0)
        self._move(self.callout, max(0, min(left, WIDTH - width)), max(0, min(top, HEIGHT - height)))

    @staticmethod
    def expanded_state(button, expanded):
        value = GObject.Value(GObject.TYPE_INT, int(expanded))
        button.update_state([Gtk.AccessibleState.EXPANDED], [value])

    @staticmethod
    def selected_style(button, selected):
        if selected:
            button.add_css_class("selected")
        else:
            button.remove_css_class("selected")

    def animations_enabled(self):
        return self.settings.get_property("gtk-enable-animations")

    def transitions(self):
        return self.reveal, self.exit, self.group_reveal, self.group_fade, self.label_reveal

    def animate(self):
        if ((self.state.visible or self._exiting) and self.animations_enabled() and not self.tick_id
                and any(t.active() for t in self.transitions())):
            self.tick_id = self.canvas.add_tick_callback(self.tick)

    def apply_frame(self, now):
        self.spread = 1.0
        scale = self.geometry.scale
        # The burst and the collapse share one path between center and slot.
        travel = self.exit.value(now) if self._exiting else 1 - self.reveal.value(now)
        self.surface.set_opacity(1 - _unit((travel - .6) / .4) if self._exiting else 1)
        poses = {self.close_button: FacePose(1 - .6 * travel if self._exiting else 1.0)}
        for index, button in enumerate(self.buttons):
            dx, dy = button_center(index)
            button.set_opacity(1)
            poses[button] = FacePose(1.0, (-dx * travel * scale, -dy * travel * scale))
        pull = travel if self._exiting else 0.0
        for index, (x, y) in enumerate(self.child_positions):
            button = self.child_buttons[index]
            button.set_opacity(1)
            poses[button] = FacePose(.12 + .88 * self.child_growth(index, now),
                                     ((CENTER_X - x) * pull * scale, (CENTER_Y - y) * pull * scale))
        if self._fading:
            fade = self.group_fade.value(now)
            if fade >= 1:
                self.clear_fading()
            for button in self._fading:
                button.set_opacity(1 - fade)
                poses[button] = FacePose(1 - fade)
        label = self.label_reveal.value(now)
        self.callout.set_opacity(_unit(label))
        nudge = 4 * (1 - label) * scale
        poses[self.callout] = FacePose(1.0, (-self._callout_side[0] * nudge, -self._callout_side[1] * nudge))
        self.labels.poses = poses
        self.labels.queue_draw()

    def tick(self, _widget, _clock):
        # The frame clock paces this at the display refresh rate; time-based
        # transitions make every rate trace the same path.
        now = monotonic()
        active = (self.state.visible or self._exiting) and any(t.active(now) for t in self.transitions())
        self.apply_frame(now)
        if not active:
            self.tick_id = 0
            if self._exiting:
                self.finish_exit()
        return active

    def stop_animation(self):
        if self.tick_id:
            self.canvas.remove_tick_callback(self.tick_id)
            self.tick_id = 0

    def motion_preference_changed(self, *_):
        if self.animations_enabled():
            self.remove_css_class("reduced-motion")
        else:
            self.add_css_class("reduced-motion")
        if not self.animations_enabled():
            self.stop_animation()
            for transition in self.transitions():
                transition.move(transition.target, animate=False)
            if self._exiting:
                self.finish_exit()
            self.clear_fading()
            self.apply_frame(monotonic())

    def child_at(self, x, y):
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        now = monotonic()
        for index, (cx, cy) in enumerate(self.child_positions):
            if math.hypot(x - cx, y - cy) > CHILD_RADIUS:
                continue
            # A child still growing from its dot is not yet a target, and one
            # overlapping another face never is.
            if self.child_growth(index, now) < .6:
                continue
            main_centers = ((CENTER_X + dx * self.spread, CENTER_Y + dy * self.spread)
                            for dx, dy in (button_center(i) for i in range(8)))
            if any(math.hypot(cx - mx, cy - my) <= CHILD_RADIUS + BUTTON_RADIUS for mx, my in main_centers):
                continue
            if any(i != index and math.hypot(cx - px, cy - py) < CHILD_RADIUS * 2
                   for i, (px, py) in enumerate(self.child_positions)):
                continue
            return index
        return None

    def pointer_motion(self, _controller, x, y):
        x, y = self.geometry.logical(x, y)
        if not self.state.visible or self.state.held or self._primary_start is not None:
            return
        if self.group_slot is not None:
            child = self.child_at(x, y)
            if child is not None:
                self.focus_child(child)
                return
        before = self.state.selected
        self.state.point(x - CENTER_X, y - CENTER_Y, spread=self.spread)
        selected = self.state.selected
        if selected is not None and str(selected) in self.groups():
            self.open_group(selected)
        elif selected is not None:
            self.close_group(animate=True)
        elif self.group_slot is not None:
            self.state.selected = self.group_slot
            self.child_selected = None
        self.changed(before)
        self.update_labels()

    def pointer_leave(self, *_):
        if self._primary_start is not None:
            return
        if not self.state.held and self.group_slot is None:
            before = self.state.selected
            self.state.selected = None
            self.changed(before)

    def pointer_press(self, gesture, _count, x, y):
        if gesture is not None:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if self._primary_start is not None or not self.state.visible:
            return
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (x, y)):
            return
        self._pointer_token += 1
        self._suppress_native = True
        self._primary_start = (x, y)
        self._press_target = self.pointer_target(x, y)
        self.press_style(self._press_target, True)
        action = self.adjustment_at(x, y)
        self._drag = (HorizontalDrag(*self.geometry.logical(x, y)), action) if action else None

    def pointer_drag(self, _gesture, dx, dy):
        if (self._primary_start is None or self._drag is None or not self.state.visible
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in (dx, dy))):
            return
        model, action = self._drag
        x, y = self._primary_start
        delta = model.move(*self.geometry.logical(x + dx, y + dy))
        if model.recognized and self.state.held:
            self._adjusted_in_hold = True
        for _ in range(abs(delta)):
            self.on_adjusted(action, "increase" if delta > 0 else "decrease", self.preview)
            if self._primary_start is None or not self.state.visible:
                break

    def pointer_drag_end(self, gesture, dx, dy):
        if self._primary_start is not None:
            x, y = self._primary_start
            self.pointer_click(gesture, 1, x + dx, y + dy)

    def cancel_pointer(self):
        self.press_style(self._press_target, False)
        self._primary_start = self._drag = self._press_target = None
        token = self._pointer_token = self._pointer_token + 1
        def unblock():
            if token == self._pointer_token:
                self._suppress_native = False
            return GLib.SOURCE_REMOVE
        GLib.idle_add(unblock)

    def press_style(self, target, pressed):
        if target is None:
            return
        kind, index = target
        button = (self.close_button if kind == "center" else
                  self.child_buttons[index] if kind == "child" else self.buttons[index])
        if pressed:
            button.add_css_class("pressed")
        else:
            button.remove_css_class("pressed")

    def native_commit(self, index, child=False):
        if not self._suppress_native and self._primary_start is None:
            if child:
                self.commit_child(index)
            elif not self.state.held:
                self.commit(index)

    def pointer_click(self, gesture, _count, x, y):
        if self._primary_start is not None:
            sx, sy = self._primary_start
            self.pointer_drag(gesture, x - sx, y - sy)
            dragged = self._drag is not None and self._drag[0].recognized
            same_face = self._press_target is not None and self.pointer_target(x, y) == self._press_target
            self.cancel_pointer()
            if dragged or not same_face:
                return
        elif gesture is not None:
            return  # Grouped drag/click release has already been handled or cancelled.
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (x, y)):
            return
        x, y = self.geometry.logical(x, y)
        if self.state.held or not self.state.visible:
            return
        if math.hypot(x - CENTER_X, y - CENTER_Y) <= CENTER_RADIUS:
            self.dismiss()
            return
        if self.group_slot is not None:
            child = self.child_at(x, y)
            if child is not None:
                self.commit_child(child)
                return
        self.state.point(x - CENTER_X, y - CENTER_Y, spread=self.spread)
        if self.state.selected is None:
            self.dismiss()
        else:
            self.commit(self.state.selected)

    def key_pressed(self, _controller, keyval, _keycode, _state):
        if not self.state.visible:
            return False
        if keyval in (Gdk.KEY_Escape, Gdk.KEY_BackSpace) and self.group_slot is not None:
            parent = self.group_slot
            self.close_group(animate=True)
            self.state.selected = parent
            self.buttons[parent].grab_focus()
            self.update_labels()
        elif keyval == Gdk.KEY_Escape:
            self.dismiss()
        elif Gdk.KEY_1 <= keyval <= Gdk.KEY_8:
            index = keyval - Gdk.KEY_1
            self.commit_child(index) if self.group_slot is not None else self.commit(index)
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_space):
            if self.get_focus() == self.close_button:
                self.dismiss()
            elif self.group_slot is not None and self.child_selected is not None:
                self.commit_child(self.child_selected)
            elif self.state.selected is not None:
                self.commit(self.state.selected)
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Up):
            direction = 1 if keyval in (Gdk.KEY_Right, Gdk.KEY_Down) else -1
            if self.group_slot is not None:
                before = self.child_selected
                self.child_selected = ((before if before is not None else -direction) + direction) % len(self.children)
                self.child_buttons[self.child_selected].grab_focus()
                self.update_labels()
            else:
                before = self.state.selected
                self.state.selected = ((before if before is not None else -direction) + direction) % 8
                self.buttons[self.state.selected].grab_focus()
                self.changed(before)
        else:
            return False
        return True

    def identify_popup(self, *_args):
        if self.overlay_supported:
            return  # Layer surfaces have no Niri window ID.
        surface = self.get_surface()
        if GdkWayland and isinstance(surface, GdkWayland.WaylandToplevel):
            surface.set_application_id(self.context_app_id)

    def _move(self, widget, x, y):
        self.labels.move(widget, *self.geometry.point(x, y))

    def position_faces(self):
        self._move(self.close_button, CENTER_X - CENTER_RADIUS, CENTER_Y - CENTER_RADIUS)
        for index, button in enumerate(self.buttons):
            dx, dy = button_center(index)
            self._move(button, CENTER_X + dx - BUTTON_RADIUS, CENTER_Y + dy - BUTTON_RADIUS)

    @staticmethod
    def adjustment_hint(action):
        return ". Scroll to adjust, or drag horizontally" if action.get("adjustment") else ""

    def scroll(self, controller, dx, dy):
        event = controller.get_current_event()
        if event is None:
            return False
        ok, x, y = event.get_position()
        if not ok:
            return False
        native = self.surface.get_native()
        translated = native.translate_coordinates(self.surface, x, y)
        return self.adjust_at(*translated, dx, dy) if translated else False

    def adjust_at(self, x, y, dx, dy):
        """Wheel up increases; horizontal-only right also increases. One step/event."""
        if (not self.state.visible or self.preview or not self.on_adjusted
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in (x, y, dx, dy))
                or (dx == 0 and dy == 0)):
            return False
        action_id = self.adjustment_at(x, y)
        if action_id is None:
            return False
        direction = "increase" if (dy < 0 if dy else dx > 0) else "decrease"
        accepted = bool(self.on_adjusted(action_id, direction, self.preview))
        if accepted and self.state.held:
            self._adjusted_in_hold = True
        return accepted

    def pointer_target(self, x, y):
        if (not self.state.visible
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in (x, y))):
            return None
        x, y = self.geometry.logical(x, y)
        if math.hypot(x - CENTER_X, y - CENTER_Y) <= CENTER_RADIUS:
            return ("center", None)
        child = self.child_at(x, y) if self.group_slot is not None else None
        if child is not None:
            return ("child", child)
        if self.group_slot is not None and any(math.hypot(x - cx, y - cy) <= CHILD_RADIUS
                                               for cx, cy in self.child_positions):
            return None
        index = button_at(x - CENTER_X, y - CENTER_Y, spread=self.spread)
        return ("root", index) if index is not None else None

    def adjustment_at(self, x, y):
        """Exact actionable circle shared by primary drag and wheel input."""
        if self.preview or not self.on_adjusted:
            return None
        target = self.pointer_target(x, y)
        if target is None or target[0] == "center":
            return None
        kind, index = target
        if kind == "root" and str(index) in self.groups():
            return None
        action_id = self.children[index] if kind == "child" else self.config["ring"][index]
        return action_id if self.config["actions"][action_id].get("adjustment") else None

    def yield_keyboard(self):
        """Keep pointer input mapped while returning the seat keyboard to the source."""
        if self._keyboard_yielded or not self.state.visible:
            return
        self._saved_focus = self.get_focus()
        self._keyboard_yielded = True
        if self.overlay_supported:
            Layer.set_keyboard_mode(self, Layer.KeyboardMode.NONE)
        Gdk.Display.get_default().flush()

    def resume_keyboard(self):
        if not self._keyboard_yielded or not self.state.visible:
            return
        self._keyboard_yielded = False
        if self.overlay_supported:
            Layer.set_keyboard_mode(self, Layer.KeyboardMode.EXCLUSIVE)
        # Normal toplevel focus was verified by the controller; another GTK
        # activation request could arrive during the next yielded adjustment.
        Gdk.Display.get_default().flush()
        focus = self._saved_focus
        if focus and focus.get_visible():
            focus.grab_focus()
        else:
            self.canvas.grab_focus()
