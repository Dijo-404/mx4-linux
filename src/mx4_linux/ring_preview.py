"""Static, accessible ring assignment preview for the native action editor."""

from .gtk_support import Gdk, Gtk
from gi.repository import Graphene, Gsk, Pango
from .ring_icons import action_icon
from .ring_model import BUTTON_RADIUS, CENTER_RADIUS, RingGeometry, button_center


class RingPreview(Gtk.Box):
    """Show the overlay's eight positions without executing assigned actions."""

    def __init__(self, config, on_selected=None):
        super().__init__(height_request=390, hexpand=True)
        self.set_layout_manager(None)
        self._positions = {}
        self.on_selected = on_selected
        self.selected = None
        self.add_css_class("mx4-ring-preview")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Actions Ring assignment preview"])
        css = Gtk.CssProvider()
        css.load_from_string("""
            .mx4-ring-preview button.preview-ring-action {
                background: #08090b; color: white; border: none;
                border-radius: 9999px; padding: 0; box-shadow: none;
                min-width: 0; min-height: 0;
            }
            .mx4-ring-preview button.preview-ring-action:hover {
                background: #41434a;
            }
            .mx4-ring-preview button.preview-ring-action.selected {
                background: #26cbbf; color: #12221f;
            }
            .mx4-ring-preview button.preview-ring-action:focus-visible {
                outline: 2px solid @accent_color; outline-offset: 3px;
            }
            .mx4-ring-preview label.preview-ring-label {
                background: @card_bg_color; color: @card_fg_color; border: 1px solid @borders;
                border-radius: 6px; padding: 6px 8px;
            }
            .mx4-ring-preview label.preview-ring-label.selected {
                border-color: #26cbbf;
            }
            .mx4-ring-preview label.preview-ring-center {
                background: alpha(@window_fg_color, 0.08);
                color: @window_fg_color;
                border-radius: 9999px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
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
            label = Gtk.Label(width_request=140, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
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
        self.selected = index
        for position, (button, label) in enumerate(zip(self.buttons, self.labels)):
            for widget in (button, label):
                if position == index:
                    widget.add_css_class("selected")
                else:
                    widget.remove_css_class("selected")

    def _clicked(self, _button, index):
        self.select(index)
        if self.on_selected:
            self.on_selected(index)

    def layout_preview(self, width):
        # Fit the entire scaled scene; keep text readable rather than scaling fonts.
        saved_scale = self.geometry.scale
        fit = min(1, max(1, width) / (640 * saved_scale))
        scale = saved_scale * fit
        cx, cy = width / 2, 190 * scale
        self._preview_height = round(390 * scale)
        label_positions = [(0, -190), (196, -102), (226, -18), (196, 96),
                           (0, 156), (-196, 96), (-226, -18), (-196, -102)]
        for index, (button, icon, label) in enumerate(zip(self.buttons, self.icons, self.labels)):
            x, y = button_center(index)
            diameter = round(BUTTON_RADIUS * 2 * scale)
            button.set_size_request(diameter, diameter)
            icon.set_pixel_size(round(24 * scale))
            self.move(button, cx + x * scale - diameter / 2, cy + y * scale - diameter / 2)
            label_width = round(140 * scale)
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
