# Windows Logi Plugin Service findings

Read-only static analysis of the supplied `C:\Program Files\Logi` installation, 2026-10-18. REA did not load or execute assemblies. The inspected managed layer dispatches Actions Ring events and resolves profile actions; the actual interactive popup renderer is not established by these methods.

## Identity and evidence

All seven inspected assemblies report version **6.4.2.3414**, target **.NET 10**, file-backed CIL, complete admitted metadata and no reported metadata issues. PluginApi is AnyCPU; the others are x86-64. Tokens below are build-local and must be paired with the artifact SHA-256/MVID recorded in the evidence.

| Assembly | SHA-256 | Artifact Evidence parent |
|---|---|---|
| LogiPluginService.dll | `5fe3acd14df1ede8956e3bc16980b1dd76d18db20d33d77d5a006afb808b355c` | `ev_986d57332f6807b434cf36dc3d5db0af9cac975523d89ca70f2b7961d7f7f749` |
| LoupedeckService.dll | `1e924b5a86267f8c13ffca814df9b28848d87107c50d1500b950f9d12ac4f835` | `ev_433b56614ff50c9e6b1b5a0e8cd01ee9d7831b1d994a0357715fb839f1370230` |
| GenericPlugin.dll | `f462a668ded5357a8451d33820f98c978be49e852adce1f3acec8f30b1b7c9f5` | `ev_3f04924eaa619d534e1bbd8725cea4a36721e27d96c90aafd6f1cea89055128b` |
| DefaultWinPlugin.dll | `35d2048d416239d9815d0cf77f46b21e1c2f18140caa5327678a1214e2905a3f` | `ev_c38c9e333a3597c072eba04d5930fce23141c92f88f59d8ca16fb0c108801c62` |
| LogiConnection.dll | `ffb407fc6c22f50410d69746f672568a95f76aac40c8fd8042b94adb9e8ecc5e` | `ev_31d12bd52cbaab89b2ba1d13d886f4844b3b7e7626cd53ed85ff0ffe472ecd6b` |
| LoupedeckShared.dll | `0690f83c5fbc09dc4d3178f4ee1ec4cfe17768126e00e8d5b6f7e073b76563f6` | `ev_1d0c064cce16a4afb7b7fdc2d4aba41cf347c7e416233c235ed298d35af7045d` |
| PluginApi.dll | `3e5160e652db43340a4c9d00726e9be891e07f18c3c3acf786385a191216ebbf` | `ev_f41865ba5cff3ca6f9bfa114da2e61d45497cfedba1cf15eea4107481a7852ee` |

Full tool Evidence is retained in the ignored `evidence/artifacts/windows-logi-plugin-service/rea-managed-evidence.json`. The report contains findings, not copied proprietary implementations.

## Ring action → service → connected client

Direct CIL call edges establish this path:

```text
GenericPlugin.ShowRadialMenuDynamicAction.SendShowDeviceEvent
  → GenericPlugin.get_LoupedeckService
  → LoupedeckService.get_Loupedeck7DeviceManager
  → Loupedeck7DeviceManager.get_RadialMenuMessageHandler
  → RadialMenuMessageHandler.OnShowDevice
  → RadialMenuShowDeviceEventArgs constructor
  → RadialMenuMessageHandler.SendEventToDevice
  → LoupedeckMessageHandler.SendEvent
```

The full namespace of the handler is `Loupedeck.Service.Devices.Loupedeck7Devices.RadialMenuMessageHandler`. Generic action token `0x06000030` starts at file byte **13349**. Its `RunCommand`, `ProcessButtonEvent2` and `ProcessTouchEvent` methods call this helper. The latter two bodies contain flattened/obfuscated control flow, so exact physical event-value semantics have not been reconstructed.

Service `OnShowDevice` is token `0x0600226e`, file byte **898672**. `ShowOverlay` (`0x06002273`, **899052**) and `HideOverlay` (`0x06002274`, **899176**) enumerate connected values, construct the matching event argument and send it through `SendEventToDevice` (`0x06002261`, **897220**). These are event-dispatch methods, not evidence of a ring drawing algorithm. Separate-client rendering is an inference from this dispatch boundary; the client process is not identified by these methods alone.

Evidence: Generic focused parent `ev_f19df3ab8c61cc108742be8003f56a598bfb10cfbbc9b108272ac958e5fc58c4`, call-edge view `ev_5d7aa6114a1119deeb9291ee12074add3f07c7a8184240d5fb0b807ced18fc2d`; service focused parent `ev_936c5ddb4d9620d6b5ed30c2c0c3a13fe6acec30c578d49a1211450d6730e3da`, call-edge view `ev_b7861573147cbd4c773059c9fe6ea5de5f6b8730a9344f44671f55ee9a215c59`.

## Runtime assignment schema and event handling

The service's `DeviceState72ControlAssignment` directly declares:

- `ControlId`
- `PressActionAssignment`
- `RotateActionAssignment`
- `NestedControlAssignments` (`get_` token `0x0600245a`)

`DeviceState72ControlActionAssignment` carries `ActionName`, `DisplayName`, `Image`, `ActiveImage`, plugin and override background/foreground colors, `AdjustmentValue`, and `AdjustmentValueData`. `RadialMenuPageChangedEventArgs` carries `ControlAssignments` and `ScaleFactor`.

`OnPageChanged` (`0x0600226c`, **898240**) creates/sends the page event, clears and rebuilds an assignment cache, and calls `SaveControlAssignmentImages`. `AssignControlAction` (`0x06002250`, **894468**) resolves the active profile/page and plugin action, can construct a profile action, and calls `ProfileLayoutPage7.SetActionName`.

Incoming `ButtonEvent` (`0x0600224e`, **894132**) calls `ProcessButtonPressedEvent` / `ProcessButtonReleasedEvent`. `DeviceState72.OnButtonEvent` (`0x06002439`, **940780**) resolves a press action, calls `TryProcessButtonEvent`, checks `IsPress`, and can call `SendAction`. `OnEncoderEvent` (`0x0600243a`, **941008**) resolves a rotate action, calls `TryProcessEncoderEvent`, reads click count and can call `SendAction`.

`MouseEvent` (`0x06002256`, **895272**) reads device/control/event type, emits MouseEnter/MouseLeave event args, and calls `ContextualWheel.SetTemporaryAdjustment` / `ResetTemporaryAdjustment`. Thus hover has an adjustment-selection role in this managed layer. It does not establish pointer hit geometry or haptic ticks.

These are static fields and possible call paths; branch guards, runtime delivery and user actions were not observed. Source: service focused Evidence parent above, with exact method bodies/anchors and direct call edges.

## Windows action and native boundaries

`DefaultWinPlugin.TaskSwitcherDynamicFolder.RunCommand` (`0x0600002e`) calls `TryGetProcessId`, `INativeMethods.ActivateProcess`, then `PluginDynamicFolder.Close`. `VolumeMuteCommand.RunCommand` (`0x0600001b`) calls `VolumeMixer.SystemVolumeToggleMute`. `ActivateMenuBar` calls `INativeMethods.SendKeyboardEvent`. These provide concrete Windows action adapters behind the action framework. Evidence call-edge view: `ev_6293d306e3ce7a51b6d1b741f1ef0b3ae629b4d819ac2731804eb70c8be365b5`.

`LogiConnection.dll` exposes P/Invoke-flagged, bodyless `Logi.LocalConnection.Shim.Server`, `.Client`, and `.Connection` methods for create/start/stop/read/write/close. `Client.StartEx` is token `0x06000020`; `Connection.AsyncRead` / `AsyncWrite` are `0x06000019` / `0x0600001a`. Bounded raw metadata-string inspection identifies `LogiLocalConnectionNativeLibrary.dll` at byte **55214**. `LogiConnectionRawClient.ConnectAsync` calls `ReconnectAsync`; the selected native-shim bodies themselves remain unavailable as CIL. Evidence parent `ev_1cb6a043704da4bb6fb4dd8d460f9cc3c02741f541138ab5804b6a9214b9ad2b`, call-edge view `ev_75fe956bfc0dd71f0cd8c15b598321f2d74cb2a86b7fef3115495369a891117f`.

`PluginApi.NativeApiLoader.Load` (`0x06000929`, **194632**) calls `Assembly.LoadFile`, `Assembly.GetType` and `Activator.CreateInstance`. Its platform implementation is loaded dynamically. `INativeOverlay` declares Create/Show/Hide; `OverlayBase.OnPaint` calls Skia drawing of primary icon/text and secondary text. This proves a generic text/icon overlay path, not that this class draws the interactive Actions Ring. Evidence parent `ev_863e5fb0cf2cb83803703b2bf4548593a104e7543c929ad84cd2fd398d6ecf5e`, call-edge view `ev_e0db535d036c97c21f7e80ad319789752acbef2051d8eeb4d6ad4405d95433b2`.

## Limits

This pass answers where ring events, nested assignments, profile execution and Windows action adapters exist. It does not recover the separate popup renderer, exact hold/release commit/cancel policy, geometry, HID++ transport, physical force thresholds or waveform samples. Some strings and control-flow decisions are obfuscated; declared CIL calls do not prove runtime invocation, successful reflection binding or a particular branch. No runtime/injection/install step was used, and the shared native REA target was not changed.
