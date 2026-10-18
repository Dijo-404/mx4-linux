import json
from types import SimpleNamespace

import pytest

import mx4_linux.actions as actions
from mx4_linux.actions import DesktopActions, DesktopConfigStore, default_config, focused_context, restore_focus, validate_config


@pytest.fixture(autouse=True)
def use_uinput_backend(monkeypatch):
    """These tests exercise the fallback, independent of installed helpers."""
    monkeypatch.setattr(actions, "_native_wtype", lambda: None)


def routine(steps, **extra):
    config = default_config()
    config["actions"]["test"] = {"label": "Test routine", "steps": steps, **extra}
    return config


def test_defaults_are_fresh_valid_and_have_eight_ring_actions():
    first = default_config()
    assert validate_config(first) is first
    assert len(first["ring"]) == 8
    first["actions"]["copy"]["steps"].clear()
    assert len(default_config()["actions"]["copy"]["steps"]) == 1


def test_optional_groups_round_trip_without_changing_parent_execution(tmp_path):
    config = default_config()
    config["ring_groups"] = {"2": ["copy", "paste", "undo"], "7": ["overview"]}
    store = DesktopConfigStore(tmp_path / "desktop.json")
    store.save(config)
    assert store.read() == config
    assert DesktopActions(config, dry_run=True).execute(config["ring"][2])["action_id"] == "terminal"


def test_legacy_configuration_validation_keeps_optional_groups_absent():
    config = default_config()
    assert "ring_groups" not in config
    assert validate_config(config) is config
    assert "ring_groups" not in config


def test_groups_accept_all_positions_and_six_distinct_children():
    config = default_config()
    config["ring_groups"] = {str(index): config["ring"][:6] for index in range(8)}
    assert validate_config(config) is config


@pytest.mark.parametrize("groups", [
    None, [], "groups", {0: ["copy"]}, {True: ["copy"]}, {"8": ["copy"]},
    {"-1": ["copy"]}, {"00": ["copy"]}, {"1.0": ["copy"]},
    {"0": []}, {"0": ["copy"] * 7}, {"0": ("copy",)}, {"0": "copy"},
    {"0": ["missing"]}, {"0": [None]}, {"0": [{}]}, {"0": ["copy", "copy"]},
])
def test_invalid_action_groups_rejected(groups):
    config = default_config()
    config["ring_groups"] = groups
    with pytest.raises(RuntimeError, match="Invalid desktop configuration: ring[_ ]group"):
        validate_config(config)


def test_terminal_environment_is_split_into_literal_argv(monkeypatch):
    monkeypatch.setenv("TERMINAL", 'kitty --title "hello $(touch nope)"')
    monkeypatch.setattr(actions.shutil, "which", lambda program: "/usr/bin/" + program)
    assert default_config()["actions"]["terminal"]["steps"][0]["argv"] == ["kitty", "--title", "hello $(touch nope)"]


def test_store_uses_xdg_default_and_returns_defaults_without_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    store = DesktopConfigStore()
    assert store.path == tmp_path / "mx4-linux" / "desktop.json"
    assert validate_config(store.read())
    assert not store.path.exists()


def test_store_round_trip_is_private_and_atomic(tmp_path):
    store = DesktopConfigStore(tmp_path / "config" / "desktop.json")
    config = routine([{"type": "text", "text": "Unicode ✓\nsecond line"}])
    store.save(config)
    assert store.read() == config
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700
    assert json.loads(store.path.read_text())["version"] == 1
    assert {p.name for p in store.path.parent.iterdir()} == {"desktop.json", "desktop.json.lock"}


def test_failed_atomic_save_preserves_original_and_cleans_tempfile(tmp_path, monkeypatch):
    store = DesktopConfigStore(tmp_path / "desktop.json")
    store.save(default_config())
    original = store.path.read_text()
    def fail_replace(*_args):
        raise OSError("disk failed")
    monkeypatch.setattr(actions.os, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="Cannot save.*disk failed"):
        store.save(routine([{"type": "delay", "milliseconds": 1}]))
    assert store.path.read_text() == original
    assert len(list(tmp_path.iterdir())) == 2


def test_invalid_config_cannot_overwrite_saved_document(tmp_path):
    store = DesktopConfigStore(tmp_path / "desktop.json")
    store.save(default_config())
    original = store.path.read_bytes()
    invalid = default_config()
    invalid["ring"] = []
    with pytest.raises(RuntimeError, match="exactly eight"):
        store.save(invalid)
    assert store.path.read_bytes() == original


def test_locked_update_preserves_concurrent_changes_and_returns_committed_document(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    path = tmp_path / 'desktop.json'
    first, second = DesktopConfigStore(path), DesktopConfigStore(path)
    first.save(default_config())
    entered, release = threading.Event(), threading.Event()
    def preference(document):
        entered.set()
        assert release.wait(2)
        document['appearance'] = 'light'
    def assignment(document):
        document['button_bindings']['83'] = 'copy'
    with ThreadPoolExecutor(max_workers=2) as executor:
        saved_preference = executor.submit(first.update, preference)
        assert entered.wait(1)
        saved_assignment = executor.submit(second.update, assignment)
        release.set()
        assert saved_preference.result()['appearance'] == 'light'
        latest = saved_assignment.result()
    assert latest['appearance'] == 'light'
    assert latest['button_bindings']['83'] == 'copy'
    assert first.read() == latest
    assert path.stat().st_mode & 0o777 == 0o600


def test_locked_update_rejects_invalid_mutation_without_replacing_document(tmp_path):
    store = DesktopConfigStore(tmp_path / 'desktop.json')
    store.save(default_config())
    original = store.path.read_bytes()
    with pytest.raises(RuntimeError, match='exactly eight'):
        store.update(lambda document: document.update(ring=[]))
    with pytest.raises(TypeError, match='must be callable'):
        store.update(None)
    assert store.path.read_bytes() == original


def test_locked_update_does_not_repair_or_replace_corrupt_document(tmp_path):
    path = tmp_path / 'desktop.json'
    path.write_text('broken')
    mutate = lambda document: document.update(appearance='light')
    with pytest.raises(RuntimeError, match='Cannot read'):
        DesktopConfigStore(path).update(mutate)
    assert path.read_text() == 'broken'


@pytest.mark.parametrize("contents", ["broken", "[]", '{"version":9}', "{}"])
def test_corrupt_document_read_is_reported(tmp_path, contents):
    path = tmp_path / "desktop.json"
    path.write_text(contents)
    with pytest.raises(RuntimeError, match="Cannot read"):
        DesktopConfigStore(path).read()


def test_large_document_rejected(tmp_path):
    path = tmp_path / "desktop.json"
    path.write_text(" " * (actions.MAX_CONFIG_BYTES + 1))
    with pytest.raises(RuntimeError, match="exceeds"):
        DesktopConfigStore(path).read()


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(version=True),
    lambda c: c.update(version=2),
    lambda c: c.update(unknown="ignored?"),
    lambda c: c.update(haptic_feedback=1),
    lambda c: c.update(ring_button=80),
    lambda c: c.update(ring_button=True),
    lambda c: c["ring"].append("copy"),
    lambda c: c["ring"].__setitem__(0, "missing"),
    lambda c: c["button_bindings"].update({"80": "copy"}),
    lambda c: c["button_bindings"].update({"416": "copy"}),
    lambda c: c["button_bindings"].update({"83": "missing"}),
    lambda c: c["button_bindings"].update({83: "copy"}),
    lambda c: c["actions"].update({"bad ID": {"label": "Bad", "steps": []}}),
    lambda c: c["actions"]["copy"].update(label=""),
    lambda c: c["actions"]["copy"].update(label="bad\nlabel"),
    lambda c: c["actions"]["copy"].update(app_id=""),
    lambda c: c["actions"]["copy"].update(unknown=True),
    lambda c: c["actions"]["copy"].update(steps=[]),
    lambda c: c["actions"]["copy"].update(steps=[{"type": "delay", "milliseconds": 1}] * 65),
])
def test_invalid_config_structure_rejected(mutate):
    config = default_config()
    mutate(config)
    with pytest.raises(RuntimeError, match="Invalid desktop configuration"):
        validate_config(config)


@pytest.mark.parametrize("step", [
    {"type": "shell", "command": "touch /tmp/nope"},
    {"type": "action", "id": "copy"},
    {"type": "niri", "action": "--help"},
    {"type": "niri", "action": "spawn-sh", "args": ["touch /tmp/nope"]},
    {"type": "niri", "action": "screenshot", "args": "--help"},
    {"type": "command", "argv": "echo nope"},
    {"type": "command", "argv": []},
    {"type": "command", "argv": [""]},
    {"type": "command", "argv": ["foo\0bar"]},
    {"type": "command", "argv": ["echo", 1]},
    {"type": "shortcut", "keys": ["no-such-key"]},
    {"type": "shortcut", "keys": ["CTRL", "KEY_LEFTCTRL"]},
    {"type": "shortcut", "keys": []},
    {"type": "shortcut", "keys": "CTRL+C"},
    {"type": "text", "text": ""},
    {"type": "text", "text": "foo\0bar"},
    {"type": "delay", "milliseconds": -1},
    {"type": "delay", "milliseconds": True},
    {"type": "delay", "milliseconds": 30_001},
    {"type": "delay", "milliseconds": 1, "extra": True},
])
def test_invalid_step_rejected_before_execution(step, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Invalid steps must not execute")
    monkeypatch.setattr(actions.subprocess, "run", forbidden)
    monkeypatch.setattr(actions.subprocess, "Popen", forbidden)
    with pytest.raises(RuntimeError, match="Invalid desktop configuration"):
        DesktopActions(routine([step]))


def test_cumulative_delay_bound():
    with pytest.raises(RuntimeError, match="more than 60000"):
        validate_config(routine([{"type": "delay", "milliseconds": 30_000}] * 3))


def test_dry_run_has_no_process_keyboard_context_or_delay_side_effect(monkeypatch):
    import evdev
    def forbidden(*_args, **_kwargs):
        pytest.fail("A dry run must have no desktop side effects")
    monkeypatch.setattr(actions.subprocess, "run", forbidden)
    monkeypatch.setattr(actions.subprocess, "Popen", forbidden)
    monkeypatch.setattr(actions.time, "sleep", forbidden)
    monkeypatch.setattr(actions, "focused_context", forbidden)
    monkeypatch.setattr(evdev, "UInput", forbidden)
    steps = [{"type": "niri", "action": "screenshot"}, {"type": "command", "argv": ["echo", "$(no shell)"]},
             {"type": "shortcut", "keys": ["CTRL", "C"]}, {"type": "text", "text": "clipboard text"},
             {"type": "delay", "milliseconds": 1}]
    executor = DesktopActions(routine(steps, app_id="test.app"), dry_run=True)
    result = executor.execute("test")
    assert result == {"ok": True, "action_id": "test", "label": "Test routine", "completed": 0,
                      "dry_run": True, "steps": steps, "condition": {"app_id": "test.app", "evaluated": False}}
    result["steps"].clear()
    assert executor.config["actions"]["test"]["steps"] == steps


def test_executor_owns_a_config_snapshot():
    config = default_config()
    executor = DesktopActions(config, dry_run=True)
    config["actions"].clear()
    assert executor.execute("copy")["ok"]


def test_focused_context_reads_json_using_shell_free_niri(monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout='{"id": 12, "app_id": "kitty", "title": "private"}')
    monkeypatch.setattr(actions.subprocess, "run", run)
    assert focused_context() == {"app_id": "kitty", "window_id": 12}
    assert calls[0][0] == ["niri", "msg", "--json", "focused-window"]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["timeout"] == 2


@pytest.mark.parametrize("payload", ["null", "bad json", "[]", '{"id":true}', '{"app_id":"foo"}'])
def test_focused_context_gracefully_handles_no_window(monkeypatch, payload):
    monkeypatch.setattr(actions, "_run", lambda *_args, **_kwargs: SimpleNamespace(stdout=payload))
    assert focused_context() is None


def test_focus_restore_verifies_target_before_injection(monkeypatch):
    calls = []
    monkeypatch.setattr(actions, "_run", lambda argv, **kwargs: calls.append(argv))
    monkeypatch.setattr(actions, "focused_context", lambda: {"app_id": "kitty", "window_id": 15})
    assert restore_focus(15)["window_id"] == 15
    assert calls == [["niri", "msg", "action", "focus-window", "--id", "15"]]
    with pytest.raises(RuntimeError, match="could not be focused"):
        restore_focus(20)
    with pytest.raises(RuntimeError, match="valid niri"):
        restore_focus("15")


@pytest.mark.parametrize("context", [None, {"app_id": "wrong.app", "window_id": 1}])
def test_application_condition_skips_all_steps(monkeypatch, context):
    monkeypatch.setattr(actions, "focused_context", lambda: context)
    monkeypatch.setattr(actions, "_run", lambda *_args, **_kwargs: pytest.fail("Mismatched app must not execute"))
    executor = DesktopActions(routine([{"type": "niri", "action": "screenshot"}], app_id="right.app"))
    result = executor.execute("test")
    assert result["skipped"] and not result["ok"]
    assert result["completed"] == 0


def test_matching_application_and_macro_step_order(monkeypatch):
    seen = []
    monkeypatch.setattr(actions, "focused_context", lambda: {"app_id": "kitty", "window_id": 1})
    monkeypatch.setattr(actions, "_run", lambda argv, **kwargs: seen.append((argv, kwargs)))
    executor = DesktopActions(routine([{"type": "niri", "action": "focus-workspace-up"},
                                       {"type": "delay", "milliseconds": 0},
                                       {"type": "niri", "action": "focus-workspace-down", "args": []}], app_id="kitty"))
    result = executor.execute("test")
    assert result["completed"] == 3
    assert [call[0][-1] for call in seen] == ["focus-workspace-up", "focus-workspace-down"]


def test_command_launch_passes_literal_argv_without_waiting_for_app(monkeypatch):
    calls = []
    reaped = []
    argv = ["kitty", "--title", "$(touch /tmp/nope); quoted title"]
    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=123, wait=lambda: reaped.append(True))
    class ImmediateThread:
        def __init__(self, target, **_kwargs):
            self.target = target
        def start(self):
            self.target()
    monkeypatch.setattr(actions.subprocess, "Popen", popen)
    monkeypatch.setattr(actions.threading, "Thread", ImmediateThread)
    result = DesktopActions(routine([{"type": "command", "argv": argv}])).execute("test")
    assert result["steps"] == [{"type": "command", "pid": 123}]
    assert calls[0][0] == argv
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["start_new_session"] is True
    assert reaped == [True]


def test_macro_cancellation_prevents_subsequent_steps(monkeypatch):
    calls = []
    monkeypatch.setattr(actions, "_run", lambda argv, **_kwargs: calls.append(argv))
    executor = DesktopActions(routine([{"type": "niri", "action": "screenshot"}] * 3))
    with pytest.raises(RuntimeError, match="after 1/3 steps: cancelled"):
        executor.execute("test", cancelled=lambda: bool(calls))
    assert len(calls) == 1


def test_cancellation_before_start_has_no_effects(monkeypatch):
    monkeypatch.setattr(actions, "_run", lambda *_args, **_kwargs: pytest.fail("Cancelled action must not run"))
    with pytest.raises(RuntimeError, match="after 0/1.*cancelled"):
        DesktopActions().execute("screenshot", cancelled=lambda: True)


def test_process_timeout_and_missing_dependency_are_actionable(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise actions.subprocess.TimeoutExpired("niri", 5)
    monkeypatch.setattr(actions.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="after 0/1.*did not respond"):
        DesktopActions().execute("screenshot")
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("missing")
    monkeypatch.setattr(actions.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="niri is not installed"):
        DesktopActions().execute("screenshot")


def test_failed_late_step_reports_completed_count(monkeypatch):
    calls = []
    def run(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("compositor disconnected")
    monkeypatch.setattr(actions, "_run", run)
    with pytest.raises(RuntimeError, match="after 1/2.*compositor disconnected"):
        DesktopActions(routine([{"type": "niri", "action": "screenshot"}] * 2)).execute("test")


class FakeKeyboard:
    def __init__(self, *_args, **_kwargs):
        self.events = []
        self.closed = False
    def write(self, event_type, code, value):
        self.events.append((event_type, code, value))
    def syn(self):
        pass
    def close(self):
        self.closed = True


def test_keyboard_is_lazy_persistent_and_releases_keys(monkeypatch):
    import evdev
    keyboards = []
    def keyboard(*args, **kwargs):
        result = FakeKeyboard(*args, **kwargs)
        keyboards.append(result)
        return result
    monkeypatch.setattr(evdev, "UInput", keyboard)
    monkeypatch.setattr(actions.time, "sleep", lambda *_args: None)
    executor = DesktopActions(routine([{"type": "shortcut", "keys": ["CTRL", "C"]}]))
    assert not keyboards
    assert executor.execute("test")["ok"]
    executor.execute("test")
    assert len(keyboards) == 1
    assert [(code, value) for _, code, value in keyboards[0].events[:4]] == [
        (evdev.ecodes.KEY_LEFTCTRL, 1), (evdev.ecodes.KEY_C, 1),
        (evdev.ecodes.KEY_C, 0), (evdev.ecodes.KEY_LEFTCTRL, 0)]
    executor.close()
    assert keyboards[0].closed
    assert executor._keyboard is None


def test_injection_failure_releases_already_pressed_modifiers(monkeypatch):
    import evdev
    class FailingKeyboard(FakeKeyboard):
        def write(self, event_type, code, value):
            super().write(event_type, code, value)
            if code == evdev.ecodes.KEY_C and value == 1:
                raise OSError("device failed")
    keyboard = FailingKeyboard()
    monkeypatch.setattr(evdev, "UInput", lambda *_args, **_kwargs: keyboard)
    executor = DesktopActions(routine([{"type": "shortcut", "keys": ["CTRL", "C"]}]))
    monkeypatch.setattr(executor, "_delay", lambda *_args: None)
    with pytest.raises(RuntimeError, match="device failed"):
        executor.execute("test")
    assert [(code, value) for _, code, value in keyboard.events[-2:]] == [
        (evdev.ecodes.KEY_C, 0), (evdev.ecodes.KEY_LEFTCTRL, 0)]


def test_release_failure_removes_device_to_clear_stuck_keys(monkeypatch):
    import evdev
    class BrokenRelease(FakeKeyboard):
        def write(self, event_type, code, value):
            super().write(event_type, code, value)
            if value == 0:
                raise OSError("disconnected")
    keyboard = BrokenRelease()
    monkeypatch.setattr(evdev, "UInput", lambda *_args, **_kwargs: keyboard)
    executor = DesktopActions(routine([{"type": "shortcut", "keys": ["CTRL", "C"]}]))
    monkeypatch.setattr(executor, "_delay", lambda *_args: None)
    with pytest.raises(RuntimeError, match="while releasing keys"):
        executor.execute("test")
    assert keyboard.closed and executor._keyboard is None


def test_keyboard_permission_error_explains_setup(monkeypatch):
    import evdev
    def denied(*_args, **_kwargs):
        raise PermissionError("no access")
    monkeypatch.setattr(evdev, "UInput", denied)
    with pytest.raises(RuntimeError, match="/dev/uinput"):
        DesktopActions().execute("copy")


def test_text_uses_clipboard_then_paste_in_order(monkeypatch):
    calls = []
    monkeypatch.setattr(actions, "_run", lambda argv, **kwargs: calls.append((argv, kwargs)))
    executor = DesktopActions(routine([{"type": "text", "text": "Unicode ✓\n"}]))
    monkeypatch.setattr(executor, "_shortcut", lambda keys, *_args: calls.append(keys))
    assert executor.execute("test")["ok"]
    assert calls == [(["wl-copy", "--type", "text/plain;charset=utf-8"], {"timeout": 3, "input": "Unicode ✓\n"}), ["CTRL", "V"]]


def test_missing_clipboard_tool_prevents_keyboard_injection(monkeypatch):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("wl-copy")
    monkeypatch.setattr(actions.subprocess, "run", missing)
    executor = DesktopActions(routine([{"type": "text", "text": "hello"}]))
    monkeypatch.setattr(executor, "_shortcut", lambda *_args: pytest.fail("No clipboard means no paste"))
    with pytest.raises(RuntimeError, match="wl-copy is not installed"):
        executor.execute("test")


def profile(config, label="General", app_id="*", **extra):
    return {"label": label, "app_id": app_id, "ring": list(reversed(config["ring"])),
            "button_bindings": {"83": "copy"}, **extra}


def test_legacy_config_round_trips_without_adding_optional_fields(tmp_path):
    config = default_config()
    before = json.loads(json.dumps(config))
    assert actions.resolve_config(config) == before
    assert actions.resolve_config(config) is not config
    DesktopConfigStore(tmp_path / "legacy.json").save(config)
    assert DesktopConfigStore(tmp_path / "legacy.json").read() == before
    assert config == before


def test_context_resolution_exact_general_and_legacy_are_detached():
    config = default_config()
    config["ring_groups"] = {"0": ["copy", "paste"]}
    config["assignment_profiles"] = {
        "general": profile(config),
        "editor": profile(config, "Editor", "editor.app", ring_groups={"1": ["undo"]}),
        "other-editor": profile(config, "Other editor", "editor.app"),
    }
    config["active_profiles"] = {"*": "general", "editor.app": "editor"}
    before = json.loads(json.dumps(config))
    exact = actions.resolve_config(config, "editor.app")
    assert exact["ring"] == config["assignment_profiles"]["editor"]["ring"]
    assert exact["ring_groups"] == {"1": ["undo"]}
    general = actions.resolve_config(config, "unknown.app")
    assert "ring_groups" not in general
    assert general["button_bindings"] == {"83": "copy"}
    assert actions.resolve_config(config, None) == general
    exact["actions"]["copy"]["steps"].clear()
    exact["assignment_profiles"]["editor"]["ring"].clear()
    general["ring"].clear()
    assert config == before
    del config["active_profiles"]["*"]
    assert actions.resolve_config(config, "Editor.app")["ring_groups"] == config["ring_groups"]
    assert actions.resolve_config(config, "editor.app")["ring_groups"] == {"1": ["undo"]}


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(assignment_profiles=[]),
    lambda c: c["assignment_profiles"].update({"invalid ID": profile(c)}),
    lambda c: c["assignment_profiles"].update({str(i): profile(c) for i in range(65)}),
    lambda c: c["assignment_profiles"]["general"].update(label=""),
    lambda c: c["assignment_profiles"]["general"].update(label="x" * 81),
    lambda c: c["assignment_profiles"]["general"].update(app_id=""),
    lambda c: c["assignment_profiles"]["general"].update(app_id="x" * 257),
    lambda c: c["assignment_profiles"]["general"].update(app_id="bad\napp"),
    lambda c: c["assignment_profiles"]["general"].update(unknown=1),
    lambda c: c["assignment_profiles"]["general"].update(ring=["missing"] * 8),
    lambda c: c["assignment_profiles"]["general"].update(ring=["copy"] * 9),
    lambda c: c["assignment_profiles"]["general"].update(ring_groups={"0": ["missing"]}),
    lambda c: c["assignment_profiles"]["general"].update(ring_groups={"0": ["copy", "copy"]}),
    lambda c: c["assignment_profiles"]["general"].update(ring_groups={"0": c["ring"][:7]}),
    lambda c: c["assignment_profiles"]["general"].update(button_bindings={"416": "copy"}),
    lambda c: c["assignment_profiles"]["general"].update(button_bindings={"83": "missing"}),
    lambda c: c.update(active_profiles=[]),
    lambda c: c.update(active_profiles={"*": "missing"}),
    lambda c: c.update(active_profiles={"some.app": "general"}),
    lambda c: c.update(active_profiles={"*": []}),
    lambda c: c.update(active_profiles={"": "general"}),
    lambda c: c.update(active_profiles={1: "general"}),
])
def test_invalid_contextual_profiles_are_rejected(mutate):
    config = default_config()
    config["assignment_profiles"] = {"general": profile(config)}
    mutate(config)
    with pytest.raises(RuntimeError, match="Invalid desktop configuration"):
        validate_config(config)


def test_active_profiles_require_profile_document_and_resolver_validates():
    config = default_config()
    config["active_profiles"] = {}
    with pytest.raises(RuntimeError, match="assignment_profiles"):
        actions.resolve_config(config)


@pytest.mark.parametrize("settings", [
    {"ring_scale": 75, "ring_motion_gain": 0.1, "ring_deadzone": 8},
    {"ring_scale": 150, "ring_motion_gain": 2.0, "ring_deadzone": 80.0},
    {"appearance": "system"}, {"appearance": "light"}, {"appearance": "dark"},
    {"haptic_events": {key: True for key in ("open", "hover", "select", "complete")}},
    {"haptic_waveforms": {"open": 0, "hover": 255}},
    {key: False for key in ("haptic_power_save", "autostart", "background", "resume_integration")},
])
def test_optional_settings_round_trip_without_mutation(tmp_path, settings):
    config = {**default_config(), **settings}
    DesktopConfigStore(tmp_path / "desktop.json").save(config)
    assert DesktopConfigStore(tmp_path / "desktop.json").read() == config


@pytest.mark.parametrize("setting,value", [
    ("ring_scale", True), ("ring_scale", 74), ("ring_scale", 151), ("ring_scale", 100.0),
    ("ring_motion_gain", False), ("ring_motion_gain", 0.09), ("ring_motion_gain", 2.01),
    ("ring_motion_gain", float("nan")), ("ring_motion_gain", float("inf")),
    ("ring_deadzone", True), ("ring_deadzone", 7), ("ring_deadzone", 81), ("ring_deadzone", "20"),
    ("appearance", "auto"), ("appearance", []),
    ("haptic_power_save", 1), ("autostart", "true"), ("background", None), ("resume_integration", 0),
    ("haptic_events", []), ("haptic_events", {"unknown": True}), ("haptic_events", {"hover": 1}),
    ("haptic_waveforms", {"hover": True}), ("haptic_waveforms", {"hover": 256}),
    ("haptic_waveforms", {"complete": -1}), ("haptic_waveforms", {"unknown": 1}),
])
def test_invalid_optional_settings_rejected(setting, value):
    config = {**default_config(), setting: value}
    with pytest.raises(RuntimeError, match="Invalid desktop configuration"):
        validate_config(config)


def adjustable(**extra):
    return routine([{"type": "shortcut", "keys": ["MUTE"]}], icon="audio-volume-high-symbolic",
                   adjustment={"increase": [{"type": "shortcut", "keys": ["VOLUMEUP"]}],
                               "decrease": [{"type": "shortcut", "keys": ["VOLUMEDOWN"]}]}, **extra)


def test_adjustments_dry_run_are_detached_and_do_not_probe_context_or_input(monkeypatch):
    import evdev
    def forbidden(*_args, **_kwargs):
        pytest.fail("Preview must not execute or query desktop state")
    monkeypatch.setattr(actions, "focused_context", forbidden)
    monkeypatch.setattr(actions.subprocess, "run", forbidden)
    monkeypatch.setattr(actions.subprocess, "Popen", forbidden)
    monkeypatch.setattr(evdev, "UInput", forbidden)
    executor = DesktopActions(adjustable(app_id="editor.app"), dry_run=True)
    result = executor.execute("test", adjustment="increase")
    assert result["steps"] == [{"type": "shortcut", "keys": ["VOLUMEUP"]}]
    assert result["adjustment"] == "increase"
    assert result["condition"] == {"app_id": "editor.app", "evaluated": False}
    result["steps"][0]["keys"].clear()
    assert executor.execute("test", adjustment="increase")["steps"][0]["keys"] == ["VOLUMEUP"]
    assert "adjustment" not in executor.execute("test")


@pytest.mark.parametrize("branch", ["up", "", 1, [], True])
def test_invalid_adjustment_branch_rejected(branch):
    with pytest.raises(RuntimeError, match="adjustment"):
        DesktopActions(adjustable(), dry_run=True).execute("test", adjustment=branch)


def test_adjustment_requires_adjustable_action():
    with pytest.raises(RuntimeError, match="adjust"):
        DesktopActions(default_config(), dry_run=True).execute("copy", adjustment="increase")


def test_adjustment_uses_shared_execution_guard_and_cancellation(monkeypatch):
    config = adjustable(app_id="editor.app")
    config["actions"]["test"]["adjustment"]["decrease"] *= 2
    seen = []
    monkeypatch.setattr(actions, "focused_context", lambda: {"app_id": "wrong", "window_id": 1})
    executor = DesktopActions(config)
    monkeypatch.setattr(executor, "_shortcut", lambda keys, *_args: seen.append(keys))
    assert executor.execute("test", adjustment="decrease")["skipped"]
    assert seen == []
    monkeypatch.setattr(actions, "focused_context", lambda: {"app_id": "editor.app", "window_id": 1})
    with pytest.raises(RuntimeError, match="after 1/2.*cancelled"):
        executor.execute("test", cancelled=lambda: bool(seen), adjustment="decrease")
    assert seen == [["VOLUMEDOWN"]]
    seen.clear()
    result = executor.execute("test", adjustment="increase")
    assert result["completed"] == 1 and result["adjustment"] == "increase"
    assert seen == [["VOLUMEUP"]]


@pytest.mark.parametrize("icon", ["", "../copy", "/tmp/icon.svg", "bad\nname", "x" * 129, [], "copy icon"])
def test_invalid_action_icon_rejected(icon):
    config = default_config()
    config["actions"]["copy"]["icon"] = icon
    with pytest.raises(RuntimeError, match="icon"):
        validate_config(config)


@pytest.mark.parametrize("adjustment", [
    None, [], {}, {"increase": []},
    {"increase": [], "decrease": [{"type": "delay", "milliseconds": 0}]},
    {"increase": [{"type": "delay", "milliseconds": 0}], "decrease": [], "other": []},
    {"increase": [{"type": "command", "argv": "echo test"}], "decrease": [{"type": "delay", "milliseconds": 0}]},
    {"increase": [{"type": "delay", "milliseconds": 30000}] * 3, "decrease": [{"type": "delay", "milliseconds": 0}]},
    {"increase": [{"type": "delay", "milliseconds": 0}] * 65, "decrease": [{"type": "delay", "milliseconds": 0}]},
])
def test_invalid_adjustment_steps_use_shared_validator(adjustment):
    config = default_config()
    config["actions"]["copy"]["adjustment"] = adjustment
    with pytest.raises(RuntimeError, match="Invalid desktop configuration"):
        validate_config(config)


def test_profile_boundary_and_empty_optional_maps_are_valid():
    config = default_config()
    config.update(assignment_profiles={str(index): profile(config) for index in range(64)},
                  active_profiles={}, haptic_events={}, haptic_waveforms={})
    assert validate_config(config) is config
    assert actions.resolve_config(config)["ring"] == config["ring"]


def test_adjustment_delay_limit_is_per_executed_branch():
    config = adjustable()
    action = config["actions"]["test"]
    for branch in action["adjustment"]:
        action["adjustment"][branch] = [{"type": "delay", "milliseconds": 30000}] * 2
    assert validate_config(config) is config


def test_extended_document_size_limit_is_enforced_before_store_write(tmp_path):
    config = default_config()
    config["actions"].update({str(index): {"label": "Large", "steps": [{"type": "text", "text": "✓" * 65536}]}
                              for index in range(6)})
    with pytest.raises(RuntimeError, match="exceeds 1 MiB"):
        DesktopConfigStore(tmp_path / "config.json").save(config)
    assert not (tmp_path / "config.json").exists()
