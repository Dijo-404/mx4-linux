# Contributing

MX4 Linux is in early development. Issues and pull requests are welcome at [Dijo-404/mx4-linux](https://github.com/Dijo-404/mx4-linux). Read the [architecture guide](docs/architecture.md) and [current validation limits](docs/validation.md) before changing hardware or desktop integration.

## Set up and check a change

Install the [system dependencies](docs/install.md#system-dependencies), then:

```bash
git clone https://github.com/Dijo-404/mx4-linux.git
cd mx4-linux
./scripts/setup.sh
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src tests
for script in scripts/*.sh; do bash -n "$script"; done
```

Use `.venv/bin/mx4-linux gui --demo` to inspect the settings interface without device access, and `actions run ID --dry-run` or `ring --preview` to inspect desktop behavior without executing a routine. Tests use simulated hardware; passing them is not a physical-device verification.

If changing udev files, check their syntax when `udevadm` is available:

```bash
udevadm verify data/70-mx4-linux.rules data/71-mx4-input.rules
```

## Pull requests

- Keep commits focused and ordered so protocol, UI and documentation changes can be reviewed independently.
- Explain the triggering problem and resulting behavior, with relevant checks and remaining uncertainty.
- Keep Solaar imports inside isolated workers and preserve the pinned dependency unless the compatibility change is deliberate and verified.
- Preserve opt-in diversion, exact reporting restoration and cancelled cleanup releases. Include regression coverage for meaningful lifecycle or validation changes.
- Keep command steps as explicit argument arrays, validate complete documents before writes and preserve cancellation/held-key cleanup.
- Update documentation when commands, configuration, access rules or supported behavior change.

Do not bundle vendor installers, downloaded proprietary binaries, virtual environments, build caches or personal configuration. Avoid user paths, device serials and unrelated desktop content in new screenshots and reports.

## Bug reports and hardware validation

Include the application version, distribution, Python/GTK/Libadwaita versions, compositor/session, Bolt or Bluetooth connection, steps to reproduce and the actual error. Relevant `doctor --json` and `devices --json` output helps; redact identifying details first.

Describe hardware checks precisely: distinguish read-only discovery, listener setup/cleanup, native input tests and physical button/settings interaction. Run write tests only on your own device, and describe which values changed and what was observed. No complete original-settings backup or rollback transaction is available.

Report sensitive security issues using [SECURITY.md](SECURITY.md), not a public issue. Flow is currently deferred; proposals for it should describe platform scope separately from the existing Niri integration.
