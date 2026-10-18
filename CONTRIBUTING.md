# Contributing

MX4 Linux is in early development. Issues and pull requests are welcome at [Dijo-404/mx4-linux](https://github.com/Dijo-404/mx4-linux). Read the [architecture guide](docs/architecture.md) and [current validation limits](docs/validation.md) before changing hardware or desktop integration.

## Set up and check a change

Install the [system dependencies](docs/install.md#system-dependencies), then:

```bash
git clone https://github.com/Dijo-404/mx4-linux.git
cd mx4-linux
./scripts/setup.sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests scripts/gtk-smoke.py
.venv/bin/python -m compileall -q src tests
for script in scripts/*.sh; do bash -n "$script"; done
npm ci --ignore-scripts
npm test
npm run check
npm run test:package
```

Build outputs (`build/`, `dist/`), pytest/Ruff caches, `__pycache__/` and source `*.egg-info/` directories are generated and can be removed when no longer needed. Keep `.venv/` for the installed development runtime. `.tools/` also holds the installed wtype checksum used by setup; its downloaded source and build directories are disposable.

The tracked records in `evidence/` support the research and validation guides. Current documentation images live in `docs/assets/`; the README shares the application icon from `src/mx4_linux/icons/`.

Use `.venv/bin/mx4-linux gui --demo` to inspect the settings interface without device access, and `actions run ID --dry-run` or `ring --preview` to inspect desktop behavior without executing a routine. Tests use simulated hardware; passing them is not a physical-device verification.

On a graphical session, `GDK_BACKEND=x11 .venv/bin/python scripts/gtk-smoke.py` checks demo loading, finite wheel/ring animations, reduced motion and the editor. For a headless machine with Xvfb installed:

```bash
GDK_BACKEND=x11 dbus-run-session -- xvfb-run -a .venv/bin/python scripts/gtk-smoke.py
```

The smoke script uses a temporary configuration directory and preview callbacks; it does not execute routines or write hardware settings. For UI changes, also check the actual window at narrow and normal widths, keyboard navigation, visible focus, both color schemes and disabled desktop animations.

`PYTHON=/usr/bin/python3 GDK_BACKEND=x11 npm run test:native` additionally installs the real npm archive into a temporary prefix, performs setup in an isolated XDG directory, checks its CLI and exercises the installed GTK runtime. It downloads Python dependencies; use the system interpreter that can import GObject. CI runs this under Xvfb.

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
- Add a concise user-visible entry under `Unreleased` in [CHANGELOG.md](CHANGELOG.md).

Do not bundle vendor installers, downloaded proprietary binaries, virtual environments, build caches or personal configuration. Avoid user paths, device serials and unrelated desktop content in new screenshots and reports.

## Code conventions and boundaries

- Use Python 3.11-compatible syntax, four-space indentation, descriptive `snake_case` names and small functions. Match the touched module's style; keep unrelated formatting out of the diff. Ruff checks syntax, definite correctness errors and unused imports through the checked-in configuration.
- Keep GTK construction and widget updates on the GTK thread. Run device I/O and potentially blocking desktop work off that thread; return results through the existing callbacks. Do not rescan hardware when a confirmed write response is sufficient.
- Keep Solaar imports and handles in workers. UI modules depend on the backend interface, and animation/state models remain testable without GTK or hardware. Add a layer only when a concrete second caller needs it.
- Animate on GTK's frame clock using elapsed time. Stop callbacks when motion settles or a widget hides; honor `gtk-enable-animations`. Native labels, keyboard operation and accessible names must remain available.
- Node code uses built-in modules, explicit argument arrays and two-space indentation. The npm launcher stays a small bridge to the native application. Keep npm installation free of hooks that install Python, invoke sudo, access devices or start the GUI.
- Validate external JSON and process responses before use. Preserve atomic configuration writes, cancellation, worker deadlines and actionable error messages. Avoid logging serials or private desktop contents.
- Add a focused regression test for changed validation, lifecycle or subprocess behavior. Avoid tests that only repeat constants or widget construction without checking behavior.

The architecture guide describes responsibilities. Keep shared behavior in its existing owner instead of duplicating it across GUI, CLI and package entry points. `.editorconfig` defines whitespace, `pyproject.toml` defines Python metadata/checks, and `package.json` defines the npm launch surface.

## CI and release preparation

CI tests Python 3.11 and 3.14, Node 22 and 24, actual npm archive installation, and a native GTK demo smoke test on Ubuntu 24.04. Native tests do not establish Niri/Wayland or physical hardware compatibility. GitHub Actions dependencies are pinned by commit; Dependabot proposes updates for review. Solaar updates need deliberate worker compatibility validation because internal APIs are used.

Before a release, update `pyproject.toml`, `src/mx4_linux/__init__.py`, `package.json` and `package-lock.json` together, move reviewed changelog entries into a dated version, then run the checks above. `npm run check` rejects differing package versions and missing runtime assets. Inspect `npm pack --dry-run` and build Python distributions with `.venv/bin/python -m build`.

The **Build release packages** workflow runs manually or when a GitHub release is published. It requires CI, verifies a release tag of `vVERSION`, and uploads wheel, source archive, npm archive and SHA-256 checksums as an Actions artifact. It does not publish to npm or PyPI. Download and review that artifact before a maintainer publishes it.

For an initial npm release, verify ownership/availability of the package name, use an npm account with publishing rights and 2FA, and publish the reviewed `.tgz` with `npm publish ./mx4-linux-VERSION.tgz --access public`. Configure [npm trusted publishing](https://docs.npmjs.com/trusted-publishers/) before adding automatic registry publication; [npm package metadata](https://docs.npmjs.com/cli/v11/configuring-npm/package-json/) documents `bin`, packaged files and platform requirements. Registry configuration and publication are maintainer actions outside application setup.

## Bug reports and hardware validation

Include the application version, distribution, Python/GTK/Libadwaita versions, compositor/session, Bolt or Bluetooth connection, steps to reproduce and the actual error. Relevant `doctor --json` and `devices --json` output helps; redact identifying details first.

Describe hardware checks precisely: distinguish read-only discovery, listener setup/cleanup, native input tests and physical button/settings interaction. Run write tests only on your own device, and describe which values changed and what was observed. No complete original-settings backup or rollback transaction is available.

Report sensitive security issues using [SECURITY.md](SECURITY.md), not a public issue. Flow is currently deferred; proposals for it should describe platform scope separately from the existing Niri integration.
