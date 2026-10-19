# Changelog

User-visible changes are recorded here. This project follows semantic versioning; 0.x releases may change configuration or behavior with migration notes.

## [Unreleased]

### Added

- Linux npm launcher with explicit per-user Python setup, bundled application sources and icons, and `npm start` support.
- Separate Mouse, Actions and Profiles views, an animated wheel preview, and clearer ring and Smart Actions controls.
- Compact circular icon ring with animated radial opening, outward hover labels, a center close button and a live labeled editor preview.
- Optional side menus containing up to six existing routines, with pointer and keyboard selection and safe held-button release.
- Finite ring transitions and selection feedback that honor the desktop's reduced-motion preference.
- GitHub CI for Python 3.11/3.14, Node 22/24, package installation and native GTK smoke checks; validated release archives and checksums.
- Development checks, contribution conventions and issue/PR templates.
- Home mouse model presentation: an entrance turn, a slow idle sway, pointer-follow lean and coasting after a released drag; all pause with the animation toggle and honor reduced motion.

### Improved

- The Actions Ring now looks and moves like the Logitech Options+ ring: a compact ring of white faces with dark icons that bursts from the center, a hovered face that turns dark, a center dot that is red while nothing would be chosen, white pill labels that fade in after a short pause, group children that grow from dots along an outer arc, and a collapse into the center after choosing or cancelling. The duplicate native tooltip beside the ring label is gone.
- Enabled mouse integration recovers automatically after reconnecting the same mouse, including interrupted reporting cleanup, with retries capped at 30-second intervals.
- One panel press opens the Actions Ring for click selection; releasing the panel never runs an action. The bottom instruction bar is removed.
- Place the Wayland Actions Ring at the current pointer on its monitor. Near screen edges and corners only the visible circles are kept on screen, so the ring stays close to the pointer, and groups turn inward.
- Right-clicking outside the Actions Ring circles, or on its center, closes the ring without choosing an action.
- Keep Python dependencies in `pyproject.toml` and share the packaged SVG icon with the README.
- Detect unused imports in development and CI checks.
- Setting changes update the confirmed value without a complete follow-up discovery scan; saving profiles keeps existing device data.
- Ring rendering reuses native labels and controls, reducing work during animation.
- Hardware writes and listener startup address the selected receiver slot; setting writes construct only the needed capabilities while preserving live validation and readback.
- Product-studio rendering of the 3D mouse: environment reflections, specular occlusion, the emissive status LED and a soft contact shadow that follows the real silhouette.
- Home card restyled as a status surface with key hints; paired primary/secondary actions, uniform 36 px controls and aligned sidebar, footer and inspector controls.
- No-device, access-denied and asleep states show the live model, a status line, numbered next steps, Check again / Setup guide and collapsible diagnostics; device-only header controls hide while no mouse is awake.
- Bounded optional paired-node discovery removes repeated one-second waits; event bursts share a GTK dispatch while preserving movement and release order.

## [0.2.0] — unreleased baseline

- Native GTK 4 / Libadwaita control panel and CLI using Solaar 1.1.20.
- Supported settings, diagnostics, haptic playback and manual hardware profiles.
- Eight-position Actions Ring, Smart Actions editor and opt-in extra-button bindings for Niri on Wayland.
- Cancellable action execution, focus restoration and exact event-reporting cleanup.
- Optional native wtype helper, separate hidraw/uinput access installers, and application icon.

Physical settings, panel interaction, haptic feel, Bluetooth and reconnect behavior still need interactive validation. Flow remains deferred.

[Unreleased]: https://github.com/Dijo-404/mx4-linux/commits/main
[0.2.0]: https://github.com/Dijo-404/mx4-linux
