# MX Master 4 Linux support research

Checked 2026-10-18 against upstream Solaar commit
`e7304c4c451cc9bb4f206a914844525e67856a28`. This is source research, not a
claim that a physical mouse has been tested with this application.

## Practical conclusion

Linux can control the MX Master 4's haptic motor through HID++; extracting
Logitech's macOS executable is not necessary for the basic haptic protocol.
Solaar documents `solaar config 'mx master 4' haptic-play 'HAPPY ALERT'` and
supports calling the same setting from its rules. Most hardware settings
are temporary and must be reapplied after reconnecting or waking the mouse.
[Solaar capabilities](https://pwr-solaar.github.io/Solaar/capabilities/).

Use Solaar **1.1.18 or newer** for the haptic and force-sensing settings.
1.1.17 introduced those settings but was withdrawn; 1.1.18 is the corrective
release. The tagged 1.1.18 source was also inspected and contains the haptic,
force-sensing and MX4 ratchet torque handling. The latest stable release shown upstream during this investigation
is 1.1.20. Source APIs below are internal APIs and should be checked against
the installed version.
[1.1.18 release](https://github.com/pwr-Solaar/Solaar/releases/tag/1.1.18),
[upstream releases](https://github.com/pwr-Solaar/Solaar/releases).

The Actions Ring is a desktop overlay, rather than a screen inside the
mouse. Logitech describes both click-then-select and hold-then-release
selection, requires Options+ for its implementation, and treats Flow and
application-specific integrations as software features. A Linux overlay
can implement the interaction independently; feature parity requires
separate desktop integrations.
[Logitech getting started](https://support.logi.com/hc/en-us/articles/28321445268247-Getting-Started-MX-Master-4).

## Device identity and capabilities

Upstream's actual device dump identifies the ordinary mouse as **B042**,
HID++ 4.5, model ID `B04200000000`. It exposes nine controls, including six
physical controls that can be diverted: middle, back, forward, gesture,
Smart Shift and Haptic. Left/right are not reported as divertable. Features
include unified battery, host switching, enhanced SmartShift, high-resolution
wheel, thumb wheel and adjustable DPI.
[MX Master 4 device dump](https://github.com/pwr-Solaar/Solaar/blob/e7304c4c451cc9bb4f206a914844525e67856a28/docs/devices/MX%20Master%204.text).

The Business variant uses **B048**; an upstream report contains its feature
dump and records a diversion UI problem on an older Solaar build. Treat this
as evidence for capability discovery, not proof every firmware/build works.
[Business variant report](https://github.com/pwr-Solaar/Solaar/issues/3264).

| Capability | HID++ feature | Solaar setting / Linux route |
| --- | --- | --- |
| Sensor DPI | `0x2201` | `dpi`; use device-reported choices |
| Wheel mode / threshold | `0x2111` | `scroll-ratchet`, `smart-shift` |
| Wheel resistance | `0x2111` | `scroll-ratchet-torque` |
| Vertical wheel | `0x2121` | `hires-smooth-invert`, `hires-smooth-resolution`, `hires-scroll-mode` |
| Thumb wheel | `0x2150` | `thumb-scroll-invert`, `thumb-scroll-mode` |
| Buttons | `0x1B04` | `reprogrammable-keys`, `divert-keys` |
| Haptic enable / strength | `0x19B0` | `haptic-level` |
| Haptic pulse | `0x19B0` | `haptic-play` |
| Panel activation force | `0x19C0` | `force-sensing`; discover its range |
| Easy-Switch | `0x1814` | `change-host`; disconnects current host |
| Gestures / Actions Ring | `0x1B04` notifications | Host application or Solaar rules |
| App profiles / macros | Host software | Desktop-specific integration |
| Flow / proprietary plugins | Host software | No compatibility established here |

Setting names and their feature association were verified in
[Solaar settings source](https://github.com/pwr-Solaar/Solaar/blob/e7304c4c451cc9bb4f206a914844525e67856a28/lib/logitech_receiver/settings_templates.py).
The source also contains an MX4-specific torque write adjustment; use a
current Solaar build if wheel resistance changes do not take effect.

`thumb-scroll-mode` and `hires-scroll-mode` mean **wheel diversion**, not a
normal scroll preference: enabling them routes events to HID++ notifications.
This application's one-request worker has no event consumer, so it deliberately
excludes both settings and `divert-keys` to preserve ordinary mouse input.

## CLI interface

The CLI is useful for configuration and initial hardware checks. It accepts
a device number, serial number or name substring. Its output is text, not a
JSON API. Pass commands as an argument array from Python, with no shell.
[CLI parser](https://github.com/pwr-Solaar/Solaar/blob/e7304c4c451cc9bb4f206a914844525e67856a28/lib/solaar/cli/__init__.py).

```sh
solaar show
solaar config 'mx master 4'
solaar config 'mx master 4' dpi 1600
solaar config 'mx master 4' scroll-ratchet Ratcheted
solaar config 'mx master 4' smart-shift 20
solaar config 'mx master 4' haptic-level 50
solaar config 'mx master 4' haptic-play 'HAPPY ALERT'
```

The last five lines change device state. Per-button settings take a control
name/ID followed by a value, for example `divert-keys Haptic Diverted`.
Diverting changes where the event goes, so the receiving process must remain
available. Query `solaar config <device> <setting>` for actual choices before
writing. The CLI may also persist values in Solaar's configuration or forward
the update to its running GUI.
[CLI configuration implementation](https://github.com/pwr-Solaar/Solaar/blob/e7304c4c451cc9bb4f206a914844525e67856a28/lib/solaar/cli/config.py).

Repeated CLI invocations have discovery/startup latency; an upstream issue
reports substantial delays. A live ring should retain its device connection
instead of launching a fresh CLI process for every hovered item.
[Haptic latency discussion](https://github.com/pwr-Solaar/Solaar/issues/3073).

## Python and notification interfaces

The installed Solaar distribution contains `logitech_receiver` and `solaar`.
Source-level entry points are:

```python
from logitech_receiver import base, device, receiver, settings_templates
from logitech_receiver.hidpp20_constants import SupportedFeature

# Enumerate DeviceInfo records; create a direct device or a receiver.
for info in base.receivers_and_devices():
    if info.isDevice:
        owner = device.create_device(base, info)
    else:
        owner = receiver.create_receiver(base, info)
    # A direct device is owner; a receiver can be iterated for paired devices.
    # Retain handles while needed and close owner when finished.

# Given a discovered, online Device named mouse:
setting = settings_templates.check_feature_setting(mouse, "haptic-level")
if setting is not None:
    current_level = setting.read(cached=False)
    # setting.write(50, save=False) changes the hardware setting.
```

`base.receivers_and_devices()` returns discovery records.
`create_device(base, info)` opens a directly connected device; receiver
creation and iteration follow Solaar's own CLI. `Device.ping()` refreshes
online status; `Device.close()` releases owned resources.
[Discovery](https://github.com/pwr-Solaar/Solaar/blob/e7304c4c451cc9bb4f206a914844525e67856a28/lib/logitech_receiver/base.py),
[Device](https://github.com/pwr-Solaar/Solaar/blob/e7304c4c451cc9bb4f206a914844525e67856a28/lib/logitech_receiver/device.py),
[Receiver](https://github.com/pwr-Solaar/Solaar/blob/e7304c4c451cc9bb4f206a914844525e67856a28/lib/logitech_receiver/receiver.py).

`Device.feature_request(feature, function, *params)` resolves a feature ID to
its runtime index. Never assume the dump's index is constant across models.
For controls, `mouse.keys` provides objects with `.key`, `.flags`,
`.set_diverted(bool)` and `.set_rawXY_reporting(bool)`.
`REPROG_CONTROLS_V4` report `0x00` contains up to four big-endian 16-bit
currently pressed control IDs; `0x10` contains signed big-endian 16-bit
`dx, dy`. Detect edges by comparing pressed sets.
[HID++ helpers](https://github.com/pwr-Solaar/Solaar/blob/e7304c4c451cc9bb4f206a914844525e67856a28/lib/logitech_receiver/hidpp20.py),
[notification decoder](https://github.com/pwr-Solaar/Solaar/blob/e7304c4c451cc9bb4f206a914844525e67856a28/lib/logitech_receiver/notifications.py).

Haptic control ID is `0x01A0`. There are sixteen named waveform IDs:
`00` sharp state change, `01` damp state change, `02` sharp collision,
`03` damp collision, `04` subtle collision, `05` happy alert, `06` angry
alert, `07` completed, `08` square, `09` wave, `0A` firework, `0B` mad,
`0C` knock, `0D` jingle, `0E` ringing and **`1B` whisper collision**.
The final ID is not `0F`. Only expose waveforms returned by capability
discovery.
[Haptic constants](https://github.com/pwr-Solaar/Solaar/blob/e7304c4c451cc9bb4f206a914844525e67856a28/lib/logitech_receiver/hidpp20_constants.py),
[control constants](https://github.com/pwr-Solaar/Solaar/blob/e7304c4c451cc9bb4f206a914844525e67856a28/lib/logitech_receiver/special_keys.py).

## Desktop integration and remaining checks

Solaar's udev rule grants seated users access to Logitech hidraw devices and
`/dev/uinput`. Distro packages may already install it. Mouse configuration
requires hidraw access; synthesizing keyboard/mouse input also requires
uinput access.
[Upstream udev rule](https://github.com/pwr-Solaar/Solaar/blob/e7304c4c451cc9bb4f206a914844525e67856a28/rules.d/42-logitech-unify-permissions.rules).

Solaar's rules can launch executables and react to diverted controls. Its
documentation explicitly limits process detection and modifier handling on
Wayland; GNOME has an extension for process detection. Do not promise
universal application profiles or global pointer positioning on every
Wayland compositor without testing that compositor.
[Solaar rules](https://pwr-solaar.github.io/Solaar/rules/).

Outstanding hardware validation: Bolt and Bluetooth discovery, wake/reconnect
handling, reported DPI/force/waveform ranges, actual haptic playback, panel
press/release events, restoration of diverted controls at exit, and behavior
when another settings daemon is present. Desktop validation must cover
overlay placement, focus restoration and chosen action dispatch. No claim
is made for Logitech Flow, Options+ account services, Marketplace plugins,
or Windows application plugin APIs.
