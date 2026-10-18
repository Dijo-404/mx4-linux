"""Choose native symbolic icons from an action's actual steps."""

from pathlib import PurePath


def action_icon(action, grouped=False):
    if grouped:
        return "view-app-grid-symbolic"
    if action.get("icon"):
        return action["icon"]
    steps = action.get("steps", [])
    if not steps:
        return "system-run-symbolic"
    step = steps[0]
    kind = step.get("type")
    if kind == "shortcut":
        keys = tuple(key.upper().removeprefix("KEY_") for key in step.get("keys", []))
        icons = {("CTRL", "C"): "edit-copy-symbolic", ("CTRL", "V"): "edit-paste-symbolic",
                 ("CTRL", "X"): "edit-cut-symbolic", ("CTRL", "Z"): "edit-undo-symbolic",
                 ("CTRL", "SHIFT", "Z"): "edit-redo-symbolic", ("PLAYPAUSE",): "media-playback-start-symbolic",
                 ("SYSRQ",): "camera-photo-symbolic"}
        return icons.get(keys, "input-keyboard-symbolic")
    if kind == "application":
        return "application-x-executable-symbolic"
    if kind == "command":
        argv = step.get("argv", [])
        command = PurePath(argv[0]).name if argv else ""
        if command in {"kitty", "foot", "alacritty", "wezterm", "gnome-terminal", "xterm", "konsole", "x-terminal-emulator"}:
            return "utilities-terminal-symbolic"
        if command in {"firefox", "chromium", "google-chrome"}:
            return "web-browser-symbolic"
        if command in {"xdg-open", "gio"}:
            return "web-browser-symbolic" if any(arg.startswith(("http://", "https://")) for arg in argv[1:]) else "folder-symbolic"
        return "application-x-executable-symbolic"
    if kind == "niri":
        name = step.get("action", "")
        if "screenshot" in name:
            return "camera-photo-symbolic"
        if "workspace" in name:
            return "go-previous-symbolic" if any(part in name for part in ("up", "previous", "left")) else "go-next-symbolic"
        return "view-grid-symbolic"
    return {"text": "insert-text-symbolic", "delay": "preferences-system-time-symbolic"}.get(kind, "system-run-symbolic")
