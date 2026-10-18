import argparse
import json
import os
from pathlib import Path
import sys

from .backend import Backend, BackendError
from .profiles import ProfileStore, apply_profile
from . import __version__


def doctor():
    nodes = []
    for path in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        try:
            fields = dict(line.split("=", 1) for line in (path / "device/uevent").read_text().splitlines() if "=" in line)
        except OSError:
            continue
        if ":0000046D:" not in fields.get("HID_ID", ""):
            continue
        node = str(Path("/dev") / path.name)
        nodes.append({"path": node, "name": fields.get("HID_NAME"),
                      "accessible": os.access(node, os.R_OK | os.W_OK)})
    return {"session": os.environ.get("XDG_SESSION_TYPE", "unknown"),
            "desktop": os.environ.get("XDG_CURRENT_DESKTOP", "unknown"), "logitech_nodes": nodes,
            "permission_help": "Run ./scripts/install-udev-rule.sh, then refresh the app."}


def select_device(scan, device_id=None):
    devices = [d for d in scan["devices"] if d.get("online")]
    if device_id:
        devices = [d for d in devices if d["id"] == device_id]
    if len(devices) != 1:
        raise BackendError("Select one online MX Master 4 using --device ID from `mx4-linux devices --json`.")
    return devices[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description="MX Master 4 controls for Linux")
    parser.add_argument("--version", action="version", version=f"mx4-linux {__version__}")
    commands = parser.add_subparsers(dest="command")
    gui = commands.add_parser("gui", help="Open the native GTK 4 control panel (default)")
    gui.add_argument("--demo", action="store_true", help="Preview without accessing hardware")
    ring = commands.add_parser("ring", help="Open Actions Ring for the focused application (also usable in a desktop key binding)")
    ring.add_argument("--preview", action="store_true", help="Select actions without executing them")
    actions = commands.add_parser("actions", help="Configure, inspect and run Smart Actions")
    action_commands = actions.add_subparsers(dest="action_command", required=True)
    action_commands.add_parser("list", help="List available action IDs")
    show = action_commands.add_parser("show", help="Print the configuration or one action")
    show.add_argument("id", nargs="?")
    run_action = action_commands.add_parser("run", help="Execute a routine in the focused application")
    run_action.add_argument("id")
    run_action.add_argument("--dry-run", action="store_true", help="Validate and print steps without running them")
    for name in ("import", "validate"):
        action_command = action_commands.add_parser(name, help=f"{name.capitalize()} a desktop configuration JSON file")
        action_command.add_argument("path")
    for name, help_text in [("devices", "Read MX Master 4 devices and supported settings"), ("doctor", "Check Logitech device access")]:
        cmd = commands.add_parser(name, help=help_text)
        cmd.add_argument("--json", action="store_true", help="Machine-readable output")
    write = commands.add_parser("set", help="Apply one setting to a selected mouse")
    write.add_argument("device", help="Device ID from devices --json")
    write.add_argument("setting", help="Setting name from devices --json")
    write.add_argument("value", help="JSON boolean/integer, e.g. true or 1600; choice IDs are numeric")
    write.add_argument("--key", type=int, help="Button ID for a button assignment")
    profile = commands.add_parser("profile", help="Save, list, or apply local profiles")
    profile.add_argument("action", choices=["list", "save", "apply"])
    profile.add_argument("name", nargs="?")
    profile.add_argument("--device", help="Select device ID; optional when one mouse is online")
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "gui", "ring"):
            from .gui import run
            return run(demo=getattr(args, "demo", False), show_ring=args.command == "ring",
                       preview_ring=getattr(args, "preview", False))
        if args.command == "doctor":
            result = doctor()
        elif args.command == "devices":
            result = Backend().scan()
        elif args.command == "set":
            try:
                value = json.loads(args.value)
            except ValueError as exc:
                raise BackendError("Use an integer choice ID or a JSON boolean (true/false).") from exc
            backend = Backend()
            device = select_device(backend.scan(), args.device)
            result = backend.write(device["id"], args.setting, value, args.key,
                                   expected_stable_id=device.get("stable_id"))
        elif args.command == "actions":
            from .actions import DesktopActions, DesktopConfigStore, validate_config, MAX_CONFIG_BYTES
            store = DesktopConfigStore()
            if args.action_command in ("import", "validate"):
                path = Path(args.path)
                if path.stat().st_size > MAX_CONFIG_BYTES:
                    raise RuntimeError("Desktop configuration exceeds 1 MiB.")
                data = validate_config(json.loads(path.read_text()))
                if args.action_command == "import":
                    store.save(data)
                result = {"ok": True, args.action_command: str(path)}
            else:
                data = store.read()
                if args.action_command == "list":
                    result = {key: {"label": value["label"], "steps": len(value["steps"])} for key, value in data["actions"].items()}
                elif args.action_command == "show":
                    if args.id and args.id not in data["actions"]:
                        raise RuntimeError(f"Unknown action: {args.id}")
                    result = data["actions"][args.id] if args.id else data
                else:
                    runner = DesktopActions(data, dry_run=args.dry_run)
                    try:
                        result = runner.execute(args.id)
                    finally:
                        runner.close()
        else:
            store = ProfileStore()
            if args.action == "list":
                result = store.read()
            else:
                if not args.name:
                    raise BackendError("A profile name is required.")
                backend = Backend()
                device = select_device(backend.scan(), args.device)
                if args.action == "save":
                    store.save(args.name, device)
                    result = {"saved": args.name, "path": str(store.path)}
                else:
                    profiles = store.read()
                    if args.name not in profiles:
                        raise BackendError(f"Unknown profile: {args.name}")
                    count = apply_profile(backend, device, profiles[args.name])
                    result = {"applied": args.name, "changes": count}
        if getattr(args, "json", False) or args.command in ("set", "profile", "actions"):
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "doctor":
            print(f"Desktop: {result['desktop']} ({result['session']})")
            for node in result["logitech_nodes"]:
                print(f"{node['path']} · {node['name']} · {'accessible' if node['accessible'] else 'permission denied'}")
            if not result["logitech_nodes"]:
                print("No Logitech hidraw devices found.")
            if any(not n["accessible"] for n in result["logitech_nodes"]):
                print(result["permission_help"])
        else:
            for device in result["devices"]:
                print(f"{device['name']} [{device['id']}] · {'online' if device['online'] else 'offline'}")
                for setting in device["settings"]:
                    print(f"  {setting['name']}: {setting.get('value')}")
            for diagnostic in result.get("diagnostics", []):
                print(f"{diagnostic.get('path', '')}: {diagnostic['message']}")
            if not result["devices"] and not result.get("diagnostics"):
                print("No accessible MX Master 4 found. Run mx4-linux doctor.")
        return 0
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"mx4-linux: {exc}", file=sys.stderr)
        return 1
    except ImportError as exc:
        print(f"mx4-linux: Missing dependency: {exc}. Run ./scripts/setup.sh and install GTK 4 / Libadwaita.", file=sys.stderr)
        return 1
