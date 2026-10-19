# Interface and motion

The supplied OpenCode design reference informs the typography, palette and spacing. This remains a native mouse configuration app: the real mouse model, circular action targets and standard keyboard interaction retain their purpose.

## Typography and surfaces

- JetBrains Mono is the required free alternative approved by the user. Regular and Bold originals ship with their SIL Open Font License; setup verifies or installs the family for the current user. Body text is 14 px, supporting labels 12 px, native page titles 22 px and the home headline 36 px (28 px in compact layouts).
- Light mode uses warm cream `#fdfcfc`, ink `#201d1d` and secondary text `#646262`. Dark mode reverses the warm neutral hierarchy with secondary text `#b9b5b5`. Existing theme preferences are preserved.
- Layout uses 8/12/16/24/32/48 px spacing, fine rules, 4 px control corners and square section boundaries. Text controls are 36 px tall and icon controls 36 px squares (32 px in the header); every button carries a 1 px border so filled, outlined and flat variants align. Primary actions are ink-filled (cream in dark mode); secondary actions are outlined.
- The home mouse card is the single dark surface, modeled on the reference's terminal mockup: device name and battery, the live model, an elevated status line (connection · integration) and key hints. ASCII markers identify home links and numbered device sections. UI chrome uses no gradients, glow or drop shadows; the only shadow is the 3D render's floor contact shadow.
- Blue identifies keyboard focus and active model landmarks. Errors remain distinguishable through inline text and native invalid-state semantics. Ring buttons stay circular so painted shapes agree with pointer targets.

## Motion

| Element | Behavior | Stop condition |
| --- | --- | --- |
| Home mouse model | Direct, unrestricted 3D drag rotation; scroll and +/− zoom; arrows rotate; Home or 0 resets. A released flick coasts with friction | Direct input ends; coasting decays below 0.08 rad/s (about 1 s) |
| Home presentation | 1.3 s ease-out-back entrance turn each time the card is shown; a pointer lean of up to 11° yaw and 8° pitch that follows the cursor; a 12 s ±17° sway after 2.5 s without input, blended in over 1.6 s and updated at 30 fps | Entrance and lean settle; sway runs while visible and enabled; pause, reduced motion or hiding stop it at the displayed pose |
| Device-page mouse model | 650 ms quaternion transition; Haptic Feedback turns sideways toward the thumb panel, Easy-Switch curves backward into an upright underside view | Transition completes; hidden or paused suspends time; system reduced motion snaps |
| Wheel preview | 140 ms camera settle, 450 ms finite wheel sequence, 140 ms camera return | Completion, route change, hidden, paused or reduced motion |
| Haptic illustration | Three expanding, fading outlines over 800 ms | Completion, route change, hidden, paused or reduced motion |
| Page and inspector changes | Native 180–250 ms fades/slides | Native transition completion; system reduced motion snaps |
| Ring | 220 ms orbit trace, fixed root targets, 180 ms shortest-path selection marker, 160 ms group reveal, 100 ms callout fade, 140 ms selected outline; direct held-direction tether and press feedback | Completion or dismissal; paint updates capped at 60 fps; execution is immediate after hiding |
| Assignment preview | Finite entry and selection transitions; arrows retain keyboard focus | Completion, hidden or reduced motion |
| Buttons and fields | 160 ms color/border easing; button contents grow to 1.035× on hover with unchanged layout and hit regions | Transition completes or pointer leaves; reduced motion disables scaling |
| Ring action icons | 160 ms easing to 1.12× on hover or popup selection; native circular faces and hit regions remain fixed | Transition completes or selection leaves; reduced motion disables scaling |

Ring pointer and keyboard selection share an interruptible marker that takes the shortest arc. Buttons remain in their final positions during entry; pressing changes only their tint and icon scale. Reduced motion shows the final selection state directly and keeps the held-direction cue.

Motion never reads the mouse or writes a setting. Preview labels remain still and clickable; surface overlays use model-space landmarks and hide during turns or when their face points away from the viewer. Automatic pose transitions retarget from the displayed pose without jumping. Device-page models stay still when a transition ends so callouts remain registered; only the home card sways. Its presentation is an offset over the user's pose: grabbing, scrolling, keys or reset first absorb the visible offset, so taking hold never makes the model jump. Hiding the viewport excludes hidden time from the animation, and the pause button preserves its choice across workspace rebuilds for the current window. Reduced motion retains direct rotation and zoom while disabling automatic pose interpolation and hover scaling.

## Runtime and asset boundaries

The renderer uses native GTK `GLArea` and an offline GLB loader. It loads the supplied mesh, node transforms and embedded textures locally, with no browser, network request or Three.js dependency. Shading is a procedural photo studio in view space (dark floor, soft ceiling, overhead key box, right strip and rear kicker) evaluated with a split-sum environment BRDF and specular occlusion, plus one direct key highlight and the model's emissive LED. A soft two-lobe floor shadow is placed under the lowest part of the silhouette using a sparse 1,008-point surface sample (about 0.4 ms per frame in Python); the camera stays orthographic so surface anchors remain registered. The home viewport allows full turns around both axes, including upside-down views, and bounds zoom to 0.7–1.6×. Keyboard focus exposes equivalent rotation, zoom and reset controls.

The existing baked product images remain available when the graphics context cannot initialize and for the authored wheel demonstration. The 34 baked camera images add 3.31 MB and share a decoded-texture LRU cache limited to 16 MiB (visible widgets also retain the textures they draw). Active transition updates are capped at 60 fps and their callbacks stop after settling; this cap is not a claim of measured performance on every GPU.

The user-supplied GLB, provenance, camera construction and reproduction tooling are recorded in [reference/model-bake.md](reference/model-bake.md). The model depicts appearance; physical state comes from confirmed device metadata. Font licensing is separate from the application license and is included alongside the bundled fonts.

## Verification

Inspect the actual GTK output in both themes at 400, 620 and 1100 px. Confirm complete home-model turns and reset, sideways and backward page transitions, model-marker registration during rotation, stable callout labels, graphics fallback, readable settings, reachable compact navigation, keyboard ring editing, pause/resume and reduced-motion behavior. Include disposal, idle-render and package-asset checks, not only screenshots. Prior interface evidence is recorded in [ui-polish-validation.md](ui-polish-validation.md); 3D interaction checks are tracked separately in [3d-interaction-validation.md](3d-interaction-validation.md).
