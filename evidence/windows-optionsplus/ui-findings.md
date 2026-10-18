# Windows Options+ UI: ring, haptics and force evidence

Static inspection only. No vendor JavaScript was executed and no hardware/configuration writes were performed by this worker. Evidence below is bounded to the supplied extracted Options+ UI bundles. It does not establish observed Windows popup or mouse execution.

## Artifact identity and method

Extracted root: `/tmp/rea-extracted-fd34f504-75e0-47dc-93d9-24f7873afa0b`.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| app.min.js | 11,641,067 | 46badde913d18764a68be7dabda7c7be2864ee05e32a9dad663bbfd98d0b0ff8 |
| cc.min.js | 10,936,540 | 5bc5f8c4fdbed723a763b02a677d4a529aeae21ea44d7b3a929427317330e971 |

Original offsets below are zero-based byte offsets, not transformed-file character locations. Exact function/webpack-module start offsets were verified against the original binary byte strings. The reviewer can re-run `evidence/windows-optionsplus/decode_ui_strings.py <original bundle path>`. It parses the literal string table as data, evaluates only a restricted arithmetic AST for the table rotation, replaces constant table calls, and folds literal string additions. It never imports, evals, or launches vendor JS. Rotation/entries: app=269/34898; cc=206/140710.

Limit: alias propagation follows identifier names globally, not lexical scope, so transformed output is an inspection aid and is not a complete deobfuscation or call-flow proof. Findings were checked against adjacent field use and original function boundaries. Original-to-decoded and decoded-to-folded span maps remain beside the extracted artifacts as `*.static-decoded-map.json` and `*.static-folded-map.json`; only replacement span boundaries have exact source mapping inside replaced text. `*.static-metadata.json` records original identity.

## Force sensing — answered statically

`app.min.js:3221660`, `_0x265569=function`: explicitly takes the first item of `getForceSensingCapabilities().buttonForceSensingCapabilities`, then constructs:

```text
LIGHT  = button.minForce
MEDIUM = button.defaultForce
HARD   = button.highForce
FIRM   = button.maxForce
```

It initializes all four values to zero when capability data is absent. This establishes that four visible sensitivity choices are device capability values, not a generic percentage curve supplied by the UI.

`app.min.js:3347168`, `_0x1eb15b=function`, maps the first `buttonsForceSensingSettings[0].currentForce` back to those four display labels. The force configuration component around original bytes 6906450–6910700 selects that same first settings item, copies it with a new `currentForce`, and applies `FORCE_SENSING_SETTINGS`. The generic card transform around original bytes 3196200–3196500 places its payload at:

```text
card.forceSensingSettings.buttonsForceSensingSettings
```

Observed fields and transformations are clear; exact HID++ force units and firmware-side conversion require the device JSON/native transport evidence. This worker did not establish physical trigger thresholds by measurement.

## Haptics — answered UI/data model; native playback remains separate

`app.min.js:3219593`, `_0x628060=function`, builds a waveform lookup from capabilities:

```text
for each name in device.hapticCapabilities.supportedWaveforms:
    waveformIds[upper(name)] = supportedWaveforms[name].id
```

`app.min.js:3220140`, `_0xf0c80a=function`, obtains intensity values from `supportedLevels` and maps `upper(levelName)` to `value`. Its initialized intensity keys are SUBTLE, LOW, MEDIUM, HIGH. Thus neither waveform IDs nor intensity wire values should be inferred from ordinal UI positions.

`app.min.js:3219117`, `_0x7d4dac=function`, the UI's low-battery haptic inhibition predicate is:

```text
batteryState exists
&& batteryState.percentage <= 10
&& card.hapticSettings.isGlobalHapticsEnabled
&& card.hapticSettings.isPowerSaveEnabled
```

UI settings seen in multiple components: `isGlobalHapticsEnabled`, `isPowerSaveEnabled`, `hapticFeedbackLevel`, and `eventSourceSettings`. UI controls disable their preview/configuration portions using the inhibition predicate. This does not by itself prove firmware power policy.

The waveform API builder around original bytes 4796700–4797500 sends the capability-selected waveform ID and `enableWaveformInfoEvent` to the agent's play endpoint. This worker only inspected this implementation and did not invoke playback.

## Ring editor and folder model — answered statically, popup behavior partial

`cc.min.js:10053960`, webpack module `0xca78`, defines root controls `[0,1,2,3,4,5,6,7]`, an editor default container of 13.75 em, folder container sizes SMALL=19 em and MEDIUM/LARGE=22 em, button size 3 em, transition duration 300 ms, and special controls 10000 and 10001. A threshold value 9 controls is used by folder rendering. This is enough to establish an eight-position editor, but the threshold use alone is not proof of the backend's maximum legal child count.

`cc.min.js:7590715`, `_0x455cc0=function`, supplies editor control geometry:

```text
normal angle = normalize(index * 360 / 8 - 90)
folder angle = normalize(parentAngle + centered_index * 22.5)
if folder child count is even: use half-step centering
if folder child count is 1: use parentAngle
position_percent = center + (radius adjusted for button half-width) * [cos(angle), sin(angle)]
```

`cc.min.js:8384751`, `_0x2f8ba8=function`, renders a normal eight-control ring or the currently selected folder's controls. In folder mode it prepends/appends the special control sentinels when below the threshold; it renders the folder parent as a center button and maps child items through the geometry function.

`cc.min.js:8362930`, `_0x139d08=function`, handles center/folder controls: clicking a folder calls `openFolder(controlId, angle, position)`, while clicking the center parent calls the close callback. Folder hover animation in `_0x5a7bb0` at `8350432` is specifically gated by `isFolder && canDrop && !suppressAnimation` and `showDropTarget`: it is a drag-and-drop editor affordance. Defaults are debounce100 ms, hover1000 ms, border fade800 ms, heartbeat600 ms. These must not be confused with live action-ring hover activation delays.

`cc.min.js:8396044`, `function _0x32fcb0`, is the editor state reducer with modes Default/Folder/Transitioning. StartOpenFolder records parent control, angle and starting position, hides controls, and marks opening; CompleteOpenFolder changes mode to Folder and reveals controls. The provider around `8400296` dispatches the folder-mode selection, schedules one `requestAnimationFrame` for position change, and completes after the 300-ms transition. Closing reverses this. The editor's center button moves from its original slot to the center.

Folder create/delete/remove commands around original bytes 4092100–4095000 carry this scope:

```text
{ deviceType, applicationName, profileName, modeName,
  workspaceName, pageName, controlIndex, ... }
```

Create additionally carries displayName and description, and commands have an undoId option. A `FolderCreated` communication event is matched to the current page and the control whose `pressAction` equals the new profile-action name (`cc.min.js:8355033`), then opens that folder after 300 ms. This directly links editor folders to profile actions, pages and press assignments, rather than a standalone array of display labels.

Inference: the ring is configured through the broader Creative Console / virtual-device profile model; its execution popup likely belongs to the native overlay/agent. These bundles prove editor behavior and data construction. They do not establish the native popup's hit regions, physical button hold/release semantics, cancellation behavior, cursor warp, or haptic tick timing.

## Read-only agent API hints supplied to root

`app.min.js:705071` builds the JSON envelope `{msg_id: (++counter).toString(), verb: method, path: route}` with an optional payload object, then JSON.stringify and transport.send. Calls use GET via `_0x331e01.fG`; subscription methods are separate.

| Agent operation | Route | UI request shape / expected response |
|---|---|---|
| Device list | `/devices/list` | GET, no payload; implementation exact start 1633463; consumers read payload.deviceInfos |
| Device resources | `/devices/resources` | GET `{deviceModel: device.deviceModel || device.modelId, id: device.id}`; exact start 1634207 |
| Model info constant | `/devices/model/info` | Declared beside device/resource routes; this worker did not establish its request payload |
| Haptic status | `/haptics/<deviceId>/status` | GET, no payload; implementation exact start 4801063 |
| Haptic event sources | `/haptics/<deviceId>/event_sources` | GET, no payload; exact start 4802562; consumer reads payload.hapticEventSources |
| Playing waveform | `/haptics/<deviceId>/get_playing_waveform` | GET route/implementation present |
| Overlay scale | `/radial_menu/overlay_scale` | get/set shared endpoint, scalePercents field |
| Profile readers | `/v2/profiles`, `/v2/profiles/slice`, `/v2/profile` | Constants present; this worker did not establish exact query payload |

Write routes also exist, including `/haptics/<id>/play_waveform` and `/stop_waveform`; no write route was called. No authentication/initial-session handshake was established in this bounded UI pass.

## Remaining unknowns

- Actual Windows/native ring popup geometry and physical input commit/cancel state machine.
- HID++ feature-level mapping from agent profile values to device reports; waveform sample formats and exact force units.
- The exact active user's profile/folder contents; this worker did not read private account data.
- Runtime corroboration and physical MX4 behavior. These were intentionally not claimed from static UI analysis.
