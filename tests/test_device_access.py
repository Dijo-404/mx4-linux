from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("hid_id,matched", [
    ("0003:0000046D:0000C548", True),
    ("0005:0000046D:0000B042", True),
    ("0005:0000046D:0000B048", True),
    ("0003:0000046D:0000C52B", False),
    ("0003:0000320F:0000C548", False),
    ("0003:0000046D:0000B042", False),
    ("0005:0000046D:0000C548", False),
    ("", False),
])
def test_permissions_target_only_rule_covered_interfaces(tmp_path, hid_id, matched):
    node = tmp_path / "hidraw4"
    (node / "device").mkdir(parents=True)
    (node / "device/uevent").write_text(f"HID_NAME=Mouse\nHID_ID={hid_id}\n")
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; mx4_hidraw_paths "$2"', "bash",
         str(ROOT / "scripts/lib-device-access.sh"), str(tmp_path)],
        capture_output=True, check=True,
    )
    assert result.stdout == (str(node).encode() + b"\0" if matched else b"")


@pytest.mark.parametrize("script", ["install-udev-rule.sh", "install-input-access.sh"])
def test_dry_run_never_executes_privileged_commands(tmp_path, script):
    # Dry-run prints every privileged command; none may execute.
    for command in ("sudo", "install", "udevadm", "modprobe"):
        fake = tmp_path / command
        fake.write_text("#!/bin/sh\necho 'UNEXPECTED PRIVILEGED COMMAND' >&2\nexit 99\n")
        fake.chmod(0o755)
    import os
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
    result = subprocess.run([str(ROOT / "scripts" / script), "--dry-run"],
                            env=env, text=True, capture_output=True, check=True)
    assert "nothing changed" in result.stdout
    assert "udevadm control --reload-rules" in result.stdout
    assert "UNEXPECTED" not in result.stderr
