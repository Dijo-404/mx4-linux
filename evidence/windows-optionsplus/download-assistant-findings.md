# LogiDownloadAssistant static investigation

`evidence/windows-optionsplus/download-assistant.json` records the copied installation's complete 30-file inventory (29,639,175 bytes), SHA-256 hashes, main PE identity, selected literal offsets, C++ header declaration line/byte locations, bounded searches and limitations. Inventory excludes one locally generated REA evidence record.

The main executable is `bin/logi_download_assistant.exe`, 19,199,488 bytes, SHA-256 `885e25d7c1c6d6fde6476260ffd1b0d30fc14cf5894d7912a6330646dd5a1078`. It is an x86-64 Windows GUI PE32+ image with 12 sections. Its fixed version record at original file offset `0x01223cb8` reports file and product version `2.0.1020.0`. REA's target-free static PE resource parser returned Evidence `ev_f571da4b02c2552eff684e66d96b1647792ec0330892aa15527d3c39dbae5d84`, with complete coverage of 12 resource directory entries and four resources: icon, icon group, version, manifest. The original Evidence record is retained under ignored `evidence/artifacts/LogiDownloadAssistant/rea-pe-resources.json`. This does not authenticate the publisher or observe execution.

The evidence supports treating this as Logitech's software discovery/recommendation/downloader helper, separate from the Options+ settings/ring implementation. The strongest shipped literal observations are:

| Original EXE offset | Observation |
|---|---|
| `0x00c9e868` | `Engine::InitializeDevio` diagnostic name |
| `0x00c9dcb0` | Diagnostic records model ID, extended model ID, device name, PID, bus type, registry status and reminder count |
| `0x00c9dd78` | Diagnostic skips a model with notifications disabled |
| `0x00c9ddb0` | Diagnostic skips a model whose software is already installed |
| `0x00c9dde8` | Diagnostic skips a model whose reminder count was exceeded |
| `0x00c9de20..0x00c9e088` | Per-model manifest download, missing-manifest and JSON-parse diagnostics |
| `0x00c9a7f0` | Configuration key `supportedAppWin` |
| `0x00c9a910` | Configuration key `versionRegistryKey` |
| `0x00c9aa00` | Configuration key `commandLineArg` |
| `0x00c9aa50` | Configuration key `manualInstall` |
| `0x00c9aa90` | Configuration key `numberOfReminders` |
| `0x00c9aad0` | Configuration key `learnMoreUrl` |
| `0x00c9e180`, `0x00c9e1b0` | Resource ZIP download/unpack failure diagnostics |
| `0x00ca11b0` | `ToastHandler::ShowNotifySoftwarePrompt` diagnostic name |
| `0x00c9f050` | `Engine::DownloadSoftware` lambda diagnostic name |
| `0x00c9f158` | `Launched installer from %s` |
| `0x00c9f178` | `Waiting for installer to finish` |
| `0x00c9f278` | `Software installed successfully` |
| `0x00c9e7c0` | Update-server URL literal (not visited) |

These literal observations and their clustering support the architectural inference, but they are not native body decompilation or runtime traces.

The shipped `include/logi/demalu/` files are actual C++ declarations, providing stronger interface evidence than guessed symbol names:

- `Demalu.h:36..40` declares `checkUpdate()`, `downloadUpdate()`, `installUpdate()` and abort. Lines 64..72 declare payload/signature authentication; line 78 declares ZIP unpacking.
- `DemaluRequest.h:62..80` carries server, business-unit, app name/version, brand, product and OS selection fields. Line 83 carries the public-key path. Lines 108, 115 and 119 declare check/download/execute flags; accompanying comments specify that executing a payload requires successful authentication and archives are unpacked. These are interface contracts, not verified runtime enforcement.
- `DemaluRequest.h:121..124` documents in-memory payload support; `downloadDirectory` and `targetDirectory` are ignored for that mode.
- `Downloader.h:27..39` declares constructors for transfer versus availability query; `Downloader_Interface.h:21..22` exposes download/progress and HTTP/result information.
- `AuthenticateData.h:14..18` declares authentication and data/signature/key-path setters.
- `DemaluEvents_Interface.h` declares update-check, download progress/completion, install and unpack callbacks.
- CMake import metadata points to the shipped C++ `lib/logi_demalu.lib` static library. Full header implementations are not present.

Bounded negative results: scanning all printable ASCII and UTF-16LE strings of at least four characters in the main executable found no exact MX Master 4 / `B042` / `B048` literal; no specific `Feature19B0`, `Feature19C0`, corresponding `feature_x...` names, `radial_menu`, Actions Ring or `force_sensing` name matched the recorded expressions. This does not establish absence of encoded/generated functionality or dynamically retrieved model data. The helper does contain generic HID++/devio strings (109 matching runs) and ten generic `Windows.Devices.Haptics` WinRT type names; these are not evidence of MX4-specific haptics or an Actions Ring.

No executable was launched, no remote manifest downloaded and no embedded URL followed. Exact device-to-software recommendations, MX4-specific manifests, recommendation rules and installer arguments remain unknown without the relevant configuration or native body analysis. The useful result for the Linux project is the separation of responsibilities: this folder primarily explains how Logitech recommends and acquires the full software, while the Options+ agent, plugin service and device depots carry the more relevant settings/ring machinery.
