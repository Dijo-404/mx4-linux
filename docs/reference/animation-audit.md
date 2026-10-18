# Options+ animation and media reference audit

Audited 2026-10-18 at 17:25 UTC (22:55 IST). This was a read-only static pass: no vendor JavaScript/native application was executed, no SSH was used, no device commands were issued, and no project code/assets were changed. Extracted data and derived contact sheets remain under `/tmp/mx4-vendor-media-audit`.

## Findings that affect the rebuild

The copied ASAR contains 263 WebP images, 73 WebM clips and 5 CSS files. I inspected contact sheets of every WebP and first/middle/end frame samples from every WebM. None of the sampled media matches the isolated silver MX Master 4 product artwork or a wheel/force/haptic/live-ring tutorial from the new screenshots. Most media is general onboarding, Smart Actions screenshots, macOS permission instructions, generic device illustrations and Flow/decorative graphics.

The new `docs/reference/img/02_point_scroll_press.png` and `03_haptic_feedback.png` were inspected directly. Their silver mouse has the angled top view, sculpted thumb rest and haptic area absent from the generic ASAR mouse illustrations. The screenshots and `looks.mp4` are stronger product references. Device-specific media may live in downloaded/cache resources outside this ASAR; that remains uninspected.

The CSS does directly establish useful scoped durations: 200 ms for configuration drawers and content movement, 180 ms for the app-specific header, 250 ms for collapsing sections, and 200 ms for tooltips. The previously documented ring-editor folder transition is 300 ms. These are separate from live ring popup motion and physical haptic timing.

## Evidence identity and extraction

Input: `evidence/artifacts/windows-optionsplus/app.asar`, 61,845,517 bytes, SHA-256 `8689e5fda6047864abe1c9f89486c03f7e1a4f960cbf0267c2f362f88d303a27`.

Member inventory: `evidence/windows-optionsplus/resources.json`, `archive.members`. Extraction checked packed member kind, positive size, data offset at least 156260, range within file length, and no parent traversal. Extraction performed literal bounded reads; no member code was imported or evaluated.

- All media/CSS hashes, sizes and absolute ASAR offsets: `/tmp/mx4-vendor-media-audit/inventory.json`.
- Extraction script: `/tmp/mx4-vendor-media-audit.py`.
- Full ffprobe stream/format metadata, decoded frame counts/PTS and sample timestamps: `/tmp/mx4-vendor-media-audit/webm-inventory.json`.
- Alpha supplemental metadata: `webm-alpha-inventory.json` in the same directory.
- Exact selected CSS declarations, selectors and offsets: `evidence/windows-optionsplus/animation-motion.json` (retained in the project).

All 73 WebMs use VP9, with 43 advertising `100/3` fps and 30 advertising `25/1` fps. There are no audio streams in these clips. Eighteen flag alpha mode. Those were additionally decoded with `libvpx-vp9` to RGBA and composited on dark backgrounds. The initially white clip `assets/0afc20aff4ee8b7dcce2.webm` is an animated Logi wordmark; white samples were not assumed empty.

## Contact sheets and inspected originals

All paths below are beneath `/tmp/mx4-vendor-media-audit`:

- `webp-sheet-0.jpg` through `webp-sheet-7.jpg`: all 263 WebPs, numbered by their index among WebP inventory entries.
- `webm-sheet-0.jpg` through `webm-sheet-6.jpg`: all 73 clips, three samples each. Samples use first frame, half the actual last frame PTS, and last PTS minus 50 ms.
- `webm-alpha-sheet-0.jpg` through `webm-alpha-sheet-2.jpg`: all 18 alpha clips, with alpha decoded correctly against a dark background.
- `alpha-2-sheet.jpg`, `alpha-56-sheet.jpg`, `alpha-71-sheet.jpg`: supplemental five-frame alpha checks, identifying the wordmark, orbiting spinner and orbiting dot respectively.

Candidate original images inspected at full resolution:

| ASAR member | Absolute byte offset | Bytes | Finding |
|---|---:|---:|---|
|`assets/fa1c6caff1e8953aac18.webp`|46689077|7272|Generic rounded mouse with warning badge, light background|
|`assets/8e5f771cbfff3d29a021.webp`|32786977|8226|Same generic mouse, dark background|

Neither is the MX Master 4 artwork. No vendor assets were copied into the Linux runtime.

## Exact CSS motion evidence

These entries are from `app.min.css`, ASAR offset **9605450**, size **1144452**, SHA-256 `76a54e21811dcf8ca820cc60e89aa569583c4d179ae523f15bf646fcd7490e8b`. Offsets below are zero-based UTF-8 member byte offsets; add 9605450 for absolute ASAR offsets. Exact full selector lists and declarations are in `selected-css-motion.json`.

| Member byte offset | Selector/scope | Exact relevant declaration |
|---:|---|---|
|316384|`.devicedetails-container .app-specific-header-container`|`transition:all .13s`|
|316523|`.devicedetails-container .app-specific-header-container.isConfigurationEnabled`|`animation:slide-right-left .18s; left:-72%`|
|330348|`.isConfigurationEnabled` descendants including `.buttons-feature-content-container`, `.haptic-feature-content-container`, `.point-and-scroll-feature-content-container`, `.point-and-scroll-press-feature-content-container`|`transform:translateX(-130px); transition:all .2s`|
|331885|Default feature-content selector list including those same classes|`transform:translateX(-30px); transition:all .2s`|
|698151|`.device-configuration-panel`|`animation:floatText .2s; position:fixed; right:0; top:0`|
|706666|`.app-profile-configuration-panel`|`animation:floatText .2s; position:fixed; right:0; top:0`|
|504191|`.collapse-body`|`overflow:hidden; transition:height .25s ease`|
|2236|`.tooltip.animationTop`|`animation:slide-up .2s ease-out forwards`|
|2299|`.tooltip.animationRight`|`animation:slide-right-animation .2s ease-out forwards`|
|2488|`.tooltip.animationLeft`|`animation:slide-left .2s ease-out forwards`|
|2792|`.tooltipWithDelay.animationTop`|`animation:slide-up .2s ease-out .5s forwards`|
|392427|`.concentric-circle .circle-layout`|`transition:all .1s ease-in-out`|
|392655|`.concentric-circle .circle-layout.haptic-feedback-circle`|`animation:showForDuration 2s ease-in-out forwards`|
|670153|`.haptic-feedback-configuration-scroll-container`|`transition:all .2s ease`|
|831436|`.action-ring-onboarding .ring-position:after`|`animation:ripple 3.2s linear 1; animation-fill-mode:forwards`|
|834467|`.action-ring-onboarding.plugins-panel ... .thumbnail.active .progress-bar`|`animation:progress .25s ease-out forwards`|
|136179|Home `.device-list-item-image:hover` / camera image hover selector list|`transform:translateY(-12px); transition:all .2s ease-in-out; transition-delay:50ms`|
|264898|`.input-range__slider-container`|`transition:none`|
|264945|`.input-range__slider`|`transition:none`|

The content's default and configuration-enabled transforms differ by 100 px. The CSS names scopes; no Windows runtime class activation or measured event timing was observed in this pass. Generic Bootstrap rules in `vendors.min.css` were not treated as product motion requirements.

Keyframe start byte offsets: `@-webkit-keyframes floatText` 706869, `@keyframes slide-up` 2868, `@keyframes slide-right-animation` 2377, `@keyframes slide-left` 2554, `@keyframes haptic_ripple_scale_7` 390320, `@keyframes haptic_ripple_scale_6` 390464, `@keyframes showForDuration` 393205.

Keyframe definitions:

- `floatText`: `0% { right:-20% } to { right:0 }`.
- `slide-up`: `0% { opacity:0; transform:translateY(12px) } to { opacity:1; transform:translateY(0) }`.
- `slide-right-animation`: opacity 0→1 and translateX(+12px)→0.
- `slide-left`: opacity 0→1 and translateX(-12px)→0.
- `haptic_ripple_scale_7`: scale 0→7, opacity 1→0, border width 1.5px→0.2142857143px.
- `haptic_ripple_scale_6`: scale 0→6, opacity 1→0, border width 1.5px→0.25px.
- `showForDuration`: opacity 1 through 80% of the 2-second animation, then opacity 0.

### Haptic illustration timings

These are visual CSS ripples, not measured motor waveforms. The base ripple is a 20×20 px circle.

| CSS class | Delay/duration pairs | Easing |
|---|---|---|
|`ripple-subtle`|(0,900 ms), (100 ms,1000 ms)|ease-out|
|`ripple-low`|(0,600 ms), (200 ms,1000 ms)|ease-out|
|`ripple-medium`|(0,700 ms), (200 ms,800 ms), (300 ms,800 ms)|Third ease-in; first two ease-out|
|`ripple-high`|(0,800 ms), (200 ms,700 ms), (300 ms,700 ms), (500 ms,600 ms)|ease-out|

Declaration offsets: 390918, 391043, 391169, 391291, 391414, 391539, 391666, 391792, 391915, 392040, 392165 respectively. Add 9605450 for absolute ASAR offsets.

### Ring timing boundary

The 3200 ms onboarding ripple and 250 ms thumbnail progress animation are tour hints. They do not establish the live ring's opening duration. Separate static evidence in `evidence/windows-optionsplus/ui-findings.md` proves a **300 ms editor-folder transition**, along with editor drag hover debounce 100 ms, hover 1000 ms, border fade 800 ms and heartbeat 600 ms. Those drag affordances are also separate from live popup hover behavior.

The supplied `looks.mp4` visually shows root expansion and child arc expansion around 200 ms. The current native reference model uses root 180 ms and child 160 ms. A WebM tutorial's total duration must not be substituted for any of these UI transition durations.

## Implementation guidance and limits

Use the scoped 200 ms native inspector slide, 180 ms context header, 250 ms section collapse and 300 ms editor folder transition as design references. Keep keyboard focus, cancellation, accessible labels and GTK's reduced-motion preference. Use haptic illustration pulses only as visual feedback; capability-supported physical waveform IDs are a separate hardware concern.

This pass did bounded frame sampling, not a complete human watch of every WebM. Full metadata and frame counts are recorded so container duration is not confused with actual decoded video length. Some containers last beyond their final video frame. No Windows application interaction, runtime animation capture, physical input test or hardware playback was performed. Hashed asset-to-route bindings and external device-specific resources remain unverified.
