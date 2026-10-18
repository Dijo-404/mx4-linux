"""Desktop launcher discovery and dispatch must never reinterpret an Exec line."""

from types import SimpleNamespace

import pytest

from mx4_linux import applications


@pytest.mark.parametrize("desktop_id", [None, [], "", ".desktop", "firefox", "/tmp/app.desktop",
                                        "../app.desktop", "nested/app.desktop", "bad\\app.desktop", "bad\n.desktop"])
def test_invalid_desktop_id_is_rejected_without_loading_gio(monkeypatch, desktop_id):
    monkeypatch.setattr(applications, "_app_info_type", lambda: pytest.fail("Invalid IDs must not query launchers"))
    assert not applications.valid_desktop_id(desktop_id)
    with pytest.raises(RuntimeError, match="valid installed application"):
        applications.launch_application(desktop_id)


def test_discovery_hides_non_menu_entries_sorts_and_never_launches(monkeypatch):
    def app(name, desktop_id, visible=True):
        return SimpleNamespace(get_display_name=lambda: name, get_name=lambda: name, get_id=lambda: desktop_id,
                               should_show=lambda: visible, get_description=lambda: None, get_icon=lambda: None,
                               launch=lambda *_: pytest.fail("Reading applications must not launch anything"))

    entries = [app("Zed", "dev.zed.Zed.desktop"), app("Hidden", "hidden.desktop", False),
               app("Browser", "browser.desktop"), app("Path", "/tmp/path.desktop")]
    monkeypatch.setattr(applications, "_app_info_type", lambda: SimpleNamespace(get_all=lambda: entries))
    assert applications.installed_applications() == [
        {"name": "Browser", "desktop_id": "browser.desktop", "description": "", "icon": None},
        {"name": "Zed", "desktop_id": "dev.zed.Zed.desktop", "description": "", "icon": None},
    ]


def test_launch_uses_installed_entry_api_with_no_exec_parsing(monkeypatch):
    lookups, launches = [], []
    app = SimpleNamespace(launch=lambda *args: launches.append(args) or True)
    monkeypatch.setattr(applications, "_app_info_type",
                        lambda: SimpleNamespace(new=lambda desktop_id: lookups.append(desktop_id) or app))
    applications.launch_application("org.example.Editor.desktop")
    assert lookups == ["org.example.Editor.desktop"]
    assert launches == [([], None)]


def test_uninstalled_application_reports_recovery(monkeypatch):
    monkeypatch.setattr(applications, "_app_info_type", lambda: SimpleNamespace(new=lambda _id: None))
    with pytest.raises(RuntimeError, match="no longer installed.*Smart Actions"):
        applications.launch_application("org.example.Missing.desktop")


def test_desktop_launch_failure_is_not_reported_as_success(monkeypatch):
    app = SimpleNamespace(launch=lambda *_: False, get_display_name=lambda: "Editor")
    monkeypatch.setattr(applications, "_app_info_type", lambda: SimpleNamespace(new=lambda _id: app))
    with pytest.raises(RuntimeError, match="could not open Editor"):
        applications.launch_application("editor.desktop")
