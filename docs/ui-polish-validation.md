# UI polish validation · 2026-10-19

This pass applies the supplied typography and design reference to the native GTK application, with JetBrains Mono as the user-approved free alternative. It adds animated views derived from the supplied mouse GLB, fluid navigation and ring feedback, responsive layouts and accessible motion controls. The visual and runtime choices are documented in [design-system.md](design-system.md).

Environment: Arch Linux, Python 3.14, GTK 4.24.1, Libadwaita 1.10.0, Niri / Wayland. Captures use the application's demo backend and temporary configuration. They contain simulated device state.

## Automated and package checks

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | 1,015 tests and 32 subtests passed |
| `npm test` | Six launcher tests passed |
| Native `scripts/gtk-smoke.py` | Stylesheet, both themes, simulated settings, wheel, ring groups, reduced motion, editor and cleanup passed |
| Ruff, Python compilation, Bash syntax and Git whitespace checks | Passed |
| `npm run test:package` | Full runtime asset inclusion and installed CLI smoke passed |
| Python wheel and source distribution | Built; packaged runtime files compared byte for byte with the working tree |
| Font installation | 13 tests passed, including installation and discovery using real, isolated Fontconfig |

The font installer verifies the actual upright Regular and Bold faces, bundles the original TTFs and OFL license, and requires no network or elevated privileges. A regression reproduces Fontconfig retaining a cached empty parent directory during first installation. Refreshing the parent font directory fixes discovery; the previous child-only refresh fails this regression. Existing installations remain usable if a new setup prerequisite fails.

The Python run emits GTK API deprecation warnings; passing these tests does not establish compatibility with a future GTK major release. Independent source review covered ring focus and settings-listener cleanup, font setup and packaging.

## Native visual and interaction review

All 36 shell captures passed exact-width checks: six views at 400, 620 and 1100 px in both themes. The inspected views include home, buttons, wheel controls, a settings inspector, underside and application settings. Separate captures cover the action editor, ring assignments, validation errors and ring groups. Narrow layouts preserve access to every route through compact navigation. Long action names remain readable without forcing the window wider.

| Preview | Image |
| --- | --- |
| Home, light | [home-light.png](assets/ui-polish/home-light.png) |
| Home, dark | [home-dark.png](assets/ui-polish/home-dark.png) |
| Buttons, 400 px | [compact-buttons.png](assets/ui-polish/compact-buttons.png) |
| Ring editor, 620 px | [ring-editor.png](assets/ui-polish/ring-editor.png) |
| Expanded ring group | [ring-group.png](assets/ui-polish/ring-group.png) |

Native render samples confirm that the camera changes over time and that surface landmarks track it while callout controls remain stable. Wheel motion settles the camera before its finite preview, and the haptic illustration completes its ripples. Pause persists across workspace rebuilds. Hidden or disposed mouse views stop their callbacks; reduced motion suppresses ambient animation. Ring and assignment-preview callbacks stop after their finite transitions. No setting reads or writes occur as a consequence of animation.

Keyboard checks cover visible focus, compact navigation, ring selection with arrows/Home/End/numbers, explicit Enter/Space activation, editor save and validation feedback. Ring navigation retains focus until activation. Representative text/background contrast pairs measure 16.34:1 for primary light text, 5.92:1 for secondary light text and 8.24:1 for secondary dark text; this is not an exhaustive accessibility audit.

## Scope

These are local UI, lifecycle and packaging results. This pass did not write physical device settings, divert buttons, execute user routines or publish a release. Existing hardware and platform evidence remains separate in [validation.md](validation.md) and [reference rebuild evidence](../evidence/reference-rebuild/). Bluetooth, wake/reconnect and other compositor behavior require their own release checks. The 60 fps animation limit is a scheduling cap, not a hardware performance benchmark.
