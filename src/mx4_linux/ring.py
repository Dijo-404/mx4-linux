"""Compact native Actions Ring with animated icon buttons and action groups."""

import math
from time import monotonic
from uuid import uuid4

from .gtk_support import Gdk, Gtk, Layer
import gi
from gi.repository import GObject, Pango

try:
    gi.require_version("GdkWayland", "4.0")
    from gi.repository import GdkWayland
except (ImportError, ValueError):
    GdkWayland = None
from .motion import Transition
from .ring_icons import action_icon
from .ring_model import BUTTON_RADIUS, CENTER_RADIUS, RingGeometry, RingState, button_at, button_center


WIDTH, HEIGHT = 720, 460
CENTER_X, CENTER_Y = 270, 220
CHILD_RADIUS = 23


class ActionsRing(Gtk.Window):
    def __init__(self, application, selected, dismissed, hovered=None, adjusted=None):
        super().__init__(application=application, title="MX4 Actions Ring", decorated=False,
                         resizable=False, default_width=WIDTH, default_height=HEIGHT)
        self.context_app_id = "io.github.mx4linux.Ring.r" + uuid4().hex
        self.state = RingState()
        self.geometry = RingGeometry()
        self.on_adjusted = adjusted
        self._adjusted_in_hold = False
        self._keyboard_yielded = False
        self._saved_focus = None
        self.on_selected, self.on_dismissed, self.on_hover = selected, dismissed, hovered
        self.config = None
        self.preview = False
        self.tick_id = 0
        self.reveal = Transition(1, duration=0.18)
        self.group_reveal = Transition(0, duration=0.16)
        self.label_reveal = Transition(1, duration=0.10)
        self.spread = 1.0
        self.group_slot = self.child_selected = None
        self.children = []
        self.child_positions = []
        self.settings = Gtk.Settings.get_default()
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
        css = Gtk.CssProvider()
        css.load_from_string("""
            window.mx4-ring { background: transparent; box-shadow: none; }
            .mx4-ring button.ring-action, .mx4-ring button.ring-close {
                background: #eef2f4; color: #15181d; border: 1px solid alpha(black, 0.15);
                border-radius: 9999px; padding: 0; box-shadow: 0 2px 5px alpha(black, 0.16);
                min-width: 0; min-height: 0;
            }
            .mx4-ring button.ring-action.selected { background: #15181d; color: white; border-color: #eef2f4; }
            .mx4-ring button.ring-close { background: #d9e1e5; }
            .mx4-ring button:focus-visible { outline: 3px solid #3584e4; outline-offset: 3px; }
            .mx4-ring .ring-callout {
                background: #f8fafb; color: #15181d; border: 1px solid alpha(black, 0.12);
                border-radius: 12px; padding: 7px 10px; box-shadow: 0 2px 5px alpha(black, 0.12);
            }
            .mx4-ring .ring-shortcut { color: #53616b; font-size: 11px; }
            .mx4-ring .ring-hint {
                background: #20242b; color: #f0f3f6; border-radius: 12px;
                padding: 6px 12px; font-size: 12px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.scale_css = Gtk.CssProvider()
        self.canvas = Gtk.DrawingArea(content_width=WIDTH, content_height=HEIGHT, focusable=True)
        self.canvas.update_property([Gtk.AccessibleProperty.LABEL],
                                    ["Actions Ring. Arrow keys select, Enter opens or runs, keys 1 through 8 choose, Escape goes back or closes."])
        self.canvas.set_draw_func(self.draw)
        self.surface = Gtk.Overlay(child=self.canvas)
        self.labels = Gtk.Fixed()
        self.surface.add_overlay(self.labels)
        self.buttons, self.icons = [], []
        for index in range(8):
            button, icon = self.make_button(BUTTON_RADIUS)
            button.connect("clicked", lambda _button, i=index: self.commit(i) if not self.state.held else None)
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
            button.connect("clicked", lambda _button, i=index: self.commit_child(i))
            focus = Gtk.EventControllerFocus()
            focus.connect("enter", lambda _controller, i=index: self.focus_child(i))
            button.add_controller(focus)
            self.labels.put(button, 0, 0)
            self.child_buttons.append(button)
            self.child_icons.append(icon)
        self.close_button = Gtk.Button(icon_name="window-close-symbolic", width_request=32, height_request=32,
                                       tooltip_text="Close Actions Ring")
        self.close_button.add_css_class("ring-close")
        self.close_button.update_property([Gtk.AccessibleProperty.LABEL], ["Close Actions Ring"])
        self.close_button.connect("clicked", lambda *_: self.dismiss())
        self.labels.put(self.close_button, CENTER_X - CENTER_RADIUS, CENTER_Y - CENTER_RADIUS)
        self.callout = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        self.callout.add_css_class("ring-callout")
        self.callout.set_can_target(False)
        self.callout_label = Gtk.Label(wrap=True, lines=2, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                       ellipsize=Pango.EllipsizeMode.END, max_width_chars=1, hexpand=True)
        self.callout.append(self.callout_label)
        self.callout_key = Gtk.Label()
        self.callout_key.add_css_class("ring-shortcut")
        self.callout.append(self.callout_key)
        self.labels.put(self.callout, 0, 0)
        self.instructions = Gtk.Label(width_request=480, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1)
        self.instructions.add_css_class("ring-hint")
        self.instructions.set_can_target(False)
        self.labels.put(self.instructions, 30, HEIGHT - 28)
        for widget in (self.callout, self.callout_label, self.callout_key, self.instructions):
            widget.get_style_context().add_provider(self.scale_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.set_child(self.surface)
        motion = Gtk.EventControllerMotion()
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
        click.connect("pressed", lambda gesture, *_: gesture.set_state(Gtk.EventSequenceState.CLAIMED))
        click.connect("released", self.pointer_click)
        self.surface.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.key_pressed)
        self.add_controller(keys)
        self.connect("close-request", lambda *_: self.dismiss() or True)
        self.connect("unmap", lambda *_: self.stop_animation())
        self.settings.connect("notify::gtk-enable-animations", self.motion_preference_changed)

    @staticmethod
    def make_button(radius):
        button = Gtk.Button(width_request=radius * 2, height_request=radius * 2)
        button.add_css_class("ring-action")
        icon = Gtk.Image(pixel_size=23)
        button.set_child(icon)
        return button, icon

    def groups(self):
        return self.config.get("ring_groups", {}) if self.config else {}

    def show_ring(self, config, held=False, preview=False):
        geometry = RingGeometry(config.get("ring_scale", 100))
        state = RingState(config.get("ring_motion_gain", .45), config.get("ring_deadzone", 24))
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
            icon.set_pixel_size(geometry.size(23))
        self.close_button.set_size_request(geometry.size(32), geometry.size(32))
        self.close_button.get_child().set_pixel_size(geometry.size(16))
        scale = geometry.scale
        self.scale_css.load_from_string(f"""
            .ring-callout {{ padding: {7 * scale}px {10 * scale}px; border-radius: {12 * scale}px; }}
            .ring-callout label {{ font-size: {13 * scale}px; }}
            .ring-shortcut {{ font-size: {11 * scale}px; }}
            .ring-hint {{ font-size: {12 * scale}px; padding: {6 * scale}px {12 * scale}px;
                         border-radius: {12 * scale}px; }}
        """)
        self.callout.set_spacing(geometry.size(8))
        self._move(self.close_button, CENTER_X - CENTER_RADIUS, CENTER_Y - CENTER_RADIUS)
        self.instructions.set_size_request(geometry.size(480), -1)
        self._move(self.instructions, 30, HEIGHT - 28)
        self.close_group()
        self.state.open(held)
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
        self.reveal.move(0, animate=False)
        self.reveal.move(1, animate=self.animations_enabled())
        self.apply_frame(monotonic())
        self.update_labels()
        self.present()
        self.canvas.grab_focus()
        self.animate()

    def hide_ring(self):
        self._adjusted_in_hold = False
        self.state.cancel()
        self.close_group()
        self.stop_animation()
        self.set_visible(False)
        Gdk.Display.get_default().flush()

    def dismiss(self):
        if self.state.visible:
            self.hide_ring()
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
            self.hide_ring()
            self.on_selected(self.config["ring"][chosen], self.preview)

    def release_button(self):
        if self.state.visible and self.state.held and self._adjusted_in_hold:
            self.state.held = False
            self._adjusted_in_hold = False
            self.update_labels()
            self.canvas.queue_draw()
            return
        index = self.state.selected
        if self.state.visible and self.state.held and index is not None and str(index) in self.groups():
            self.commit(index)
            return
        index = self.state.release()
        if index is not None:
            self.hide_ring()
            self.on_selected(self.config["ring"][index], self.preview)
        else:
            self.update_labels()
            self.canvas.queue_draw()

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
        if before != self.state.selected or self.state.held:
            self.canvas.queue_draw()

    def focus_action(self, index):
        if self.state.visible and not self.state.held:
            if self.group_slot is not None and index != self.group_slot:
                self.close_group()
            before = self.state.selected
            self.state.selected = index
            self.changed(before)

    def open_group(self, index, focus=False):
        children = self.groups().get(str(index))
        if not children or not self.state.visible:
            return
        if self.group_slot != index:
            if self.group_slot is not None:
                self.expanded_state(self.buttons[self.group_slot], False)
            self.group_slot, self.children, self.child_selected = index, list(children), None
            self.expanded_state(self.buttons[index], True)
            for i, button in enumerate(self.child_buttons):
                button.set_visible(i < len(children))
                if i < len(children):
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

    def close_group(self):
        if self.group_slot is not None:
            self.expanded_state(self.buttons[self.group_slot], False)
        self.group_slot = self.child_selected = None
        self.children = []
        self.child_positions = []
        self.group_reveal.move(0, animate=False)
        for button in getattr(self, "child_buttons", []):
            button.set_visible(False)

    def focus_child(self, index):
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
            self.hide_ring()
            self.on_selected(action_id, self.preview)

    def update_labels(self):
        for index, button in enumerate(self.buttons):
            self.selected_style(button, index == self.state.selected or index == self.group_slot)
        for index, button in enumerate(self.child_buttons):
            self.selected_style(button, index == self.child_selected)
        selected = self.state.selected
        child = self.child_selected
        self.callout.set_visible(selected is not None or child is not None)
        if child is not None and self.group_slot is not None:
            action_id = self.children[child]
            x, y = self.child_positions[child]
            position, width = (x + CHILD_RADIUS + 10, y - 18), 176
            key = str(child + 1)
        elif self.group_slot is not None:
            action_id = self.config["ring"][self.group_slot]
            position, width = (CENTER_X + 150, 4), 230
            key = "group"
        elif selected is not None:
            action_id = self.config["ring"][selected]
            dx, dy = button_center(selected)
            x, y = CENTER_X + dx, CENTER_Y + dy
            width = 116 if dx < -40 else 170
            if abs(dx) < 40:
                position = (x - width / 2, y - 86 if dy < 0 else y + 40)
            elif dx < 0:
                position = (max(8, x - BUTTON_RADIUS - width - 10), y - 20)
            else:
                position = (x + BUTTON_RADIUS + 10, y - 20)
            key = str(selected + 1)
        else:
            action_id = None
        if action_id is not None:
            text = self.config["actions"][action_id]["label"]
            if text != self.callout_label.get_text():
                self.label_reveal.move(0, animate=False)
                self.label_reveal.move(1, animate=self.animations_enabled())
                self.animate()
            self.callout_label.set_text(text)
            self.callout_label.set_lines(1 if self.group_slot is not None and child is None else 2)
            self.callout_key.set_text(key)
            self.callout.set_size_request(self.geometry.size(width), -1)
            self._move(self.callout, *position)
            self.callout.set_opacity(self.label_reveal.value())
        if self.group_slot is not None:
            hint = f"{'Preview · ' if self.preview else ''}Choose a group action · 1–{len(self.children)} · Esc to go back"
        else:
            hint = ("Preview · " if self.preview else "") + ("Hold · move · release" if self.state.held else "Choose an action · 1–8 · Esc to close")
        if action_id and self.config["actions"][action_id].get("adjustment") and not (
                self.group_slot is not None and child is None):
            hint += " · Scroll to adjust"
        self.instructions.set_text(hint)
        self.instructions.set_tooltip_text(hint)
        footer_height = self.instructions.measure(Gtk.Orientation.VERTICAL, self.geometry.size(480)).natural
        self.labels.move(self.instructions, self.geometry.size(30), self.geometry.size(HEIGHT) - footer_height - 1)

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

    def animate(self):
        if self.state.visible and self.animations_enabled() and not self.tick_id:
            self.tick_id = self.canvas.add_tick_callback(self.tick)

    def apply_frame(self, now):
        reveal = self.reveal.value(now)
        self.spread = 0.78 + 0.22 * reveal
        self.surface.set_opacity(reveal)
        for index, button in enumerate(self.buttons):
            dx, dy = button_center(index)
            self._move(button, CENTER_X + dx * self.spread - BUTTON_RADIUS,
                             CENTER_Y + dy * self.spread - BUTTON_RADIUS)
        self.callout.set_opacity(self.label_reveal.value(now))
        if self.group_slot is not None:
            progress = self.group_reveal.value(now)
            dx, dy = button_center(self.group_slot)
            origin_x, origin_y = CENTER_X + dx, CENTER_Y + dy
            self.child_positions = []
            for index in range(len(self.children)):
                offset = index - (len(self.children) - 1) / 2
                x, y = CENTER_X + 214 - abs(offset) * 7, CENTER_Y + offset * 62
                px, py = origin_x + (x - origin_x) * progress, origin_y + (y - origin_y) * progress
                self.child_positions.append((px, py))
                self._move(self.child_buttons[index], px - CHILD_RADIUS, py - CHILD_RADIUS)
                self.child_buttons[index].set_opacity(progress)
            if self.child_selected is not None:
                x, y = self.child_positions[self.child_selected]
                self._move(self.callout, x + CHILD_RADIUS + 10, y - 18)

    def tick(self, _widget, _clock):
        now = monotonic()
        self.apply_frame(now)
        self.canvas.queue_draw()
        active = self.state.visible and any(t.active(now) for t in (self.reveal, self.group_reveal, self.label_reveal))
        if not active:
            self.tick_id = 0
        return active

    def stop_animation(self):
        if self.tick_id:
            self.canvas.remove_tick_callback(self.tick_id)
            self.tick_id = 0

    def motion_preference_changed(self, *_):
        if not self.animations_enabled():
            self.stop_animation()
            for transition in (self.reveal, self.group_reveal, self.label_reveal):
                transition.move(transition.target, animate=False)
            self.apply_frame(monotonic())
            self.canvas.queue_draw()

    def child_at(self, x, y):
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        for index, (cx, cy) in enumerate(self.child_positions):
            if math.hypot(x - cx, y - cy) > CHILD_RADIUS:
                continue
            # Revealing children start on their parent. A trigger click must
            # never execute an overlapping, still emerging child action.
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
        if not self.state.visible or self.state.held:
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
            self.close_group()
        elif self.group_slot is not None:
            self.state.selected = self.group_slot
            self.child_selected = None
        self.changed(before)
        self.update_labels()

    def pointer_leave(self, *_):
        if not self.state.held and self.group_slot is None:
            before = self.state.selected
            self.state.selected = None
            self.changed(before)

    def pointer_click(self, _gesture, _count, x, y):
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
            self.close_group()
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

    def draw(self, _area, cr, width, height):
        if self.state.visible and self.state.held:
            cr.scale(self.geometry.scale, self.geometry.scale)
            cr.arc(CENTER_X + self.state.x, CENTER_Y + self.state.y, 3, 0, math.tau)
            cr.set_source_rgb(0.2, 0.52, 0.92)
            cr.fill()

    def identify_popup(self, *_args):
        if self.overlay_supported:
            return  # Layer surfaces have no Niri window ID.
        surface = self.get_surface()
        if GdkWayland and isinstance(surface, GdkWayland.WaylandToplevel):
            surface.set_application_id(self.context_app_id)

    def _move(self, widget, x, y):
        self.labels.move(widget, *self.geometry.point(x, y))

    @staticmethod
    def adjustment_hint(action):
        return ". Scroll to adjust" if action.get("adjustment") else ""

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
        x, y = self.geometry.logical(x, y)
        child = self.child_at(x, y) if self.group_slot is not None else None
        if child is not None:
            action_id = self.children[child]
        else:
            if self.group_slot is not None and any(math.hypot(x - cx, y - cy) <= CHILD_RADIUS
                                                   for cx, cy in self.child_positions):
                return False
            index = button_at(x - CENTER_X, y - CENTER_Y, spread=self.spread)
            if index is None or str(index) in self.groups():
                return False
            action_id = self.config["ring"][index]
        if not self.config["actions"][action_id].get("adjustment"):
            return False
        direction = "increase" if (dy < 0 if dy else dx > 0) else "decrease"
        accepted = bool(self.on_adjusted(action_id, direction, self.preview))
        if accepted and self.state.held:
            self._adjusted_in_hold = True
        return accepted

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
