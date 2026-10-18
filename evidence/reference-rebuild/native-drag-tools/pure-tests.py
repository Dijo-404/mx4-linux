"""Pure preparation tests. Never invokes --run or the native client."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


with patch("subprocess.Popen", side_effect=AssertionError("No process allowed")), \
        patch("socket.socket", side_effect=AssertionError("No display socket allowed")):
    hook = module("mx4_hook_pure_tests", "owned_gtk_hook.py")
    wrapper = module("mx4_plan_pure_tests", "plan.py")


class Stream:
    def __init__(self, name, log, on_close=None):
        self.name, self.log, self.on_close = name, log, on_close
    def close(self):
        self.log.append(self.name + "-closed")
        if self.on_close:
            self.on_close()
    def fileno(self):
        return 99
    def write(self, _value):
        self.log.append(self.name + "-write")
        raise BrokenPipeError
    def flush(self):
        self.log.append(self.name + "-flush")


class Process:
    def __init__(self, log):
        self.alive = True
        self.returncode = None
        self.stdin = Stream("stdin", log, self.exit)
        self.stdout = Stream("stdout", log)
        self.stderr = Stream("stderr", log)
    def exit(self):
        self.alive, self.returncode = False, 0
    def poll(self):
        return None if self.alive else self.returncode


class PreparationTests(unittest.TestCase):
    def test_import_and_default_plan_are_pure(self):
        self.assertFalse(wrapper.ROOT_REVIEW_COMPLETE)
        with patch("subprocess.Popen", side_effect=AssertionError("No process allowed")), \
                patch("socket.socket", side_effect=AssertionError("No display socket allowed")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(wrapper.main([]), 0)
        self.assertIn("root hook-review gate is closed", output.getvalue())
        self.assertNotIn("gi", sys.modules)
        self.assertFalse(any(name.startswith("mx4_linux") for name in sys.modules))

    def test_every_branch_is_literal_noop_and_detached(self):
        for scale in (75, 100, 150):
            document = hook.noop_document(scale)
            hook.assert_all_noops(document)
            self.assertEqual(document["ring_scale"], scale)
            self.assertIsNone(document["ring_button"])
            document["actions"]["root-0"]["adjustment"]["increase"][0]["argv"][0] = "/usr/bin/false"
            with self.assertRaises(hook.ProbeFailure):
                hook.assert_all_noops(document)
            hook.assert_all_noops(hook.noop_document(scale))

    def test_hidden_optional_executable_branch_is_rejected(self):
        document = hook.noop_document(100)
        document["unknown-future-branch"] = {"nested": [{"type": "text", "text": "unwanted"}]}
        with self.assertRaises(hook.ProbeFailure):
            hook.assert_all_noops(document)

    def test_quantization_uses_logical_units_only(self):
        self.assertEqual(hook.quantized_coordinate(40.75), 10432)
        self.assertEqual(hook.quantized_coordinate(0), 0)
        for value in (-1, float("nan"), float("inf"), 16385, True):
            with self.assertRaises(hook.ProbeFailure):
                hook.quantized_coordinate(value)

    def test_initial_up_warmup_is_distinct_in_bounds_and_skips_after_first_or_while_down(self):
        self.assertEqual(hook.initial_motion_warmup((312, 144), (40, 40, 720, 460), 0, False), (314, 144))
        self.assertEqual(hook.initial_motion_warmup((9.5, 5), (0, 0, 10, 10), 0, False), (7.5, 5))
        self.assertEqual(hook.initial_motion_warmup((16383, 5), (0, 0, 16384, 10), 0, False), (16381, 5))
        self.assertIsNone(hook.initial_motion_warmup((5, 5), (0, 0, 10, 10), 1, False))
        self.assertIsNone(hook.initial_motion_warmup((5, 5), (0, 0, 10, 10), 0, True))
        with self.assertRaises(hook.ProbeFailure):
            hook.initial_motion_warmup((.5, .5), (0, 0, 1, 1), 0, False)

    def test_initial_enter_precedes_marker_and_only_requested_motion_qualifies(self):
        log, native = [], []
        class Client:
            sequence, may_be_down, motion_box = 0, False, (0, 0, 100, 100)
            def command(self, kind, x, y):
                self.sequence += 1
                log.append((kind, x, y))
                if self.sequence == 1:
                    log.append(("enter-without-time",))  # Not native motion evidence.
                else:
                    native.append({"x": x / 256, "y": y / 256, "kind": "motion"})
        client = Client()
        def marker():
            log.append(("marker",))
            return len(native)
        before, warmup = hook.request_motion_with_initial_warmup(client, (20, 30), marker)
        self.assertEqual(warmup, (22, 30))
        self.assertEqual(log, [("M", 22 * 256, 30 * 256), ("enter-without-time",),
                               ("marker",), ("M", 20 * 256, 30 * 256)])
        self.assertEqual(native[before:], [{"x": 20, "y": 30, "kind": "motion"}])
        client.may_be_down = True
        start = len(log)
        _, warmup = hook.request_motion_with_initial_warmup(client, (21, 30), marker)
        self.assertIsNone(warmup)
        self.assertEqual(log[start:], [("marker",), ("M", 21 * 256, 30 * 256)])

    def test_paths_keep_below_threshold_and_reverse_sign(self):
        self.assertEqual(hook.case_offsets("plain", 10, 12), [(5, 0)])
        self.assertEqual(hook.case_offsets("right", 10, 12), [(23, 0)])
        self.assertEqual(hook.case_offsets("left", 10, 12), [(-23, 0)])
        self.assertEqual(hook.case_offsets("reverse", 10, 12), [(47, 0), (-47, 0)])
        for name in hook.CASES:
            self.assertIsInstance(hook.case_offsets(name, 10, 12), list)

    def test_mapped_shield_waits_for_exact_extent_and_monitor_before_returning(self):
        samples = iter([
            {"mapped": False, "width": None, "height": None, "monitor_matches": False},
            {"mapped": True, "width": 1, "height": 1, "monitor_matches": True},
            {"mapped": True, "width": 1440, "height": 900, "monitor_matches": False},
            {"mapped": True, "width": 1440, "height": 900, "monitor_matches": True},
        ])
        observed, diagnostic = [], {}
        def bounded_wait(predicate, _message):
            for _ in range(4):
                ready = predicate()
                observed.append(ready)
                if ready:
                    return
            self.fail("Prepared allocation wait did not settle")
        hook.wait_exact_allocation(lambda: next(samples), (1440, 900), bounded_wait, diagnostic)
        self.assertEqual(observed, [False, False, False, True])
        self.assertEqual(diagnostic, {"expected_width": 1440, "expected_height": 900,
                                     "observed_width": 1440, "observed_height": 900,
                                     "mapped": True, "monitor_matches": True})

    def test_wrong_extent_remains_failure_and_retains_only_allocation_diagnostic(self):
        diagnostic = {}
        def bounded_wait(predicate, message):
            self.assertFalse(predicate())
            self.assertFalse(predicate())
            raise hook.ProbeFailure(message)
        with self.assertRaises(hook.ProbeFailure):
            hook.wait_exact_allocation(lambda: {"mapped": True, "width": 1, "height": 1,
                                              "monitor_matches": True},
                                       (1440, 900), bounded_wait, diagnostic)
        self.assertEqual(diagnostic["observed_width"], 1)
        self.assertNotIn("identity", diagnostic)

    def paint_fixture(self, realized=True):
        log, state = [], {"mapped": False, "tick_active": True, "allocation_matches": False}
        class Clock:
            counter = 4
            callback = None
            def get_frame_counter(self): return self.counter
            def connect_after(self, signal, callback):
                log.append(("clock-connect", signal)); self.callback = callback; return 1
            def request_phase(self, phases): log.append(("request-phase", phases))
            def disconnect(self, handler): log.append(("clock-disconnect", handler))
        clock = Clock()
        class Widget:
            current_clock = clock if realized else None
            callbacks = {}
            def get_frame_clock(self): return self.current_clock
            def get_mapped(self): return state["mapped"]
            def connect_after(self, signal, callback):
                log.append(("widget-connect", signal)); self.callbacks[signal] = callback; return signal
            def disconnect(self, handler): log.append(("widget-disconnect", handler))
            def queue_draw(self): log.append(("queue-draw",))
        class Display:
            def sync(self): log.append(("gdk-own-connection-sync",))
        def observe():
            return dict(state, visible=state["mapped"], width=720, height=460,
                        monitor_matches=True, show_state_matches=True)
        return Widget(), clock, Display(), observe, state, log

    def test_observer_precedes_show_and_captures_natural_final_frame_during_settle(self):
        widget, clock, display, observe, state, log = self.paint_fixture()
        diagnostic = {}
        def show_and_settle():
            log.append(("show",))
            state["mapped"] = True
            clock.callback(clock)  # Reveal/allocation not final; cannot qualify.
            self.assertFalse(diagnostic["after_paint_observed"])
            state.update(tick_active=False, allocation_matches=True)
            clock.callback(clock)  # A fresh signal at the same counter qualifies.
        def bounded_wait(predicate, _message, seconds):
            self.assertEqual(seconds, .3)
            self.assertTrue(predicate())  # Captured before this wait began.
        hook.show_after_paint(widget, show_and_settle, observe, display, bounded_wait, 99, diagnostic)
        log.append(("client-created",))
        self.assertLess(log.index(("clock-connect", "after-paint")), log.index(("show",)))
        self.assertEqual(log[-2:], [("gdk-own-connection-sync",), ("client-created",)])
        self.assertNotIn(("queue-draw",), log)
        self.assertTrue(diagnostic["natural_after_paint"])
        self.assertFalse(diagnostic["probe_queue_draw_requested"])
        self.assertFalse(diagnostic["probe_request_phase_requested"])
        self.assertEqual(diagnostic["frame_before"], diagnostic["frame_after"])
        self.assertEqual(diagnostic["observed_frame_count"], 2)
        self.assertFalse(diagnostic["last_frame"]["tick_active"])

    def test_first_realize_attaches_before_first_natural_frame(self):
        widget, clock, display, observe, state, log = self.paint_fixture(realized=False)
        diagnostic = {}
        def show_and_settle():
            log.append(("show",))
            widget.current_clock = clock
            widget.callbacks["realize"](widget)
            state.update(mapped=True, tick_active=False, allocation_matches=True)
            widget.callbacks["map"](widget)
            clock.callback(clock)
        def bounded_wait(predicate, _message, seconds): self.assertTrue(predicate())
        hook.show_after_paint(widget, show_and_settle, observe, display, bounded_wait, 99, diagnostic)
        self.assertEqual(log.count(("clock-connect", "after-paint")), 1)
        self.assertTrue(diagnostic["natural_after_paint"])
        self.assertTrue(diagnostic["observer_installed_before_show"])

    def test_probe_redraw_fallback_is_explicit_then_syncs_before_client(self):
        widget, clock, display, observe, state, log = self.paint_fixture()
        diagnostic, waits = {}, []
        def show_and_settle():
            log.append(("show",))
            state.update(mapped=True, tick_active=False, allocation_matches=True)
        def bounded_wait(predicate, message, seconds):
            waits.append(seconds)
            self.assertFalse(predicate())
            if len(waits) == 1:
                raise hook.ProbeFailure(message)
            self.assertIn(("queue-draw",), log)
            self.assertIn(("request-phase", 99), log)
            clock.callback(clock)
            self.assertTrue(predicate())
        hook.show_after_paint(widget, show_and_settle, observe, display, bounded_wait, 99, diagnostic)
        log.append(("client-created",))
        self.assertEqual(waits, [.3, 1.7])
        self.assertFalse(diagnostic["natural_after_paint"])
        self.assertTrue(diagnostic["probe_queue_draw_requested"])
        self.assertTrue(diagnostic["probe_request_phase_requested"])
        self.assertLess(log.index(("queue-draw",)), log.index(("request-phase", 99)))
        self.assertEqual(log[-2:], [("gdk-own-connection-sync",), ("client-created",)])

    def test_missing_fresh_frame_fails_without_sync_and_disconnects_all_observers(self):
        widget, _clock, display, observe, state, log = self.paint_fixture()
        def show_and_settle(): state.update(mapped=True, tick_active=False, allocation_matches=True)
        def bounded_wait(predicate, message, seconds):
            self.assertFalse(predicate())
            raise hook.ProbeFailure(message)
        with self.assertRaisesRegex(hook.ProbeFailure, "Fresh owned surface after-paint"):
            hook.show_after_paint(widget, show_and_settle, observe, display, bounded_wait, 99, {})
        self.assertNotIn(("gdk-own-connection-sync",), log)
        self.assertIn(("clock-disconnect", 1), log)
        self.assertIn(("widget-disconnect", "realize"), log)
        self.assertIn(("widget-disconnect", "map"), log)

    def test_cancellation_never_triggers_probe_redraw(self):
        widget, _clock, display, observe, state, log = self.paint_fixture()
        def show_and_settle(): state.update(mapped=True, tick_active=False, allocation_matches=True)
        def bounded_wait(predicate, message, seconds):
            raise hook.ProbeFailure("Probe cancellation requested")
        with self.assertRaisesRegex(hook.ProbeFailure, "Probe cancellation requested"):
            hook.show_after_paint(widget, show_and_settle, observe, display, bounded_wait, 99, {})
        self.assertNotIn(("queue-draw",), log)
        self.assertNotIn(("gdk-own-connection-sync",), log)

    def test_stale_allocation_or_active_tick_cannot_qualify_or_force_redraw(self):
        for missing in ("allocation_matches", "tick_active"):
            widget, clock, display, observe, state, log = self.paint_fixture()
            diagnostic = {}
            def show_and_settle():
                state.update(mapped=True, tick_active=False, allocation_matches=True)
                state[missing] = missing == "tick_active"
                clock.callback(clock)
            def bounded_wait(predicate, message, seconds):
                self.assertFalse(predicate())
                raise hook.ProbeFailure(message)
            with self.assertRaisesRegex(hook.ProbeFailure, "not ready for bounded probe redraw"):
                hook.show_after_paint(widget, show_and_settle, observe, display, bounded_wait, 99, diagnostic)
            self.assertFalse(diagnostic["after_paint_observed"])
            self.assertNotIn(("queue-draw",), log)
            self.assertNotIn(("gdk-own-connection-sync",), log)

    def test_typed_native_recorder_requires_device_time_widget_surface_and_mapping(self):
        metadata = {"has_device": True, "event_time": 123, "widget_matches": True,
                    "surface_matches": True, "mapped": True}
        for kind in ("motion", "press", "release"):
            record = hook.typed_native_observation(kind, 20.5, 42, **metadata)
            self.assertEqual(record, {"kind": kind, "x": 20.5, "y": 42, "native_time": 123})
        for key, invalid in (("has_device", False), ("event_time", 0), ("event_time", None),
                             ("widget_matches", False), ("surface_matches", False), ("mapped", False)):
            with self.subTest(key=key, invalid=invalid), self.assertRaises(hook.ProbeFailure):
                hook.typed_native_observation("motion", 20, 42, **{**metadata, key: invalid})
        with self.assertRaises(hook.ProbeFailure):
            hook.typed_native_observation("motion", float("nan"), 42, **metadata)

    def test_zero_time_motion_is_unqualified_and_only_requested_qualified_motion_can_match(self):
        metadata = {"has_device": True, "event_time": 0, "widget_matches": True,
                    "surface_matches": True, "mapped": True}
        evidence = []
        def observe(x, y, time):
            value = hook.qualified_typed_observation("motion", x, y, **{**metadata, "event_time": time})
            if value is not None:
                evidence.append(value)
        def seen():
            return any(value["kind"] == "motion" and value["x"] == 270 and value["y"] == 104
                       for value in evidence)
        observe(270, 104, 0)
        self.assertEqual(evidence, [])
        self.assertFalse(seen())
        observe(272, 104, 123)
        self.assertFalse(seen())
        observe(270, 104, 124)
        self.assertTrue(seen())
        for kind in ("press", "release"):
            with self.assertRaises(hook.ProbeFailure):
                hook.qualified_typed_observation(kind, 270, 104, **metadata)
        for key in ("has_device", "widget_matches", "surface_matches", "mapped"):
            with self.subTest(key=key), self.assertRaises(hook.ProbeFailure):
                hook.qualified_typed_observation("motion", 270, 104, **{**metadata, key: False})
        with self.assertRaises(hook.ProbeFailure):
            hook.qualified_typed_observation("motion", float("nan"), 104, **metadata)

    def test_drag_end_requires_explicit_up_and_captured_start(self):
        self.assertIsNone(hook.drag_end_release_point(None, (5, 0), False))
        self.assertIsNone(hook.drag_end_release_point((270, 104), (5, 0), False))
        self.assertEqual(hook.drag_end_release_point((270, 104), (5, 0), True), (275, 104))
        with self.assertRaises(hook.ProbeFailure):
            hook.drag_end_release_point(None, (5, 0), True)
        with self.assertRaises(hook.ProbeFailure):
            hook.drag_end_release_point((270, 104), (float("nan"), 0), True)

    def test_one_up_slot_requires_first_strict_native_release_then_excludes_derived_duplicates(self):
        metadata = {"has_device": True, "event_time": 123, "widget_matches": True,
                    "surface_matches": True, "mapped": True}
        slot = {"requested": False, "consumed": False}
        item, reason = hook.observation_with_release_slot("release", 275, 104, metadata, slot)
        self.assertIsNone(item)
        self.assertIn("outside", reason)
        self.assertFalse(slot["consumed"])
        slot["requested"] = True
        for changed in ({"event_time": 0}, {"has_device": False}, {"mapped": False}):
            with self.assertRaises(hook.ProbeFailure):
                hook.observation_with_release_slot("release", 275, 104, {**metadata, **changed}, slot)
            self.assertFalse(slot["consumed"])
        item, reason = hook.observation_with_release_slot("release", 275, 104, metadata, slot)
        self.assertEqual(item["native_time"], 123)
        self.assertIsNone(reason)
        self.assertTrue(slot["consumed"])
        duplicate, reason = hook.observation_with_release_slot("release", 275, 104,
            {**metadata, "has_device": False, "event_time": 0, "mapped": False}, slot)
        self.assertIsNone(duplicate)
        self.assertIn("Derived", reason)

    def test_final_counts_are_retained_for_failed_case_with_bounded_callbacks(self):
        events = [{"kind": kind, "case": "plain", "scale": 100} for kind in ("press", "motion", "release")]
        selected = [{"action_id": "root-0", "case": "plain", "scale": 100} for _ in range(10)]
        result = hook.final_observation_summary(events, selected, [], [], "plain", 100)
        self.assertEqual(result["native_event_counts"], {"press": 1, "motion": 1, "release": 1})
        self.assertEqual(result["current_case_ordinary_callbacks"], 10)
        self.assertEqual(len(result["recent_ordinary_callbacks"]), 6)

    def test_recorder_diagnostic_keeps_fixed_reason_but_excludes_arbitrary_exception_text(self):
        self.assertEqual(hook.recorder_diagnostic(hook.ProbeFailure("Fixed verification reason")),
                         "Fixed verification reason")
        self.assertEqual(hook.recorder_diagnostic(AttributeError("private object title/content")),
                         "AttributeError")

    def test_rejected_observation_metadata_has_only_fixed_labels_booleans_and_time(self):
        metadata = {"has_device": False, "event_time": 0, "mapped": True,
                    "widget_matches": True, "surface_matches": True, "device": object()}
        item = hook.rejection_metadata("ring", "release", "released", metadata,
                                       hook.ProbeFailure("Fixed verification reason"))
        self.assertEqual(item, {"owner": "ring", "kind": "release", "source_signal": "released",
                                "reason": "Fixed verification reason", "has_device": False,
                                "event_time": 0, "mapped": True, "widget_matches": True,
                                "surface_matches": True})
        private = hook.rejection_metadata("private", "private", "private",
                                           {"event_time": object(), "mapped": object()},
                                           AttributeError("private title/content"))
        self.assertNotIn("private", str(private))
        self.assertEqual(private["reason"], "AttributeError")
        self.assertIsNone(private["event_time"])

    def test_motion_failure_diagnostic_is_bounded_owned_numeric_and_marks_new_samples(self):
        events = [{"owner": "shield", "kind": "motion", "x": index, "y": index + .5,
                   "case": "plain", "device": object(), "title": "private"} for index in range(100)]
        diagnostic = hook.motion_failure_diagnostic("ring", (272, 104), (312, 144), events, 98, "plain", 100)
        self.assertEqual(diagnostic["expected_local"], [272, 104])
        self.assertEqual(diagnostic["expected_global"], [312, 144])
        samples = diagnostic["recent_owned_motion_samples"]
        self.assertEqual(len(samples), 6)
        self.assertEqual([sample["after_request"] for sample in samples], [False] * 4 + [True] * 2)
        self.assertTrue(all(set(sample) == {"owner", "x", "y", "case", "after_request"} for sample in samples))
        malformed = hook.motion_failure_diagnostic("private", (float("nan"), object()), (1, 2),
            [{"kind": "motion", "owner": "ring", "x": object(), "y": float("inf"), "case": "private"}],
            0, "private", object())
        self.assertEqual(malformed["expected_owner"], "unknown")
        self.assertEqual(malformed["expected_local"], [None, None])
        self.assertEqual(malformed["recent_owned_motion_samples"][0]["case"], "unknown")
        self.assertNotIn("private", str(malformed))

    def make_client(self, fail_release=False, fail_quit=False):
        log = []
        client = object.__new__(hook.PointerClient)
        client.closed = False
        client.sequence = 0
        client.may_be_down = True
        client.cleanup_record = {"release_attempted": True, "release_flushed": True,
                                 "release_compositor_sync": True}
        client.messages, client.buffer = [], bytearray()
        client.process = Process(log)
        client.pump = lambda: log.append("pump")
        client._drain = lambda: log.append("drain")
        def command(kind):
            log.append(kind)
            if kind == "U" and fail_release:
                raise hook.ProbeFailure("injected release failure")
            if kind == "Q":
                if fail_quit:
                    raise hook.ProbeFailure("injected quit failure")
                client.process.exit()
        client.command = command
        return client, log

    def test_cleanup_attempts_release_before_quit_and_reaps_before_stream_close(self):
        client, log = self.make_client()
        with patch.object(hook.os, "read", return_value=b""):
            result = client.close()
        self.assertLess(log.index("U"), log.index("Q"))
        self.assertLess(log.index("Q"), log.index("stdin-closed"))
        self.assertLess(log.index("stdin-closed"), log.index("stdout-closed"))
        self.assertTrue(result["reaped_without_kill"])
        self.assertEqual(result["errors"], [])

    def test_release_ack_failure_still_quits_closes_pipe_and_reaps(self):
        client, log = self.make_client(fail_release=True, fail_quit=True)
        with patch.object(hook.os, "read", return_value=b""):
            result = client.close()
        self.assertEqual(log[:3], ["U", "Q", "stdin-closed"])
        self.assertEqual(result["errors"], ["explicit-release-failed", "quit-ack-failed"])
        self.assertTrue(result["reaped_without_kill"])

    def test_uncertain_press_is_marked_before_pipe_write(self):
        client, log = self.make_client()
        client.may_be_down = False
        del client.command
        with self.assertRaises(hook.ProbeFailure):
            client.command("D")
        self.assertTrue(client.may_be_down)
        self.assertIn("stdin-write", log)


if __name__ == "__main__":
    unittest.main()
