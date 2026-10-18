# Native ring gesture verification

On 2026-10-19, the production GTK ring passed 72 owned Wayland input cases: 36 with animations enabled and 36 with reduced motion. Each mode covered 75%, 100% and 150% ring sizes. The detailed, sanitized record is [native-drag-proof.json](native-drag-proof.json).

The tested `ring.py` is byte-identical to commit `b97eff81aba6f8470b09610f67005ab719d58799`, SHA-256 `f576cbf5dec56d9101ac4f39d458648785d2c05ea543e689588d72379d48fa73`. Surrounding application changes were in progress, so this result identifies the ring sources rather than claiming final application acceptance.

| Check | Animations enabled | Reduced motion |
| --- | ---: | ---: |
| Cases passed | 36/36 | 36/36 |
| Natural fresh paint barriers | 37/37 | 37/37 |
| Probe redraw fallbacks used | 0 | 0 |
| Native presses / motions / qualified releases | 36 / 125 / 33 | 36 / 125 / 33 |
| Helpers released, flushed, synchronized and reaped | 37/37 | 37/37 |
| Prior exact focus restored | Yes | Yes |
| Action effects | 0 | 0 |
| Nonfatal GDK frame timing warnings | 8 | 14 |

Cases covered ordinary clicks, vertical motion, left and right adjustments, reversal, pinned root and child targets, rejection, large motion, hide cancellation, the next click after cancellation, and preview behavior. Three deliberate ring unmaps per mode do not require a GTK release callback; the helper still had to release the button, flush, synchronize and exit successfully. Zero-timestamp motions and programmatic or duplicate release signals were excluded from native evidence.

Input went through the compositor's official virtual-pointer protocol to the production gestures. The opaque owned shield used layer TOP and the ring used OVERLAY on the same output. A native motion and owned-surface/coordinate check preceded every press. The temporary observer recorded existing gesture callbacks without adding a competing gesture. Every executable fixture branch was literal `/usr/bin/true`; no runner or controller executed it. There were no HID, keyboard, clipboard or host-switch operations. Actual human drag or thumb-wheel feel is outside this proof.

These runs were **not warning-free**. GTK 4.24.1 logged `gdk_frame_timings_throttling_hint: runtime check failed: (self->result != GDK_FRAME_PREPARING)` eight/fourteen times. No gesture, paint-readiness, cleanup or focus assertion failed, and neither log contained a traceback or another warning/error. A read-only review of primary GTK sources established no concrete application lifecycle defect. The exact toolkit/layer-shell/compositor/instrumentation interaction remains unconfirmed; this is a documented validation limitation, not a confirmed upstream bug. The private function concerns frame scheduling; see [GTK timing source](https://raw.githubusercontent.com/GNOME/gtk/4.24.1/gdk/gdkframetimings.c), [frame clock source](https://raw.githubusercontent.com/GNOME/gtk/4.24.1/gdk/gdkframeclock.c), and the [after-paint signal documentation](https://docs.gtk.org/gdk4/signal.FrameClock.after-paint.html).

The exact tested source tools and their hashes are retained in [native-drag-tools](native-drag-tools/README.md). The execution gate is closed. A temporary-copy archive check rebuilt the client with strict compiler warnings, passed the sanitized display-free C parser tests, all 26 Python pure tests and the memory-only GObject ordering test. The rebuilt binary matched the executed client hash; that archive check did not execute it. Raw temporary logs and recovery files remain outside the repository; the public JSON omits prior window identities and private state.
