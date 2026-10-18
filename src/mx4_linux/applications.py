"""Installed desktop applications, discovered read-only and launched on demand.

Keep desktop entry handling in GIO: it understands Flatpak, desktop field codes,
DBus activation and terminal applications without reparsing an Exec command.
"""


def valid_desktop_id(value):
    return (isinstance(value, str) and 8 < len(value) <= 256
            and value.endswith(".desktop") and value.isprintable()
            and not any(character in value for character in ("/", "\\")))


def _app_info_type():
    try:
        from gi.repository import GioUnix
        return GioUnix.DesktopAppInfo
    except ImportError:
        from gi.repository import Gio
        return Gio.DesktopAppInfo


def installed_applications():
    """Return visible launchers, never running them or evaluating their Exec."""
    applications = {}
    for application in _app_info_type().get_all():
        desktop_id = application.get_id()
        if application.should_show() and valid_desktop_id(desktop_id):
            applications[desktop_id] = {
                "desktop_id": desktop_id,
                "name": application.get_display_name() or application.get_name() or desktop_id,
                "description": application.get_description() or "",
                "icon": application.get_icon(),
            }
    return sorted(applications.values(), key=lambda application: (application["name"].casefold(), application["desktop_id"]))


def launch_application(desktop_id):
    """Launch exactly the installed entry selected by a saved application step."""
    if not valid_desktop_id(desktop_id):
        raise RuntimeError("Choose a valid installed application.")
    try:
        application = _app_info_type().new(desktop_id)
        if application is None:
            raise RuntimeError(f"{desktop_id} is no longer installed. Choose another application in Smart Actions.")
        if not application.launch([], None):
            raise RuntimeError(f"The desktop could not open {application.get_display_name()}.")
    except (ImportError, ValueError) as exc:
        raise RuntimeError("Opening installed applications requires the GIO desktop application library.") from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Cannot open {desktop_id}: {exc}") from exc
