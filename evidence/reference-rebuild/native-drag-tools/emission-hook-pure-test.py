"""Memory-only GObject hook ordering test; no GTK/app/display imports or input."""
import sys
from unittest.mock import patch

with patch("socket.socket", side_effect=AssertionError("No display socket")), \
        patch("subprocess.Popen", side_effect=AssertionError("No child process")):
    from gi.repository import GObject

    class PureProof(GObject.Object):
        __gsignals__ = {"proof": (GObject.SignalFlags.RUN_LAST, None, (float, float))}

    instance = PureProof()
    calls = []

    def before_handler(obj, x, y):
        calls.append(("hook", obj, x, y))
        return True

    hook_id = GObject.add_emission_hook(PureProof, "proof", before_handler)
    instance.connect("proof", lambda obj, x, y: calls.append(("handler", obj, x, y)))
    instance.emit("proof", 1.25, 2.5)
    GObject.signal_remove_emission_hook(GObject.signal_lookup("proof", PureProof), hook_id)
    assert [name for name, *_ in calls] == ["hook", "handler"]
    assert calls[0][1:] == (instance, 1.25, 2.5)
    assert not any(name.startswith(("gi.repository.Gtk", "gi.repository.Gdk", "mx4_linux"))
                   for name in sys.modules)
    print("PASS: typed GObject hook arguments and before-handler ordering; no GTK/display/process")
