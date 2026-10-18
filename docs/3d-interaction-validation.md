# 3D interaction validation · 2026-10-19

The home, Haptic Feedback and Easy-Switch views now use the exact supplied
`mxmaster4mac-whitesilver-ar-360.glb` through native GTK `GLArea`. Its 64,001
triangles, eight mesh/material groups and embedded texture data are retained;
there is no geometry simplification. The runtime copy has SHA-256
`305d2b776c0c0d064406253e13c9f222b01b55abf68667bb93ceb588e4e4fd35`, matching the
supplied file byte for byte.

## Interaction

- Home: drag in any direction for full rotation, scroll or +/− to zoom, arrow
  keys to rotate, Home/0 or Reset view to return. Model manipulation does not
  navigate; Customize mouse is a separate button.
- Haptic Feedback: a finite 650 ms turn reveals the thumb-side panel.
- Easy-Switch: a continuous backward turn settles into the upright underside
  reference. Switch computer opens the host selector; closing it preserves the
  underside. This keeps the animation visible on compact windows.
- Hover: ordinary button contents grow by 3.5%; ring icons grow by 12% while
  their circular faces and input targets retain their original size.

The renderer uses original base-color, normal and material textures, neutral
studio lighting and up to 4× multisampling. Reduced motion skips automatic turns
and hover scaling while preserving direct manipulation. Transitions suspend
when hidden or paused and stop scheduling frames when complete. Static artwork
remains available if OpenGL cannot initialize; the existing finite wheel demo
retains its baked frames.

## Checks

The final full suite passed **1,059 tests and 32 subtests**. The focused run
passed 48 model-loader, GPU-renderer and motion tests plus 49 shell-navigation
tests. It exercises all-direction turns, finite backward paths,
interruption and hidden time, reduced motion, zoom limits, projected controls,
GL failures, context recreation, cleanup, and the compact computer-selector flow.
All six npm tests passed, as did Ruff, Python compilation, changed-document
links and whitespace checks. The Python suite reports existing GTK/PyGObject
deprecation warnings.

Native pixel tests verify that actual model rotation changes the rendered image
and that multisampling produces partial-coverage edges. Independent callback
instrumentation observed no renders during 300 ms after a transition settled,
or while the view was hidden. GTK smoke checks pass without the initial callout
allocation warning: readiness updates now run after the render callback.

Python/npm packaging includes the runtime GLB and all three new renderer/motion
modules. The wheel and source archive match all 84 runtime modules/assets byte
for byte. The 365-file npm package passes installed-CLI smoke testing. The native checks use simulated
device data and do not write hardware settings or execute user routines.

## Native visual review

The final review produced 47 captures at exact 400×740, 620×740 and 1100×800
window sizes in both themes: 18 page views, 18 explicit hover states, six host
selectors and five frames of the backward turn. All model views initialized
successfully with 4× multisampling. The settled views parked their animations;
hover left input bounds unchanged. Inspected frames showed no clipping or
misplaced controls. The host selector opens through the real callout and closes
back to the underside, including at 400 pixels.

Representative captures: [home](assets/3d-interaction/home.png),
[Haptic Feedback](assets/3d-interaction/haptics.png),
[Easy-Switch](assets/3d-interaction/easy-switch.png), and
[compact Easy-Switch](assets/3d-interaction/easy-switch-compact.png).
Capture metadata is retained in [results.json](assets/3d-interaction/results.json).
