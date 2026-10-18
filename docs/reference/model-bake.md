# User supplied MX Master 4 model: asset preparation

The current interface loads the byte-identical supplied GLB through native
`Gtk.GLArea` for free rotation and page transitions. The baked assets below are
retained as a graphics fallback and for the finite wheel preview. The original
camera preparation notes describe the earlier image-only implementation; see
[the current design system](../design-system.md) for the runtime renderer.

## Native animated camera presentation

The interface now also ships 34 transparent 512×512 camera samples, 17 for each
view, in `orbit-*.png` and `orbit.json` (3,314,185 PNG bytes). These use the same
supplied geometry, original materials, environment, target and framing as the
approved stills. The camera travels ±9° around world Y. The source file contains
no animation tracks; the camera path is authored presentation, not device motion
telemetry. The source asset's provenance and rights limitations below still apply.

GTK interpolates adjacent textures and the corresponding reprojected source-space
surface anchors together. The button labels stay still, while surface markers,
curved leaders and underside indicators follow the visible geometry. Camera drift
has a 12-second period and damped pointer steering on the detail view. Playback
can pause at any pose, respects the desktop reduced-motion preference, and stops
its frame-clock callback when hidden. Resuming does not count hidden elapsed time.
The wheel demonstration briefly settles the camera, plays the existing bounded
wheel sequence, and restores the camera smoothly.

The shared decoded-texture LRU cache has a 16 MiB budget; widgets additionally
retain their currently drawn textures. There is no runtime Three.js, WebKit,
network request or GLB parser. This is a bounded native presentation rather than
a free camera for arbitrary 3D model inspection.

Reproduction uses the same temporary tools described below. Provision the
recorded Three package beside `bake.mjs`, then run `node bake.mjs --orbit` with
`MX4_MODEL_PATH` pointing to the supplied source. For a copied research directory,
also set `MX4_SURFACE_ANCHORS` to the application's `surface-anchors.json`.
The baker checks the source SHA-256 and emits the PNGs and `orbit.json` beside
itself, including per-frame hashes and exact projected control positions.

The overview and underside at both extremes and the center were visually
inspected after rendering. Native regression checks cover source registration,
artifact hashes, pause/resume, reduced motion, hidden-view suspension, bounded
decoded cache, wheel completion and interruption. These checks do not establish
asset redistribution rights or performance on every Linux graphics driver.

The actual model has been inspected and rendered. Approved assets are in `src/mx4_linux/assets/mouse/`: `overview.png`, `underside.png`, `wheel-01.png` through `wheel-07.png`, `surface-anchors.json`, and `manifest.json`. Root reviewed the overview `a60`, upright underside `u2` and every unique wheel frame. Nine transparent768×768 PNGs total **1,827,217 bytes**; copied files were checked against the manifest hashes. The byte-identical source is retained in [models/](models/mxmaster4mac-whitesilver-ar-360.glb). No application runtime dependency was added.

Source `/home/dj/Downloads/mxmaster4mac-whitesilver-ar-360.glb`: **10,097,632 bytes**, SHA256 `305d2b776c0c0d064406253e13c9f222b01b55abf68667bb93ceb588e4e4fd35`. User supplied; glTF metadata records Adobe Substance 3D Painter 11.0.3. No license or rights information is embedded; redistribution rights have not been inferred. Source remains byte identical. Its underside says “MX Master 4 for Mac”; geometry alone does not identify the connected mouse SKU.

## Inventory and dimensions

Valid glTF 2 binary: 23,876-byte JSON chunk and 10,073,728-byte BIN chunk, header length equals file length. Self contained, **no external URIs, required or used extensions, animations, skins, cameras, or morph targets**. One scene, 17 nodes, eight meshes, eight PBR materials, 49,465 accessor vertices and 64,001 triangles. Twenty-five embedded PNG textures, 512 or 1024 square. Full metadata: `/tmp/mx4-glb-prep/inventory.json`; decoded document: `document.json`.

The root scale is 0.001; child rotations convert source Z-up millimeter coordinates to glTF Y-up meter coordinates. Actual loaded world bounds: `[-0.0563174313,-0.0005030157,-0.0658517003]` to `[0.0319643036,0.0502806078,0.0622339198]`. Dimensions approximately **88.282 mm wide × 50.784 mm high × 128.086 mm long**. Scale comes from the provided model; no measurement of physical hardware was performed.

Parts: METAL_EXTRA_PARTS, LEFT_BUTTON, RIGHT_SIDE_RUBBER, PATTERN_PLASTIC_SHELL, RIGHT_BUTTON, MAIN_PLASTIC_BOTTOM, HAPTIC_RUBBER, MAIN_PLASTIC. Names are model author labels and do not encode HID control IDs. MAIN_PLASTIC_BOTTOM includes visible small controls despite its name. Materials include patterned shell, plastic, left/right rubber, metal, and alpha blended left/right buttons; all PBR base color/metallic roughness/normal/occlusion textures were preserved.

## Cameras and observed visual result

Contact sheets `contact.png`, `contact-2.png`, and `contact-3.png` document camera exploration. The approved overview shows the nose toward upper-left, palm toward lower-right, and the left thumb controls and haptic grooves clearly. Its alpha silhouette is tall and narrow, closer to `01_buttons.png` than the initial top-down `f` candidate. The underside shows actual feet, sensor, power switch, channel labels and switch button. Model details and materials were visibly inspected at full output resolution and intended roughly 270px product height. The rendering is an accurate view of the supplied asset, with no rebuilt substitute mesh or generated illustration.

Orthographic extent 0.17m square, target `[-.0121765638413,.0248887960325,-.00180889026777]`, near .001, far 10. Overview camera offset `[-.15,.23,.13]`, camera local Z roll `1.047198` rad. Underside offset `[-.04,-.3,.07]`, roll `3.14159265` rad. Original root transforms retained. Manifest stores complete settings, output dimensions, alpha bounds and hashes.

## Bounded authored wheel movement

No animation tracks were supplied. Connected-island analysis welded coincident source positions to account for normal/UV seams. METAL_EXTRA_PARTS has 17 islands. Its largest island is the visually confirmed main wheel, 14,878 triangles, source bounds approximately 9.50×24.46×24.46mm. Pivot `[-1.5095460415,33.2052001953,21.7716288567]` mm, source X axis. Cloned geometry preserves all attributes, material and original indices apart from choosing this triangle subset. Source mesh uses remaining triangles. Pivot translation is local, then the wheel alone rotates around X.

Zero-angle separated rendering is **pixel identical** to original overview (ImageMagick absolute-error metric 0). The peak frame differs only within `49x73+413+215`, the main wheel region; body and other controls stay unchanged. Full 90° and negative rotations reveal a dark authored material sector; this was visibly checked and rejected. Final motion is positive approximately 0→12°→0, sixteen samples using eight unique PNG images, with mirrored playback. `overview.png` doubles as frame zero. The 450ms suggestion is an authored native UI choice, not a measured vendor timing. Use static overview when reduced motion is enabled; run the finite cycle only for an appropriate wheel illustration. Final wheel inspection crop: `/tmp/mx4-glb-prep/final-wheel-inspection.png`.

The second metal island is consistent with the thumb wheel (2,240 triangles, bounds 8.98×21.23×14.93mm), but its axis was not verified; no thumb wheel rotation was baked.

## Registration and live-state limits

`surface-anchors.json` records normalized coordinates over the **whole transparent 768 square canvas**, plus source-space surface positions from raycasting visually chosen pixels. It includes main wheel, mode button, thumb wheel, haptic panel and the three small side controls; root/UI owner maps them to verified control IDs. These geometric anchors must not be treated as model-supplied HID metadata. Underside switch/power anchors are raycast; three channel-dot centers are approximate manual pixel registration. Cropping needs corresponding coordinate remapping.

The source has a green power indicator and a white **channel 1** indicator baked into the illustration. They are static artwork. Actual battery, power, paired hosts and active channel must come from device metadata. Fresh host rows/readouts can communicate real state; native overlays at the supplied dot positions can cover static dots if the illustration itself depicts status. No source image was repainted to imply live state.

## Tools and reproduction

No Blender was installed. Existing cached Chromium and Playwright were reused. Temporary official npm `three@0.186.1`, downloaded only to this /tmp research directory, SHA256 `8cd068708ea44f2c73c944b1cead2ba2f0d5c15c8fc194e5700f4e4f4a033fe7`. npm URL and integrity are in `tool-provenance.json`; Chromium/Playwright paths are in final manifest. No project npm/Python packages changed.

Renderer: Three WebGLRenderer with SwiftShader Chromium, antialias + alpha, SRGB output, ACES filmic tone mapping exposure 1.0; RoomEnvironment PMREM with sigma .04. The background is transparent; no floor or background plane. All image textures are embedded. Browser network routing blocks every non-loopback request; the local HTTP server only serves rendering data. GLB contents are parsed data and no embedded application/vendor code was executed.

Scripts `bake.mjs`, `viewer.html`, `components.py`, `wheel-island.py`, `wheel-island.json`, `wheel-views.json`, and `rebake-final.py` are in `/tmp/mx4-glb-prep/`. With the exact recorded temporary Three package and cached browser tools present, run `python3 /tmp/mx4-glb-prep/rebake-final.py`. It checks source hash, regenerates wheel island and two views/eight samples, and exits. Rendering processes and loopback servers have exited successfully; no interactive/focus/input/hardware operations occurred.

The original implementation loaded only baked PNGs/manifest/coordinates. The current native 3D view also reads the supplied GLB and embedded textures; neither Three nor a browser or web view is used at runtime. Asset preparation used the 3dviz-pro-max workflow and lighting guidance; no kit or catalog geometry was used.

Portable research helpers are retained in [model-bake/](model-bake/), with pinned tool provenance. Playwright-core was version1.63.0. To reproduce without adding project dependencies, copy that directory to a scratch location, provision the recorded Three package at its `three/package`, set `MX4_MODEL_PATH` to the retained GLB and `MX4_CHROMIUM`/`MX4_PLAYWRIGHT_MODULE` to compatible local tools, then run `python3 rebake-final.py`. It verifies the source hash and writes outputs beside the research script; it does not replace runtime assets. `frame-00.png` corresponds to `overview.png` and frames01–07 to `wheel-01.png`–`wheel-07.png`. Rendering and antialiasing may vary across graphics/browser versions.

Eight-frame enlarged inspection strip: `/tmp/mx4-glb-prep/final-wheel-strip.png`, ordered frame0 through frame7 left to right. Bake scripts resolve their own directory and accept `MX4_MODEL_PATH`, `MX4_CHROMIUM`, and `MX4_PLAYWRIGHT_MODULE` overrides; keep the pinned Three package at sibling `three/package`. These are tooling path overrides, not runtime application dependencies.

Preparation follow-up: the portable bake scripts now default to the sibling `../models/mxmaster4mac-whitesilver-ar-360.glb` suitable for `docs/reference/model-bake/`; `MX4_MODEL_PATH` overrides it. HTTP serving accepts only viewer.html, wheel-island.json, JavaScript beneath Three/package, and the explicit model route; normalized path.relative and realpath containment reject traversal, sibling-prefix paths and static symlink escapes. `node bake.mjs --self-check` tests allowed files and traversal rejection without opening Chromium or rendering. Reproduction writes images alongside its script; the earlier statement about no project paths written applies to this /tmp preparation, not running the script after copying it into documentation.
