"""Choose recognizable icons from action behavior rather than editable labels."""

import pytest

from mx4_linux.ring_icons import action_icon


@pytest.mark.parametrize(("step", "icon"), [
    ({"type": "shortcut", "keys": ["key_ctrl", "key_c"]}, "edit-copy-symbolic"),
    ({"type": "shortcut", "keys": ["CTRL", "V"]}, "edit-paste-symbolic"),
    ({"type": "shortcut", "keys": ["CTRL", "SHIFT", "Z"]}, "edit-redo-symbolic"),
    ({"type": "shortcut", "keys": ["PLAYPAUSE"]}, "media-playback-start-symbolic"),
    ({"type": "shortcut", "keys": ["SUPER", "Q"]}, "input-keyboard-symbolic"),
    ({"type": "command", "argv": ["/usr/bin/kitty", "--title", "Work"]}, "utilities-terminal-symbolic"),
    ({"type": "command", "argv": ["x-terminal-emulator"]}, "utilities-terminal-symbolic"),
    ({"type": "command", "argv": ["firefox"]}, "web-browser-symbolic"),
    ({"type": "command", "argv": ["xdg-open", "https://example.org"]}, "web-browser-symbolic"),
    ({"type": "command", "argv": ["gio", "open", "/home/user/notes"]}, "folder-symbolic"),
    ({"type": "command", "argv": ["custom-application"]}, "application-x-executable-symbolic"),
    ({"type": "niri", "action": "focus-workspace-up"}, "go-previous-symbolic"),
    ({"type": "niri", "action": "focus-workspace-down"}, "go-next-symbolic"),
    ({"type": "niri", "action": "screenshot-screen"}, "camera-photo-symbolic"),
    ({"type": "text", "text": "Hello"}, "insert-text-symbolic"),
    ({"type": "delay", "milliseconds": 100}, "preferences-system-time-symbolic"),
])
def test_icons_describe_the_action_even_after_renaming(step, icon):
    action = {"label": "My custom name", "steps": [step]}
    assert action_icon(action) == icon


def test_group_icon_distinguishes_an_action_collection_from_its_parent_routine():
    action = {"label": "Clipboard", "steps": [{"type": "shortcut", "keys": ["CTRL", "C"]}]}
    assert action_icon(action, grouped=True) == "view-app-grid-symbolic"


def test_incomplete_editor_drafts_have_a_neutral_icon():
    assert action_icon({"label": "New action", "steps": []}) == "system-run-symbolic"
