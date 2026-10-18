# Windows Options+ native agent: static findings

## Scope and identity

Evidence label `WIN-NATIVE-ID`: local artifact `evidence/artifacts/windows-optionsplus/logioptionsplus_agent.exe`, 68,391,064 bytes, SHA-256 `0d7149a8ded851cb492f75e3b78306cc68b60dc623b2341267db17bb15b1f03c`. Read bytes only; no Windows executable was launched, injected, modified or attached to. GNU `file` and `objdump -x/-h` identify PE32+, x86-64 (`machine=0x8664`), Windows GUI subsystem, linker metadata 14.38, image base `0x140000000`, entry RVA `0x234e278`. The seven section names are `.text`, `.rdata`, `.data`, `.pdata`, `CPADinfo`, `.rsrc`, `.reloc`. The original `.rdata` file span begins at `0x02f15200`, RVA `0x02f16000`; do not confuse file offsets below with virtual addresses. COFF timestamp is metadata only, not an authenticated build time.

A `VS_FIXEDFILEINFO` signature and version tuple were observed at original file offset `0x040bdce8`: file version `2.9.4631.0`, product version `2.9.11591.0`. This small fixed-record observation does not authenticate the publisher or identify the Electron frontend version.

Exact case-insensitive ASCII and UTF-16LE searches for `MX Master 4`/`MXMaster4`/`mx_master_4`, `B042`, and `B048` found no matching literal name/ID in this executable. This bounded search does not establish that the binary lacks support: device identity can reside in external depots or encoded tables. Do not independently attribute all native features below to MX4 without the separate device-depot evidence.

## Reusable metadata evidence

Evidence label `WIN-NATIVE-SCHEMA`: `evidence/windows-optionsplus/inspect_agent.py` is an owned, stdlib-only, read-only parser. It validates PE32+ headers/section bounds and serialized protobuf wire lengths, fixed-width fields, field numbers, varint widths and nesting. It scans matching `logi/(protocol|common_protocol)/*.proto` descriptors with final `syntax=proto3`, with a maximum 512 KiB per candidate. It recovered 74 descriptors. The reviewed `agent-schemas.json` retains nine relevant protocols, including complete haptics, force, radial and common message schemas, with selected device, LPS, application and profile/card messages. The full metadata extraction is ignored at `evidence/artifacts/windows-optionsplus/agent-all-schemas.json`.

Reproduction:

```sh
python3 evidence/windows-optionsplus/inspect_agent.py \
  evidence/artifacts/windows-optionsplus/logioptionsplus_agent.exe \
  --output evidence/windows-optionsplus/agent-schemas.json \
  --all-descriptors-output evidence/artifacts/windows-optionsplus/agent-all-schemas.json
```

Every selected descriptor byte range was reread from the original EXE; its byte length and SHA-256 were asserted against the output. The parser also rejected deliberately truncated 8-byte, 4-byte, length-delimited, invalid-tag and overlong-varint inputs, and the signed enum values `INVALID_ARG=-1`, `LUS_ERROR=-15` were checked. `py_compile` passed. No app code was changed.

Whole-descriptor source spans are end-exclusive:

| Protocol | Original file span | SHA-256 |
|---|---|---|
| `force_sensing.proto` | `0x032c6580..0x032c68c4` | `3c3a0eec166b222a12df269521b28f440de477625c7ee43241d7e5cb2c82908d` |
| `haptics.proto` | `0x032c8190..0x032c9487` | `a93e4c9d3d980577daf6c6ca0ca49c7193da4377846535332b6f203b0c923241` |
| `radial_menu.proto` | `0x0331ba20..0x0331bc36` | `de24f58ca9a36ee332e6139229373b4dd0215312e9b72fb0a1fa52e69b5876dd` |
| `common_protocol/message.proto` | `0x0332da90..0x0332dec8` | `568683958f961d20797b3a0f574bc887c3c668f52cb34fb5616c974fdc6bbf4f` |
| `lps.proto` | `0x03197210..0x0319be1f` | `0e15d1bf1711d477c9d818ac98bce33af77b9061fe607bebb045d47d8012f38a` |
| `profiles_v2.proto` | `0x032f8d60..0x032fd08a` | `cc8c0479b38e62df654dfe5cb871b1dd8847d0d436ed7194accf8953298adef6` |
| `devices.proto` | `0x032d9c60..0x032e47e7` | `ebbea46284aace3d1adb0c4862c9002dfa7c6d5a892db5d8baf01a3c368f96ad` |
| `card_register.proto` | `0x032ffdb0..0x032fff5e` | `827b3b21624b4ffd0f085017b01bfd11da60c3d13107c0a45c46c18675c0eb93` |

Schemas establish API data definitions, field numbers, scalar types and enums. Their presence alone establishes neither handler registration nor execution, nor a HID++ packet layout.

## Actions Ring / native and plugin layers

Structured schema observations (`WIN-NATIVE-SCHEMA`):

- `ShowMenu` has state enum `PRESS=0`, `RELEASE=1` (#1), `slot_id` string (#2), `button_name` string (#3).
- `RadialMenuEventType` is `INVALID=0`, `SHOWN=1`, `DISMISSED=2`, `ACTION_EXECUTED=3`, `HOVERED=4`, `ANALOG_VALUE_LIMIT_REACHED=5`.
- `RadialMenuScaleParams.scale_percents` is int32 (#1).
- LPS `ControlAssignment` identifies a uint32 `control_id` (#1), separate `press_action_assignment` (#2) and `rotate_action_assignment` (#3), plus recursively nested control assignments (#4). `AssignmentsChangedData` contains `device_id` (#1), control assignments (#2), scale factor (#3).
- `AdjustmentValueData` contains `value_string` (#1), `format_string` (#2), and float `value`, `min_value`, `max_value`, `default_value` (#3..6). `AdjustmentValueChangedData` names the control/action and carries the adjustment value/data and click count.
- `StartRadialMenuDeviceRequestParameters` carries device ID/model, firmware version, serial number and image dimensions. This is a virtual/plugin-device API model, not a discovery proof for attached physical hardware.
- LPS `ExecuteAction` contains `action_name`, `number_of_ticks`, and a `google.protobuf.Struct` of parameters. A separate registered-action request names the registered action ID and tick count.
- `profiles_v2.Card` holds nested cards, selected child and child ordering (#12..14); haptic settings (#61), force-sensing settings (#62), waveform execution data (#64), and haptic properties (#65). Assignments bind a slot to a card.

Embedded diagnostic/name observations (`WIN-NATIVE-STRINGS`; string-only):

| Offset | Observed literal / implication boundary |
|---|---|
| `0x031065b0` | Source path ending `virtual_device/radial_menu_device_interface.cpp` |
| `0x03106ef0` | `on_show_button_press`; nearby log at `0x03107048` says `'show' command sent to Actions Ring.` |
| `0x031070a0` | `on_show_button_release`; log at `0x03107160` says `'select' sent to Actions Ring.` |
| `0x03106fb8` | `Block device is not ready. Actions Ring will not be displayed` |
| `0x031073b8` | `on_update_radial_menu_items` |
| `0x03107508` | `on_adjustment_delta_changed` |
| `0x031075a8` | `on_adjustment_value_changed` |
| `0x03107df0`, `0x03107e70` | `on_enter`, `on_leave`; adjacent logs describe hover in/out |
| `0x03106c50` | `LPS state changed to ACTIVE. Attempting to establish LPS <-> Radial Menu connection` |
| `0x031079e0` | `radial-menu` virtual-device name |
| `0x031db650` | Native namespace `logi::radial_menu::directx11::radial_menu::init` |
| `0x031dbb30` onward | Native menu configuration/display/hide/select and event-loop diagnostic names |
| `0x031dc988..0x031dcaa0` | Diagnostic text distinguishes visible/opening/hidden/closing and ignores repeated show/hide |
| `0x031dd9e0` | Window class literal `Radial_Menu_window` |
| `0x031de5b8..0x031de5d0` | DirectX shader profile/entry names `ps_4_0`, `ps_main`, `vs_4_0`, `vs_main` |

Inference, supported by schemas plus names but without native body decompilation: the shipped Windows product appears to split the configuration frontend, a native ring overlay, and LPS action/plugin state. These observations caution against treating a renderer-side preview as the complete action executor. They do not by themselves prove exact timing, angle geometry, selection thresholds or animation curves.

## Haptics and force sensing

`WIN-NATIVE-SCHEMA` force definitions:

- `ButtonForceSensingCapability`: uint32 `button_index` #1, `min_force` #2, `default_force` #3, `max_force` #4, `high_force` #5; bool `force_change_capability` #6, `layer_config_capability` #7; uint32 `num_thresholds` #8 and `default_l2_force` #9.
- `ButtonForceSensing`: uint32 `button_index` #1, `current_force` #2.
- `ButtonLayerConfig`: uint32 `button_index` #1, bool `layer1_enabled` #2, `layer2_enabled` #3.
- Global force settings/capability messages contain repeated per-button messages.

`WIN-NATIVE-SCHEMA` haptic definitions:

- `HapticSettings`: global enable bool #1, map of per-source settings #2, feedback level uint32 #3, power-save enable bool #4.
- `HapticCapabilities`: power-save capability, default level, supported level format/list, named waveform map, independent stop/get-playing/info-event capabilities, breathing-exercise mappings and cycle counts.
- `PlayWaveFormRequest` has uint32 waveform ID #1 and info-event enable bool #2.
- `PlayWaveFormStatus`: started=0, ignored=1.
- `WaveformRunningStatus`: none=0, software waveform=1, device waveform=2, unknown/error=255.
- `HapticStatus`: enabled=0, disabled=1. Zero is an enum value, not an ordinary false flag.
- Waveform enum: `SHARP_STATE_CHANGE=0`, `DAMP_STATE_CHANGE=1`, `SHARP_COLLISION=2`, `DAMP_COLLISION=3`, `SUBTLE_COLLISION=4`, `HAPPY_ALERT=5`, `ANGRY_ALERT=6`, `COMPLETED=7`, `SQUARE=8`, `WAVE=9`, `FIREWORK=10`, `MAD=11`, `KNOCK=12`, `JINGLE=13`, `RINGING=14`, `SIMPLE_BREATHING=15`, `BOX_BREATHING=16`, `BELLY_BREATHING=17`, `TIMER=18`, `START_END_BREATHING_SIGNAL=19`, `NONE=255`. This is the application-wide enum; attached-device capabilities must determine which values a device supports.
- `Config.HapticDeviceConfig` holds per-source supported events/waveform IDs, named waveform IDs, event/source/name blacklists and a plugin-source capability flag. Sources can declare unsupported-on-Windows/unsupported-on-macOS. This explains why a waveform's existence does not guarantee every action uses it.

`WIN-NATIVE-STRINGS` haptic/force names and logs:

- `0x03112f80`: `haptic_config.json`; loader messages at `0x03112fa0..0x03113110` refer to per-device depot loading.
- `0x030f3860`: `eventMapping.yaml`; event manager fallback diagnostic at `0x030f3d70` describes device-specific mapping falling back to `DEFAULT`.
- `0x031139f0`: `feature_19b0_haptic_feedback`; `0x03117430`: `feature_x19b0_haptic_feedback`. Both names exist; presence does not identify which configuration path a particular device uses.
- `0x03113db0`: diagnostic says plugin event handlers are registered when supported; adjacent alternative says registration is skipped when disabled.
- `0x031160f0` / `0x031196f0`: haptic `on_radial_menu_event` handlers; nearby enum diagnostics connect ring events to haptic processing conceptually, but the exact ring-event-to-waveform mapping requires the device config or body analysis.
- `0x03125ca0`: missing/incorrect `hapticFeedbackLevel` defaults to medium `60` according to the diagnostic literal.
- `0x03119f40`: diagnostic says two pressure layers are enabled by default for a device/button.
- `0x0311a188`: invalid-force-level diagnostic.
- `0x0311a860`: configuration diagnostic fields include `button_index`, `force_level`, `actual_force`, `actual_force_l2`.
- `0x033cbf18..0x033cc0f0`: decoder names include `Feature19C0ForceSensingButton::getCapabilities`, `getButtonCapabilities`, `getButtonConfig`, `setButtonconfig`, `resetButtonConfig`, `getLayerConfig`, `setLayerConfig`.

Inference: force levels appear to be translated from user-facing configuration to actual per-layer values; haptics are filtered by global/source enable, OS support, blacklists and capability checks. These are precise investigation leads, not proof of the transformation formula, HID++ function indices, payload endianness or successful physical playback.

## Transport / read-only inspection leads

`WIN-NATIVE-SCHEMA` common message envelope: `msg_id` string #1, verb #2, `path` string #3, `origin` string #4, `result` #5, payload `google.protobuf.Any` #6. Verbs: GET=1, SET=2, BROADCAST=3, REMOVE=4, SUBSCRIBE=5, UNSUBSCRIBE=6, OPTIONS=7. `Routes.Route` carries verb, path, payload type string, example JSON and endpoint name (#1..5). Success result enum is 1; negative result values are signed int32.

`WIN-NATIVE-STRINGS`: named-pipe server paths/names at `0x030b1ce8..0x030b20d0`; JSON parser at `0x03170c88`, `payload` and `@type` at `0x03170d00/08`, protobuf `JsonStringToMessage` diagnostics at `0x03170d10..0x03170d68`. The binary also contains a separate WebSocket API server (`0x030b2270` `/api/websocket_server`), but static strings do not prove it is enabled or listening.

Useful route literals (not tested by this subtask):

| Offset | Route |
|---|---|
| `0x03172c80` | `/routes` |
| `0x030c07f8` | `/devices/%s/list` |
| `0x030c0810` | `/devices/%s/info` |
| `0x030c0ab8` | `/devices/resources` |
| `0x030c0ba0` | `/devices/model/info` |
| `0x030c0bb8` | `/devices/support/info` |
| `0x030c0bd0` | `/devices/%s/defaults` |
| `0x030c0c20` | `/resources/list_depot_resources` |
| `0x030c0c98` | `/devices/%s/resource` |
| `0x030cc750` | `/options/devices/list` |
| `0x030c2a40` | `/radial_menu/show` |
| `0x030c2a58` | `/radial_menu/event` |
| `0x030c2c28` | `/haptics/%s/config` |
| `0x030c2c70` | `/haptics/%s/play_waveform` |
| `0x030c2d80` | `/force_sensing/%s/for` |
| `0x030c2d98` | `/force_sensing/%s/layer_config` |

Device resource messages support model/depot IDs and resource keys, MIME types, data-URI selection and URL fields. Exact route-to-payload bindings remain unknown without route registration bodies or a successful read-only `/routes` response.

## Remaining unknowns / confidence boundaries

The native agent schemas and original byte identities are high-confidence static observations. Native function names and logs are literal observations with moderate architectural value, not recovered implementations. No native decompilation provider was configured; no tool installation was attempted. No remote IPC request or hardware experiment was performed by this subtask. Native MX4 model attribution, exact HID++ function IDs, event-to-waveform mappings, ring timing/geometry, and runtime registration remain unresolved here and should be combined with separately retained depot and frontend evidence.
