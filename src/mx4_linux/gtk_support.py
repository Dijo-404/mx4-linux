"""Load layer-shell before GTK's Wayland library, as its Python API requires."""

from ctypes import CDLL, RTLD_GLOBAL

try:
    CDLL("libgtk4-layer-shell.so.0", mode=RTLD_GLOBAL)
except OSError:
    pass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as Layer
except (ImportError, ValueError):
    Layer = None
