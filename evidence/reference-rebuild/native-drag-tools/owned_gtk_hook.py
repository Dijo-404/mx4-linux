"""Prepared, gated gesture-only GTK hook. Import and pure helpers do not use GTK.

No DesktopActions, controller, worker, HID, clipboard or executing runner is used.
The production ring receives real Wayland pointer events only in the reviewed
run function. Native callbacks merely record IDs, directions and preview flags.
"""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import time
from uuid import uuid4


NOOP = {"type": "command", "argv": ["/usr/bin/true"]}
CASES = (
    "plain", "vertical", "right", "left", "reverse", "pinned-root", "child",
    "reject", "huge", "hide-cancel", "after-cancel", "preview",
)


class ProbeFailure(RuntimeError):
    """Static diagnostic only; no personal app/context data belongs in errors."""


def require(condition, message):
    if not condition:
        raise ProbeFailure(message)


def noop_document(scale):
    """Construct from scratch, never load defaults or the user's document."""
    require(type(scale) is int and 75 <= scale <= 150, "Invalid scale")
    actions = {
        f"root-{index}": {"label": f"Harmless root {index + 1}", "steps": [deepcopy(NOOP)]}
        for index in range(8)
    }
    for action_id in ("root-0", "child-0"):
        actions.setdefault(action_id, {"label": "Harmless child", "steps": [deepcopy(NOOP)]})
        actions[action_id]["adjustment"] = {
            direction: [deepcopy(NOOP)] for direction in ("increase", "decrease")
        }
    actions["child-1"] = {"label": "Harmless plain child", "steps": [deepcopy(NOOP)]}
    document = {
        "version": 1, "ring": [f"root-{index}" for index in range(8)],
        "actions": actions, "ring_groups": {"2": ["child-0", "child-1"]},
        "button_bindings": {}, "ring_button": None, "haptic_feedback": False,
        "ring_scale": scale,
    }
    assert_all_noops(document)
    return document


def assert_all_noops(document):
    """Reject any executable step at any depth except the exact literal no-op."""
    def walk(value):
        if isinstance(value, dict):
            if "type" in value:
                require(value == NOOP, "Document contains a non-no-op step")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(document)
    for action in document["actions"].values():
        require(action["steps"] == [NOOP], "Ordinary branch must be literal no-op")
        if "adjustment" in action:
            require(set(action["adjustment"]) == {"increase", "decrease"}, "Unknown adjustment branch")
            require(all(steps == [NOOP] for steps in action["adjustment"].values()),
                    "Adjustment branch must be literal no-op")


def quantized_coordinate(value):
    require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 16384,
            "Invalid absolute coordinate")
    return round(value * 256)


def initial_motion_warmup(position, box, sequence, may_be_down):
    """A single UP-only first-command point, farther than proof tolerance."""
    if sequence != 0 or may_be_down:
        return None
    left, top, width, height = box
    limits = (left * 256, top * 256, (left + width) * 256, (top + height) * 256)
    target = tuple(quantized_coordinate(value) for value in position)
    require(limits[0] <= target[0] < limits[2] and limits[1] <= target[1] < limits[3],
            "Initial requested motion is outside helper bounds")
    for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2)):
        candidate = (position[0] + dx, position[1] + dy)
        if min(candidate) < 0 or max(candidate) > 16384:
            continue
        point = tuple(quantized_coordinate(value) for value in candidate)
        if limits[0] <= point[0] < limits[2] and limits[1] <= point[1] < limits[3]:
            return candidate
    raise ProbeFailure("No distinct bounds-safe initial UP motion point")


def request_motion_with_initial_warmup(client, position, mark):
    warmup = initial_motion_warmup(position, client.motion_box, client.sequence, client.may_be_down)
    if warmup is not None:
        client.command("M", *(quantized_coordinate(value) for value in warmup))
    before = mark()  # Only the subsequent requested actual motion qualifies.
    client.command("M", *(quantized_coordinate(value) for value in position))
    return before, warmup


def wait_exact_allocation(observe, expected, wait_until, diagnostic):
    """Wait for configure/allocation, not just the earlier map notification.

    Dependencies are injected so this ordering can be checked without GTK.
    Only dimensions and booleans enter the retained diagnostic.
    """
    def ready():
        sample = observe()
        diagnostic.update({
            "expected_width": expected[0], "expected_height": expected[1],
            "observed_width": sample.get("width"), "observed_height": sample.get("height"),
            "mapped": sample.get("mapped") is True,
            "monitor_matches": sample.get("monitor_matches") is True,
        })
        return (diagnostic["mapped"] and diagnostic["monitor_matches"] and
                (diagnostic["observed_width"], diagnostic["observed_height"]) == expected)
    wait_until(ready, "Owned shield exact output allocation did not settle")


def show_after_paint(widget, show_and_settle, observe, display, wait_until, phases, diagnostic):
    """Observe before show; qualify a fresh final frame, then sync own GDK.

    A natural AFTER_PAINT captured during show/reveal/allocation is sufficient.
    A bounded fallback redraw is explicitly marked as a probe intervention.
    No native pointer helper may be constructed until this returns successfully.
    """
    require(not widget.get_mapped(), "Paint observer must be installed before show")
    diagnostic.update(observer_installed_before_show=True, after_paint_observed=False,
                      natural_after_paint=False, probe_queue_draw_requested=False,
                      probe_request_phase_requested=False, frame_before=None, frame_after=None,
                      gdk_display_sync_called=False, observed_frame_count=0, last_frame=None,
                      observer_error_types=[])
    current = {"clock": None, "handler": None, "show_started": False}
    handlers = []

    def snapshot():
        sample = observe()
        value = {key: sample.get(key) is True for key in
                 ("mapped", "visible", "allocation_matches", "monitor_matches", "show_state_matches")}
        value["tick_active"] = sample.get("tick_active") is not False
        for key in ("width", "height"):
            number = sample.get(key)
            value[key] = number if type(number) is int and 0 <= number <= 16384 else None
        return value

    def matches(sample):
        return all(sample[key] for key in
                   ("mapped", "visible", "allocation_matches", "monitor_matches", "show_state_matches")) and not sample["tick_active"]

    def painted(clock):
        try:
            sample = snapshot()
            counter = clock.get_frame_counter()
            diagnostic["observed_frame_count"] = min(4096, diagnostic["observed_frame_count"] + 1)
            diagnostic["last_frame"] = dict(sample, frame=counter)
            # The new signal, not a cached counter, establishes freshness. A
            # frame already in progress when attached may keep the same count.
            if (current["show_started"] and clock == current["clock"] and
                    widget.get_frame_clock() == clock and matches(sample)):
                diagnostic.update(after_paint_observed=True, frame_after=counter,
                                  natural_after_paint=not diagnostic["probe_queue_draw_requested"])
        except Exception as error:
            if len(diagnostic["observer_error_types"]) < 8:
                diagnostic["observer_error_types"].append(type(error).__name__)

    def attach(*_arguments):
        try:
            clock = widget.get_frame_clock()
            if clock is None or clock == current["clock"]:
                return
            if current["handler"] is not None:
                current["clock"].disconnect(current["handler"])
            current.update(clock=clock, handler=clock.connect_after("after-paint", painted))
            diagnostic["frame_before"] = clock.get_frame_counter()
        except Exception as error:
            if len(diagnostic["observer_error_types"]) < 8:
                diagnostic["observer_error_types"].append(type(error).__name__)

    def ready():
        require(not diagnostic["observer_error_types"], "Paint readiness observer failed")
        return diagnostic["after_paint_observed"]

    try:
        handlers.extend(widget.connect_after(name, attach) for name in ("realize", "map"))
        attach()
        current["show_started"] = True
        show_and_settle()
        natural_timeout = "Natural owned surface after-paint did not settle"
        try:
            wait_until(ready, natural_timeout, seconds=.3)
        except ProbeFailure as error:
            if str(error) != natural_timeout:
                raise
            require(current["clock"] is not None and matches(snapshot()),
                    "Owned surface is not ready for bounded probe redraw")
            diagnostic["probe_queue_draw_requested"] = True
            widget.queue_draw()
            diagnostic["probe_request_phase_requested"] = True
            current["clock"].request_phase(phases)
            wait_until(ready, "Fresh owned surface after-paint did not settle", seconds=1.7)
        require(widget.get_frame_clock() == current["clock"] and matches(snapshot()),
                "Owned surface changed during paint readiness barrier")
    finally:
        if current["handler"] is not None:
            current["clock"].disconnect(current["handler"])
        for handler in handlers:
            widget.disconnect(handler)
    # Flush/handle the remap commit on GTK's connection before a different
    # Wayland connection is allowed to issue any virtual pointer command.
    display.sync()
    diagnostic["gdk_display_sync_called"] = True


def typed_native_observation(kind, x, y, *, has_device, event_time, widget_matches,
                             surface_matches, mapped):
    """Typed controller signals count only with current native provenance."""
    require(has_device is True and type(event_time) is int and event_time > 0,
            "Typed observation lacks a current native device/time")
    require(widget_matches is True and surface_matches is True and mapped is True,
            "Typed observation does not belong to the current mapped owned surface")
    require(kind in ("motion", "press", "release") and
            all(type(value) in (int, float) and math.isfinite(value) for value in (x, y)),
            "Invalid typed native coordinates")
    return {"kind": kind, "x": x, "y": y, "native_time": event_time}


def qualified_typed_observation(kind, x, y, **metadata):
    """Zero-time owned motion is explicitly unqualified, never native evidence."""
    if (kind == "motion" and type(metadata.get("event_time")) is int and
            metadata["event_time"] == 0 and
            all(metadata.get(key) is True for key in
                ("has_device", "widget_matches", "surface_matches", "mapped"))):
        require(all(type(value) in (int, float) and math.isfinite(value) for value in (x, y)),
                "Invalid typed native coordinates")
        return None
    return typed_native_observation(kind, x, y, **metadata)


def drag_end_release_point(start, offset, explicit_up_requested):
    """A drag-end is release evidence only inside an explicit native U request."""
    if not explicit_up_requested:
        return None
    require(start is not None and len(start) == 2 and len(offset) == 2,
            "Native drag-end lacks a captured primary start")
    require(all(type(value) in (int, float) and math.isfinite(value) for value in (*start, *offset)),
            "Invalid native drag-end coordinates")
    return start[0] + offset[0], start[1] + offset[1]


def observation_with_release_slot(kind, x, y, metadata, slot):
    if kind == "release" and not slot["requested"]:
        return None, "Release signal outside explicit native U request"
    if kind == "release" and slot["consumed"]:
        return None, "Derived release after qualified native U evidence"
    item = qualified_typed_observation(kind, x, y, **metadata)
    if item is None:
        return None, "Zero-time owned motion excluded from native evidence"
    if kind == "release":
        slot["consumed"] = True  # Only after full native provenance succeeds.
    return item, None


def final_observation_summary(events, selected, adjusted, dismissals, case, scale):
    current = [event for event in events if event.get("case") == case and event.get("scale") == scale]
    return {
        "native_event_counts": {kind: sum(event.get("kind") == kind for event in events)
                                for kind in ("press", "motion", "release")},
        "ordinary_callbacks": len(selected), "adjustment_callbacks": len(adjusted),
        "dismissals": len(dismissals),
        "recent_ordinary_callbacks": selected[-6:], "recent_adjustment_callbacks": adjusted[-6:],
        "current_case": case, "current_scale": scale,
        "current_case_native_counts": {kind: sum(event.get("kind") == kind for event in current)
                                       for kind in ("press", "motion", "release")},
        "current_case_ordinary_callbacks": sum(entry.get("case") == case and entry.get("scale") == scale
                                               for entry in selected),
        "current_case_adjustment_callbacks": sum(entry.get("case") == case and entry.get("scale") == scale
                                                 for entry in adjusted),
    }


def recorder_diagnostic(error):
    # Our ProbeFailure sites use fixed verification reasons. Never include
    # arbitrary GI exception text, which could contain object/data details.
    return str(error) if isinstance(error, ProbeFailure) else type(error).__name__


def rejection_metadata(owner, kind, source_signal, metadata, error):
    item = {
        "owner": owner if owner in ("ring", "shield") else "unknown",
        "kind": kind if kind in ("motion", "press", "release") else "unknown",
        "source_signal": source_signal if source_signal in ("motion", "pressed", "released", "drag-end") else "unknown",
        "reason": recorder_diagnostic(error),
    }
    for key in ("has_device", "mapped", "widget_matches", "surface_matches"):
        value = metadata.get(key)
        item[key] = value if type(value) is bool else None
    value = metadata.get("event_time")
    item["event_time"] = value if type(value) is int and 0 <= value <= 0xFFFFFFFF else None
    return item


def motion_failure_diagnostic(owner, local, global_position, events, before, case, scale):
    """Bounded coordinates/owned labels only; no surface/device/window objects."""
    def coordinate(value):
        return value if type(value) in (int, float) and math.isfinite(value) else None
    def fixed_case(value):
        return value if value in (*CASES, "setup") else "unknown"
    samples = []
    for index in range(max(0, len(events) - 64), len(events)):
        event = events[index]
        if event.get("kind") == "motion" and event.get("owner") in ("ring", "shield"):
            samples.append({"owner": event["owner"], "x": coordinate(event.get("x")),
                            "y": coordinate(event.get("y")), "case": fixed_case(event.get("case")),
                            "after_request": index >= before})
    return {
        "expected_owner": owner if owner in ("ring", "shield") else "unknown",
        "expected_local": [coordinate(value) for value in local],
        "expected_global": [coordinate(value) for value in global_position],
        "case": fixed_case(case), "scale": scale if type(scale) is int else None,
        "recent_owned_motion_samples": samples[-6:],
    }


def case_offsets(name, threshold, detent):
    """Pure logical paths. Actual start/hit positions come from production geometry."""
    require(name in CASES, "Unknown case")
    require(all(type(value) in (int, float) and math.isfinite(value) and 0 < value <= 64
                for value in (threshold, detent)), "Invalid drag constants")
    short = threshold / 2
    step = threshold + detent + 1
    reverse = threshold + 3 * detent + 1
    return {
        "plain": [(short, 0)], "vertical": [(0, short)],
        "right": [(step, 0)], "left": [(-step, 0)],
        "reverse": [(reverse, 0), (-reverse, 0)],
        "pinned-root": [(step, 0)], "child": [(step, 0)],
        "reject": [(step, 0)], "huge": [],
        "hide-cancel": [(step, 0)], "after-cancel": [(short, 0)],
        "preview": [(step, 0)],
    }[name]


class PointerClient:
    """Stop-and-wait transport. ACK alone never authorizes a press.

    `down` is marked uncertain BEFORE writing D. On every exit request U first,
    then Q/EOF and collect the client's release-first cleanup. Never kill it.
    The pump callback must leave the owned shield alive until close returns.
    """

    def __init__(self, binary, output, extent, box, pump):
        self.pump = pump
        self.sequence = 0
        self.may_be_down = False
        self.motion_box = tuple(box)
        self.cleanup_record = None
        self.messages = []
        self.buffer = bytearray()
        self.closed = False
        argv = [str(binary), "--run", "--owner-pid", str(os.getpid()), "--output", output,
                "--extent", str(extent[0]), str(extent[1]), "--bounds",
                *(str(value) for value in box)]
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, bufsize=0)
        os.set_blocking(self.process.stdout.fileno(), False)
        os.set_blocking(self.process.stderr.fileno(), False)
        try:
            ready = self._next_message(2)
            require(ready.get("event") == "ready" and
                    ready.get("ack_scope") == "compositor-sync-only", "Client readiness failed")
        except BaseException:
            self.close()
            raise

    def _drain(self):
        while True:
            try:
                value = os.read(self.process.stdout.fileno(), 2048)
            except BlockingIOError:
                break
            if not value:
                break
            self.buffer.extend(value)
            require(len(self.buffer) <= 8192, "Client output bound exceeded")
            while b"\n" in self.buffer:
                line, _, remaining = self.buffer.partition(b"\n")
                self.buffer = bytearray(remaining)
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError) as exc:
                    raise ProbeFailure("Invalid client acknowledgement") from exc
                require(isinstance(message, dict), "Invalid client message shape")
                if message.get("event") == "cleanup":
                    self.cleanup_record = message
                self.messages.append(message)
                require(len(self.messages) <= 256, "Client message count exceeded")

    def _next_message(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.pump()
            self._drain()
            if self.messages:
                return self.messages.pop(0)
            require(self.process.poll() is None, "Client exited before acknowledgement")
            select.select([self.process.stdout], [], [], .005)
        raise ProbeFailure("Client acknowledgement deadline")

    def command(self, kind, *values):
        require(not self.closed and self.process.poll() is None, "Client is unavailable")
        self.sequence += 1
        if kind == "D":
            self.may_be_down = True
        payload = " ".join((kind, str(self.sequence), *(str(value) for value in values))) + "\n"
        try:
            self.process.stdin.write(payload.encode("ascii"))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ProbeFailure("Client command pipe failed") from exc
        ack = self._next_message(2)
        require(ack.get("event") == "ack" and ack.get("sequence") == self.sequence and
                ack.get("kind") == kind and ack.get("ack_scope") == "compositor-sync-only",
                "Client acknowledgement mismatch")
        if kind == "U":
            self.may_be_down = False

    def close(self):
        if self.closed:
            return self.summary
        errors = []
        if self.process.poll() is None:
            if self.may_be_down:
                try:
                    self.command("U")
                except BaseException:
                    errors.append("explicit-release-failed")
            if self.process.poll() is None:
                try:
                    self.command("Q")
                except BaseException:
                    errors.append("quit-ack-failed")
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass
        # The helper has its own short deadlines and release-first EOF path.
        # A slow/unexpected child is reaped while keeping owned surfaces alive.
        # Deliberately no terminate(), kill(), communicate(timeout), or timeout
        # wrapper that could SIGKILL a device with a button still down.
        while self.process.poll() is None:
            try:
                self.pump()
            except BaseException:
                if "cleanup-pump-failed" not in errors:
                    errors.append("cleanup-pump-failed")
            try:
                self._drain()
            except ProbeFailure:
                if "cleanup-output-failed" not in errors:
                    errors.append("cleanup-output-failed")
            time.sleep(.01)
        try:
            self._drain()
        except ProbeFailure:
            if "cleanup-output-failed" not in errors:
                errors.append("cleanup-output-failed")
        stderr = bytearray()
        while len(stderr) < 4096:
            try:
                part = os.read(self.process.stderr.fileno(), 4096 - len(stderr))
            except BlockingIOError:
                break
            if not part:
                break
            stderr.extend(part)
        self.process.stdout.close()
        self.process.stderr.close()
        cleanup = self.cleanup_record or {}
        self.summary = {
            "returncode": self.process.returncode,
            "release_attempted": cleanup.get("release_attempted") is True,
            "release_flushed": cleanup.get("release_flushed") is True,
            "release_compositor_sync": cleanup.get("release_compositor_sync") is True,
            "reaped_without_kill": True, "errors": errors,
            "stderr_present": bool(stderr),
        }
        self.closed = True
        return self.summary


def run_owned_gtk_probe(options):
    """Entry reserved for root-reviewed explicit execution. Never called by plan."""
    require(getattr(options, "root_review_complete", False) is True,
            "Root hook-review gate remains closed")
    require(getattr(options, "coordinated", False) is True,
            "Exclusive native probe coordination is required")
    require(bool(options.output), "An explicit output connector is required")
    require(len(options.expected_ring_sha256) == 64,
            "A reviewed ring source hash is required")
    for name in options.cases:
        case_offsets(name, options.threshold, options.detent)
    require("hide-cancel" not in options.cases or options.include_hide_cancel,
            "Intentional hide-cancellation case remains separately gated")
    if "after-cancel" in options.cases:
        index = options.cases.index("after-cancel")
        require(index > 0 and options.cases[index - 1] == "hide-cancel",
                "After-cancel proof requires the immediately preceding hide-cancel case")
    root = Path(__file__).resolve().parent
    project = Path(options.project).resolve()
    ring_source = project / "src/mx4_linux/ring.py"
    require(hashlib.sha256(ring_source.read_bytes()).hexdigest() == options.expected_ring_sha256,
            "Ring source differs from reviewed snapshot")
    binary = root / "virtual-pointer-client"
    preparation = json.loads((root / "preparation-result.json").read_text())
    require(hashlib.sha256(binary.read_bytes()).hexdigest() == preparation["client_sha256"],
            "Client binary differs from prepared snapshot")
    evidence = Path(options.evidence).resolve()
    require(evidence.is_relative_to(Path("/tmp")) and not evidence.exists(),
            "Evidence must be a new temporary file under /tmp")
    return _run_with_private_environment(options, project, binary)


def _run_with_private_environment(options, project, binary):
    # Everything above this point is pure file/config validation. Display/app
    # imports and processes occur only inside the explicit reviewed run path.
    names = ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "GDK_BACKEND")
    previous_environment = {name: os.environ.get(name) for name in names}
    with tempfile.TemporaryDirectory(prefix="mx4-native-drag-owned-") as private:
        directory = Path(private)
        (directory / "config").mkdir(mode=0o700)
        (directory / "state").mkdir(mode=0o700)
        os.environ["XDG_CONFIG_HOME"] = str(directory / "config")
        os.environ["XDG_STATE_HOME"] = str(directory / "state")
        os.environ["GDK_BACKEND"] = "wayland"
        try:
            return _run_native(options, project, binary, directory)
        finally:
            for name, value in previous_environment.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _run_native(options, project, binary, directory):
    # Import only on explicit execution; no display/app launch during preparation.
    from mx4_linux.actions import focused_context, restore_focus, validate_config
    from mx4_linux.gtk_support import Adw, Gdk, Gio, GLib, Gtk, Layer
    from gi.repository import GObject
    import mx4_linux.ring as ring_module
    from mx4_linux.ring import ActionsRing, CENTER_X, CENTER_Y, WIDTH, HEIGHT
    from mx4_linux.ring_model import button_at, button_center

    context = GLib.MainContext.default()
    cancellation = {"requested": False, "cleanup": False}
    previous_signals = {}
    def requested(_number, _frame):
        cancellation["requested"] = True
    def pump():
        while context.pending():
            context.iteration(False)

    def until(predicate, message, seconds=2):
        end = time.monotonic() + seconds
        while not predicate():
            pump()
            require(not cancellation["requested"] or cancellation["cleanup"], "Probe cancellation requested")
            require(time.monotonic() < end, message)
            time.sleep(.005)
        pump()

    def output_snapshot():
        value = subprocess.run(["niri", "msg", "--json", "outputs"],
                               capture_output=True, text=True, timeout=2, check=True)
        require(len(value.stdout) <= 65536, "Output inventory bound exceeded")
        outputs = json.loads(value.stdout)
        output = outputs.get(options.output)
        require(isinstance(output, dict) and isinstance(output.get("logical"), dict),
                "Chosen output has no current logical geometry")
        logical = output["logical"]
        require(logical.get("transform") == "Normal", "Only normal output transform is prepared")
        require(all(type(logical.get(key)) is int for key in ("x", "y", "width", "height")),
                "Invalid chosen output geometry")
        return {key: logical[key] for key in ("x", "y", "width", "height", "scale", "transform")}

    previous_focus = focused_context()
    require(previous_focus is not None and type(previous_focus.get("window_id")) is int,
            "An exact prior focus identity is required")
    require(Path(ring_module.__file__).resolve() == (project / "src/mx4_linux/ring.py").resolve(),
            "Imported production ring does not match the reviewed project source")
    baseline_output = output_snapshot()
    extent = (baseline_output["width"], baseline_output["height"])
    require(all(0 < value <= 16384 for value in extent), "Output extent is unsupported")
    Gtk.init()
    app = Adw.Application(application_id="io.github.mx4proof.DragHarness.r" + uuid4().hex,
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    display = Gdk.Display.get_default()
    require(display is not None and display.__gtype__.name == "GdkWaylandDisplay" and
            Layer is not None and Layer.is_supported(), "Native Wayland layer-shell is required")
    monitors = display.get_monitors()
    matches = [monitors.get_item(index) for index in range(monitors.get_n_items())
               if monitors.get_item(index).get_connector() == options.output]
    require(len(matches) == 1, "Chosen output does not identify exactly one GDK monitor")
    monitor = matches[0]
    rectangle = monitor.get_geometry()
    require((rectangle.x, rectangle.y, rectangle.width, rectangle.height) ==
            (baseline_output["x"], baseline_output["y"], *extent),
            "GDK and compositor logical output geometry disagree")
    result = {
        "proof_level": "native GTK gesture-only; no executing runner/controller/HID",
        "ring_source_sha256": options.expected_ring_sha256,
        "gtk_backend": "wayland", "layer_shell": True,
        "threshold_logical": options.threshold, "detent_logical": options.detent,
        "runner_input_effects": 0, "hardware_effects": 0, "action_effects": 0,
        "cases": [], "helper_cleanup": [], "passed": False,
        "initial_up_motion_warmups": [],
        "per_show_paint_barriers": [],
        "prior_exact_focus_restored": False,
    }
    all_events, selected, adjusted, dismissals, gesture_cancels = [], [], [], [], []
    recorder_errors = []
    result["native_recorder_errors"] = recorder_errors
    recorder_rejections = result["native_recorder_rejections"] = []
    unqualified_motion = result["unqualified_zero_timestamp_motion"] = {
        "count": 0, "count_capped": False, "samples": [],
    }
    unqualified_release = result["unqualified_release_signals"] = {"count": 0, "count_capped": False, "samples": []}
    current_case = {"name": "setup", "scale": None, "accept": True}
    shield = ring = client = None
    namespace = "mx4-drag-proof-shield-" + uuid4().hex
    ring_origin = (40, 40)
    owned_surfaces, emission_hooks = {}, []
    captured_primary_starts = {}
    native_up = {"requested": False, "consumed": False}

    def record_native(window, owner):
        # The local GdkEvent union does not box into Python reliably. Observe
        # supported typed coordinates plus current device/time instead. Hooks
        # observe the existing production gestures BEFORE their handlers can
        # hide the ring on release; no competing ring gesture is added.
        window.connect("map", lambda *_: owned_surfaces.update({owner: window.get_surface()}))
        target = ring.surface if owner == "ring" else window.get_child()
        def current_metadata(controller, expected_widget):
            widget = controller.get_widget()
            surface = window.get_surface()
            return {
                "has_device": controller.get_current_event_device() is not None,
                "event_time": controller.get_current_event_time(),
                "widget_matches": widget == expected_widget and widget.get_native() == window,
                "surface_matches": surface is not None and surface == owned_surfaces.get(owner),
                "mapped": bool(window.get_mapped()),
            }
        def unqualified_release_signal(source_signal, metadata, reason):
            if unqualified_release["count"] < 4096:
                unqualified_release["count"] += 1
            else:
                unqualified_release["count_capped"] = True
            if len(unqualified_release["samples"]) < 8:
                unqualified_release["samples"].append(rejection_metadata(owner, "release", source_signal, metadata,
                    ProbeFailure(reason)))
        def observed(controller, expected_widget, kind, x, y, source_signal):
            metadata = {}
            try:
                if kind == "release" and (not native_up["requested"] or native_up["consumed"]):
                    # A later derived signal can arrive after production has
                    # hidden/unmapped the ring. It has no native evidence slot.
                    try:
                        metadata.update(current_metadata(controller, expected_widget))
                    except Exception:
                        pass
                    _, reason = observation_with_release_slot(kind, x, y, {}, native_up)
                    unqualified_release_signal(source_signal, metadata, reason)
                    return
                require(len(all_events) < 4096, "Native event bound exceeded")
                widget = controller.get_widget()
                translated = widget.translate_coordinates(target, x, y) if widget else None
                require(translated is not None, "Typed native coordinates could not be translated")
                metadata.update(current_metadata(controller, expected_widget))
                item, unqualified_reason = observation_with_release_slot(kind, *translated, metadata, native_up)
                if item is None:
                    if kind == "release":
                        unqualified_release_signal(source_signal, metadata, unqualified_reason)
                        return
                    if unqualified_motion["count"] < 4096:
                        unqualified_motion["count"] += 1
                    else:
                        unqualified_motion["count_capped"] = True
                    if len(unqualified_motion["samples"]) < 8:
                        sample = rejection_metadata(owner, kind, source_signal, metadata,
                            ProbeFailure("Zero-time owned motion excluded from native evidence"))
                        sample.update({"x": translated[0], "y": translated[1]})
                        unqualified_motion["samples"].append(sample)
                    return  # Not appended to all_events and cannot satisfy a native motion guard.
                item.update({
                    "owner": owner, "button": 1 if kind in ("press", "release") else None,
                    "source_signal": source_signal,
                    "case": current_case["name"], "scale": current_case["scale"],
                    "at": time.monotonic(),
                })
                if kind == "press":
                    captured_primary_starts[owner] = tuple(translated)
                # Avoid counting a duplicate observation of one native event.
                recent = all_events[-8:] if kind == "release" else all_events[-1:]
                if not any(all(event.get(key) == item.get(key)
                               for key in ("owner", "kind", "native_time", "x", "y")) for event in recent):
                    all_events.append(item)
            except Exception as error:
                if len(recorder_errors) < 8:
                    recorder_errors.append(recorder_diagnostic(error))
                    recorder_rejections.append(rejection_metadata(owner, kind, source_signal, metadata, error))
        motion = Gtk.EventControllerMotion()
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        # wl_pointer.enter carries no timestamp and can accompany initial map;
        # it is never counted as proof of an actual requested native motion.
        motion.connect("motion", lambda controller, x, y:
                       observed(controller, window, "motion", x, y, "motion"))
        window.add_controller(motion)
        if owner == "ring":
            controllers = ring.surface.observe_controllers()
            clicks = [controllers.get_item(index) for index in range(controllers.get_n_items())
                      if isinstance(controllers.get_item(index), Gtk.GestureClick) and
                      controllers.get_item(index).get_button() == 1]
            require(len(clicks) == 1, "Expected exactly one production primary click gesture")
            click = clicks[0]
            expected_widget = ring.surface
        else:
            click = Gtk.GestureClick(button=1)
            click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            window.add_controller(click)  # No claim/action handlers on the no-op shield.
            expected_widget = window
        for signal_name, kind in (("pressed", "press"), ("released", "release")):
            def emission(gesture, _count, x, y, watched=click, signal_kind=kind,
                         name=signal_name, attached=expected_widget):
                if gesture == watched:
                    observed(gesture, attached, signal_kind, x, y, name)
                return True  # Remain installed; never claim/change/stop the signal.
            hook_id = GObject.add_emission_hook(Gtk.GestureClick, signal_name, emission)
            require(bool(hook_id), "Typed gesture emission hook is unavailable")
            emission_hooks.append((GObject.signal_lookup(signal_name, Gtk.GestureClick), hook_id))
        if owner == "ring":
            drags = [controllers.get_item(index) for index in range(controllers.get_n_items())
                     if isinstance(controllers.get_item(index), Gtk.GestureDrag) and
                     controllers.get_item(index).get_button() == 1]
            require(len(drags) == 1, "Expected exactly one production primary drag gesture")
            drag = drags[0]
            def drag_finished(gesture, dx, dy):
                if gesture != drag:
                    return True
                if not native_up["requested"] or native_up["consumed"]:
                    # Resets/hides can end a GTK drag outside a native U event.
                    # Diagnose them, but never count them as pointer releases.
                    metadata = {}
                    try:
                        metadata.update(current_metadata(gesture, ring.surface))
                    except Exception:
                        pass
                    reason = ("Derived release after qualified native U evidence" if native_up["consumed"]
                              else "Release signal outside explicit native U request")
                    unqualified_release_signal("drag-end", metadata, reason)
                    return True
                try:
                    point = drag_end_release_point(captured_primary_starts.get(owner), (dx, dy), True)
                    observed(gesture, ring.surface, "release", *point, "drag-end")
                except Exception as error:
                    if len(recorder_errors) < 8:
                        recorder_errors.append(recorder_diagnostic(error))
                        recorder_rejections.append(rejection_metadata(owner, "release", "drag-end", {}, error))
                return True
            hook_id = GObject.add_emission_hook(Gtk.GestureDrag, "drag-end", drag_finished)
            require(bool(hook_id), "Production drag-end emission hook is unavailable")
            emission_hooks.append((GObject.signal_lookup("drag-end", Gtk.GestureDrag), hook_id))

    def surface_checks(require_ring=True):
        require(not cancellation["requested"] or cancellation["cleanup"], "Probe cancellation requested")
        require(not recorder_errors, "Native event recorder failed")
        require(shield is not None and shield.get_mapped(), "Owned shield is not mapped")
        require(Layer.get_namespace(shield) == namespace and
                Layer.get_layer(shield) == Layer.Layer.TOP and
                Layer.get_monitor(shield) == monitor and
                Layer.get_exclusive_zone(shield) == -1 and
                all(Layer.get_anchor(shield, edge) for edge in
                    (Layer.Edge.LEFT, Layer.Edge.RIGHT, Layer.Edge.TOP, Layer.Edge.BOTTOM)),
                "Owned shield identity/placement changed")
        surface = shield.get_surface()
        require((surface.get_width(), surface.get_height()) == extent and
                display.get_monitor_at_surface(surface) == monitor,
                "Owned shield does not cover chosen output")
        if require_ring:
            require(ring is not None and ring.get_mapped() and ring.state.visible and ring.overlay_supported,
                    "Production ring is not mapped")
            require(Layer.get_layer(ring) == Layer.Layer.OVERLAY and
                    Layer.get_monitor(ring) == monitor and
                    Layer.get_exclusive_zone(ring) == -1 and
                    Layer.get_anchor(ring, Layer.Edge.LEFT) and
                    Layer.get_anchor(ring, Layer.Edge.TOP) and
                    not Layer.get_anchor(ring, Layer.Edge.RIGHT) and
                    not Layer.get_anchor(ring, Layer.Edge.BOTTOM) and
                    Layer.get_margin(ring, Layer.Edge.LEFT) == ring_origin[0] and
                    Layer.get_margin(ring, Layer.Edge.TOP) == ring_origin[1] and
                    display.get_monitor_at_surface(ring.get_surface()) == monitor,
                    "Production ring identity/placement changed")

    def native_motion(owner, local, global_position):
        require(client is not None, "Native client is missing")
        surface_checks(require_ring=owner == "ring")
        before = len(all_events)
        try:
            before, warmup = request_motion_with_initial_warmup(client, global_position, lambda: len(all_events))
            if warmup is not None:
                result["initial_up_motion_warmups"].append({
                    "owner": owner, "global": list(warmup),
                    "case": current_case["name"], "scale": current_case["scale"],
                })
            def seen():
                return any(event["owner"] == owner and event["kind"] == "motion" and
                           event["x"] is not None and abs(event["x"] - local[0]) <= 1.5 and
                           abs(event["y"] - local[1]) <= 1.5
                           for event in all_events[before:])
            until(seen, "Expected native owned motion was not observed")
            surface_checks(require_ring=owner == "ring")
            last_motion = next((event for event in reversed(all_events) if event["kind"] == "motion"), None)
            require(last_motion is not None and last_motion["owner"] == owner and
                    abs(last_motion["x"] - local[0]) <= 1.5 and abs(last_motion["y"] - local[1]) <= 1.5,
                    "Unexpected surface received the latest motion")
            return last_motion
        except ProbeFailure:
            result["native_motion_failure"] = motion_failure_diagnostic(
                owner, local, global_position, all_events, before,
                current_case["name"], current_case["scale"])
            raise

    def ring_motion(logical):
        local = ring.geometry.point(*logical)  # GTK units, never multiply monitor scale.
        position = (ring_origin[0] + local[0], ring_origin[1] + local[1])
        before = len(adjusted)
        event = native_motion("ring", local, position)
        require(len(adjusted) - before <= options.max_emissions,
                "Native motion exceeded adjustment emission cap")
        return event

    def exact_hit(logical):
        x, y = logical
        if ring.group_slot is not None:
            child = ring.child_at(x, y)
            if child is not None:
                return ring.children[child]
        slot = button_at(x - CENTER_X, y - CENTER_Y, spread=ring.spread)
        return ring.config["ring"][slot] if slot is not None else None

    def guarded_press(logical, action_id):
        require(not client.may_be_down, "A fresh prepress cannot follow an unreleased primary")
        # Force a distinct, harmless prepress position before the witnessed
        # native point; identical compositor coordinates may omit motion.
        ring_motion((logical[0] + 2, logical[1]))
        event = ring_motion(logical)
        require(time.monotonic() - event["at"] < .5, "Native prepress observation became stale")
        require(exact_hit(logical) == action_id, "Prepress hit differs from intended action")
        require(output_snapshot() == baseline_output, "Output geometry changed before press")
        surface_checks()
        require(time.monotonic() - event["at"] < .5, "Output check exceeded native prepress age")
        before = len(all_events)
        client.command("D")
        until(lambda: any(item["kind"] == "press" for item in all_events[before:]),
              "Native primary press was not observed")
        presses = [item for item in all_events[before:] if item["kind"] == "press"]
        local = ring.geometry.point(*logical)
        require(len(presses) == 1 and presses[0]["owner"] == "ring" and presses[0]["button"] == 1 and
                presses[0]["x"] is not None and abs(presses[0]["x"] - local[0]) <= 1.5 and
                abs(presses[0]["y"] - local[1]) <= 1.5,
                "Primary press did not reach the exact guarded ring point")

    def release(expect_native=True):
        # May be called after intentional ring hide. Never move or press then;
        # only U/cleanup is allowed while the shield retains the whole output.
        surface_checks(require_ring=False)
        before = len(all_events)
        native_up.update(requested=True, consumed=False)
        try:
            client.command("U")
            if expect_native:
                until(lambda: any(item["kind"] == "release" and item["button"] == 1
                                  for item in all_events[before:]),
                      "Native primary release was not observed")
            else:
                pump()
        finally:
            native_up["requested"] = False

    def close_client():
        nonlocal client
        if client is not None:
            cancellation["cleanup"] = True
            try:
                summary = client.close()
                result["helper_cleanup"].append(summary)
                require(summary["returncode"] == 0 and summary["release_attempted"] and
                        summary["release_flushed"] and summary["release_compositor_sync"] and
                        not summary["errors"], "Native helper release/reap was not confirmed")
            finally:
                client = None
                cancellation["cleanup"] = False

    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous_signals[number] = signal.signal(number, requested)
    try:
        shield = Gtk.Window(application=app, title="MX4 harmless pointer verification shield",
                            decorated=False, resizable=False,
                            default_width=extent[0], default_height=extent[1])
        Layer.init_for_window(shield)
        Layer.set_namespace(shield, namespace)
        Layer.set_monitor(shield, monitor)
        Layer.set_layer(shield, Layer.Layer.TOP)
        Layer.set_keyboard_mode(shield, Layer.KeyboardMode.NONE)
        Layer.set_exclusive_zone(shield, -1)
        for edge in (Layer.Edge.LEFT, Layer.Edge.RIGHT, Layer.Edge.TOP, Layer.Edge.BOTTOM):
            Layer.set_anchor(shield, edge, True)
            Layer.set_margin(shield, edge, 0)
        # This is intentionally an opaque, non-actionable drawing area. No
        # keyboard/clipboard/gesture/action handler or executing runner exists.
        cover = Gtk.DrawingArea(hexpand=True, vexpand=True)
        def draw_cover(_area, cairo, _width, _height):
            cairo.set_source_rgb(.055, .065, .075)
            cairo.paint()
        cover.set_draw_func(draw_cover)
        shield.set_child(cover)
        record_native(shield, "shield")
        # Observe before first present, including frames during configuration.
        # No virtual pointer exists until exact output coverage + paint settle.
        allocation = result["owned_shield_allocation"] = {}
        def observe_shield_allocation():
            surface = shield.get_surface()
            return {
                "mapped": bool(shield.get_mapped()),
                "width": surface.get_width() if surface else None,
                "height": surface.get_height() if surface else None,
                "monitor_matches": bool(surface and display.get_monitor_at_surface(surface) == monitor),
            }
        def show_shield_and_settle():
            shield.present()
            wait_exact_allocation(observe_shield_allocation, extent, until, allocation)
        def observe_shield_frame():
            sample = observe_shield_allocation()
            sample.update(visible=bool(shield.get_visible()), tick_active=False, show_state_matches=True,
                          allocation_matches=(sample["width"], sample["height"]) == extent)
            return sample
        paint = {"owner": "shield", "case": "setup", "scale": None, "show_generation": 0,
                 "expected_width": extent[0], "expected_height": extent[1]}
        result["per_show_paint_barriers"].append(paint)
        show_after_paint(shield, show_shield_and_settle, observe_shield_frame, display, until,
                         Gdk.FrameClockPhase.PAINT | Gdk.FrameClockPhase.AFTER_PAINT, paint)
        surface_checks(require_ring=False)

        # Full-output coverage proof uses motion only. No down is possible in
        # this branch; every observed native point must belong to the shield.
        client = PointerClient(binary, options.output, extent, (0, 0, *extent), pump)
        for point in ((2, 2), (extent[0] - 3, 2),
                      (2, extent[1] - 3), (extent[0] - 3, extent[1] - 3)):
            native_motion("shield", point, point)
        close_client()
        result["owned_shield_corner_native_motion_checks"] = 4

        def selected_callback(action_id, preview):
            selected.append({"action_id": action_id, "preview": bool(preview),
                             "case": current_case["name"], "scale": current_case["scale"]})
        def adjusted_callback(action_id, direction, preview=False):
            adjusted.append({"action_id": action_id, "direction": direction, "preview": bool(preview),
                             "case": current_case["name"], "scale": current_case["scale"]})
            return current_case["accept"]
        ring = ActionsRing(app, selected_callback, lambda: dismissals.append(current_case["name"]),
                           adjusted=adjusted_callback)
        require(ring.overlay_supported, "Production ring did not initialize as native layer-shell")
        Layer.set_monitor(ring, monitor)
        Layer.set_exclusive_zone(ring, -1)
        Layer.set_anchor(ring, Layer.Edge.LEFT, True)
        Layer.set_anchor(ring, Layer.Edge.TOP, True)
        Layer.set_anchor(ring, Layer.Edge.RIGHT, False)
        Layer.set_anchor(ring, Layer.Edge.BOTTOM, False)
        Layer.set_margin(ring, Layer.Edge.LEFT, ring_origin[0])
        Layer.set_margin(ring, Layer.Edge.TOP, ring_origin[1])
        record_native(ring, "ring")
        controllers = ring.surface.observe_controllers()
        for index in range(controllers.get_n_items()):
            controller = controllers.get_item(index)
            if isinstance(controller, Gtk.Gesture):
                controller.connect("cancel", lambda gesture, _sequence:
                    gesture_cancels.append({"type": gesture.__gtype__.name,
                                           "case": current_case["name"], "scale": current_case["scale"]}))
        previous_animations = ring.settings.get_property("gtk-enable-animations")
        expected_animations = not options.reduced_motion
        ring.settings.set_property("gtk-enable-animations", expected_animations)
        effective_animations = bool(ring.settings.get_property("gtk-enable-animations"))
        require(effective_animations == expected_animations, "Process-local animation policy readback failed")
        result["gtk_enable_animations"] = effective_animations
        result["reduced_motion"] = bool(options.reduced_motion)

        for scale in options.scales:
            document = noop_document(scale)
            validate_config(document)
            assert_all_noops(document)
            for name in options.cases:
                require(name != "hide-cancel" or options.include_hide_cancel,
                        "Intentional hide-cancellation case remains separately gated")
                current_case.update(name=name, scale=scale, accept=name != "reject")
                captured_primary_starts.pop("ring", None)
                require(ring_origin[0] + math.ceil(WIDTH * scale / 100) + 4 < extent[0] and
                        ring_origin[1] + math.ceil(HEIGHT * scale / 100) + 4 < extent[1],
                        "Scaled ring does not fit the owned output")
                expected_size = (round(WIDTH * scale / 100), round(HEIGHT * scale / 100))
                def show_ring_and_settle():
                    ring.show_ring(deepcopy(document), preview=name == "preview")
                    until(lambda: ring.get_mapped() and not ring.tick_id, "Production ring reveal did not settle")
                    until(lambda: ring.get_surface().get_width() >= expected_size[0] and
                          ring.get_surface().get_height() >= expected_size[1],
                          "Scaled native allocation did not settle")
                def observe_ring_frame():
                    surface = ring.get_surface()
                    width = surface.get_width() if surface else None
                    height = surface.get_height() if surface else None
                    return {
                        "mapped": bool(ring.get_mapped()), "visible": bool(ring.get_visible()),
                        "width": width, "height": height, "tick_active": bool(ring.tick_id),
                        "monitor_matches": bool(surface and display.get_monitor_at_surface(surface) == monitor),
                        "allocation_matches": (width is not None and height is not None and
                                               width >= expected_size[0] and height >= expected_size[1]),
                        "show_state_matches": bool(ring.state.visible and ring.config.get("ring_scale") == scale),
                    }
                paint = {"owner": "ring", "case": name, "scale": scale,
                         "show_generation": len(result["per_show_paint_barriers"]),
                         "expected_width": expected_size[0], "expected_height": expected_size[1]}
                result["per_show_paint_barriers"].append(paint)
                show_after_paint(ring, show_ring_and_settle, observe_ring_frame, display, until,
                                 Gdk.FrameClockPhase.PAINT | Gdk.FrameClockPhase.AFTER_PAINT, paint)
                surface_checks()
                width, height = ring.get_surface().get_width(), ring.get_surface().get_height()
                require(width >= ring.geometry.size(WIDTH) and height >= ring.geometry.size(HEIGHT),
                        "Ring surface allocation is unexpectedly small")
                client = PointerClient(binary, options.output, extent,
                                       (*ring_origin, width, height), pump)
                action_id = "root-0"
                start = (CENTER_X + button_center(0)[0], CENTER_Y + button_center(0)[1])
                if name == "child":
                    group = (CENTER_X + button_center(2)[0], CENTER_Y + button_center(2)[1])
                    ring_motion(group)
                    until(lambda: ring.group_slot == 2 and not ring.tick_id,
                          "Native group hover did not fully reveal children")
                    require(len(ring.child_positions) == 2, "Expected two native group children")
                    start = tuple(ring.child_positions[0])
                    action_id = "child-0"
                before = (len(selected), len(adjusted), len(all_events), len(gesture_cancels))
                guarded_press(start, action_id)
                for dx, dy in case_offsets(name, options.threshold, options.detent):
                    ring_motion((start[0] + dx, start[1] + dy))
                if name == "pinned-root":
                    ring_motion((CENTER_X + button_center(1)[0], CENTER_Y + button_center(1)[1]))
                if name == "huge":
                    ring_motion((WIDTH - 4, start[1]))
                if name == "hide-cancel":
                    # Intentional cancellation setup, not teardown: retain the
                    # ring object and opaque shield; only U/Q/EOF may follow.
                    callbacks_before_hide = (len(selected), len(adjusted))
                    ring.hide_ring()
                    pump()
                    require(not ring.state.visible and all(getattr(ring, field) is None
                            for field in ("_primary_start", "_drag", "_press_target")),
                            "Intentional ring hide retained captured pointer state")
                    release(expect_native=False)
                    close_client()
                    require(all(getattr(ring, field) is None
                            for field in ("_primary_start", "_drag", "_press_target")),
                            "Old release revived captured pointer state")
                    require((len(selected), len(adjusted)) == callbacks_before_hide,
                            "Intentional hide/release dispatched an old callback")
                else:
                    release()
                    close_client()
                pump()
                chosen = selected[before[0]:]
                changes = adjusted[before[1]:]
                native = all_events[before[2]:]
                cancelled = gesture_cancels[before[3]:]
                require(any(event["owner"] == "ring" and event["kind"] == "press" for event in native),
                        "Case has no native production-ring press")
                if name != "hide-cancel":
                    require(any(event["kind"] == "release" for event in native),
                            "Case has no native primary release observation")
                if name in ("plain", "vertical", "after-cancel"):
                    require(not changes and len(chosen) == 1 and
                            chosen[0]["action_id"] == action_id and not chosen[0]["preview"],
                            "Subthreshold gesture did not select exactly once")
                elif name == "preview":
                    require(all(entry["preview"] for entry in chosen + changes),
                            "Preview callback lost its preview flag")
                else:
                    require(not chosen and changes and all(entry["action_id"] == action_id for entry in changes),
                            "Drag main suppression or initial-hit pinning failed")
                    directions = {entry["direction"] for entry in changes}
                    require(directions <= {"increase", "decrease"}, "Unexpected adjustment direction")
                    if name == "left":
                        require(directions == {"decrease"}, "Left drag direction failed")
                    elif name == "reverse":
                        require(directions == {"increase", "decrease"}, "Native drag reversal was not observed")
                    else:
                        require(directions == {"increase"}, "Right drag direction failed")
                result["cases"].append({
                    "name": name, "scale": scale, "passed": True,
                    "native_press": sum(event["kind"] == "press" for event in native),
                    "native_motion": sum(event["kind"] == "motion" for event in native),
                    "native_release": sum(event["kind"] == "release" for event in native),
                    "ordinary_callbacks": len(chosen), "adjustment_callbacks": len(changes),
                    "adjustment_directions": [entry["direction"] for entry in changes],
                    "callback_preview_flags": [entry["preview"] for entry in chosen + changes],
                    "gtk_gesture_cancel_signals": len(cancelled),
                    "intentional_ring_hide_cancellation_setup": name == "hide-cancel",
                    "captured_pointer_state_cleared": True if name == "hide-cancel" else None,
                    "native_release_not_required_after_intentional_unmap": name == "hide-cancel",
                    "effect_count": 0,
                })
                ring.hide_ring()
        result["passed"] = True
    except BaseException as error:
        # No raw user contexts, titles, source paths or subprocess output.
        result["failure"] = str(error) if isinstance(error, ProbeFailure) else type(error).__name__
        raise
    finally:
        cancellation["cleanup"] = True
        try:
            if client is not None:
                summary = client.close()
                result["helper_cleanup"].append(summary)
                client = None
        finally:
            # The client has been reaped with release attempted before any
            # surface destruction. The intentional ring hide case above is the
            # sole exception to hide ordering, while shield remains mapped.
            for signal_id, hook_id in emission_hooks:
                GObject.signal_remove_emission_hook(signal_id, hook_id)
            if ring is not None:
                if "previous_animations" in locals():
                    ring.settings.set_property("gtk-enable-animations", previous_animations)
                ring.hide_ring()
                ring.destroy()
            if shield is not None:
                shield.destroy()
            pump()
            if previous_focus and type(previous_focus.get("window_id")) is int:
                try:
                    result["prior_exact_focus_restored"] = restore_focus(previous_focus["window_id"]) == previous_focus
                except Exception:
                    result["prior_exact_focus_restored"] = False
            app.quit()
            for number, previous in previous_signals.items():
                signal.signal(number, previous)
            # Private sanitized artifact only; no screenshots or raw event dump.
            result["final_observations"] = final_observation_summary(
                all_events, selected, adjusted, dismissals, current_case["name"], current_case["scale"])
            result["passed"] = result["passed"] and result["prior_exact_focus_restored"] and all(
                item["returncode"] == 0 and item["release_attempted"] and item["release_flushed"] and
                item["release_compositor_sync"] and not item["errors"]
                for item in result["helper_cleanup"])
            destination = Path(options.evidence).resolve()
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(result, stream, indent=2)
                stream.write("\n")
    require(result["passed"] and result["prior_exact_focus_restored"],
            "Native probe or exact prior-focus restoration was not confirmed")
    return result


if __name__ == "__main__":
    raise SystemExit("Use the default-plan wrapper; direct hook execution is disabled.")
