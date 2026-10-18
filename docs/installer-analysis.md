# Supplied macOS installer: static evidence

The supplied ZIP contains the **Logi Options+ installer/downloader**, not an installed Options+ application tree. It provides useful names for feature discovery, but this pass did **not recover an MX Master 4 HID++ implementation or usable haptic packet formats**. The haptic names found in the executable are route and feature-flag constants; they do not establish handlers or device commands.

Analysis date: 2026-10-18. Target: `logioptionsplus_installer.zip` (supplied locally). The installer was never executed. No discovered URL was contacted and no device commands were sent.

## Identity and scope

| Object | SHA-256 |
| --- | --- |
| ZIP, 20,260,402 bytes | `0b8f5cd5a79fe2b5dea82f8bb11804abd97385ed19e85114420ef01f72222c73` |
| `Contents/MacOS/logioptionsplus_installer`, 38,242,448 bytes | `86aab5e5ed0619263663e47c9ea035c10956221e62939c7226c6321050e2f976` |
| `Contents/Info.plist` | `a997b5cb28d477473a74aa21dd4a09a2ef101d087a4f7c5a868276d96a9c4f54` |

The plist declares `com.logi.optionsplus.installer`, bundle version `2.8.981479`, supported platform `MacOSX`, minimum system version `14.0`, and executable `logioptionsplus_installer`. These are parsed artifact declarations, not authenticated publisher claims. The universal Mach-O declares x86_64 and arm64 slices. [L1]

## Findings

| Question | Result | Evidence and boundary |
| --- | --- | --- |
| What is packaged? | Answered: one `logioptionsplus_installer.app`, one executable candidate, no nested app/framework/helper/JavaScript components identified by REA's bundle projection. | R1, R2; complete within ZIP central-directory inventory and path conventions. |
| Is the full installed agent included? | Answered within inventory: no separate `logioptionsplus_agent` executable or installed Options+ app tree appears. | R1, R2; the agent's **name** occurs in the installer executable, which does not supply its binary. |
| Is this an online bootstrapper? | Strong static inference: yes. | L1: installer identity, `-[AppDelegate _download]`, pipeline/depot downloader symbols, update-host literals, and downloader UI resources. Execution and actual network transactions were not observed. |
| Are there feature hints? | Answered: haptic, pointer-speed and gesture route constants exist. | L1; constant names and literals only, without recovered calls or handlers. |
| Does it reveal MX Master 4 HID++/haptic packets? | Unresolved: no such implementation or packet layout was established. | L1 bounded searches; native decompilation unavailable in this environment. Absence of matching strings is not proof of absent compiled functionality. |

The archive has 314 entries and declares 47,673,958 total uncompressed bytes, including AppleDouble metadata. The installer has 126 resource files: 40 HTML legal documents, 40 localization string files, 17 PNGs, 11 OTF fonts, five TIFFs, five nibs, three WOFF2 fonts, two GIFs, and one each of CSS, ICNS, and MP4. This inventory supports an installer UI interpretation; it does not contain a standalone device database, firmware bundle, or full application resource tree identifiable by member path. [R1, R2, L1]

Selected original-file offsets in the universal executable:

| Offset (decimal) | Literal observation | Interpretation |
| --- | --- | --- |
| 10,968,150 | `-[AppDelegate _download]` | Named installer download method; its execution was not observed. |
| 10,974,611 | `Starting downloading updates through pipeline.` | Download pipeline log string. |
| 11,024,316 | `https://updates.logitech.com` | Embedded update-host candidate; no request was made. |
| 11,125,222 | `Downloading LZMA-compressed depot '%s' of build_id '%s'...` | Installer can refer to separately downloaded depot payloads. |
| 11,019,457 | `pipeline://logioptionsplus/data/devices.json` | Resource identifier; the corresponding JSON file is not a ZIP member. |
| 11,029,190 | `/haptic_settings/configure` | Route constant, not a hardware command. |
| 11,033,247 | `/haptics/%s/play_waveform` | Route constant, not a packet format. |
| 11,030,575 | `/mouse/%s/pointer_speed` | Route constant. |

All table offsets are from L1 and refer to ZIP-extracted executable bytes. Similar literals recur in the arm64 slice. The x86_64 symbol table includes `logi::routes::haptics` names such as `s_play_waveform`, plus `logi::configuration::feature_flag_names::s_windows_haptics_integration_enabled`. That namespace and naming support the interpretation that the haptic strings are shared constants. There is no recovered call graph establishing their consumers. [L1]

Across all 270,221 maximal printable ASCII runs of length at least six in the universal binary, the recorded searches found no `hidpp`/`HID++`, `IOHID`, `SmartShift`, or word-bounded `MX Master 4`/`MX4` matches. Both slices' declared symbol tables likewise have no `hidpp` or `IOHID` symbol-name matches. These searches cannot exclude encoded or unnamed code. The declared dependency lists include IOKit and Bluetooth frameworks, which alone do not establish mouse protocol handling. [L1]

## Evidence ledger

R-prefixed entries below are actual REA Evidence records from version 6.7.0. L1 is explicitly an ordinary Python observation record, not REA Evidence.

| Ref | File | Identity |
| --- | --- | --- |
| R1 | [rea-artifact.json](https://github.com/Dijo-404/mx4-linux/blob/main/evidence/installer/rea-artifact.json) | Inspection `ev_a458ebcb5cfa2b290aae048a1370d4b84f2dd725b06959221be61274711cfac4`; embedded inventory `ev_fdfe8f918415b78fb5d8c453e365d717ddb2aff349034007f9ca644f3c14affc` |
| R2 | [rea-apple-graph.json](https://github.com/Dijo-404/mx4-linux/blob/main/evidence/installer/rea-apple-graph.json) | `ev_0f06ae230e91d98f2a5d5fe16e0b380a6a4409f7b7c5fc3ebe7d06d55fc1b014` |
| R3 | [rea-extraction.json](https://github.com/Dijo-404/mx4-linux/blob/main/evidence/installer/rea-extraction.json) | `ev_b524d96264bb91f49ab18e6c98a536a2588d32b81e8ed09b7033ce516b2a71ea` |
| L1 | [static-observations.json](https://github.com/Dijo-404/mx4-linux/blob/main/evidence/installer/static-observations.json) | `local_sha256_aae3f58937b3b9fa452afa4b8add785b487179f584a1f019d27b4b4079a390e4` |

R1/R2/R3 bind to graph manifest `agm_170c0e8ef3e979ca2dcc2db5a4b24daa3ac25d5bb922707c97482db22a4bfdd7`, graph digest `91e576e7fde24933493a0b8368709064476693a11213840ba8a02c71bf623ead`. R3 reports successful containment verification. The extracted binary and plist digests agree between R2 and L1.

REA's Codex registration was aligned after setup, but REA tools were not connected in the active agent session. Analysis therefore used the version-pinned CLI. Archive inspection, safe extraction, and Apple graph projection succeeded. The following REA native operations returned explicit `capability_unavailable` errors because their `native-macos` utilities require macOS:

- [rea-macho.json](https://github.com/Dijo-404/mx4-linux/blob/main/evidence/installer/rea-macho.json): `inspect_macho`.
- [rea-plist.json](https://github.com/Dijo-404/mx4-linux/blob/main/evidence/installer/rea-plist.json): `inspect_plist`.

These two files are error records and have no REA Evidence IDs. No analysis engine was installed. The fallback [inspect_static.py](https://github.com/Dijo-404/mx4-linux/blob/main/evidence/installer/inspect_static.py) uses Python's standard library to parse the supplied ZIP/plist and this Mach-O's declared headers, sections, dependencies and symbol names. It performs no disassembly or semantic recovery. Complete limitations are retained in each record.

## Reproduction

The ZIP and extracted vendor executable are intentionally outside the repository; the retained evidence consists of metadata and selected strings. From the repository root:

```sh
npx -y rea-agents@6.7.0 inspect-artifact "$HOME/Downloads/logioptionsplus_installer.zip" --format json > evidence/installer/rea-artifact.json
npx -y rea-agents@6.7.0 extract-artifact "$HOME/Downloads/logioptionsplus_installer.zip" --format json > evidence/installer/rea-extraction.json
python3 evidence/installer/inspect_static.py "$HOME/Downloads/logioptionsplus_installer.zip" --output evidence/installer/static-observations.json
```

For graph projection, create an input JSON object `{"inventory_evidence": [<R1 normalized_result.substeps[0].evidence>]}` and pass its path to `npx -y rea-agents@6.7.0 project-apple-application-graph INPUT.json --format json`. REA extraction chooses a new temporary output directory; the retained extraction record names the original session's directory, which is not required by the Python reproduction. Raw evidence also preserves the original local source paths to keep its provenance and identifiers intact.

## Remaining reconstruction work

The most useful next vendor artifact is a **full offline installer or installed Options+ app tree containing its backend/agent and device database**. Its digest must be recorded as a new target. Until that evidence or a documented protocol/capture is available, the application should discover features from the connected device and use independently established HID++ definitions. Haptics, Actions Ring, application profiles and Flow remain separate implementation questions; the route strings above do not establish how to reproduce them.

No macOS binary execution, live hardware validation, native handler decompilation, code-signature verification, or network behavior was covered by this pass.
