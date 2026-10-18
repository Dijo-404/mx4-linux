"""Static, accessible ring assignment preview for the native action editor."""

from .gtk_support import Gdk, Gtk
from gi.repository import Pango
from .ring_icons import action_icon
from .ring_model import BUTTON_RADIUS, CENTER_RADIUS, button_center


class RingPreview(Gtk.Fixed):
    """Show the overlay's eight positions without executing assigned actions."""

    def __init__(self, config, on_selected=None):
        super().__init__(width_request=640, height_request=390, halign=Gtk.Align.CENTER)
        self.on_selected = on_selected
        self.selected = None
        self.add_css_class("mx4-ring-preview")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Actions Ring assignment preview"])
        css = Gtk.CssProvider()
        css.load_from_string("""
            .mx4-ring-preview button.preview-ring-action {
                background: #08090b; color: white; border: none;
                border-radius: 9999px; padding: 0; box-shadow: none;
                min-width: 52px; min-height: 52px;
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
        label_positions = [(250, 0), (446, 88), (476, 172), (446, 286),
                           (250, 346), (54, 286), (24, 172), (54, 88)]
        for index, (label_x, label_y) in enumerate(label_positions):
            x, y = button_center(index)
            button = Gtk.Button(width_request=BUTTON_RADIUS * 2, height_request=BUTTON_RADIUS * 2)
            button.add_css_class("preview-ring-action")
            icon = Gtk.Image(pixel_size=24)
            button.set_child(icon)
            button.connect("clicked", self._clicked, index)
            self.put(button, 320 + x - BUTTON_RADIUS, 190 + y - BUTTON_RADIUS)
            label = Gtk.Label(width_request=140, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                              max_width_chars=14, lines=2, ellipsize=Pango.EllipsizeMode.END,
                              justify=Gtk.Justification.CENTER)
            label.add_css_class("preview-ring-label")
            label.set_can_target(False)
            self.put(label, label_x, label_y)
            self.buttons.append(button)
            self.icons.append(icon)
            self.labels.append(label)
        center = Gtk.Label(label="×", width_request=CENTER_RADIUS * 2, height_request=CENTER_RADIUS * 2)
        center.add_css_class("preview-ring-center")
        center.set_can_target(False)
        center.update_state([Gtk.AccessibleState.HIDDEN], [True])
        self.put(center, 320 - CENTER_RADIUS, 190 - CENTER_RADIUS)
        self.update_config(config)

    def update_config(self, config):
        self.config = config
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
