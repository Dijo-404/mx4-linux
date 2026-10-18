# New Windows GUI reference audit

Read-only audit, 2026-10-18. No application actions, hardware writes, SSH interactions, or project-code edits were performed. All 221 local PNG references were inspected on labeled contact sheets; representative originals were opened at their actual resolution for layout and text. The user explicitly requested UI like this folder, so these Windows captures now provide stronger shell/layout evidence than the earlier cropped macOS video. Flow remains explicitly deferred.

## Evidence inventory and retained analysis artifacts

Source directory: `/home/dj/Projects/mx4-linux/docs/reference/img`.

- 202 new Windows application captures, all 964×548.
- 19 older cropped video frames, 720px wide and varying heights. They remain useful for the actual popup/group behavior and motion.
- Full filename/dimension inventory: `/tmp/mx4-new-reference-inventory.tsv` (indices000–220).
- Twelve labeled contact sheets: `/tmp/mx4-new-reference-sheets/sheet-00.jpg` through `sheet-11.jpg`. Each has up to20 images,4 columns×5 rows. Scroll captures were compared before being grouped; they expose real additional fields/categories rather than arbitrary duplicates.
- Cropped analysis-only right panels plus OCR: `/tmp/mx4-new-reference-ocr/`. OCR used the available Afrikaans Latin model and is fallible; labels below were cross-checked visually on originals/contact sheets. Do not treat OCR as exact vendor schema.

| Family | New images | Distinct content reviewed |
|---|---:|---|
|00 Home|6|Home, utility tooltips, device hover|
|01 Buttons|45|Overview; seven target selections; recommended and expanded action lists; horizontal-wheel alternatives|
|02 Point/Scroll/Press|8|Overview; scroll wheel and lower controls; thumb wheel; pressure; pointer speed|
|03 Haptics|6|Overview; main/lower policy panels; empty plugin list|
|04 Easy-Switch|1|Underside mouse and three channels|
|05 Flow|2|Welcome and setup; deferred|
|06 Device Settings/tour|13|About/defaults/remove/backup;10 tour states|
|07 Context/tooltips|10|Global/battery/support tooltip and application selection with scroll|
|08 Add device|1|Connection-type selection|
|09 Smart Actions|38|Templates, scrolling/filter/sort, Manage, labels, login gate,6 tour states|
|10 Actions Ring|49|Layout/customize/catalog/plugins/profiles/search/settings/tour|
|11 Marketplace|6|Plugins/icons/profiles/manage/menu/settings|
|12 Account|1|Login modal|
|13 App Settings|16|General/theme/language/support/rating/service/notifications; one mislabeled wallpaper|

## Strongest design findings

The reference is a sparse desktop utility, not a dense dashboard. Its primary device experience is a large original-looking mouse workspace with small anchored callout labels, plus a left section navigation. Choosing a callout switches to an editing state: the section navigation disappears, the mouse shifts left, the context selector remains at the top, and a fixed-width inspector opens on the right. This is a more important visual match than copying individual sliders into rows.

The shell has two navigation levels:

1. Home: greeting, large device tile/status and top utilities for Add Device, Smart Actions, Actions Ring, Account, Application Settings.
2. Device: back arrow and device name, left sections, application/global context at top right, device workspace, bottom-left battery/transport pill and bottom-right support icon.

Actions Ring and Smart Actions are separate top-level workspaces reached from Home and cross-links. They are not merely two preferences groups under the mouse. A Linux shell can use the same hierarchy within one native window/stack and keep accessible navigation available at small widths.

## Visual measurements (observed at964×548)

These are screenshot pixels, not prescribed GTK logical dimensions. Preserve relative geometry while allowing readable native text, resizing and system scale.

| Element | Observed geometry/style |
|---|---|
|Window|964×548, subtly rounded outer corners, plain dark background; thin neutral outline|
|Main background|Exact flat sample RGB25,25,25 = `#191919`|
|Accent|Selected button sample RGB0,234,208 = `#00ead0`; near-black text/icon on accent|
|Secondary surface|`#333333` for expanded detail/group surfaces and inactive radio circles|
|Catalog expanded heading|RGB204,251,246 = `#ccfbf6`, dark text|
|Catalog/action cards|Black fill, thin gray border, small corner radius; neutral white icon/text|
|Heading|Device/title approx26px, heavy weight; inspector titles20–22px; body14–16px; menu/callout12–14px|
|Font|Rounded sans appearance, but exact family is not evidenced. Use native system sans; do not assert Segoe or import web fonts|
|Main title|Back arrow aroundx62,y58; title startsx86; top inset~45px|
|Sidebar|Startsx47; labels/icons throughx254. Selected item has turquoise rounded rectangle~40px high; rows spaced~56px apart|
|Overview mouse|About205×270px, centered aroundx556,y292; tasteful soft shadow, substantial negative space|
|Editing state|Right inspector beginsx643 and occupies321px (one third). Main mouse recenters aroundx325. Inspector title/header~70px high; form body independently scrolls|
|Inspector padding|~24–32px; Search row~278×48px; selected action row~285×47px|
|Callouts|Small dark fill,1px neutral border,~28px high; anchored white circular markers; selected callout+marker turquoise. Slight shadow. Do not recreate screenshot label overlap/clipping|
|Context toolbar|Global four-circle icon plus underline, divider, Add Application; top right of overview and top of editing canvas|
|Battery pill|Bottom left aroundx58,y481,102×38px; black surface, thin gray border, green percent/battery, white Bluetooth glyph. Tooltip adds connection detail|
|Support|Bottom right~50×50px bordered icon button; tooltips carry visible meaning|
|Ring editor|Canvasx0–642; catalogx643–963. Context/profile picker centered near canvas top right; ring centeredaroundx321,y299; root radius~76px; action circles~42px; centerclose~26px; outward label cards; bottom Undo/Redo and Help/Settings icons|
|Light/theme|App Settings offers Follow OS Theme, Light Theme, Dark Theme as three visual cards. Active dark card has accent outline/check. Only dark full application screens are supplied|

Native implementation should use a few central semantic color tokens, not inline arbitrary per-widget styles. Default follow-system must remain possible. The accent/background values describe supplied dark UI, not permission to reduce light-mode contrast. At620px widths, collapse sidebar and stack the inspector below/behind the workspace; keep the selected control and back route visible. A permanent minimum-width321px inspector plus large canvas will overflow.

## Screen and interaction map

All filenames below are relative to `docs/reference/img/` unless marked older video.

### Home and connection

`00_home.png`, `00a`–`00e` show:

- Good Evening heading; Add Device; Smart Actions; Actions Ring; Account; Settings.
- Large mouse device card with battery100% and Bluetooth. Device hover is a selectable tile state; there is no proof of a second mouse.
- A promotional GitHub Copilot offer with Discover and dismiss. This is vendor advertising, not a feature that helps control the mouse; use the equivalent space for actual local device/integration status or a local getting-started card.
- `08_add_device.png`: Select connection type, Bluetooth row, and note to plug in receiver or use Bluetooth. A Linux equivalent can open the real system Bluetooth settings and rescan; do not simulate pairing or modify channels automatically.

### Buttons and applications

`01_buttons.png` is the key shell target. Sidebar labels are Buttons; Point, Scroll, Press; Haptic Feedback; Easy-Switch; Flow; Settings. Do not implement Flow during this scope.

The mouse callouts are Middle button, Print screen, Show Actions Ring (haptic panel), Forward, Horizontal scroll (thumb wheel), Back, Shift wheel mode. Screenshot callout words describe the current assignment; they are not proof of the HID++ control ID. Continue using verified IDs and avoid implying the thumb wheel is an ordinary button.

Selecting a button opens `01a_middle_button.png` style inspector titled Actions. It has Search and Recommended, with a radio-like current selection. Expanded lists use Recommended, Smart Actions, Other Actions. Examples of recommended alternatives vary by target: middle/shift-wheel/task view/show desktop/gestures/keyboard shortcut/Spotlight Effects/Show Actions Ring. Selected Show Actions Ring expands into explanatory copy and a working Configure Actions Ring link (`01c_show_actions_ring.png`).

The repeated expanded captures reveal these ordinary action labels: Action center, Advanced click, Back, Brightness down/up, Calculator, Change pointer speed, Close window, Copy/Cut/Paste, Desktop left/right, Dictation, Do nothing, Emoji, Emojis menu, Forward, Input language, Lock, Maximize/Minimize window, Mute/Unmute speaker, New browser tab, Next/Previous, Open application/file/folder/web page, Play/Pause, Print screen, Redo/Undo, Right Ctrl, Screen capture, Screen snip, Shift wheel mode, Switch application, This PC, Volume down/up, Windows Copilot, Zoom in/out. Screenshot labels such as Spotlight Effects and Advanced click do not establish their complete behavior. Windows-only actions need an actual Linux counterpart or a truthful availability explanation.

`01e_horizontal_scroll.png`/`scroll1` has a separate thumb-wheel action list: Horizontal scroll, Zoom in/out, Volume up/down, Navigate between tabs, Keyboard shortcut; Other Actions includes Brightness up/down, Do nothing, Forward/Back, Navigate between desktops, Next/Previous, Switch between windows. The plan currently provides thumb direction and overlay adjustment, not global physical thumb-wheel action diversion; keep that limitation explicit unless implemented and verified.

`07d_add_application.png` through scroll6: right-side Select Applications panel, Global settings selected, Predefined Customizations and Other Applications with checkbox/application icon/name rows. `07a` tooltip names Global settings. Linux profile matching must use real observed Wayland app IDs, not assume desktop-file IDs match. Gio launcher discovery is useful for launch selection; manual exact ID/Use Current App supports context selection.

### Point, Scroll, Press

`02_point_scroll_press.png` shows four callouts with summary values: Scroll wheel; Thumb wheel; Button press sensitivity; Pointer speed. Clicking each opens its inspector while retaining all summaries on the mouse.

- `02a_scroll_wheel.png` and lower captures: Scroll direction Inverted/Standard; Scrolling force with percentage/slider; Smooth scrolling switch and explanatory text; SmartShift switch; Sensitivity Value percentage slider. Verified Linux mapping must come from the actual setting meaning. Do not relabel raw diversion/reporting bits as smooth scroll speed.
- `02b_thumb_wheel.png`: Thumb wheel speed40% and Thumb wheel direction Default/Inverted. Only direction is established in the current safe setting allowlist. A dead speed slider would be misleading.
- `02c_button_press_sensitivity.png`: Light, Medium, Hard, Firm; orange warning icon on current Light. This is UI evidence of four vendor choices, not proof of four hardware threshold values. Current hardware reads established min/default/max only, so Linux Light/Medium/Firm plus advanced validated raw value is accurate; do not invent Hard.
- `02d_pointer_speed.png`: Extend sensor range to8K DPI toggle; Pointer speed2300DPI slider. Safe Linux DPI descriptor already exposes its range. An extra toggle can be represented only if it has actual behavior; do not fake a separate sensor-mode hardware flag.

### Haptic Feedback

`03_haptic_feedback.png`: one Haptic feedback callout summarizing Feedback On, Intensity Medium, Battery saving mode Off. `03a...` inspector:

- Haptic feedback master switch.
- Haptic intensity: Subtle, Low, Medium(Default), High.
- Enable haptic for app plugins expander; empty compatible-app explanation; Install and Uninstall Plugins link; `03b_install_plugins.png` shows a genuine empty state, no installed haptic plugins, Find More From Marketplace button.
- Haptic feedback for Windows OS explanatory card, followed by Actions Ring switch+Open Actions Ring, Smart Actions+Open Smart Actions, Gesture switch, Switch Screens switch, Battery saving mode switch.
- Battery saving copy says below10%; recovered software predicate and current plan use numeric<=10%. Keep numeric validation/freshness from runtime rather than string parsing.

Local Linux ring open/hover/select/complete policy is a valid implementation, but it is a different source set from vendor OS/plugin/gesture/display-crossing integration. Label it local feedback and do not claim Windows OS haptics, gesture feedback or monitor-crossing feedback from an enabled-looking switch. Continuous strength0–100 and supported waveforms are verified; preset strengths must be actual supported/readback-backed values.

### Easy-Switch

`04_easy_switch.png`: underside mouse rendering; three channel rows, current row turquoise, other paired row white outline, unpaired row dim; name/OS/transport metadata; Easy-Switch with Actions Ring description and Configure Actions Ring cross-link. Screenshot repeats one hostname on two channels; no need to retain it in reports.

Use actual reported names/current/paired info. Fresh explicit channel request can disconnect the mouse. Do not report successful switch without evidence. Keep the stop-and-wait-for-reporting-restoration contract from Task4. Screens do not prove a safe write/ACK protocol.

### Device Settings and onboarding

`06_settings.png`/scroll1/scroll2:

- About: firmware version, update date/status, Check For Update; serial with Copy; support link; Launch Feature Tour.
- General: Swap left/right buttons (heading visible; full control not fully captured).
- Other: Device backup login/cloud, Restore to Default, Remove device/unpair explanation.

These reveal additional vendor UI, not authorized guessed factory reset/firmware/unpair APIs. Provide actual local config import/export/reset and metadata; label firmware status only if queried. A local support/tour/keyboard guide can work offline. Avoid an inert update button or false latest-version claim.

`06a_feature_tour_1`–`10_all_set` show an optional skippable tour: Meet MX Master4; pointer speed; wheel modes; gestures; Haptic Sense Panel; haptic intensity; Actions Ring; recommended profiles/plugins; completion/restart. Start/end use full turquoise background; middle steps use large art left, explanation/control card right, dots at bottom, Back/Next and Skip. The tour is an observed workflow currently missing from Tasks5–7. A concise native optional guide can match this structure without vendor login/plugin installation.

### Smart Actions

`09_smart_actions.png`:

- Header with Back, Smart Actions title, Search, Import, prominent Create.
- Left Templates/Manage navigation and Feature Tour.
- Dismissible turquoise explanation banner.
- Filter chips All/Trending/Productivity/Meetings/AI/Leisure/For designers/For developers; horizontal overflow arrow; sort Most Recent/Alphabetic (`09a`).
- Two-column black template cards, workflow/app icons, title, description, category chip, Add/Get.
- Manage page (`09c`) shows separate help text, labels/filter management (`09d` modal: Create new label, Confirm) and an empty state.
- Create (`09e`) is gated by Login in this vendor installation. **No real Smart Action routine form is captured**; Linux's existing local structured builder should remain usable without recreating the account gate.

Observed template titles include Work Mode, Meeting Mode, Work Mode Google Suite/MS Suite, Select & Search The Web, Take A Break, Shop For Less, Ask Perplexity, Adobe Firefly, Open Copilot on my browser, Google Gemini, AI Reply to a Message, AI Correct Grammar, AI Summary, AI Translate, AI Explain Code, AI Tell a Mouse Joke, Be right back, Morning Setup, Relax Time, Check the latest scores, Watch the game live, AI Rephrase Text, AI Create Email, Pull project changes, Open URLs, Meeting Helper, Search in Slack, Check Uncommitted Code Changes, Rebase the master, Easy Speed Change(Premiere Pro), Set In and Out Points(Premiere Pro). These names are screenshots, not verified scripts. Do not ship a destructive Git rebase template or pretend native cloud prompt integration because its card appears here. Generic local website, launcher and shortcut templates can use the same card/search/filter workflow.

`09f` tour shows introduction, login/later gate, explanation, template gallery, create your own and completion. Exact delays cannot be recovered from the stills.

### Actions Ring layout/editor/catalog/profiles

`10_actions_ring.png`: Layout/Settings sidebar with a large Customize Ring CTA over a blurred ring. The CTA leads to the full editor (`10a`), rather than presenting every preference at once.

`10a_actions_ring_customize.png`: compact8-root ring with separate black circles, centerX and outward labels. Current Windows default labels: Play/Pause; New Note; Explore AI; Lock Workstation; Logi Options+; Windows Screenshot; Emoji; Windows Explorer. Left/right platform defaults differ from the macOS video. The Linux defaults should be honest local counterparts and configurable.

Editor toolbar: Global/profile icon dropdown, Add profile plus; catalog All Actions dropdown, Marketplace bag, Search. Canvas bottom Undo/Redo icons (currently gray), Help, Settings. No evidence that disabled Undo/Redo works until edits exist; if implemented, it must undo actual draft state, not just recolor the icon.

`10g` profile selector current Default General Profile/check plus Add Profile. `10h` menu: Rename, Duplicate, Export, Remove. `10i`: General Profile / Application Profile / Import Profile cards, Cancel/Exit. `10j` selection page: Search and app-icon grid, supported/other applications, Select another application at end. This strengthens the Task6 need for duplicate and profile-specific export/import, not only rename/delete.

`10l_ring_settings_popover.png`: Adapt to App switch with explanation that active application controls assignments. Current model resolves app context automatically but has no explicit persisted adapt-on/off flag. A real toggle should change resolution behavior and be testable; do not add a decorative switch.

`10m_ring_search.png`: right panel becomes action search; close restores catalog; empty input displays Search any action. Search should retain canvas/selection context.

`10k` Help menu: How To submenu Device Feature Info / Customization Tips / How To Video Library; Support; What's New. A local keyboard/help/guide equivalent suffices where vendor links/services do not exist.

`10n_actions_ring_settings.png`: General Support/Feature Tour, Appearance Size Small/Medium(Default)/Large. Map presets to actual ring_scale values75/100/150 while retaining advanced model bounds if desired.

Catalog: expanders with pale turquoise selected headings, gray body, individual black action rows, leading white icons, small adjustment affordance where supported. Selecting All Actions (`10c`) changes to installed plugin cards System Actions and Actions Ring; System menu opens Plugin Settings modal (`10d/10e`) with version/author/Show more/Homepage. This is a vendor plugin management shell, not evidence Python should load arbitrary vendor binaries.

Observed System Actions categories and rows across all10b scroll states:

- Media & Volume: System Volume, Toggle Mute, Volume Up/Down, Play/Pause, Next/Previous Track, Stop, Play Sound.
- Open: Open Application, Open File, Web Page, Windows Explorer, Windows Settings, Windows Desktop, Windows Settings Application…, Open Folder.
- Advanced: Windows Run, Run; later Multi-action(macro), Multi-toggle, Custom Adjustment. These are under different nested groups in the screenshots; avoid assuming one flat duplicate key.
- Navigation: Activate Menu Bar; Desktop: Add/Next/Previous/Close Desktop.
- Utilities: Windows Screenshot, Windows Search, Magnifier, Windows Actions.
- System: Screen Brightness, Reset Screen Brightness, Lock Workstation.
- Mouse: Mouse Click, Mouse Scroll, Move Mouse Cursor.
- Keyboard: Keyboard Shortcut, Paste Text, Emoji; Advanced: Paste Unicode Characters, Keyboard Layout.
- Date and Time: Date, Day Of Year, Moon Phase, Week Number.
- Widgets: Beats Per Minute, Bell, Stopwatch.
- Clipboard: Clear Clipboard, Unformat Clipboard, Make Clipboard Lowercase/Sentence…/Title…/Uppercase, Toggle Clipboard Case, URL Encode/Decode Clipboard, HTML Encode/Decode Clipboard.
- Easy-Switch: Easy-Switch1 Device,2 Devices,3 Devices; Media Toggle Mute repeated in plugin/category context.

`10f_ring_plugin_actions_ring_expanded.png`: Device Navigation→Switch Profile; Folders→Folder; Adapt to App On/Off. Folder/group action is already in the model. Dynamic profile-switch and adapt-toggle actions are newly observed, not yet provided by33 generic presets.

Older `15-explore-ai-nested-ring.png`: selected parent on left plus six ordered child positions to right; four actions and two Add slots; root/application context persists. All eight root plus six children should remain reachable without drag-only controls.

### Ring interaction and animation evidence

The new captures are stills; they cannot establish exact easing, spring constants, panel-slide duration, hover delays, or click/release semantics. Do not label guessed values recovered.

The older62.9s video provides real temporal evidence: root ring expands from center about0.2s at46.1–46.3, child arc reveals about0.2s near47s, hovered item darkens and tooltip appears, selecting a child opens its ordinary website. Existing180msroot/160msgroup/100mslabel native animations remain reasonable evidence-aligned approximations. The former300ms editor timers from static vendor code do not establish all livepopup timing.

**Additional written behavior established by new tour screenshots**:

- `10o_actions_ring_tour_2`: Open Actions Ring with button press; assign Show Actions Ring on device configuration page.
- `...tour_3`: use cursor to select and click an action.
- `...tour_4`: adjustable actions support **hover and scroll** and **click-and-drag left/right**. This is clear source text, though no actual drag movement/timing is captured. Implement both for adjustable root/child targets with bounded work, focus correctness, and duplicate selection/release suppression.
- `...tour_5`: automatic app adaptation, default/custom application profiles.
- `...tour_6`: optional plugin login/install vs Continue Without Plugins; Linux requires no vendorlogin.

For shell transitions, recommended native approximation (design judgment, not recovered timings):180–220ms ease-out between overview and selected inspector, preserving spatial continuity of the mouse;120–160ms hover/selected-state opacity;150–200ms stack crossfade or small slide for same-level pages. Avoid huge fly-in motion, constant animation, or animating hardware values before confirmed readback. Rapid navigation cancels/replaces prior visual motion and sets semantic state immediately. Honor `gtk-enable-animations`; reduced motion presents final positions immediately. Buttons, page state, cancel/quit and error logic must not depend on animation completion.

### Marketplace, account and Application Settings boundaries

`11_marketplace_*` are separate titled Windows windows, with blue rather than main-shell turquoise accent. Profiles/Plugins/Icons tabs, Discover categories, Search, sort, Install cards; Manage empty state; color theme Light/Dark/System. These are vendor content/service workflows. No compatible Linux plugin ABI/install/auth contract is established; preserve honest local preset/icon selection instead of a fake store.

`12_account.png`: Account modal with Login/Create account and offers; there is no local login capability to rebuild from static frames. Do not require login for local assignments.

`13_app_settings*`: left General / Feedback & Support / Logi Services / Notifications / Privacy & Data. General version/check update/automatic updates, system language menu, three appearance cards. Language-menu scrolls show a multilingual vendor list; implementing only a menu without translated UI would be cosmetic.

Feedback & Support provides feedback/rate/support/Bluetooth report UI; Logi Services has background plugin service, Restart and On-Screen Overlay; Notifications shows star-rating/service warnings, low battery, lock-key and mic overlay switches. These require actual service/observer infrastructure before claiming parity. Local background integration/restart, device status and ring feedback settings provide meaningful counterparts. Do not imply a running Logi Plugin Service on Linux.

**Bad evidence file:** `13e_app_settings_privacy_data.png` is solely desktop wallpaper. It contains no app privacy UI. Do not invent privacy controls or claim they were observed.

## Gaps and priority changes against current Tasks5–7

### P0: satisfy the explicit new visual request

1. Add a genuine Home/device overview and top utility entrypoints, then a reference-shaped device sidebar/workspace. Current Task5's singleflat9-page sidebar is functionally useful but does not fully resemble the new shell. Keep secondary hardware profiles reachable without crowding the reference primary sidebar.
2. Build the selected-callout editing state with central mouse repositioning and right inspector. Existing separate editor can handle deep routine building, but basic mouse binding/scroll/haptic editing should preserve the reference visual context. Hotspots must enter real selected-target forms.
3. Use dark charcoal/turquoise style tokens, clear large mouse artwork, compact anchored labels and substantial negative space. Generic boxed-list pages alone will miss the supplied appearance.
4. Ring editor must prioritize canvas+catalog, profile/context toolbar, selected groups and custom label/icon forms; keep its ring distinct from native overlay colors. Preserve responsive minimum size and keyboard paths.
5. Carry actual native animations through page/inspector/ring/tooltip states with reduced motion; document approximated timing vs measuredvideo.

### P1: newly evidenced functional workflows that fit local implementation

- Task3: horizontal click-drag adjustment as well as wheel, with root/child tests and no second click action on release.
- Task6/model/runtime: persisted Adapt to App enable/disable and a working equivalent in ring settings; General fallback when off. The current optional model fields do not include this.
- Task6/profile management: Duplicate plus profile-specific Import/Export, not just global config import/export. Support Default General/App/Import entry flow and preserve all shared action references.
- Task5/7: optional skippable local feature tour/help with real navigation/cross-links, no login gate; restart accessible in Settings.
- Task6/7 Smart Actions: visibly separate Templates/Manage, searchable local templates and routine list, Import/Create, category/sort if offered. Never execute on selecting/editing.
- Task6: draft Undo/Redo if icon controls are included; otherwise omit unavailable icons.
- Task5/7: metadata copy, actual support/help, appearance cards. Implement only real behavior for update/Bluetooth buttons.
- Task6catalog: new observed Switch Profile and Adapt On/Off actions need local state transitions (not shell commands), or clear availability. Existing33presets do not equal full screenshot catalog.

### P2: larger platform work; keep evidence/availability explicit

- Global physical thumb-wheel reassignments and speed, gesture/task-view/screen-crossing hooks, left/right swap, synthetic mouse clicks/scroll/move on Wayland, clipboard transforms, date/time/widgets, OS haptic policy, plugin service, marketplace install/auth, firmware update/defaults/unpair, translated language packs are beyond current verified primitives.
- Some simple local date/time/clipboard/stopwatch counterparts may be implementable, but they need actual APIs, limits, cancellation/privacy behavior and tests. They must not appear successful from inert rows.
- Flow stays deferred by explicit user instruction. No need to create its setup flow or networking service.

## Verification additions

- Compare actual GTK screenshots at1100×800 and at the source-like964×548; also620px narrow mode. Check Home, Buttons overview, selected control inspector, Point/Scroll/Press, Haptics, Easy-Switch, SmartActions templates/manage, ring editor/root/group/catalog search, theme settings. Native UI should be visibly recognizable from these references.
- At each state verify focused keyboard route and back/navigation path, no overlapping callouts, no cut-off inspector final rows, selected labels independent of color, real busy/readback/error behavior.
- Verify context/profile selectors while an incomplete draft exists; disable Adapt to App and prove General behavior; duplicate/import/export preserves references; source-task cancellation cannot leak old application binding.
- Test wheel and horizontaldrag adjustments on roots/children; close/context-cancel clears queued work; clicking+dragging does not run the ordinary action on release; previews never launch.
- Test rapid page/inspector changes while animations run and with animations disabled. Required state updates must not await a transition callback.
- Take cropped app-only screenshots without retaining unrelated private desktop windows. Mock hardware for visual coverage, label demo evidence separately from actual device readback.

## Evidence limits

Only local supplied stills and previously reviewed video/static findings were used. No new remote Windows UI inspection occurred; SSH was unnecessary to read supplied screenshots. No new live mouse checks were attempted. Font family, shell animation timing/easing, full privacy settings, genuine Smart Actions creation form, vendor account/store ABI and unobserved hardware capabilities remain unestablished.
