import subprocess
from types import SimpleNamespace

import pytest

from mx4_linux.backend import Backend, BackendError
from mx4_linux.cli import main, select_device


def test_worker_timeout_becomes_actionable_error(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 90)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(BackendError, match="Wake it"):
        Backend().scan()


def test_worker_malformed_response_surfaces_stderr(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="missing Solaar"))
    with pytest.raises(BackendError, match="missing Solaar"):
        Backend().scan()


def test_device_selection_requires_exact_unambiguous_target():
    scan = {"devices": [{"id": "a", "online": True}, {"id": "b", "online": True}]}
    with pytest.raises(BackendError):
        select_device(scan)
    assert select_device(scan, "b")["id"] == "b"
    with pytest.raises(BackendError):
        select_device(scan, "c")


def test_cli_rejects_unparsed_values_before_worker(monkeypatch, capsys):
    monkeypatch.setattr(Backend, "write", lambda *args: pytest.fail("Must not contact hardware"))
    assert main(["set", "id", "haptic-level", "fifty"]) == 1
    assert "JSON boolean" in capsys.readouterr().err


def test_cli_forwards_numeric_key_and_boolean(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(Backend, "write", lambda *args: seen.append(args) or {"ok": True})
    assert main(["set", "id", "reprogrammable-keys", "86", "--key", "83"]) == 0
    assert seen[0][1:] == ("id", "reprogrammable-keys", 86, 83)


def test_actions_cli_dry_run_never_runs_a_desktop_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("mx4_linux.actions._run", lambda *a, **kw: pytest.fail("Dry-run must not execute anything"))
    assert main(["actions", "run", "copy", "--dry-run"]) == 0
    assert '"dry_run": true' in capsys.readouterr().out


def test_actions_cli_rejects_bad_import_without_overwriting_config(monkeypatch, tmp_path, capsys):
    from mx4_linux.actions import DesktopConfigStore, default_config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    store = DesktopConfigStore()
    store.save(default_config())
    before = store.path.read_bytes()
    bad = tmp_path / "bad.json"
    bad.write_text('{"version":9}')
    assert main(["actions", "import", str(bad)]) == 1
    assert "Invalid desktop configuration" in capsys.readouterr().err
    assert store.path.read_bytes() == before
