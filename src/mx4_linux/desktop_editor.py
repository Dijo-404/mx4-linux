"""Native editor for the local Actions Ring and Smart Actions configuration.

Editing never executes an action. A private draft holds incomplete JSON until
the user saves, and the store validates the complete configuration before it is
accepted. Selecting another routine preserves every field of the current one.
"""

from copy import deepcopy
import json
from pathlib import Path
import shlex
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from .actions import SHOW_RING, set_button_assignment, ACTION_ID, MAX_ACTIONS, MAX_GROUP_ACTIONS, MAX_STEPS, validate_config
from .applications import installed_applications
from .ring_preview import RingPreview


SLOT_NAMES = ("Top", "Upper right", "Right", "Lower right", "Bottom",
              "Lower left", "Left", "Upper left")
BUTTON_NAMES = {82: "Middle button", 83: "Back button", 86: "Forward button",
                195: "Gesture button", 196: "Wheel mode button", 416: "Haptic panel"}
TEMPLATES = (
    ("Keyboard shortcut", "shortcut", "Keyboard shortcut", [{"type": "shortcut", "keys": ["CTRL", "C"]}]),
    ("Open application", "launch", "Open application", []),
    ("Insert text", "text", "Insert text", [{"type": "text", "text": "Hello from MX4 Linux"}]),
    ("Multiple steps", "routine", "Copy and paste", [
        {"type": "shortcut", "keys": ["CTRL", "C"]},
        {"type": "delay", "milliseconds": 150},
        {"type": "shortcut", "keys": ["CTRL", "V"]},
    ]),
)
STEP_TYPES = (
    ("Keyboard shortcut", "shortcut", {"type": "shortcut", "keys": ["CTRL", "C"]}),
    ("Run command", "command", {"type": "command", "argv": ["xdg-open", str(Path.home())]}),
    ("Insert text", "text", {"type": "text", "text": "Hello"}),
    ("Wait", "delay", {"type": "delay", "milliseconds": 150}),
    ("Niri desktop action", "niri", {"type": "niri", "action": "toggle-overview", "args": []}),
    ("Open application", "application", None),
)
STEP_NAMES = {kind: label for label, kind, _default in STEP_TYPES}


def _step_values(step):
    """Represent valid steps as editable strings without changing their data."""
    kind = step["type"]
    if kind == "shortcut":
        value, extra = " + ".join(step["keys"]), ""
    elif kind == "command":
        value, extra = shlex.join(step["argv"]), ""
    elif kind == "application":
        value, extra = step["desktop_id"], ""
    elif kind == "text":
        value, extra = step["text"], ""
    elif kind == "delay":
        value, extra = str(step["milliseconds"]), ""
    else:
        value, extra = step["action"], shlex.join(step.get("args", []))
    return {"type": kind, "value": value, "extra": extra}


def _values_step(values):
    """Parse form values; command strings become literal argv, never a shell."""
    kind, value = values["type"], values["value"]
    if kind == "shortcut":
        return {"type": kind, "keys": [key.strip().upper() for key in value.split("+") if key.strip()]}
    if kind == "command":
        return {"type": kind, "argv": shlex.split(value)}
    if kind == "application":
        return {"type": kind, "desktop_id": value}
    if kind == "text":
        return {"type": kind, "text": value}
    if kind == "delay":
        return {"type": kind, "milliseconds": int(value)}
    return {"type": kind, "action": value, "args": shlex.split(values["extra"])}


class _DraftError(ValueError):
    def __init__(self, action_id, message):
        super().__init__(message)
        self.action_id = action_id


def _label(text, *, dim=False, alert=False):
    options = {"accessible_role": Gtk.AccessibleRole.ALERT} if alert else {}
    widget = Gtk.Label(label=text, xalign=0, wrap=True, **options)
    if dim:
        widget.add_css_class("dim-label")
    return widget


def _page():
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                  margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
    box.add_css_class("mx4-editor-page")
    clamp = Adw.Clamp(maximum_size=760, tightening_threshold=560)
    clamp.set_child(box)
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    scroll.set_child(clamp)
    return box, scroll


def _action_dropdown(*, accessible_name, **kwargs):
    from .reference_shell import bounded_dropdown
    choice = bounded_dropdown([], accessible_name=accessible_name)
    for name, value in kwargs.items():
        choice.set_property(name.replace("_", "-"), value)
    return choice


def _intro(title, description):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class("mx4-editor-intro")
    heading = _label(title)
    heading.add_css_class("title-2")
    box.append(heading)
    box.append(_label(description, dim=True))
    return box


class ApplicationPicker(Gtk.Box):
    """An inline, searchable list: choosing a launcher only changes the draft."""

    def __init__(self, on_selected, on_cancel):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("mx4-application-picker")
        self.on_selected = on_selected
        self.applications = installed_applications()
        header = Gtk.Box(spacing=8)
        self.search = Gtk.SearchEntry(hexpand=True, placeholder_text="Search installed applications")
        self.search.update_property([Gtk.AccessibleProperty.LABEL], ["Search installed applications"])
        self.search.connect("search-changed", self._search_changed)
        self.search.connect("stop-search", lambda *_: on_cancel())
        header.append(self.search)
        cancel = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Cancel application selection")
        cancel.update_property([Gtk.AccessibleProperty.LABEL], ["Cancel application selection"])
        cancel.connect("clicked", lambda *_: on_cancel())
        header.append(cancel)
        self.append(header)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.list.connect("row-activated", lambda _list, row: self.on_selected(row.application))
        self.rows = []
        for application in self.applications:
            row = Adw.ActionRow(title=application["name"], subtitle=application["desktop_id"],
                                use_markup=False, activatable=True, title_lines=1, subtitle_lines=1)
            row.application = application
            row.search_text = " ".join((application["name"], application["desktop_id"], application["description"])).casefold()
            icon = (Gtk.Image.new_from_gicon(application["icon"]) if application["icon"]
                    else Gtk.Image.new_from_icon_name("application-x-executable-symbolic"))
            icon.set_pixel_size(24)
            row.add_prefix(icon)
            row.add_suffix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
            self.list.append(row)
            self.rows.append(row)
        self.list.set_filter_func(lambda row: self.search.get_text().casefold().strip() in row.search_text)
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         min_content_height=180, max_content_height=270,
                                         propagate_natural_height=True, child=self.list)
        self.append(self.scroll)
        self.empty = _label("No installed applications found. You can add a Run command step instead.", dim=True)
        self.append(self.empty)
        self._search_changed()

    def _search_changed(self, *_):
        self.list.invalidate_filter()
        query = self.search.get_text().casefold().strip()
        any_match = any(query in row.search_text for row in self.rows)
        self.scroll.set_visible(any_match)
        self.empty.set_visible(not any_match)
        self.empty.set_text("No applications match your search." if self.applications
                            else "No installed applications found. You can add a Run command step instead.")


class DesktopEditor(Adw.Window):
    def __init__(self, parent, store, on_saved):
        super().__init__(title="Actions Ring and Smart Actions", transient_for=parent,
                         modal=True, default_width=840, default_height=760)
        self.add_css_class("mx4-shell")
        self.add_css_class("mx4-editor")
        self.style = Adw.StyleManager.get_default()
        self.settings = Gtk.Settings.get_default()
        self.style_handler = self.motion_handler = 0
        self.compact = False
        self.connect("map", self._connect_visual_preferences)
        self.connect("unmap", self._disconnect_visual_preferences)
        self.connect("destroy", self._disconnect_visual_preferences)
        self._sync_appearance()
        self.store = store
        self.on_saved = on_saved
        self.config = deepcopy(store.read())
        self.drafts = {
            action_id: {"id": action_id, "label": action["label"],
                        "app_id": action.get("app_id", ""),
                        "steps_text": json.dumps(action["steps"], indent=2, ensure_ascii=False)}
            for action_id, action in self.config["actions"].items()
        }
        self.selected_id = next(iter(self.drafts))
        self.dirty = False
        self.saving = False
        self.loading = True
        self.syncing_steps = False
        self.allow_close = False
        self.close_dialog = None
        self.ring_choices = []
        self.group_choices = []
        self.binding_choices = []
        self.page_boxes = []
        self.toolbar_rows = []
        self.choice_rows = []
        self.application_picker = None
        self.integration = getattr(parent, "desktop", None)
        self.connect("close-request", self._close_requested)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="Actions Ring and Smart Actions",
                                           subtitle="Customize your mouse workflow")
        header.set_title_widget(self.window_title)
        self.save_button = Gtk.Button(label="Save changes", sensitive=False)
        self.save_button.set_tooltip_text("Save changes · Ctrl+S")
        self.save_button.update_property([Gtk.AccessibleProperty.LABEL], ["Save changes"])
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", self._save)
        header.pack_end(self.save_button)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._sync_motion()
        switcher = Gtk.StackSwitcher(stack=self.stack, halign=Gtk.Align.CENTER,
                                     margin_top=10, margin_bottom=10)
        content.append(switcher)
        self._build_ring()
        self._build_actions()
        content.append(self.stack)

        self.error = _label("", alert=True)
        self.error.add_css_class("error")
        self.error.set_selectable(True)
        self.error.set_focusable(True)
        self.error.set_margin_start(24)
        self.error.set_margin_end(24)
        self.error.set_margin_top(10)
        self.error.set_margin_bottom(14)
        self.error.set_visible(False)
        content.append(self.error)
        toolbar.set_content(content)
        responsive = Adw.BreakpointBin(child=toolbar, width_request=320, height_request=300)
        compact = Adw.Breakpoint(condition=Adw.BreakpointCondition.parse("max-width: 620px"))
        compact.connect("apply", lambda *_: self._set_compact(True))
        compact.connect("unapply", lambda *_: self._set_compact(False))
        responsive.add_breakpoint(compact)
        self.set_content(responsive)
        keys = Gtk.EventControllerKey()
        # Let native popovers consume Escape before dismissing the editor.
        keys.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)
        self._refresh_choices()
        self._load_selected()
        self.loading = False

    def _sync_appearance(self, *_):
        if self.style.get_dark():
            self.remove_css_class("mx4-light")
        else:
            self.add_css_class("mx4-light")

    def _sync_motion(self, *_):
        enabled = self.settings.get_property("gtk-enable-animations")
        self.stack.set_transition_duration(180 if enabled else 0)
        if enabled:
            self.remove_css_class("reduced-motion")
        else:
            self.add_css_class("reduced-motion")

    def _connect_visual_preferences(self, *_):
        self._sync_appearance()
        self._sync_motion()
        if not self.style_handler:
            self.style_handler = self.style.connect("notify::dark", self._sync_appearance)
        if not self.motion_handler:
            self.motion_handler = self.settings.connect("notify::gtk-enable-animations", self._sync_motion)

    def _disconnect_visual_preferences(self, *_):
        if self.style_handler:
            self.style.disconnect(self.style_handler)
            self.style_handler = 0
        if self.motion_handler:
            self.settings.disconnect(self.motion_handler)
            self.motion_handler = 0

    def _set_compact(self, compact):
        self.compact = compact
        self.window_title.set_title("Actions & routines" if compact else "Actions Ring and Smart Actions")
        self.save_button.set_label("Saving…" if self.saving else "Save" if compact else "Save changes")
        for page in self.page_boxes:
            page.set_margin_start(16 if compact else 24)
            page.set_margin_end(16 if compact else 24)
        for row in self.toolbar_rows:
            row.set_orientation(Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL)
        for body, copy, choice in self.choice_rows:
            body.set_orientation(Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL)
            copy.set_hexpand(not compact)
            choice.set_hexpand(compact)
            choice.set_size_request(-1 if compact else 230, -1)

    def _choice_row(self, title, subtitle, choice):
        from .reference_shell import choice_row
        row = choice_row(title, subtitle, choice)
        body = row.get_child()
        copy = body.get_first_child()
        body.set_orientation(Gtk.Orientation.HORIZONTAL)
        copy.set_hexpand(True)
        copy.set_valign(Gtk.Align.CENTER)
        choice.set_hexpand(False)
        choice.set_size_request(230, -1)
        self.choice_rows.append((body, copy, choice))
        return row

    def _key_pressed(self, _controller, keyval, _keycode, state):
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        if modifiers == Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_s, Gdk.KEY_S):
            if self.dirty and not self.saving:
                self._save()
            return True
        if keyval == Gdk.KEY_Escape and not modifiers:
            if self.application_picker is not None:
                box, previous_focus = self.application_picker
                if box.get_visible() and box.get_root() is self:
                    box.set_visible(False)
                    if previous_focus is not None and previous_focus.get_root() is self:
                        previous_focus.grab_focus()
                    return True
            if not self.saving:
                self.close()
            return True
        return False

    def _build_ring(self):
        page, scroll = _page()
        self.page_boxes.append(page)
        page.append(_intro("Your shortcuts, one gesture away",
                           "Hold the haptic panel, move toward an action, then release. "
                           "A short click opens the ring for point-and-click selection. "
                           "Choose what each position does below."))
        if self.integration is not None:
            integration_group = Adw.PreferencesGroup()
            self.integration_status = Adw.ActionRow(title="Mouse integration", use_markup=False)
            self.integration_status.set_subtitle_lines(0)
            self.integration_toggle = Gtk.Button(valign=Gtk.Align.CENTER)
            self.integration_toggle.connect("clicked", self._toggle_integration)
            self.integration_status.add_suffix(self.integration_toggle)
            integration_group.add(self.integration_status)
            page.append(integration_group)
            self.update_integration_status()
        application_controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_application_button = Gtk.Button(label="Add application", halign=Gtk.Align.START,
                                                 valign=Gtk.Align.CENTER)
        self.add_application_button.connect("clicked", self._open_ring_application_picker)
        application_controls.append(self.add_application_button)
        self.ring_application_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, visible=False)
        application_controls.append(self.ring_application_box)
        self.application_notice = _label("", dim=True)
        self.application_notice.set_visible(False)
        application_controls.append(self.application_notice)
        page.append(application_controls)
        self.ring_preview = RingPreview(self.config, on_selected=self._preview_selected)
        page.append(self.ring_preview)
        group = Adw.PreferencesGroup(title="Ring positions", description="Eight actions, clockwise from the top. Edit their steps in Smart Actions.")
        for index, name in enumerate(SLOT_NAMES):
            choice = _action_dropdown(accessible_name=f"Action at {name.lower()} ring position", valign=Gtk.Align.CENTER)
            choice.set_tooltip_text(f"Action at ring position {index + 1}: {name}")
            choice.connect("notify::selected", self._ring_changed, index)
            self.ring_choices.append(choice)
            group.add(self._choice_row(name, f"Position {index + 1} · key {index + 1} in the ring", choice))
        page.append(group)

        groups = Adw.PreferencesGroup(description="Turn a ring position into a group of up to six routines. "
                                      "Choose them in display order; leave every entry empty to run the position's action directly.")
        for index, name in enumerate(SLOT_NAMES):
            row = Adw.ExpanderRow(title=name, use_markup=False)
            choices = []
            for child in range(MAX_GROUP_ACTIONS):
                choice = _action_dropdown(accessible_name=f"Group action {child + 1} at {name.lower()} ring position",
                                          valign=Gtk.Align.CENTER)
                choice.connect("notify::selected", self._group_changed, index)
                row.add_row(self._choice_row(f"Action {child + 1}", "", choice))
                choices.append(choice)
            self.group_choices.append((row, choices))
            groups.add(row)
        self.groups_expander = Gtk.Expander(label="Action groups (optional)")
        self.groups_expander.set_child(groups)
        page.append(self.groups_expander)

        behavior = Adw.PreferencesGroup(title="Default assignments", description="These are the default/root assignments. Edit application-specific buttons using the device page context selector.")
        ring_button = self.config["ring_button"]
        self.trigger_row = Adw.ActionRow(use_markup=False, title="Open Actions Ring",
                                        subtitle=BUTTON_NAMES.get(ring_button, "Manual only · use Open Actions Ring"))
        behavior.add(self.trigger_row)
        feedback = Adw.ActionRow(use_markup=False, title="Haptic feedback",
                                 subtitle="Feel a pulse when the ring opens.")
        self.haptics = Gtk.Switch(active=self.config["haptic_feedback"], valign=Gtk.Align.CENTER)
        self.haptics.update_property([Gtk.AccessibleProperty.LABEL], ["Haptic feedback when the ring opens"])
        self.haptics.connect("notify::active", self._haptics_changed)
        feedback.add_suffix(self.haptics)
        feedback.set_activatable_widget(self.haptics)
        behavior.add(feedback)
        page.append(behavior)

        bindings = Adw.PreferencesGroup(
                                        description="A shortcut replaces that button's normal function while desktop controls are enabled.")
        for cid, name in BUTTON_NAMES.items():
            choice = _action_dropdown(accessible_name=f"Smart Action for {name.lower()}", valign=Gtk.Align.CENTER)
            choice.set_tooltip_text(f"Smart Action for {name.lower()}")
            choice.connect("notify::selected", self._binding_changed, cid)
            self.binding_choices.append((cid, choice))
            bindings.add(self._choice_row(name, "", choice))
        shortcuts = Gtk.Expander(label="Additional button shortcuts")
        self.shortcuts_expander = shortcuts
        shortcuts.set_child(bindings)
        page.append(shortcuts)
        self.stack.add_titled(scroll, "ring", "Actions Ring")

    def _build_actions(self):
        page, scroll = _page()
        self.page_boxes.append(page)
        page.append(_intro("Make a shortcut your own",
                           "Choose a routine, name it, then add the steps you need. "
                           "Assign it to a ring position or mouse button in Actions Ring. "
                           "Editing and saving never run a routine."))
        chooser = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        chooser_title = _label("Routine")
        chooser_title.add_css_class("heading")
        chooser.append(chooser_title)
        chooser_row = Gtk.Box(spacing=12)
        self.action_choice = _action_dropdown(accessible_name="Routine to edit", hexpand=True)
        self.action_choice.set_tooltip_text("Select a routine to edit")
        self.action_choice.connect("notify::selected", self._action_selected)
        chooser_row.append(self.action_choice)
        self.delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete selected routine",
                                 valign=Gtk.Align.CENTER)
        self.delete.update_property([Gtk.AccessibleProperty.LABEL], ["Delete selected routine"])
        self.delete.add_css_class("destructive-action")
        self.delete.connect("clicked", self._delete_selected)
        chooser_row.append(self.delete)
        chooser.append(chooser_row)

        add_row = Gtk.Box(spacing=12)
        self.template_choice = _action_dropdown(accessible_name="New routine template")
        self.template_choice.set_model(Gtk.StringList.new([item[0] for item in TEMPLATES]))
        self.template_choice.set_tooltip_text("Starting point for a new routine")
        self.template_choice.set_hexpand(True)
        add_row.append(self.template_choice)
        self.add_routine_button = Gtk.Button(label="New routine", valign=Gtk.Align.CENTER)
        self.add_routine_button.connect("clicked", self._add_routine)
        add_row.append(self.add_routine_button)
        chooser.append(add_row)
        self.toolbar_rows.append(add_row)
        self.action_application_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, visible=False)
        chooser.append(self.action_application_box)
        page.append(chooser)

        fields = Adw.PreferencesGroup(title="Routine details")
        self.name_entry = Adw.EntryRow(title="Name")
        self.name_entry.set_max_length(80)
        self.id_entry = Adw.EntryRow(title="Action ID")
        self.id_entry.set_max_length(64)
        self.app_entry = Adw.EntryRow(title="Only in this application (optional)")
        self.app_entry.set_max_length(256)
        for widget in (self.name_entry, self.id_entry, self.app_entry):
            widget.connect("changed", self._draft_changed)
        fields.add(self.name_entry)
        fields.add(self.app_entry)
        page.append(fields)
        self.details_error = _label("", alert=True)
        self.details_error.add_css_class("error")
        self.details_error.set_visible(False)
        page.append(self.details_error)
        page.append(_label("Leave the application field empty to run anywhere. "
                           "To limit a routine, enter the exact focused Niri app ID.", dim=True))

        steps_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.steps_group = Adw.PreferencesGroup(title="Steps", description="Run from top to bottom. Expand a step to edit it.")
        steps_section.append(self.steps_group)
        self.step_rows = []
        self.step_error = _label("", alert=True)
        self.step_error.add_css_class("error")
        self.step_error.set_visible(False)
        steps_section.append(self.step_error)

        step_add = Gtk.Box(spacing=12)
        self.step_choice = _action_dropdown(accessible_name="New step type")
        self.step_choice.set_model(Gtk.StringList.new([item[0] for item in STEP_TYPES]))
        self.step_choice.set_hexpand(True)
        self.step_choice.set_tooltip_text("Type of step to add")
        step_add.append(self.step_choice)
        self.add_step_button = Gtk.Button(label="Add step", valign=Gtk.Align.CENTER)
        self.add_step_button.connect("clicked", self._add_step)
        step_add.append(self.add_step_button)
        steps_section.append(step_add)
        self.toolbar_rows.append(step_add)
        self.step_add_box = step_add
        self.step_application_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, visible=False)
        steps_section.append(self.step_application_box)
        page.append(steps_section)

        steps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title = _label("Steps (JSON)")
        title.add_css_class("heading")
        steps_box.append(title)
        self.steps_view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.NONE,
                                       left_margin=12, right_margin=12,
                                       top_margin=12, bottom_margin=12)
        self.steps_view.set_accepts_tab(False)
        self.steps_view.update_property([Gtk.AccessibleProperty.LABEL], ["Routine steps JSON"])
        self.steps_buffer = self.steps_view.get_buffer()
        self.steps_buffer.connect("changed", self._draft_changed)
        code_scroll = Gtk.ScrolledWindow(min_content_height=230, vexpand=True)
        code_scroll.add_css_class("card")
        code_scroll.set_child(self.steps_view)
        steps_box.append(code_scroll)
        self.json_box = steps_box
        self.json_box.set_visible(False)
        page.append(steps_box)

        self.json_toggle = Gtk.CheckButton(label="Edit steps as JSON")
        self.json_toggle.connect("toggled", self._toggle_json)
        page.append(self.json_toggle)

        advanced = Gtk.Expander(label="Advanced routine settings")
        advanced_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12)
        id_group = Adw.PreferencesGroup()
        id_group.add(self.id_entry)
        advanced_box.append(id_group)
        advanced_box.append(_label("Action IDs use lowercase letters, numbers, hyphens and underscores. "
                                   "Ring positions and button assignments follow ID changes automatically.", dim=True))
        advanced.set_child(advanced_box)
        self.advanced = advanced
        page.append(advanced)

        help_expander = Gtk.Expander(label="Step examples and help")
        help_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                           margin_top=12, margin_bottom=8)
        help_box.append(_label("Use a JSON array containing one or more steps. Command arguments are literal strings. "
                               "Keyboard shortcuts and text need virtual keyboard access; niri commands and application conditions need niri. "
                               "Saving a routine does not execute it.", dim=True))
        examples = [
            {"type": "shortcut", "keys": ["CTRL", "SHIFT", "S"]},
            {"type": "command", "argv": ["kitty"]},
            {"type": "text", "text": "Hello"},
            {"type": "delay", "milliseconds": 500},
            {"type": "niri", "action": "focus-workspace-down", "args": []},
        ]
        code = Gtk.Label(label=json.dumps(examples, indent=2), xalign=0, selectable=True,
                         wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=1,
                         margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        code.add_css_class("monospace")
        help_box.append(code)
        help_box.append(_label("Use Linux key names such as CTRL, ALT, SHIFT, SUPER, ENTER and F1. "
                               "A routine can contain up to 64 steps. Each delay is at most 30 seconds, with 60 seconds total.", dim=True))
        help_expander.set_child(help_box)
        page.append(help_expander)
        self.stack.add_titled(scroll, "actions", "Smart Actions")

    def update_integration_status(self):
        if self.integration is None:
            return
        desktop = self.integration
        self.integration_status.set_subtitle(desktop.message)
        active = desktop.session.running
        self.integration_toggle.set_label("Disable" if active else "Enable")
        self.integration_toggle.set_sensitive(
            not desktop.window.demo and not desktop.session.stopping
            and (active or (not desktop.starting and not desktop.window.busy
                            and not desktop.window.pending_hardware
                            and bool(desktop.window.current and desktop.window.current.get("online")))))
        self.integration_toggle.set_tooltip_text("Use the saved ring and button assignments" if not active
                                                 else "Restore the mouse's previous button behavior")

    def _toggle_integration(self, *_):
        if self.integration.session.running:
            self.integration.stop()
        else:
            self.integration.start()
        self.update_integration_status()

    @staticmethod
    def _clear_box(box):
        while (child := box.get_first_child()) is not None:
            box.remove(child)

    def _show_application_picker(self, box, on_selected):
        previous_focus = self.get_focus()
        self._clear_box(box)
        try:
            picker = ApplicationPicker(on_selected, lambda: box.set_visible(False))
        except Exception as exc:
            self.error.set_text(f"Installed applications could not be read: {exc}")
            self.error.set_visible(True)
            return None
        box.append(picker)
        self.application_picker = (box, previous_focus)
        box.set_visible(True)
        picker.search.grab_focus()
        return picker

    def _open_ring_application_picker(self, *_):
        if self.ring_application_box.get_visible():
            self.ring_application_box.set_visible(False)
            return
        position = _action_dropdown(accessible_name="Ring position for application")
        position.set_model(Gtk.StringList.new(list(SLOT_NAMES)))
        position.set_selected(self.ring_preview.selected or 0)
        picker = self._show_application_picker(
            self.ring_application_box,
            lambda application: self._add_application(application, ring_index=position.get_selected()))
        if picker is None:
            return
        target = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        target.append(_label("Replace the action at"))
        target.append(position)
        self.ring_application_box.prepend(target)

    def _new_draft(self, prefix, label, steps):
        number = 1
        used_ids = set(self.drafts) | {draft["id"] for draft in self.drafts.values()}
        while f"{prefix}-{number}" in used_ids:
            number += 1
        action_id = f"{prefix}-{number}"
        # Shared assignment validators use this working document while the
        # editable draft can still contain incomplete form values.
        self.config["actions"][action_id] = {"label": label[:80], "steps": deepcopy(steps)}
        self.drafts[action_id] = {"id": action_id, "label": label[:80], "app_id": "",
                                  "steps_text": json.dumps(steps, indent=2)}
        self.selected_id = action_id
        self._mark_dirty()
        return action_id

    def _add_application(self, application, *, ring_index=None):
        if len(self.drafts) >= MAX_ACTIONS:
            return
        action_id = self._new_draft("application", application["name"],
                                    [{"type": "application", "desktop_id": application["desktop_id"]}])
        if ring_index is not None:
            self.config["ring"][ring_index] = action_id
            self.config.get("ring_groups", {}).pop(str(ring_index), None)
            self.ring_preview.select(ring_index)
            self.application_notice.set_text(f"{application['name']} assigned to {SLOT_NAMES[ring_index].lower()}. Save changes to use it.")
            self.application_notice.set_visible(True)
        self.ring_application_box.set_visible(False)
        self.action_application_box.set_visible(False)
        self._refresh_choices()
        self._load_selected()
        if ring_index is None:
            self.name_entry.grab_focus()

    def _choose_step_application(self, index=None, box=None):
        action_id = self.selected_id
        values = self.drafts[action_id]["form_values"]
        if index is None and len(values) >= MAX_STEPS:
            return
        target = values[index] if index is not None else None
        box = box if box is not None else self.step_application_box

        def chosen(application):
            # A picker must not replace a different draft if a routine changed.
            if (action_id != self.selected_id or self.drafts[action_id].get("form_values") is not values
                    or (target is not None and not any(value is target for value in values))):
                box.set_visible(False)
                return
            value = _step_values({"type": "application", "desktop_id": application["desktop_id"]})
            if target is None:
                if len(values) >= MAX_STEPS:
                    return
                values.append(value)
                selected = len(values) - 1
            else:
                selected = next(i for i, item in enumerate(values) if item is target)
                values[selected] = value
            box.set_visible(False)
            self._mark_dirty()
            self._render_steps(expanded=selected)
            self._validate_selected()

        self._show_application_picker(box, chosen)

    def _mark_dirty(self):
        self.dirty = True
        self.save_button.set_sensitive(True)
        self.window_title.set_subtitle("Unsaved changes")
        self.error.set_visible(False)

    def _show_step_error(self, message=""):
        self.step_error.set_text(message)
        self.step_error.set_visible(bool(message))

    def _form_steps(self, values):
        steps = []
        for index, value in enumerate(values, 1):
            try:
                steps.append(_values_step(value))
            except (ValueError, KeyError) as exc:
                raise ValueError(f"Step {index}: {exc}. Check the field and any quotation marks.") from exc
        return steps

    def _validate_selected(self):
        draft = self.drafts[self.selected_id]
        self.details_error.set_visible(False)
        for field in (self.name_entry, self.id_entry):
            field.remove_css_class("error")
            field.reset_state(Gtk.AccessibleState.INVALID)
        issue = None
        if not draft["label"]:
            issue = self.name_entry, "Give this routine a name."
        elif not ACTION_ID.fullmatch(draft["id"]):
            issue = self.id_entry, "Use 1–64 lowercase letters, numbers, hyphens or underscores for the action ID."
        elif any(key != self.selected_id and other["id"] == draft["id"] for key, other in self.drafts.items()):
            issue = self.id_entry, "This action ID already belongs to another routine. Choose a unique ID."
        if issue:
            field, message = issue
            field.add_css_class("error")
            field.update_state([Gtk.AccessibleState.INVALID], [int(Gtk.AccessibleInvalidState.TRUE)])
            self.details_error.set_text(message)
            self.details_error.set_visible(True)
            if field is self.id_entry:
                self.advanced.set_expanded(True)
            self._show_step_error()
            return False
        try:
            steps = (self._form_steps(draft["form_values"]) if draft.get("form_values") is not None
                     else json.loads(draft["steps_text"]))
            action = {**deepcopy(self.config["actions"].get(self.selected_id, {})), "label": draft["label"], "steps": steps}
            action.pop("app_id", None)
            if draft["app_id"]:
                action["app_id"] = draft["app_id"]
            document = deepcopy(self.config)
            document["actions"][draft["id"]] = action
            validate_config(document)
        except (RuntimeError, ValueError, RecursionError) as exc:
            self._show_step_error(str(exc).removeprefix("Invalid desktop configuration: "))
            return False
        self._show_step_error()
        return True

    def _render_steps(self, expanded=None):
        for row in self.step_rows:
            self.steps_group.remove(row)
        self.step_rows.clear()
        values = self.drafts[self.selected_id].get("form_values") or []
        self.steps_group.set_description("Run from top to bottom. Expand a step to edit it." if values
                                         else "Add your first step below.")
        self.add_step_button.set_sensitive(len(values) < MAX_STEPS)
        for index, value in enumerate(values):
            row = Adw.ExpanderRow(title=f"{index + 1}. {STEP_NAMES[value['type']]}",
                                  subtitle=self._step_summary(value), use_markup=False)
            row.set_title_lines(2)
            row.set_subtitle_lines(1)
            for icon, label, offset in (("user-trash-symbolic", "Remove step", 0),
                                        ("go-down-symbolic", "Move step down", 1),
                                        ("go-up-symbolic", "Move step up", -1)):
                button = Gtk.Button(icon_name=icon, tooltip_text=f"{label}: step {index + 1}", valign=Gtk.Align.CENTER)
                button.update_property([Gtk.AccessibleProperty.LABEL], [f"{label}: step {index + 1}"])
                button.add_css_class("flat")
                button.set_sensitive(offset == 0 or 0 <= index + offset < len(values))
                button.connect("clicked", self._move_step, index, offset)
                row.add_suffix(button)
            kind = value["type"]
            if kind == "text":
                text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                    top_margin=10, bottom_margin=10, left_margin=12, right_margin=12)
                text.set_tooltip_text("Text to insert, including line breaks")
                text.update_property([Gtk.AccessibleProperty.LABEL], [f"Text to insert in step {index + 1}"])
                text.get_buffer().set_text(value["value"])
                text.get_buffer().connect("changed", self._text_step_changed, index, row)
                scroll = Gtk.ScrolledWindow(min_content_height=100)
                scroll.set_child(text)
                row.add_row(scroll)
            elif kind == "application":
                application_row = Adw.ActionRow(title="Installed application", subtitle=value["value"],
                                                use_markup=False, subtitle_lines=0)
                row.add_row(application_row)
            else:
                title = {"shortcut": "Keys, separated by +", "command": "Application and arguments",
                         "delay": "Wait in milliseconds (0–30000)", "niri": "Niri action"}[kind]
                entry = Adw.EntryRow(title=title, text=value["value"])
                entry.connect("changed", self._step_changed, index, "value", row)
                row.add_row(entry)
                if kind == "niri":
                    arguments = Adw.EntryRow(title="Arguments (optional)", text=value["extra"])
                    arguments.connect("changed", self._step_changed, index, "extra", row)
                    row.add_row(arguments)
            if kind in ("application", "command"):
                application_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                                          margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)
                choose = Gtk.Button(label="Choose application", halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
                picker_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, visible=False)
                choose.connect("clicked", lambda _button, step=index, container=picker_box:
                               self._choose_step_application(step, container))
                application_box.append(choose)
                application_box.append(picker_box)
                row.add_row(application_box)
            hint = {"shortcut": "For example: CTRL + SHIFT + S. Use Linux key names such as ENTER, F1 and SPACE.",
                    "command": 'For example: kitty --title "My terminal". Quote arguments with spaces; commands run directly.',
                    "application": "Opens the installed application's desktop launcher when this routine runs.",
                    "text": "Insert this text into the focused application. Line breaks are preserved.",
                    "delay": "1000 milliseconds = 1 second. Keep waits short for responsive routines.",
                    "niri": "For example: toggle-overview or focus-workspace-down. Requires Niri."}[kind]
            row.add_row(Adw.ActionRow(title=hint, title_lines=0, use_markup=False))
            self.steps_group.add(row)
            self.step_rows.append(row)
            row.set_expanded(index == expanded)

    @staticmethod
    def _step_summary(value):
        text = value["value"].replace("\n", " ↵ ")
        if value["type"] == "delay":
            text += " ms"
        return text[:100] or "Needs a value"

    def _step_changed(self, entry, index, field, row):
        if self.loading:
            return
        self.drafts[self.selected_id]["form_values"][index][field] = entry.get_text()
        row.set_subtitle(self._step_summary(self.drafts[self.selected_id]["form_values"][index]))
        self._mark_dirty()
        self._validate_selected()

    def _text_step_changed(self, buffer, index, row):
        start, end = buffer.get_bounds()
        self.drafts[self.selected_id]["form_values"][index]["value"] = buffer.get_text(start, end, True)
        row.set_subtitle(self._step_summary(self.drafts[self.selected_id]["form_values"][index]))
        self._mark_dirty()
        self._validate_selected()

    def _move_step(self, _button, index, offset):
        values = self.drafts[self.selected_id]["form_values"]
        if offset:
            other = index + offset
            values[index], values[other] = values[other], values[index]
        else:
            del values[index]
        self._mark_dirty()
        self._render_steps(index + offset if values else None)
        self._validate_selected()

    def _add_step(self, *_):
        values = self.drafts[self.selected_id]["form_values"]
        if len(values) >= MAX_STEPS:
            return
        if STEP_TYPES[self.step_choice.get_selected()][1] == "application":
            self._choose_step_application()
            return
        values.append(_step_values(deepcopy(STEP_TYPES[self.step_choice.get_selected()][2])))
        self._mark_dirty()
        self._render_steps(len(values) - 1)
        self._validate_selected()

    def _toggle_json(self, *_):
        if self.loading:
            return
        self.step_application_box.set_visible(False)
        draft = self.drafts[self.selected_id]
        use_json = self.json_toggle.get_active()
        try:
            if use_json:
                steps = self._form_steps(draft["form_values"])
                draft["steps_text"] = json.dumps(steps, indent=2, ensure_ascii=False)
                draft["form_values"] = None
                self.syncing_steps = True
                self.steps_buffer.set_text(draft["steps_text"])
                self.syncing_steps = False
            else:
                steps = json.loads(draft["steps_text"])
                if not self._validate_selected():
                    raise ValueError("Fix the JSON steps before returning to the step builder.")
                draft["form_values"] = [_step_values(step) for step in steps]
                self._render_steps()
        except (ValueError, KeyError, TypeError, RecursionError) as exc:
            self.loading = True
            self.json_toggle.set_active(not use_json)
            self.loading = False
            self._show_step_error(str(exc))
            return
        draft["json_mode"] = use_json
        self.steps_group.set_visible(not use_json)
        self.step_add_box.set_visible(not use_json)
        self.json_box.set_visible(use_json)
        self._show_step_error()

    def _refresh_choices(self):
        previous = self.loading
        self.loading = True
        self.choice_ids = list(self.drafts)
        labels = [self.drafts[key]["label"] or "Untitled routine" for key in self.choice_ids]
        for index, choice in enumerate(self.ring_choices):
            choice.set_model(Gtk.StringList.new(labels))
            choice.set_selected(self.choice_ids.index(self.config["ring"][index]))
        for index, (row, choices) in enumerate(self.group_choices):
            children = self.config.get("ring_groups", {}).get(str(index), [])
            row.set_subtitle(f"{len(children)} actions · opens a submenu" if children else "No group · runs the assigned action")
            for child, choice in enumerate(choices):
                choice.set_model(Gtk.StringList.new(["No action"] + labels))
                choice.set_selected(self.choice_ids.index(children[child]) + 1 if child < len(children) else 0)
        for cid, choice in self.binding_choices:
            values = ["Normal button function", "Show Actions Ring"] + labels
            model = choice.get_model()
            if model is None or values != [model.get_string(i) for i in range(model.get_n_items())]:
                choice.set_model(Gtk.StringList.new(values))
            binding = self.config["button_bindings"].get(str(cid))
            choice.set_selected(1 if cid == self.config["ring_button"] else self.choice_ids.index(binding) + 2 if binding else 0)
        self.action_choice.set_model(Gtk.StringList.new(labels))
        self.action_choice.set_selected(self.choice_ids.index(self.selected_id))
        self.delete.set_sensitive(len(self.drafts) > 1)
        self.add_routine_button.set_sensitive(len(self.drafts) < MAX_ACTIONS)
        self.add_application_button.set_sensitive(len(self.drafts) < MAX_ACTIONS)
        self.loading = previous
        self._refresh_ring_preview()

    def _refresh_labels(self):
        previous = self.loading
        self.loading = True
        index = self.choice_ids.index(self.selected_id)
        label = self.drafts[self.selected_id]["label"] or "Untitled routine"
        for choice in self.ring_choices + [self.action_choice]:
            choice.get_model().splice(index, 1, [label])
        for _cid, choice in self.binding_choices:
            choice.get_model().splice(index + 2, 1, [label])
        for _row, choices in self.group_choices:
            for choice in choices:
                choice.get_model().splice(index + 1, 1, [label])
        self.loading = previous
        self._refresh_ring_preview()

    def _refresh_ring_preview(self):
        config = deepcopy(self.config)
        config["actions"] = {key: {**self.config["actions"].get(key, {}), "label": draft["label"]}
                             for key, draft in self.drafts.items()}
        self.ring_preview.update_config(config)

    def _preview_selected(self, index):
        if not self.loading:
            self.ring_choices[index].grab_focus()

    def _ring_changed(self, choice, _param, index):
        if not self.loading and choice.get_selected() < len(self.choice_ids):
            self.config["ring"][index] = self.choice_ids[choice.get_selected()]
            self._mark_dirty()
            self._refresh_ring_preview()
            self.ring_preview.select(index)

    def _binding_changed(self, choice, _param, cid):
        if self.loading:
            return
        selected = choice.get_selected()
        if selected > len(self.choice_ids) + 1:
            return
        value = None if selected == 0 else SHOW_RING if selected == 1 else self.choice_ids[selected - 2]
        current = SHOW_RING if cid == self.config["ring_button"] else self.config["button_bindings"].get(str(cid))
        if value == current:
            return
        try:
            set_button_assignment(self.config, cid, value, root=True)
        except RuntimeError as exc:
            self.error.set_text(str(exc))
            self.error.set_visible(True)
            self._refresh_choices()
            return
        self.trigger_row.set_subtitle(BUTTON_NAMES.get(self.config["ring_button"], "Manual only · use Open Actions Ring"))
        self._refresh_choices()
        self._mark_dirty()

    def _group_changed(self, _choice, _param, index):
        if self.loading:
            return
        row, choices = self.group_choices[index]
        selected = [choice.get_selected() for choice in choices]
        if any(value > len(self.choice_ids) for value in selected):
            return
        children = [self.choice_ids[value - 1] for value in selected if value]
        previous = self.config.get("ring_groups", {}).get(str(index), [])
        if len(children) != len(set(children)):
            self.loading = True
            for child, choice in enumerate(choices):
                choice.set_selected(self.choice_ids.index(previous[child]) + 1 if child < len(previous) else 0)
            self.loading = False
            self.error.set_text("Each action can appear only once within a group. Choose a different routine.")
            self.error.set_visible(True)
            return
        if children == previous:
            return
        if children:
            self.config.setdefault("ring_groups", {})[str(index)] = children
        else:
            self.config.get("ring_groups", {}).pop(str(index), None)
        self.loading = True
        for child, choice in enumerate(choices):
            choice.set_selected(self.choice_ids.index(children[child]) + 1 if child < len(children) else 0)
        self.loading = False
        row.set_subtitle(f"{len(children)} actions · opens a submenu" if children else "No group · runs the assigned action")
        self._mark_dirty()
        self._refresh_ring_preview()

    def _haptics_changed(self, *_):
        if not self.loading:
            self.config["haptic_feedback"] = self.haptics.get_active()
            self._mark_dirty()

    def _draft_changed(self, source, *_):
        if self.loading or self.syncing_steps:
            return
        start, end = self.steps_buffer.get_bounds()
        self.drafts[self.selected_id].update({
            "id": self.id_entry.get_text(), "label": self.name_entry.get_text(),
            "app_id": self.app_entry.get_text(),
            "steps_text": self.steps_buffer.get_text(start, end, True),
        })
        self._mark_dirty()
        if source == self.name_entry:
            self._refresh_labels()
        self._validate_selected()

    def _action_selected(self, choice, _param):
        index = choice.get_selected()
        if not self.loading and index < len(self.choice_ids):
            self.selected_id = self.choice_ids[index]
            self._load_selected()

    def _load_selected(self):
        previous = self.loading
        self.loading = True
        self.step_application_box.set_visible(False)
        self.action_application_box.set_visible(False)
        draft = self.drafts[self.selected_id]
        self.name_entry.set_text(draft["label"])
        self.id_entry.set_text(draft["id"])
        self.app_entry.set_text(draft["app_id"])
        self.steps_buffer.set_text(draft["steps_text"])
        if "form_values" not in draft:
            try:
                draft["form_values"] = [_step_values(step) for step in json.loads(draft["steps_text"])]
            except (ValueError, KeyError, TypeError, RecursionError):
                draft["form_values"] = None
                draft["json_mode"] = True
        json_mode = draft.get("json_mode", False)
        self.json_toggle.set_active(json_mode)
        self.steps_group.set_visible(not json_mode)
        self.step_add_box.set_visible(not json_mode)
        self.json_box.set_visible(json_mode)
        self._render_steps()
        self._show_step_error()
        self.loading = previous
        self._validate_selected()

    def _add_routine(self, *_):
        if len(self.drafts) >= MAX_ACTIONS:
            return
        _, prefix, label, steps = TEMPLATES[self.template_choice.get_selected()]
        if prefix == "launch":
            self._show_application_picker(self.action_application_box, self._add_application)
            return
        self._new_draft(prefix, label, steps)
        self._refresh_choices()
        self._load_selected()
        self.name_entry.grab_focus()

    def _delete_selected(self, *_):
        if len(self.drafts) <= 1:
            return
        action_id = self.selected_id
        fallback = next(key for key in self.drafts if key != action_id)
        label = self.drafts[action_id]["label"] or action_id
        replacement = self.drafts[fallback]["label"] or fallback
        dialog = Adw.MessageDialog(transient_for=self, modal=True, heading=f"Delete {label}?",
                                   body=f"Its ring positions will use {replacement}. Its button shortcuts and group entries will be removed. Changes apply when you save.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete routine")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(_dialog, response):
            if response != "delete":
                return
            del self.drafts[action_id]
            self.config["actions"].pop(action_id, None)
            self.config["ring"] = [fallback if item == action_id else item for item in self.config["ring"]]
            self.config["button_bindings"] = {cid: item for cid, item in self.config["button_bindings"].items()
                                               if item != action_id}
            if "ring_groups" in self.config:
                self.config["ring_groups"] = {
                    position: remaining for position, children in self.config["ring_groups"].items()
                    if (remaining := [item for item in children if item != action_id])
                }
            self.selected_id = fallback
            self._mark_dirty()
            self._refresh_choices()
            self._load_selected()

        dialog.connect("response", answered)
        dialog.present()

    def _candidate(self):
        candidate = deepcopy(self.config)
        actions = {}
        renames = {}
        for original_id, draft in self.drafts.items():
            action_id = draft["id"]
            if action_id in actions:
                raise _DraftError(original_id, f"Action ID {action_id!r} is used by more than one routine.")
            try:
                steps = (self._form_steps(draft["form_values"]) if draft.get("form_values") is not None
                         else json.loads(draft["steps_text"]))
            except (ValueError, RecursionError) as exc:
                raise _DraftError(original_id, f"Cannot read steps for {draft['label'] or original_id}: {exc}") from exc
            action = {**deepcopy(self.config["actions"].get(original_id, {})), "label": draft["label"], "steps": steps}
            action.pop("app_id", None)
            if draft["app_id"]:
                action["app_id"] = draft["app_id"]
            try:
                document = deepcopy(self.config)
                document["actions"][action_id] = action
                validate_config(document)
            except RuntimeError as exc:
                raise _DraftError(original_id, str(exc)) from exc
            actions[action_id] = action
            renames[original_id] = action_id
        candidate["actions"] = actions
        candidate["ring"] = [renames[item] for item in candidate["ring"]]
        candidate["button_bindings"] = {cid: renames[item] for cid, item in candidate["button_bindings"].items()}
        if "ring_groups" in candidate:
            candidate["ring_groups"] = {position: [renames[item] for item in children]
                                        for position, children in candidate["ring_groups"].items()}
        return candidate

    def _save(self, *_):
        if self.saving:
            return
        try:
            candidate = validate_config(self._candidate())
        except (RuntimeError, ValueError, OSError, RecursionError) as exc:
            if isinstance(exc, _DraftError):
                self.selected_id = exc.action_id
                self._refresh_choices()
                self._load_selected()
                self.stack.set_visible_child_name("actions")
            self.error.set_text(self.details_error.get_text() if isinstance(exc, _DraftError) and self.details_error.get_visible()
                                else str(exc).removeprefix("Invalid desktop configuration: "))
            self.error.set_visible(True)
            self.error.grab_focus()
            return
        self.saving = True
        self.stack.set_sensitive(False)
        self.save_button.set_sensitive(False)
        self.save_button.set_label("Saving…")
        self.window_title.set_subtitle("Saving changes…")
        self.error.set_visible(False)

        def persist():
            try:
                self.store.save(candidate)
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            else:
                error = None
            GLib.idle_add(self._save_finished, error)

        # File locks and fsync can wait indefinitely on a slow filesystem.
        try:
            threading.Thread(target=persist, name="mx4-config-save").start()
        except RuntimeError as exc:
            self._save_finished(str(exc))

    def _save_finished(self, error):
        self.saving = False
        self.stack.set_sensitive(True)
        self.save_button.set_label("Save" if self.compact else "Save changes")
        if error:
            self.save_button.set_sensitive(self.dirty)
            self.window_title.set_subtitle("Unsaved changes")
            self.error.set_text(error)
            self.error.set_visible(True)
            self.error.grab_focus()
            return GLib.SOURCE_REMOVE
        self.dirty = False
        self.save_button.set_sensitive(False)
        self.window_title.set_subtitle("Changes saved")
        try:
            self.on_saved()
        except Exception as exc:
            self.error.set_text(f"Configuration saved, but desktop controls could not reload: {exc}")
            self.error.set_visible(True)
            return GLib.SOURCE_REMOVE
        self.allow_close = True
        self.close()
        return GLib.SOURCE_REMOVE

    def _close_requested(self, *_):
        if self.saving:
            return True
        if self.allow_close or not self.dirty:
            return False
        if self.close_dialog is not None:
            self.close_dialog.present()
            return True
        dialog = Adw.MessageDialog(transient_for=self, modal=True, heading="Discard unsaved changes?",
                                   body="Your ring assignments and routines have not been saved.")
        dialog.add_response("cancel", "Keep editing")
        dialog.add_response("discard", "Discard changes")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        self.close_dialog = dialog

        def answered(_dialog, response):
            self.close_dialog = None
            if response == "discard":
                self.allow_close = True
                self.close()

        dialog.connect("response", answered)
        dialog.present()
        return True


def present_desktop_editor(parent, store, on_saved, cid=None, page="ring"):
    """Present a modal editor; call ``on_saved()`` after a validated save."""
    if page not in ("ring", "actions"):
        raise ValueError("Unknown editor page")
    window = DesktopEditor(parent, store, on_saved)
    window.stack.set_visible_child_name(page)
    if cid is not None:
        window.shortcuts_expander.set_expanded(True)
        target = next((choice for key, choice in window.binding_choices if key == cid), window.ring_preview)
        if page == "ring":
            window.connect("map", lambda *_: target.grab_focus())
        window.window_title.set_subtitle(f"Default/root assignment · {BUTTON_NAMES.get(cid, f'button {cid}')}")
    window.present()
    return window
