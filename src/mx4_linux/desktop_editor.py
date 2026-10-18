"""Native editor for the local Actions Ring and Smart Actions configuration.

Editing never executes an action. A private draft holds incomplete JSON until
the user saves, and the store validates the complete configuration before it is
accepted. Selecting another routine preserves every field of the current one.
"""

from copy import deepcopy
import json
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango


SLOT_NAMES = ("Top", "Upper right", "Right", "Lower right", "Bottom",
              "Lower left", "Left", "Upper left")
BUTTON_NAMES = {82: "Middle button", 83: "Back button", 86: "Forward button",
                195: "Gesture button", 196: "Wheel mode button", 416: "Haptic panel"}
TEMPLATES = (
    ("Keyboard shortcut", "shortcut", "Keyboard shortcut", [{"type": "shortcut", "keys": ["CTRL", "C"]}]),
    ("Launch application", "launch", "Launch application", [{"type": "command", "argv": ["xdg-open", str(Path.home())]}]),
    ("Insert text", "text", "Insert text", [{"type": "text", "text": "Hello from MX4 Linux"}]),
    ("Multiple steps", "routine", "Copy and paste", [
        {"type": "shortcut", "keys": ["CTRL", "C"]},
        {"type": "delay", "milliseconds": 150},
        {"type": "shortcut", "keys": ["CTRL", "V"]},
    ]),
)


class _DraftError(ValueError):
    def __init__(self, action_id, message):
        super().__init__(message)
        self.action_id = action_id


def _label(text, *, dim=False):
    widget = Gtk.Label(label=text, xalign=0, wrap=True)
    if dim:
        widget.add_css_class("dim-label")
    return widget


def _page():
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                  margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
    clamp = Adw.Clamp(maximum_size=820, tightening_threshold=650)
    clamp.set_child(box)
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    scroll.set_child(clamp)
    return box, scroll


def _action_dropdown(**kwargs):
    choice = Gtk.DropDown(enable_search=True, **kwargs)
    factory = Gtk.SignalListItemFactory()

    def setup(_factory, item):
        item.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=30))

    def bind(_factory, item):
        label = item.get_child()
        text = item.get_item().get_string()
        label.set_text(text)
        label.set_tooltip_text(text)

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    choice.set_factory(factory)
    return choice


class DesktopEditor(Adw.Window):
    def __init__(self, parent, store, on_saved):
        super().__init__(title="Actions Ring and Smart Actions", transient_for=parent,
                         modal=True, default_width=900, default_height=760)
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
        self.loading = True
        self.allow_close = False
        self.close_dialog = None
        self.ring_choices = []
        self.binding_choices = []
        self.connect("close-request", self._close_requested)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Actions Ring and Smart Actions"))
        save = Gtk.Button(label="Save changes")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        header.pack_end(save)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        switcher = Gtk.StackSwitcher(stack=self.stack, halign=Gtk.Align.CENTER,
                                     margin_top=10, margin_bottom=10)
        content.append(switcher)
        self._build_ring()
        self._build_actions()
        content.append(self.stack)

        self.error = _label("", dim=False)
        self.error.add_css_class("error")
        self.error.set_selectable(True)
        self.error.set_margin_start(24)
        self.error.set_margin_end(24)
        self.error.set_margin_top(10)
        self.error.set_margin_bottom(14)
        self.error.set_visible(False)
        content.append(self.error)
        toolbar.set_content(content)
        self.set_content(toolbar)
        self._refresh_choices()
        self._load_selected()
        self.loading = False

    def _build_ring(self):
        page, scroll = _page()
        page.append(_label("Press the haptic panel to open the ring, then choose an action. "
                           "Assign a Smart Action to any of the eight positions."))
        group = Adw.PreferencesGroup(title="Ring positions", description="Positions run clockwise from the top.")
        for index, name in enumerate(SLOT_NAMES):
            row = Adw.ActionRow(use_markup=False, title=f"{index + 1}. {name}")
            choice = _action_dropdown(valign=Gtk.Align.CENTER)
            choice.set_tooltip_text(f"Action at ring position {index + 1}: {name}")
            choice.set_hexpand(False)
            choice.connect("notify::selected", self._ring_changed, index)
            row.add_suffix(choice)
            self.ring_choices.append(choice)
            group.add(row)
        page.append(group)

        behavior = Adw.PreferencesGroup(title="Ring behavior")
        ring_button = self.config["ring_button"]
        behavior.add(Adw.ActionRow(use_markup=False, title="Open Actions Ring",
                                  subtitle=BUTTON_NAMES.get(ring_button, f"Button {ring_button}")))
        feedback = Adw.ActionRow(use_markup=False, title="Haptic feedback",
                                 subtitle="Feel a pulse when the ring opens.")
        self.haptics = Gtk.Switch(active=self.config["haptic_feedback"], valign=Gtk.Align.CENTER)
        self.haptics.connect("notify::active", self._haptics_changed)
        feedback.add_suffix(self.haptics)
        feedback.set_activatable_widget(self.haptics)
        behavior.add(feedback)
        page.append(behavior)

        bindings = Adw.PreferencesGroup(title="Optional button shortcuts",
                                        description="A shortcut replaces that button's normal function while desktop controls are enabled.")
        for cid, name in BUTTON_NAMES.items():
            if cid == ring_button:
                continue
            row = Adw.ActionRow(use_markup=False, title=name)
            choice = _action_dropdown(valign=Gtk.Align.CENTER)
            choice.set_tooltip_text(f"Smart Action for {name.lower()}")
            choice.connect("notify::selected", self._binding_changed, cid)
            row.add_suffix(choice)
            self.binding_choices.append((cid, choice))
            bindings.add(row)
        page.append(bindings)
        self.stack.add_titled(scroll, "ring", "Actions Ring")

    def _build_actions(self):
        page, scroll = _page()
        page.append(_label("Create routines from keyboard shortcuts, applications, text, delays and niri desktop commands. "
                           "Steps run in order when you choose the action."))
        chooser_row = Gtk.Box(spacing=12)
        self.action_choice = _action_dropdown(hexpand=True)
        self.action_choice.set_tooltip_text("Select a routine to edit")
        self.action_choice.connect("notify::selected", self._action_selected)
        chooser_row.append(self.action_choice)
        self.delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete selected routine")
        self.delete.add_css_class("destructive-action")
        self.delete.connect("clicked", self._delete_selected)
        chooser_row.append(self.delete)
        page.append(chooser_row)

        fields = Adw.PreferencesGroup(title="Selected routine")
        self.name_entry = Adw.EntryRow(title="Name")
        self.name_entry.set_max_length(80)
        self.id_entry = Adw.EntryRow(title="Action ID")
        self.id_entry.set_max_length(64)
        self.app_entry = Adw.EntryRow(title="Only in application (optional app ID)")
        self.app_entry.set_max_length(256)
        for widget in (self.name_entry, self.id_entry, self.app_entry):
            widget.connect("changed", self._draft_changed)
            fields.add(widget)
        page.append(fields)
        page.append(_label("Action IDs use lowercase letters, numbers, hyphens and underscores. "
                           "An application condition must match the focused niri app ID exactly. "
                           "Leave it empty to run in any application.", dim=True))

        steps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title = _label("Steps (JSON)")
        title.add_css_class("heading")
        steps_box.append(title)
        self.steps_view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.NONE,
                                       left_margin=12, right_margin=12,
                                       top_margin=12, bottom_margin=12)
        self.steps_view.set_accepts_tab(False)
        self.steps_buffer = self.steps_view.get_buffer()
        self.steps_buffer.connect("changed", self._draft_changed)
        code_scroll = Gtk.ScrolledWindow(min_content_height=230, vexpand=True)
        code_scroll.add_css_class("card")
        code_scroll.set_child(self.steps_view)
        steps_box.append(code_scroll)
        page.append(steps_box)

        add_row = Gtk.Box(spacing=12)
        self.template_choice = Gtk.DropDown.new_from_strings([item[0] for item in TEMPLATES])
        self.template_choice.set_hexpand(True)
        add_row.append(self.template_choice)
        add = Gtk.Button(label="Add routine")
        add.connect("clicked", self._add_routine)
        add_row.append(add)
        page.append(add_row)

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
                         margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        code.add_css_class("monospace")
        help_box.append(code)
        help_box.append(_label("Use Linux key names such as CTRL, ALT, SHIFT, SUPER, ENTER and F1. "
                               "A routine can contain up to 64 steps. Each delay is at most 30 seconds, with 60 seconds total.", dim=True))
        help_expander.set_child(help_box)
        page.append(help_expander)
        self.stack.add_titled(scroll, "actions", "Smart Actions")

    def _refresh_choices(self):
        previous = self.loading
        self.loading = True
        self.choice_ids = list(self.drafts)
        labels = [f"{self.drafts[key]['label'] or 'Untitled'} · {self.drafts[key]['id']}"
                  for key in self.choice_ids]
        for index, choice in enumerate(self.ring_choices):
            choice.set_model(Gtk.StringList.new(labels))
            choice.set_selected(self.choice_ids.index(self.config["ring"][index]))
        for cid, choice in self.binding_choices:
            choice.set_model(Gtk.StringList.new(["Normal button function"] + labels))
            binding = self.config["button_bindings"].get(str(cid))
            choice.set_selected(self.choice_ids.index(binding) + 1 if binding else 0)
        self.action_choice.set_model(Gtk.StringList.new(labels))
        self.action_choice.set_selected(self.choice_ids.index(self.selected_id))
        self.delete.set_sensitive(len(self.drafts) > 1)
        self.loading = previous

    def _ring_changed(self, choice, _param, index):
        if not self.loading and choice.get_selected() < len(self.choice_ids):
            self.config["ring"][index] = self.choice_ids[choice.get_selected()]
            self.dirty = True

    def _binding_changed(self, choice, _param, cid):
        if self.loading:
            return
        selected = choice.get_selected()
        if selected == 0:
            self.config["button_bindings"].pop(str(cid), None)
        elif selected <= len(self.choice_ids):
            self.config["button_bindings"][str(cid)] = self.choice_ids[selected - 1]
        else:
            return
        self.dirty = True

    def _haptics_changed(self, *_):
        if not self.loading:
            self.config["haptic_feedback"] = self.haptics.get_active()
            self.dirty = True

    def _draft_changed(self, source, *_):
        if self.loading:
            return
        start, end = self.steps_buffer.get_bounds()
        self.drafts[self.selected_id] = {
            "id": self.id_entry.get_text(), "label": self.name_entry.get_text(),
            "app_id": self.app_entry.get_text(),
            "steps_text": self.steps_buffer.get_text(start, end, True),
        }
        self.dirty = True
        if source in (self.name_entry, self.id_entry):
            self._refresh_choices()

    def _action_selected(self, choice, _param):
        index = choice.get_selected()
        if not self.loading and index < len(self.choice_ids):
            self.selected_id = self.choice_ids[index]
            self._load_selected()

    def _load_selected(self):
        previous = self.loading
        self.loading = True
        draft = self.drafts[self.selected_id]
        self.name_entry.set_text(draft["label"])
        self.id_entry.set_text(draft["id"])
        self.app_entry.set_text(draft["app_id"])
        self.steps_buffer.set_text(draft["steps_text"])
        self.loading = previous

    def _add_routine(self, *_):
        _, prefix, label, steps = TEMPLATES[self.template_choice.get_selected()]
        number = 1
        used_ids = set(self.drafts) | {draft["id"] for draft in self.drafts.values()}
        while f"{prefix}-{number}" in used_ids:
            number += 1
        action_id = f"{prefix}-{number}"
        self.drafts[action_id] = {"id": action_id, "label": label, "app_id": "",
                                  "steps_text": json.dumps(steps, indent=2)}
        self.selected_id = action_id
        self.dirty = True
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
                                   body=f"Its ring positions will use {replacement}. Its button shortcuts will be removed. Changes apply when you save.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete routine")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(_dialog, response):
            if response != "delete":
                return
            del self.drafts[action_id]
            self.config["ring"] = [fallback if item == action_id else item for item in self.config["ring"]]
            self.config["button_bindings"] = {cid: item for cid, item in self.config["button_bindings"].items()
                                               if item != action_id}
            self.selected_id = fallback
            self.dirty = True
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
                steps = json.loads(draft["steps_text"])
            except (ValueError, RecursionError) as exc:
                raise _DraftError(original_id, f"Cannot read steps for {draft['label'] or original_id}: {exc}") from exc
            action = {"label": draft["label"], "steps": steps}
            if draft["app_id"]:
                action["app_id"] = draft["app_id"]
            actions[action_id] = action
            renames[original_id] = action_id
        candidate["actions"] = actions
        candidate["ring"] = [renames[item] for item in candidate["ring"]]
        candidate["button_bindings"] = {cid: renames[item] for cid, item in candidate["button_bindings"].items()}
        return candidate

    def _save(self, *_):
        try:
            candidate = self._candidate()
            self.store.save(candidate)
        except (RuntimeError, ValueError, OSError, RecursionError) as exc:
            if isinstance(exc, _DraftError):
                self.selected_id = exc.action_id
                self._refresh_choices()
                self._load_selected()
                self.stack.set_visible_child_name("actions")
            self.error.set_text(str(exc))
            self.error.set_visible(True)
            return
        self.dirty = False
        try:
            self.on_saved()
        except Exception as exc:
            self.error.set_text(f"Configuration saved, but desktop controls could not reload: {exc}")
            self.error.set_visible(True)
            return
        self.allow_close = True
        self.close()

    def _close_requested(self, *_):
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


def present_desktop_editor(parent, store, on_saved):
    """Present a modal editor; call ``on_saved()`` after a validated save."""
    window = DesktopEditor(parent, store, on_saved)
    window.present()
    return window
