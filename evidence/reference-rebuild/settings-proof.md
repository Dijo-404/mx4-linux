# Temporary persistent-settings proof

**Executed successfully after independent review.** All ten cases passed against
the physical MX Master4 using reviewed core commit `49dc6f4`. Each alternative was
read back and every original restored before the next case. Final raw wheel
mode/threshold/torque and all six reporting/mapping snapshots matched; application
configuration and remembered settings remained unchanged. Sanitized results are
in [settings-proof.json](settings-proof.json); the reviewed harness is
[settings-proof.py](settings-proof.py). Private recovery values remain outside
the repository.

Preparation itself performed no hardware I/O. The original temporary commands
and constraints below describe the root-controlled run; they are not an
invitation to run concurrently with mouse configuration activity.

The root agent must review `/tmp/mx4-settings-proof.py` against the final current
worker API before running. Preparation reviewed `Backend.write`,
`profiles.apply_profile` / full validation, `configuration_lock`, `Ownership`,
`_validate_value`, `_read_live`, Solaar's enhanced SmartShift/torque writers and
the prior physical-proof GET helper. This tests production individual writes;
it does not claim a live `apply_profile` or reconnect test.

## Commands

From `/home/dj/Projects/mx4-linux`:

```sh
.venv/bin/python /tmp/mx4-settings-proof.py
.venv/bin/python /tmp/mx4-settings-proof.py --self-check
.venv/bin/python -m py_compile /tmp/mx4-settings-proof.py
.venv/bin/python -m ruff check /tmp/mx4-settings-proof.py
```

These four checks have passed during preparation. The first two explicitly
report `hardware_io: false`. The self-check only exercises pure Demo-derived
descriptors/alternatives/hidden wheel threshold cases, an inert JSON child,
and three injected evidence-save failures after an inert mutation;
it does not open the Demo GUI, GTK, Solaar or HID.

Only after root review and with the normal integration listener stopped:

```sh
.venv/bin/python /tmp/mx4-settings-proof.py --run
```

Run exactly one instance, in the current environment with the **real current
XDG state paths** so its locks see the normal application's journal/listener.
Do not isolate `XDG_STATE_HOME`. Do not run concurrently with focus/input proof
scripts or GUI settings writes. No user mouse gesture is required. Keep the
mouse awake; do not press the remapped Back button during its brief case.

Collect the running exec session to completion. Tool waits and user progress
messages remain root's responsibility. A request may legitimately take 90s.

## Preflight and containment

- Require exactly one online MX4, valid non-demo stable identity, no scan
  diagnostics, and all 11 reviewed descriptors. Validate every original value
  and derive the complete ten-case plan before the first setter.
- Hold the production per-unit **nonblocking configuration lock for the whole
  batch**, excluding the normal event listener, other profile batch and host
  switch. Check the production listener lock and journal using a short
  `Ownership` scope, releasing it before a production native mapping write
  reacquires it. Refuse any existing or invalid reporting journal/active lock.
- GET all six original reporting flags/mappings and the enhanced wheel's
  first three bytes: mode, disengage threshold, torque. Snapshot helper checks
  fresh `require_identity` before and after these GETs. Refuse preexisting
  owned diversion/rawXY bits.
- Every production `Backend.write` carries `expected_stable_id` and
  `remember=False`; every setter is preceded by a fresh same-unit scan and
  production descriptor validation. No user desired settings are enabled,
  replaced, replayed or updated.
- No host switching, pairing, factory reset, haptic pulse or reporting/diversion
  setter. Haptic waveform 0 was a separate prior test and is not repeated here.
- Only Back CID 83 is remapped, preferring allowed Forward CID 86, then another
  reviewed nonprimary mapping from 82/195/196/416. The complete six-control
  baseline must match after restoration.

## Cases and confirmation

The ten potential persistent cases are DPI; haptic strength; panel force;
vertical inversion; high-resolution scrolling; thumb inversion; SmartShift;
ratchet mode; ratchet torque; and Back 83 native mapping.

Choice alternatives are the closest supported choice. Ordinary integer ranges
move by one valid step, booleans invert, and panel force selects only the
fresh descriptor's exact minimum/default/maximum. A fixed/uneditable capability
is skipped. No pressure unit, named hardness or speed mapping is invented.

The wheel trio forms a coupled restoration group. In free spin, SmartShift's
descriptor reports 1 and hides its retained threshold. The GET-only raw snapshot
therefore records the real threshold. Wheel cases are skipped if that byte does
not round-trip through the reviewed public setter: raw 1–49, or raw 255 exposed
as logical 50. The writer maps logical 50 to raw 255, so raw 0 or raw 50 cannot
be assumed exactly restorable. The recipe never sends unreviewed raw SETs.

To test threshold while originally free spinning, the harness first selects
the verified mode 2 so the production threshold readback can confirm it. Wheel
restoration selects mode 2, restores the exact original threshold and torque,
then restores the original mode. Both fresh descriptors and **all three exact
raw bytes** must match the original group. Torque preserves mode through the
reviewed Solaar writer; unexpected coupled-byte changes fail the case.

Before each case, every original setting, mapping, flag and wheel byte must
still match. Originals and attempted case are atomically/fsync persisted before
any write. After the alternative, the production acknowledgment/readback and a
new independent scan/snapshot must confirm the change. A `finally` restores the
case and freshly confirms **all** originals before any next case. On failed
test acknowledgment, it still attempts the known original restoration and then
stops; it never continues with another unrelated case.

Saving the cleanup marker/result cannot prevent restoration: checkpoint errors
are caught, restoration runs, and evidence-save failure is reported only after
that cleanup attempt. The self-check injects failures at the marker, at the
cleanup result, and at both, proving the original value is restored exactly
once and the case fails instead of continuing. The earlier fsynced pre-write
baseline/intent remains available if later evidence updates cannot be saved.

## Interruption, timeout and recovery evidence

SIGINT/SIGTERM set a cancellation flag. The current worker is allowed to finish;
the case restores in `finally`, ignoring cancellation during restoration, and
no next case starts. Children start in their own session so terminal Ctrl+C
does not interrupt a child midway through a physical write.

Production `Backend.request` uses `subprocess.run(timeout=90)`, which would kill
a worker on timeout. This harness inherits production `Backend.write` but
replaces only `request` with private IPC that **never kills a mutating child**.
At 90s it prints a sanitized watchdog marker, retains the configuration lock,
and waits for the child to exit before failing into restoration. If a child
never exits, the harness intentionally remains alive holding the lock. Do not
SIGKILL it or start another operation; root must inspect/resolve that blocked
worker first. This is the explicit limit to bounded termination: safe cleanup
cannot be guaranteed for a permanently hung or physically disconnected device.

Recovery evidence is always retained in a private mode-700
`/tmp/mx4-settings-proof-*/recovery.json` (mode 600), including successful runs.
It contains only a SHA256 unit digest, original capability/value descriptors,
six flag/mapping snapshots, three wheel bytes, attempted case and sanitized
exception class/status. It omits stable IDs, device paths, host names, window
titles, original app configuration and desired settings. Child IPC containing
the identity remains in memory; stderr/raw diagnostics are suppressed.

An uncertain restoration is a failure even if a setter acknowledged. Preserve
the recovery file and stop; never remove the application's journal or try to
adopt unknown reporting flags. A successful final record must say `complete`,
`passed: true`, `restored: true`, with configuration/desired settings unchanged
and exact six-control restoration. Inspect the result list for skipped cases;
do not describe skipped cases as verified.

The recovery document starts with `passed: false`; only final complete fresh
restoration and config/desired checks can set it true. Errors leave it false.
If writing the failure record also fails, sanitized stdout still reports the
failure and evidence-save errors; the older durable record is retained.

## Practical limits

The production configuration lock excludes its listener/host/profile paths,
but ordinary scalar writes in other app processes do not all acquire this
lock. Run with the GUI idle, no third-party mouse configuration activity. Fresh
whole-device equality checks detect observable interference; HID++ provides no
universal external-writer ownership token. No subjective feel, wheel direction
gesture, pointer-distance measurement or Bluetooth reconnect is proven by
acknowledgment/readback. The preparation agent performed no physical test; the
subsequent root-controlled run and its limits are recorded above.
