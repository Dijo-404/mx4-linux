"""Catalog creation is pure: every template validates without launching anything."""

from copy import deepcopy

import pytest

import mx4_linux.actions as actions
from mx4_linux.action_catalog import catalog_action, catalog_entries


def config_with(action):
    config = actions.default_config()
    config["actions"]["catalog"] = action
    return config


def test_catalog_entries_have_search_metadata_and_are_detached():
    entries = catalog_entries()
    assert len(entries) >= 30
    assert len({entry["id"] for entry in entries}) == len(entries)
    assert {entry["category"] for entry in entries} >= {"Editing", "Navigation", "Media", "Desktop", "Open"}
    for entry in entries:
        assert all(isinstance(entry[key], str) and entry[key] for key in ("id", "category", "label", "description", "icon"))
    before = deepcopy(catalog_entries())
    entries[0]["label"] = "changed"
    next(entry for entry in entries if entry["id"] == "command")["parameter"]["type"] = "shell"
    assert catalog_entries() == before


def test_all_fixed_presets_validate_and_preview_without_execution(monkeypatch):
    import evdev
    def forbidden(*_a, **_k):
        pytest.fail("Catalog and preview must not execute")
    monkeypatch.setattr(actions.subprocess, "run", forbidden)
    monkeypatch.setattr(actions.subprocess, "Popen", forbidden)
    monkeypatch.setattr(actions, "focused_context", forbidden)
    monkeypatch.setattr(evdev, "UInput", forbidden)
    for entry in catalog_entries():
        if "parameter" in entry:
            continue
        action = catalog_action(entry["id"])
        actions.validate_config(config_with(action))
        executor = actions.DesktopActions(config_with(action), dry_run=True)
        assert executor.execute("catalog")["steps"] == action["steps"]
        for branch in action.get("adjustment", {}):
            assert executor.execute("catalog", adjustment=branch)["steps"] == action["adjustment"][branch]


@pytest.mark.parametrize("preset,keys", [
    ("copy", ["CTRL", "C"]), ("paste", ["CTRL", "V"]), ("cut", ["CTRL", "X"]),
    ("undo", ["CTRL", "Z"]), ("redo", ["CTRL", "SHIFT", "Z"]), ("select-all", ["CTRL", "A"]),
    ("find", ["CTRL", "F"]), ("save", ["CTRL", "S"]), ("back", ["ALT", "LEFT"]),
    ("forward", ["ALT", "RIGHT"]), ("previous-app", ["ALT", "TAB"]), ("close-window", ["ALT", "F4"]),
    ("play-pause", ["PLAYPAUSE"]), ("media-next", ["NEXTSONG"]), ("media-previous", ["PREVIOUSSONG"]),
    ("mute", ["MUTE"]), ("volume-up", ["VOLUMEUP"]), ("volume-down", ["VOLUMEDOWN"]),
])
def test_shortcut_presets(preset, keys):
    assert catalog_action(preset)["steps"] == [{"type": "shortcut", "keys": keys}]


@pytest.mark.parametrize("preset,main,increase,decrease", [
    ("adjustable-volume", ["MUTE"], ["VOLUMEUP"], ["VOLUMEDOWN"]),
    ("adjustable-zoom", ["CTRL", "0"], ["CTRL", "EQUAL"], ["CTRL", "MINUS"]),
    ("adjustable-brightness", ["BRIGHTNESSUP"], ["BRIGHTNESSUP"], ["BRIGHTNESSDOWN"]),
])
def test_adjustable_presets(preset, main, increase, decrease):
    action = catalog_action(preset)
    assert action["steps"] == [{"type": "shortcut", "keys": main}]
    assert action["adjustment"] == {
        "increase": [{"type": "shortcut", "keys": increase}],
        "decrease": [{"type": "shortcut", "keys": decrease}],
    }


@pytest.mark.parametrize("preset,value,step", [
    ("application", ["kitty", "--title", "$(literal)"], {"type": "command", "argv": ["kitty", "--title", "$(literal)"]}),
    ("command", ["printf", "a; b"], {"type": "command", "argv": ["printf", "a; b"]}),
    ("file", "/tmp/a file.pdf", {"type": "command", "argv": ["xdg-open", "/tmp/a file.pdf"]}),
    ("folder", "/tmp/my folder", {"type": "command", "argv": ["xdg-open", "/tmp/my folder"]}),
    ("file", "relative.txt", {"type": "command", "argv": ["xdg-open", "./relative.txt"]}),
    ("file", "--help", {"type": "command", "argv": ["xdg-open", "./--help"]}),
    ("file", "notes:2026.txt", {"type": "command", "argv": ["xdg-open", "./notes:2026.txt"]}),
    ("folder", "archive:2026", {"type": "command", "argv": ["xdg-open", "./archive:2026"]}),
    ("file", "file:///tmp/a", {"type": "command", "argv": ["xdg-open", "./file:///tmp/a"]}),
    ("folder", "https://example.com", {"type": "command", "argv": ["xdg-open", "./https://example.com"]}),
    ("url", "https://example.com/a?q=b#c", {"type": "command", "argv": ["xdg-open", "https://example.com/a?q=b#c"]}),
    ("url", "http://localhost:8080/", {"type": "command", "argv": ["xdg-open", "http://localhost:8080/"]}),
    ("shortcut", ["CTRL", "SHIFT", "P"], {"type": "shortcut", "keys": ["CTRL", "SHIFT", "P"]}),
    ("text", "Unicode ✓\ntext", {"type": "text", "text": "Unicode ✓\ntext"}),
])
def test_templates_validate_and_detach_inputs(preset, value, step):
    before = deepcopy(value)
    action = catalog_action(preset, value)
    assert action["steps"] == [step]
    actions.validate_config(config_with(action))
    if isinstance(value, list):
        value.clear()
    assert action["steps"] == [step]
    action["steps"].clear()
    assert catalog_action(preset, before)["steps"] == [step]


@pytest.mark.parametrize("preset,value", [
    ("application", "kitty --title hello"), ("command", []), ("command", ["echo", 1]),
    ("command", ["echo\0bad"]), ("file", ""), ("folder", "\0"), ("folder", []),
    ("url", "file:///tmp/private"), ("url", "javascript:alert(1)"), ("url", "https:///nohost"),
    ("url", "https://"), ("url", "https://bad host/"), ("url", "https://example.com\n.evil"),
    ("url", "https://example.com/\0"), ("url", "https://example.com:bad/"),
    ("url", "https://example.com:99999/"), ("url", "https://-bad.example/"),
    ("shortcut", "CTRL+C"), ("shortcut", ["CTRL", "CTRL"]), ("text", ""), ("text", None),
    ("unknown", None), ([], None), ("copy", "unexpected"),
])
def test_invalid_catalog_requests_rejected(preset, value):
    with pytest.raises(RuntimeError):
        catalog_action(preset, value)


def test_new_catalog_entries_are_detached_and_availability_is_not_action_schema(monkeypatch):
    from mx4_linux.action_catalog import catalog_availability
    entries = {item['id']: item for item in catalog_entries()}
    assert {'iso-date', 'moon-phase', 'clipboard-sentence-case', 'switch-profile', 'adapt-on', 'adapt-off',
            'stopwatch', 'tap-tempo', 'bell', 'multi-toggle', 'do-nothing', 'sound', 'keyboard-layout-next',
            'new-tab', 'media-stop'} <= entries.keys()
    assert 'approximate' in entries['moon-phase']['description'].lower()
    assert 'session' in entries['multi-toggle']['description'].lower()
    monkeypatch.setattr('mx4_linux.action_catalog.shutil.which', lambda _: None)
    availability = catalog_availability('clipboard-upper')
    assert availability['available'] is False
    action = catalog_action('clipboard-upper')
    assert 'available' not in action and 'availability' not in action
    assert catalog_action('bell')['steps'] == [{'type': 'local', 'operation': 'bell'}]


def test_local_profile_catalog_template_requires_real_config_and_reference():
    cfg = actions.default_config()
    cfg['assignment_profiles'] = {'work': {'label': 'Work', 'app_id': '*', 'ring': cfg['ring'][:], 'button_bindings': {}}}
    action = catalog_action('switch-profile', 'work', config=cfg)
    assert action['steps'] == [{'type': 'local', 'operation': 'switch-profile', 'profile_id': 'work'}]
    with pytest.raises(RuntimeError, match='profile|configuration'):
        catalog_action('switch-profile', 'missing', config=cfg)
    with pytest.raises(RuntimeError, match='configuration'):
        catalog_action('switch-profile', 'work')


def test_toggle_and_literal_local_sound_templates_validate_without_discovery(monkeypatch):
    monkeypatch.setattr('mx4_linux.action_catalog.shutil.which', lambda _: pytest.fail('construction discovers helper'))
    branches = [[{'type': 'delay', 'milliseconds': 0}], [{'type': 'delay', 'milliseconds': 1}]]
    action = catalog_action('multi-toggle', branches)
    assert action['steps'] == branches[0] and action['toggle_steps'] == branches[1:]
    branches[1].clear()
    assert action['toggle_steps'][0]
    assert catalog_action('sound', '/tmp/literal $(test).wav')['steps'] == [
        {'type': 'command', 'argv': ['pw-play', '--', '/tmp/literal $(test).wav']}]
    with pytest.raises(RuntimeError):
        catalog_action('sound', 'https://example.com/sound.wav')
