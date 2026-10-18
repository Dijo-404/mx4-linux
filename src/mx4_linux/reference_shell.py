"""Reference-shaped native navigation, artwork workspace and target inspectors.

Hardware work stays in ControlWindow's serialized job/change/readback pipeline.
This module only builds and routes widgets; no animation performs device I/O.
"""
from pathlib import Path

from .actions import SHOW_RING, resolve_config
from .gtk_support import Adw, Gdk, Gtk
from .mouse_view import ASSETS, BUTTONS, ModelPicture, MouseView
from gi.repository import Pango

DEVICE_ROUTES = {'buttons': 'Buttons', 'point': 'Point, Scroll, Press', 'haptics': 'Haptic Feedback',
                 'easy-switch': 'Easy-Switch', 'settings': 'Settings'}
TITLES = {'home': 'MX4 Linux', **DEVICE_ROUTES, 'actions': 'Actions Ring & Smart Actions',
          'ring': 'Actions Ring', 'smart-actions': 'Smart Actions', 'configuration': 'Local Configuration',
          'profiles': 'Mouse profiles', 'application': 'Application Settings', 'guide': 'Meet your MX Master 4'}
TARGET_TITLES = {**{f'button:{cid}': item[0] for cid, item in BUTTONS.items()},
                 'wheel': 'Scroll wheel', 'thumb': 'Thumb wheel', 'pointer': 'Pointer speed',
                 'pressure': 'Button press sensitivity', 'haptics': 'Haptic feedback'}


def label(text, style=None):
    widget = Gtk.Label(label=text, wrap=True, xalign=0)
    if style:
        widget.add_css_class(style)
    return widget


def button(text, callback, *, icon=None):
    widget = Gtk.Button(label=text)
    if icon:
        widget.set_icon_name(icon)
        widget.set_tooltip_text(text)
    widget.connect('clicked', lambda *_: callback())
    return widget


def page_box(spacing=18):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing,
                   margin_start=24, margin_end=24, margin_top=18, margin_bottom=24)


def scroll(child):
    return Gtk.ScrolledWindow(child=child, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)


def bounded_dropdown(strings, *, accessible_name, max_chars=18):
    """Keep closed controls small without changing exact model identities."""
    choice = Gtk.DropDown.new_from_strings(strings)
    choice.set_enable_search(True)
    choice.update_property([Gtk.AccessibleProperty.LABEL], [accessible_name])
    def factory(chars):
        value = Gtk.SignalListItemFactory()
        def setup(_factory, item):
            item.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                     max_width_chars=chars, single_line_mode=True))
        def bind(_factory, item):
            text = item.get_item().get_string()
            child = item.get_child()
            child.set_text(text)
            child.set_tooltip_text(text)
            child.update_property([Gtk.AccessibleProperty.LABEL], [text])
        value.connect('setup', setup)
        value.connect('bind', bind)
        return value
    choice.set_factory(factory(max_chars))
    choice.set_list_factory(factory(48))
    def selected(widget, _property):
        item = widget.get_selected_item()
        widget.set_tooltip_text(item.get_string() if item else '')
    choice.connect('notify::selected-item', selected)
    selected(choice, None)
    return choice


class ReferenceShell:
    def __init__(self, window):
        self.window = window
        self.context_app = None
        self.narrow = False
        self.updating_context = False
        self.inspector_stack = None
        self.inspector = None
        self.sidebar = None
        self.art = None
        self.targets = {}
        self.preference_widgets = {}
        self.provider = Gtk.CssProvider()
        self.provider.load_from_path(str(Path(__file__).parent / 'assets' / 'reference.css'))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        window.add_css_class('mx4-shell')
        self.style = Adw.StyleManager.get_default()
        self.style_handler = self.style.connect('notify::dark', self.apply_theme_class)
        self.apply_appearance((window.desktop.config or {}).get('appearance', 'dark'))
        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.header.set_show_title(True)
        self.back = button('Back', window.go_back, icon='go-previous-symbolic')
        self.header.pack_start(self.back)
        self.heading = label('MX4 Linux', 'mx4-heading')
        self.heading.set_wrap(False)
        self.heading.set_single_line_mode(True)
        self.header.pack_start(self.heading)
        self.utilities = Gtk.Box(spacing=4)
        for text, route, icon in [('Smart Actions', 'smart-actions', 'emblem-ok-symbolic'),
                                  ('Actions Ring', 'ring', 'view-grid-symbolic'),
                                  ('Application Settings', 'application', 'emblem-system-symbolic')]:
            self.utilities.append(button(text, lambda route=route: window.navigate(route), icon=icon))
        self.header.pack_end(self.utilities)
        self.toolbar.add_top_bar(self.header)
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=200)
        responsive = Adw.BreakpointBin(child=self.stack, width_request=280, height_request=280)
        point = Adw.Breakpoint(condition=Adw.BreakpointCondition.parse('max-width: 759px'))
        point.connect('apply', lambda *_: self.set_narrow(True))
        point.connect('unapply', lambda *_: self.set_narrow(False))
        responsive.add_breakpoint(point)
        self.toolbar.set_content(responsive)
        self.build_home()
        self.device_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.context_bar = Gtk.Box(spacing=10)
        self.context_bar.add_css_class('mx4-context')
        self.context_bar.append(Gtk.Image(icon_name='view-grid-symbolic'))
        self.context_label = label('Assignments for')
        self.context_bar.append(self.context_label)
        self.context = bounded_dropdown(['General'], accessible_name='Assignment context')
        self.context.set_tooltip_text('Preview existing assignments for this application context')
        self.context.update_property([Gtk.AccessibleProperty.LABEL], ['Assignment context'])
        self.context.connect('notify::selected', self.context_changed)
        self.context_bar.append(self.context)
        self.context_detail = label('General', 'mx4-subtle')
        self.context_detail.set_wrap(False)
        self.context_detail.set_max_width_chars(24)
        self.context_detail.set_ellipsize(Pango.EllipsizeMode.END)
        self.context_detail.set_single_line_mode(True)
        self.context_detail.set_hexpand(True)
        self.context_bar.append(self.context_detail)
        self.context_bar.append(button('Edit actions', lambda: window.desktop.configure()))
        self.context_reveal = Gtk.Revealer(child=self.context_bar, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                          transition_duration=180, reveal_child=False)
        self.device_page.append(self.context_reveal)
        compact = Gtk.Box(spacing=8)
        compact.append(Gtk.Image(icon_name='view-grid-symbolic'))
        self.header_context = bounded_dropdown(['General'], accessible_name='Assignment context')
        self.header_context.set_tooltip_text('Assignment context · preview existing application profiles')
        self.header_context.update_property([Gtk.AccessibleProperty.LABEL], ['Assignment context'])
        self.header_context.connect('notify::selected', self.context_changed)
        compact.append(self.header_context)
        compact.append(button('Edit actions', lambda: window.desktop.configure(), icon='list-add-symbolic'))
        self.compact_context = compact
        self.header_context_reveal = Gtk.Revealer(child=compact, transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
                                                 transition_duration=180, reveal_child=False)
        self.header.set_title_widget(self.header_context_reveal)
        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.device_page.append(self.controls)
        responsive = Adw.BreakpointBin(child=self.device_page, width_request=280, height_request=280)
        point = Adw.Breakpoint(condition=Adw.BreakpointCondition.parse('max-width: 759px'))
        point.add_setter(self.context_detail, 'visible', False)
        point.add_setter(self.context_label, 'visible', False)
        responsive.add_breakpoint(point)
        self.stack.add_named(responsive, 'device')
        self.actions_box = page_box()
        self.actions_box.append(label('Your shortcuts, one gesture away', 'mx4-heading'))
        self.actions_box.append(label('Customize the ring and build local routines. Selecting or editing an action never runs it.', 'mx4-subtle'))
        self.stack.add_named(scroll(Adw.Clamp(child=self.actions_box, maximum_size=800)), 'actions')
        profiles = page_box()
        profiles.append(label('A setup for every task', 'mx4-heading'))
        profiles.append(label('Save confirmed hardware settings for this mouse. Button shortcuts and ring positions are configured separately.', 'mx4-subtle'))
        self.profile_controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        profiles.append(self.profile_controls)
        self.stack.add_named(scroll(Adw.Clamp(child=profiles, maximum_size=800)), 'profiles')
        self.build_application()
        self.build_configuration()
        self.build_guide()
        self.update_context()

    def build_home(self):
        home = page_box()
        self.add_device_button = button('Add device', self.window.add_device)
        self.header.pack_end(self.add_device_button)
        self.updating_devices = False
        self.device_result = {'devices': [], 'diagnostics': []}
        self.device_selector = bounded_dropdown([], accessible_name='Connected mouse', max_chars=28)
        self.device_selector.connect('notify::selected', self.device_selected)
        self.device_chooser_row = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, visible=False)
        self.device_chooser_row.append(label('Mouse'))
        self.device_chooser_row.append(self.device_selector)
        home.append(self.device_chooser_row)
        if self.window.demo:
            home.append(label('PREVIEW · All device controls are simulated. No hardware is accessed.', 'mx4-subtle'))
        grid = Gtk.Box(spacing=32, vexpand=True, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, margin_start=60)
        self.device_card = Gtk.Button()
        self.device_card.add_css_class('device-card')
        self.device_card.connect('clicked', lambda *_: self.window.navigate('buttons'))
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        image = ModelPicture()
        image.set_filename(str(ASSETS / 'overview.png'))
        image.set_size_request(240, 240)
        self.home_name = label('MX Master 4', 'title-3')
        self.home_name.set_halign(Gtk.Align.CENTER)
        card.append(self.home_name)
        card.append(image)
        self.home_battery = label('Reading battery…', 'battery-chip')
        self.home_battery.set_halign(Gtk.Align.CENTER)
        card.append(self.home_battery)
        self.home_integration = label('', 'mx4-subtle')
        self.home_integration.set_halign(Gtk.Align.CENTER)
        card.append(self.home_integration)
        self.device_card.set_child(card)
        grid.append(self.device_card)
        note = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, valign=Gtk.Align.CENTER)
        note.add_css_class('mx4-home-note')
        note.append(label('Make it yours', 'title-2'))
        note.append(label('Tune your pointer, scrolling and feedback.\nKeep your favorite actions close.', 'mx4-subtle'))
        note.append(button('Explore your mouse', lambda: self.window.navigate('guide')))
        self.home_note = note
        grid.append(note)
        home.append(grid)
        self.home_grid = grid
        self.home_empty = label('Connect with Bluetooth or a Logi Bolt receiver, wake the mouse, then Add device to check again.', 'mx4-subtle')
        home.append(self.home_empty)
        self.stack.add_named(scroll(home), 'home')

    def build_application(self):
        body = page_box()
        body.append(label('Application Settings', 'mx4-heading'))
        body.append(label('MX4 Linux · native local controls', 'mx4-subtle'))
        appearance = Adw.PreferencesGroup(title='Appearance')
        row = Adw.ComboRow(title='Color theme', model=Gtk.StringList.new(['Dark', 'Light', 'System']), sensitive=self.window.desktop.config is not None)
        names = ['dark', 'light', 'system']
        row.set_selected(names.index((self.window.desktop.config or {}).get('appearance', 'dark')))
        handler = row.connect('notify::selected', lambda widget, _p: self.window.save_local_preference('appearance', names[widget.get_selected()]))
        self.preference_widgets[('appearance', None)] = (row, handler, lambda value, row=row: row.set_selected(names.index(value)))
        appearance.add(row)
        body.append(appearance)
        assignments = Adw.PreferencesGroup(title='Actions Ring')
        adapt = Adw.SwitchRow(title='Adapt to App', subtitle='Use the active application profile; otherwise use General assignments.',
                             active=(self.window.desktop.config or {}).get('adapt_to_app', True),
                             sensitive=self.window.desktop.config is not None)
        handler = adapt.connect('notify::active', lambda widget, _p: self.window.save_local_preference('adapt_to_app', widget.get_active()))
        self.preference_widgets[('adapt_to_app', None)] = (adapt, handler, adapt.set_active)
        assignments.add(adapt)
        body.append(assignments)
        help_group = Adw.PreferencesGroup(title='Help')
        row = Adw.ActionRow(title='Learn your mouse', subtitle='An optional guide to controls and local actions.')
        row.add_suffix(button('Open guide', lambda: self.window.navigate('guide')))
        help_group.add(row)
        row = Adw.ActionRow(title='Project support', subtitle='Installation, permissions and issue reports.')
        row.add_suffix(button('Open support', self.window.open_support))
        help_group.add(row)
        body.append(help_group)
        body.append(label('Local assignments and mouse settings work without a vendor account.', 'mx4-subtle'))
        local = Adw.PreferencesGroup(title='Local configuration')
        row = Adw.ActionRow(title='Backup and reset', subtitle='Local action, preference and mouse profile files.')
        row.add_suffix(button('Open', lambda: self.window.navigate('configuration')))
        local.add(row)
        body.append(local)
        self.stack.add_named(scroll(Adw.Clamp(child=body, maximum_size=760)), 'application')

    def build_configuration(self):
        body = page_box()
        self.configuration_status = label('Actions and preferences are stored locally. Backup and reset controls are not yet available here.', 'mx4-subtle')
        body.append(self.configuration_status)
        self.configuration_paths = label(f'Actions and preferences: {self.window.desktop.store.path}\n\nMouse profiles: {self.window.store.path}\n\nRemembered mouse settings: {self.window.remembered.path}')
        self.configuration_paths.set_selectable(True)
        body.append(self.configuration_paths)
        body.append(button('Copy configuration locations', lambda: self.copy_text(self.configuration_paths.get_text())))
        body.append(label('Opening this page does not change settings or reset a device.', 'mx4-subtle'))
        self.stack.add_named(scroll(Adw.Clamp(child=body, maximum_size=760)), 'configuration')

    def build_guide(self):
        body = page_box(24)
        body.append(label('Meet your MX Master 4', 'mx4-heading'))
        body.append(label('Choose a feature to explore. You can leave this guide at any time and return from Settings.', 'mx4-subtle'))
        for title, detail, route in [
            ('Make each button yours', 'Select a callout to choose a built-in function or edit its local shortcut.', 'buttons'),
            ('Point, scroll and press', 'Set pointer resolution, wheel behavior and the supported panel threshold.', 'point'),
            ('Feel your actions', 'Adjust physical strength and choose local Actions Ring feedback events.', 'haptics'),
            ('Your paired computers', 'Read the three channels and explicitly request a paired channel.', 'easy-switch'),
            ('Actions, close at hand', 'Preview the ring safely, then build your own local routines.', 'ring'),
        ]:
            group = Adw.PreferencesGroup()
            row = Adw.ActionRow(title=title, subtitle=detail, use_markup=False)
            row.set_subtitle_lines(3)
            row.add_suffix(button('Explore', lambda route=route: self.window.navigate(route)))
            group.add(row)
            body.append(group)
        body.append(button('All set · Home', lambda: self.window.navigate('home')))
        self.stack.add_named(scroll(Adw.Clamp(child=body, maximum_size=760)), 'guide')

    def update_context(self):
        config = self.window.desktop.config or {}
        app_ids = sorted({p['app_id'] for p in config.get('assignment_profiles', {}).values() if p['app_id'] != '*'})
        current = self.window.desktop.context or {}
        if current.get('app_id') and current['app_id'] not in app_ids:
            app_ids.append(current['app_id'])
        self.context_ids = [None, *app_ids]
        self.updating_context = True
        labels = ['General', *app_ids]
        current_labels = [self.context.get_model().get_string(i) for i in range(self.context.get_model().get_n_items())]
        if current_labels != labels:
            self.context.set_model(Gtk.StringList.new(labels))
        index = self.context_ids.index(self.context_app) if self.context_app in self.context_ids else 0
        self.context.set_selected(index)
        current_labels = [self.header_context.get_model().get_string(i) for i in range(self.header_context.get_model().get_n_items())]
        if current_labels != labels:
            self.header_context.set_model(Gtk.StringList.new(labels))
        self.header_context.set_selected(index)
        self.updating_context = False
        active = config.get('active_profiles', {})
        pid = active.get(self.context_app) or active.get('*')
        text = config.get('assignment_profiles', {}).get(pid, {}).get('label', 'Default General Profile')
        self.context_detail.set_text(text)
        self.context_detail.set_tooltip_text(text)
        context_text = self.context_app or 'General'
        self.header_context.set_tooltip_text(f'Assignment context · {context_text}\n{text}')
        self.context.set_tooltip_text(f'Assignment context · {context_text}\n{text}')

    def context_changed(self, dropdown, _param):
        if self.updating_context or dropdown.get_selected() >= len(self.context_ids):
            return
        selected = self.context_ids[dropdown.get_selected()]
        if self.context_app == selected:
            return
        self.context_app = selected
        self.update_context()
        if self.window.current and self.window.current.get('online'):
            self.window.render_workspace()

    def effective_config(self):
        if self.window.desktop.config is None:
            # Display native functions while preserving the invalid document.
            # This is a UI-only unavailable state, never a runtime config.
            return {'actions': {}, 'button_bindings': {}, 'ring_button': None}
        return resolve_config(self.window.desktop.config, self.context_app, for_editing=True)

    def update_devices(self, result):
        self.device_result = result
        self.updating_devices = True
        try:
            names = [d['name'] for d in self.window.devices]
            current = self.device_selector.get_model()
            if names != [current.get_string(i) for i in range(current.get_n_items())]:
                self.device_selector.set_model(Gtk.StringList.new(names))
            index = next((i for i, device in enumerate(self.window.devices) if device['id'] == self.window.selected_id), Gtk.INVALID_LIST_POSITION)
            self.device_selector.set_selected(index)
        finally:
            self.updating_devices = False
        self.device_chooser_row.set_visible(len(self.window.devices) > 1)

    def device_selected(self, chooser, parameter):
        if self.updating_devices:
            return
        if self.window.busy or self.window.pending_hardware:
            self.update_devices(self.device_result)
            return
        self.window.select_device(chooser, parameter, self.device_result)

    def connection_summary(self):
        device = self.window.current or {}
        if self.window.demo:
            return 'Preview · simulated connection'
        if device.get('transport'):
            return str(device['transport'])
        path = device.get('path')
        state = 'Connected' if device.get('online') else 'Offline'
        return f'{state} · {path}' if path else f'{state} · transport not reported'

    def update_battery(self):
        device = self.window.current or {}
        text = device.get('battery') or 'Battery unavailable'
        self.home_battery.set_text(f'{text}  ·  {self.connection_summary()}')
        if hasattr(self, 'device_battery'):
            self.device_battery.set_text(text)

    def update_home(self):
        device = self.window.current
        self.device_card.set_sensitive(bool(device))
        self.home_empty.set_visible(not bool(device))
        if not device:
            self.home_name.set_text('Connect an MX Master 4')
            self.home_battery.set_text('No device available')
        else:
            self.home_name.set_text(device['name'])
            self.update_battery()
        self.device_selector.set_sensitive(len(self.window.devices) > 1 and not self.window.busy and not self.window.pending_hardware)
        self.home_integration.set_text('Integration enabled' if self.window.desktop.session.running else 'Integration off')

    def build_workspace(self):
        window = self.window
        if self.art:
            self.art.dispose_art()
        self.preference_widgets = {key: value for key, value in self.preference_widgets.items() if key[0] == 'appearance'}
        self.inspector_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=200)
        inspector_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inspector_body.add_css_class('mx4-inspector')
        head = Gtk.Box(spacing=8)
        head.add_css_class('mx4-inspector-header')
        head.append(button('Close inspector', window.close_inspector, icon='go-previous-symbolic'))
        self.inspector_title = label('', 'mx4-inspector-heading')
        self.inspector_title.set_hexpand(True)
        head.append(self.inspector_title)
        inspector_body.append(head)
        inspector_body.append(self.inspector_stack)
        self.inspector = Adw.OverlaySplitView(sidebar=inspector_body, sidebar_position=Gtk.PackType.END,
                                              min_sidebar_width=300, max_sidebar_width=370,
                                              sidebar_width_fraction=.34, show_sidebar=False, pin_sidebar=True)
        self.inspector.set_enable_show_gesture(False)
        self.inspector.connect("notify::show-sidebar", self.inspector_visibility_changed)
        workspace = Gtk.Box()
        self.sidebar = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT, transition_duration=250,
                                    reveal_child=True)
        nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        nav.add_css_class('mx4-sidebar')
        nav.set_size_request(200, -1)
        self.nav_buttons = {}
        icons = {'buttons': 'input-mouse-symbolic', 'point': 'preferences-desktop-mouse-symbolic',
                 'haptics': 'audio-volume-high-symbolic', 'easy-switch': 'computer-symbolic', 'settings': 'emblem-system-symbolic'}
        for route, title in DEVICE_ROUTES.items():
            item = button(title, lambda route=route: window.navigate(route))
            content = Gtk.Box(spacing=9)
            content.append(Gtk.Image(icon_name=icons[route]))
            content.append(label(title))
            item.set_child(content)
            item.set_halign(Gtk.Align.FILL)
            child = item.get_child()
            if isinstance(child, Gtk.Label):
                child.set_xalign(0)
            nav.append(item)
            self.nav_buttons[route] = item
        nav.append(Gtk.Box(vexpand=True))
        nav.append(button('Feature guide', lambda: window.navigate('guide')))
        self.sidebar.set_child(nav)
        workspace.append(self.sidebar)
        art_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.art_notice = label('', 'mx4-subtle')
        self.art_notice.set_halign(Gtk.Align.CENTER)
        art_column.append(self.art_notice)
        self.art = MouseView(window.select_target)
        window.wheel = self.art
        art_column.append(self.art)
        foot = Gtk.Box(spacing=10, margin_start=18, margin_end=18, margin_bottom=14)
        self.device_battery = label('', 'battery-chip')
        self.device_battery.set_halign(Gtk.Align.START)
        foot.append(self.device_battery)
        foot.append(Gtk.Box(hexpand=True))
        self.art_preview = button('Preview wheel', self.preview_art)
        foot.append(self.art_preview)
        art_column.append(foot)
        workspace.append(art_column)
        self.inspector.set_content(workspace)
        responsive = Adw.BreakpointBin(child=self.inspector, width_request=280, height_request=250)
        point = Adw.Breakpoint(condition=Adw.BreakpointCondition.parse('max-width: 759px'))
        point.add_setter(self.inspector, 'collapsed', True)
        responsive.add_breakpoint(point)
        window.controls.append(responsive)
        self.device_body = page_box()
        self.device_body_scroll = scroll(self.device_body)
        self.inspector_stack.add_named(self.device_body_scroll, 'device-settings')
        self.host_body = page_box()
        self.host_scroll = scroll(self.host_body)
        self.inspector_stack.add_named(self.host_scroll, 'hosts')
        self.targets = {}
        self.force_buttons = {}
        self.force_state = None
        self.build_setting_targets()
        window.add_device_policies(self.device_body, self.host_body)
        self.build_metadata()
        if window.selected_target not in self.targets:
            window.selected_target = None
        self.update_route()

    def inspector_visibility_changed(self, split, _property):
        # Backdrop/Escape dismissal in narrow mode must update semantic state
        # immediately, just like the explicit inspector Back button.
        if not split.get_show_sidebar() and (self.window.selected_target or self.window.route in ('settings', 'easy-switch')):
            self.window.close_inspector()

    def target_page(self, target, description=''):
        body = page_box(16)
        if description:
            body.append(label(description, 'mx4-subtle'))
        self.inspector_stack.add_named(scroll(body), target)
        self.targets[target] = body
        return body

    def build_setting_targets(self):
        window = self.window
        settings = {s['name']: s for s in window.current['settings']}
        groups = {'pointer': ('Pointer resolution', ['dpi']),
                  'wheel': ('Scroll wheel', ['scroll-ratchet', 'scroll-ratchet-torque', 'hires-smooth-invert', 'hires-smooth-resolution', 'smart-shift']),
                  'thumb': ('Horizontal scrolling', ['thumb-scroll-invert']),
                  'pressure': ('Supported pressure threshold', ['force-sensing']),
                  'haptics': ('Physical mouse feedback', ['haptic-level', 'haptic-play'])}
        for target, (title, names) in groups.items():
            if not any(name in settings for name in names):
                continue
            detail = {'pointer': 'Choose a resolution reported by this mouse.',
                      'wheel': 'Changes apply after the mouse confirms its new value.',
                      'thumb': 'Reverse horizontal scrolling with the thumb wheel.',
                      'pressure': 'Choose how firmly you press the haptic panel. Advanced values use the range reported by this mouse.',
                      'haptics': 'Strength is a continuous hardware value. Preview sends one supported waveform.'}[target]
            body = self.target_page(target, detail)
            if target == 'pressure':
                self.build_force_presets(body, settings['force-sensing'])
            group = Adw.PreferencesGroup(title=title)
            if target == 'pressure':
                advanced = Gtk.Expander(label='Advanced · raw threshold')
                advanced.set_child(group)
                body.append(advanced)
            else:
                body.append(group)
            for name in names:
                if name in settings:
                    window.add_setting(group, settings[name])
            if target == 'haptics':
                self.build_feedback_policy(body, settings.get('haptic-play'))
        mapping = settings.get('reprogrammable-keys')
        supported = {key['value']: key for key in mapping.get('keys', [])} if mapping else {}
        for cid in BUTTONS:
            if cid not in supported:
                continue
            body = self.target_page(f'button:{cid}', 'Choose what this control does. Local shortcuts run while mouse integration is enabled.')
            self.build_local_binding(body, cid)
            native = Adw.PreferencesGroup(title='Built-in mouse function')
            item = dict(mapping)
            item['keys'] = [supported[cid]]
            window.add_setting(native, item)
            body.append(native)
            body.append(button('Edit Smart Actions', lambda cid=cid: window.desktop.configure(cid=cid, page='actions')))

    def build_local_binding(self, body, cid):
        config = self.effective_config()
        group = Adw.PreferencesGroup(title='Local assignment')
        if self.window.desktop.config is None:
            group.add(Adw.ActionRow(title='Configuration unavailable', subtitle='Fix or import a valid local action configuration before assigning shortcuts.'))
            body.append(group)
            return
        row = Adw.ActionRow(title='Local action', subtitle='Default keeps the built-in mouse function.')
        keys = [None, SHOW_RING, *config['actions']]
        dropdown = bounded_dropdown(['Default', 'Show Actions Ring', *[action['label'] for action in config['actions'].values()]],
                                    accessible_name=f'Local assignment for {BUTTONS[cid][0]}', max_chars=16)
        assigned = SHOW_RING if cid == config['ring_button'] else config['button_bindings'].get(str(cid))
        dropdown.set_selected(keys.index(assigned) if assigned in keys else 0)
        def changed(widget, _param):
            if not getattr(self, 'updating_bindings', False) and widget.get_selected() < len(keys):
                self.window.save_button_assignment(cid, keys[widget.get_selected()], self.context_app)
        dropdown.connect('notify::selected', changed)
        row.add_suffix(dropdown)
        self.binding_widgets[cid] = (dropdown, keys)
        group.add(row)
        body.append(group)

    def build_force_presets(self, body, setting):
        if not all(type(setting.get(key)) is int for key in ('minimum', 'default', 'maximum')):
            return
        if not setting['minimum'] <= setting['default'] <= setting['maximum']:
            return
        box = Gtk.Box(spacing=6, homogeneous=True)
        for name, key in [('Light', 'minimum'), ('Medium', 'default'), ('Firm', 'maximum')]:
            item = button(name, lambda key=key: self.window.change(setting, setting[key]))
            item.set_tooltip_text(f'Reported {key}: {setting[key]}')
            box.append(item)
            self.force_buttons[setting[key]] = (item, name)
        body.append(box)
        self.force_state = label('', 'mx4-subtle')
        body.append(self.force_state)
        self.update_force_state()

    def update_force_state(self):
        if not self.force_buttons:
            return
        value = next((s.get('value') for s in self.window.current['settings'] if s['name'] == 'force-sensing'), None)
        for threshold, (item, _name) in self.force_buttons.items():
            item.add_css_class('suggested-action') if threshold == value else item.remove_css_class('suggested-action')
        self.force_state.set_text('Current: ' + self.force_buttons.get(value, (None, 'Custom'))[1])

    def build_feedback_policy(self, body, waveform):
        config = self.window.desktop.config or {}
        supported = self.window.desktop.config is not None and bool(waveform and waveform.get('choices'))
        group = Adw.PreferencesGroup(title='Local Actions Ring feedback', description='These events belong to MX4 Linux. Physical feedback requires mouse integration.')
        body.append(group)
        master = Adw.SwitchRow(title='Actions Ring feedback', active=config.get('haptic_feedback', True) if self.window.desktop.config is not None else False, sensitive=supported)
        handler = master.connect('notify::active', lambda row, _p: self.window.save_local_preference('haptic_feedback', row.get_active()))
        self.preference_widgets[('haptic_feedback', None)] = (master, handler, master.set_active)
        group.add(master)
        for source, title in [('open', 'Ring opens'), ('hover', 'Action hover'), ('select', 'Action selected'), ('complete', 'Routine completes')]:
            row = Adw.SwitchRow(title=title, active=config.get('haptic_events', {}).get(source, source == 'hover') if self.window.desktop.config is not None else False, sensitive=supported)
            handler = row.connect('notify::active', lambda row, _p, source=source: self.window.save_local_preference('haptic_events', row.get_active(), subkey=source))
            self.preference_widgets[('haptic_events', source)] = (row, handler, row.set_active)
            group.add(row)
        power = Adw.SwitchRow(title='Battery saving mode', subtitle='Suppress local pulses at 10% or lower when a recent numeric battery reading is available.',
                             active=config.get('haptic_power_save', False), sensitive=supported)
        handler = power.connect('notify::active', lambda row, _p: self.window.save_local_preference('haptic_power_save', row.get_active()))
        self.preference_widgets[('haptic_power_save', None)] = (power, handler, power.set_active)
        group.add(power)
        body.append(button('Open Actions Ring', lambda: self.window.navigate('ring')))

    def copy_text(self, text):
        Gdk.Display.get_default().get_clipboard().set(text)

    def build_metadata(self):
        device = self.window.current
        group = Adw.PreferencesGroup(title='About this mouse')
        self.device_body.prepend(group)
        self.metadata_copy_buttons = {}
        for field, title in [('name', 'Device model'), ('stable_id', 'Device identifier'),
                             ('firmware', 'Firmware'), ('serial', 'Serial number'), ('identity_source', 'Identity source')]:
            value = device.get(field)
            if value is None:
                continue
            row = Adw.ActionRow(title=title, subtitle=str(value), use_markup=False)
            row.set_subtitle_selectable(True)
            if field in ('name', 'stable_id', 'serial'):
                copy = button('Copy', lambda value=str(value): self.copy_text(value))
                row.add_suffix(copy)
                self.metadata_copy_buttons[field] = copy
            group.add(row)
        group.add(Adw.ActionRow(title='Connection', subtitle=self.connection_summary(), use_markup=False))
        row = Adw.ActionRow(title='Local hardware profiles', subtitle='Save and apply confirmed settings for this mouse.')
        row.add_suffix(button('Open', lambda: self.window.navigate('profiles')))
        group.add(row)
        row = Adw.ActionRow(title='Backup and reset', subtitle='Local configuration files.')
        row.add_suffix(button('Open', lambda: self.window.navigate('configuration')))
        group.add(row)
        row = Adw.ActionRow(title='Feature guide')
        row.add_suffix(button('Start', lambda: self.window.navigate('guide')))
        group.add(row)
        row = Adw.ActionRow(title='Support')
        row.add_suffix(button('Open', self.window.open_support))
        group.add(row)
        self.host_body.append(button('Configure Actions Ring', lambda: self.window.navigate('ring')))
        self.host_body.append(label('Switch requests are sent once. The mouse may disconnect; the app does not assume a successful channel change.', 'mx4-subtle'))

    def callout_specs(self):
        route, window = self.window.route, self.window
        settings = {s['name']: s for s in window.current['settings']}
        config = self.effective_config()
        specs = {}
        if route == 'buttons':
            for cid, (title, anchor, offset) in BUTTONS.items():
                target = f'button:{cid}'
                if target not in self.targets:
                    continue
                action = config['button_bindings'].get(str(cid))
                mapped = settings.get('reprogrammable-keys', {}).get('value') or {}
                actual = mapped.get(str(cid), mapped.get(cid))
                if actual is not None and actual != cid:
                    key = next((key for key in settings['reprogrammable-keys'].get('keys', []) if key['value'] == cid), {})
                    reported = next((choice['label'] for choice in key.get('choices', []) if choice['value'] == actual), None)
                    title = reported if reported and reported != str(actual) else BUTTONS.get(actual, ('Reported mouse function',))[0]
                text = config['actions'].get(action, {}).get('label', title)
                if cid == config['ring_button']:
                    text = 'Show Actions Ring'
                specs[target] = (text, anchor, offset)
            if 'thumb' in self.targets:
                specs['thumb'] = ('Horizontal scroll', 'thumb_wheel', (60, 45))
        if route == 'point':
            for key, title, anchor, offset in [('wheel', 'Scroll wheel', 'main_wheel', (22, -20)),
                                               ('thumb', 'Thumb wheel', 'thumb_wheel', (-160, 0)),
                                               ('pressure', 'Press sensitivity', 'haptic_panel', (-170, 50)),
                                               ('pointer', 'Pointer speed', 'wheel_mode', (45, 75))]:
                if key not in self.targets:
                    continue
                if key == 'pointer':
                    title += f" · {settings['dpi'].get('value', '—')} DPI"
                specs[key] = (title, anchor, offset)
        if route == 'haptics' and 'haptics' in self.targets:
            value = settings.get('haptic-level', {}).get('value')
            specs['haptics'] = (f'Haptic feedback · {value if value is not None else "—"}', 'haptic_panel', (-165, 30))
        return specs

    def update_route(self):
        window = self.window
        route = window.route
        self.back.set_visible(route != 'home')
        self.add_device_button.set_visible(route == 'home')
        self.heading.set_text('MX Master 4' if route in DEVICE_ROUTES else TITLES[route])
        self.stack.set_visible_child_name('device' if route in DEVICE_ROUTES else 'actions' if route in ('ring', 'smart-actions') else route)
        self.context_reveal.set_reveal_child(self.narrow and route in ('buttons', 'point', 'haptics'))
        self.header_context_reveal.set_reveal_child(not self.narrow and route in ('buttons', 'point', 'haptics'))
        if self.inspector is None or not window.current:
            return
        for key, item in self.nav_buttons.items():
            item.add_css_class('active') if key == route else item.remove_css_class('active')
        special = route in ('settings', 'easy-switch')
        specs = self.callout_specs()
        self.art_notice.set_visible(route in ('buttons', 'point', 'haptics') and not specs)
        self.art_notice.set_text('This mouse did not report controls for this page. Wake it and refresh.')
        self.art.set_targets(specs, window.selected_target, underside=route == 'easy-switch')
        self.art.set_indicators(window.current.get('easy_switch', {}).get('current'), window.current.get('online'))
        self.art_preview.set_visible(route in ('point', 'haptics'))
        self.art_preview.set_label('Preview feedback' if route == 'haptics' else 'Preview wheel')
        self.art_preview.set_tooltip_text('Animate the illustration without changing the mouse')
        self.update_battery()
        target = window.selected_target
        show = bool(target) or special
        if self.inspector.get_width() == 0:
            # A context/device rebuild can enter a selected inspector before
            # its first allocation. Set the native split state while unmapped
            # so its reveal animation cannot divide a zero-sized content area.
            visible = self.inspector.get_visible()
            self.inspector.set_visible(False)
            self.inspector.set_show_sidebar(show)
            self.inspector.set_visible(visible)
        else:
            self.inspector.set_show_sidebar(show)
        self.sidebar.set_reveal_child(not bool(target))
        if special:
            target = 'hosts' if route == 'easy-switch' else 'device-settings'
        if target:
            self.inspector_stack.set_visible_child_name(target)
            self.inspector_title.set_text(TARGET_TITLES.get(target, DEVICE_ROUTES.get(route, 'Settings')))

    def preview_art(self):
        if self.window.route == 'haptics':
            self.art.pulse()
        else:
            self.art.spin()

    def update_readback(self):
        config = self.effective_config()
        self.updating_bindings = True
        try:
            for cid, (dropdown, keys) in getattr(self, 'binding_widgets', {}).items():
                assigned = SHOW_RING if cid == config['ring_button'] else config['button_bindings'].get(str(cid))
                dropdown.set_selected(keys.index(assigned) if assigned in keys else 0)
        finally:
            self.updating_bindings = False
        if self.art and self.window.current:
            specs = self.callout_specs()
            self.update_force_state()
            self.art.specs = specs
            self.art.set_selected(self.window.selected_target)
        self.update_home()

    def set_narrow(self, narrow):
        self.narrow = narrow
        if hasattr(self, 'add_device_button'):
            if narrow:
                self.add_device_button.set_icon_name('list-add-symbolic')
                self.add_device_button.set_tooltip_text('Add device')
            else:
                self.add_device_button.set_icon_name(None)
                self.add_device_button.set_label('Add device')
        if hasattr(self, "home_grid"):
            self.home_grid.set_margin_start(0 if narrow else 60)
        if hasattr(self, "home_note"):
            self.home_note.set_visible(not narrow)
        if hasattr(self, 'compact_context'):
            self.context_reveal.set_reveal_child(narrow and self.window.route in ('buttons', 'point', 'haptics'))
            self.header_context_reveal.set_reveal_child(not narrow and self.window.route in ('buttons', 'point', 'haptics'))

    def sync_local_preferences(self):
        if self.window.desktop.config is None:
            return
        config = self.window.desktop.config
        defaults = {'appearance': 'dark', 'haptic_feedback': True, 'haptic_power_save': False, 'adapt_to_app': True}
        for (name, subkey), (widget, handler, setter) in self.preference_widgets.items():
            value = config.get(name, defaults.get(name)) if subkey is None else config.get(name, {}).get(subkey, subkey == 'hover')
            widget.handler_block(handler)
            try:
                setter(value)
            finally:
                widget.handler_unblock(handler)

    def apply_appearance(self, appearance):
        self.style.set_color_scheme({'light': Adw.ColorScheme.FORCE_LIGHT, 'dark': Adw.ColorScheme.FORCE_DARK,
                                     'system': Adw.ColorScheme.DEFAULT}[appearance])
        self.apply_theme_class()

    def apply_theme_class(self, *_):
        if self.style.get_dark():
            self.window.remove_css_class('mx4-light')
        else:
            self.window.add_css_class('mx4-light')

    def close(self):
        if self.art:
            self.art.dispose_art()
        self.style.disconnect(self.style_handler)
        Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), self.provider)
