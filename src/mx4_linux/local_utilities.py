"""Small native counterparts for Stopwatch, Tap Tempo and the system bell."""
from collections import deque
import time

from .gtk_support import Adw, Gdk, GLib, Gtk


class Stopwatch:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = None
        self.total = 0.0

    def elapsed(self):
        return self.total + (self.clock() - self.started if self.started is not None else 0)

    def toggle(self):
        if self.started is None:
            self.started = self.clock()
        else:
            self.total = self.elapsed()
            self.started = None

    def reset(self):
        self.total = 0.0
        if self.started is not None:
            self.started = self.clock()

    def pause(self):
        if self.started is not None:
            self.toggle()


class TapTempo:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.taps = deque(maxlen=8)

    def tap(self):
        now = self.clock()
        if self.taps and (now - self.taps[-1] > 3 or now <= self.taps[-1]):
            self.taps.clear()
        self.taps.append(now)
        return self.bpm()

    def bpm(self):
        if len(self.taps) < 2:
            return None
        return 60 * (len(self.taps) - 1) / (self.taps[-1] - self.taps[0])


class UtilityWindows:
    """GTK-thread only; hiding a stopwatch pauses it and removes its update source."""
    def __init__(self, parent):
        self.parent = parent
        self.windows = {}
        self.timer = 0
        self.stopwatch = Stopwatch()
        self.tempo = TapTempo()

    def show(self, operation):
        if operation == 'bell':
            display = Gdk.Display.get_default()
            if display is None:
                raise RuntimeError('A graphical display is required to request the system bell.')
            display.beep()  # A request; the desktop may suppress audible feedback.
            return
        if operation not in ('stopwatch', 'tap-tempo'):
            raise RuntimeError('Unsupported native utility.')
        if operation not in self.windows:
            self.windows[operation] = self._window(operation)
        self.windows[operation].present()

    def _window(self, operation):
        watch = operation == 'stopwatch'
        window = Adw.Window(title='Stopwatch' if watch else 'Tap Tempo', transient_for=self.parent,
                            application=self.parent.get_application(), default_width=340, default_height=240)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      margin_start=24, margin_end=24, margin_top=12, margin_bottom=24)
        label = Gtk.Label(label='00:00.0' if watch else 'Tap to measure BPM')
        label.add_css_class('title-1')
        box.append(label)
        description = Gtk.Label(label=('Pauses when hidden.' if watch else
                                       'Local tap-tempo counterpart. Last 8 taps; resets after 3 seconds.'), wrap=True)
        description.add_css_class('dim-label')
        box.append(description)
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        primary = Gtk.Button(label='Start' if watch else 'Tap')
        primary.add_css_class('suggested-action')
        reset = Gtk.Button(label='Reset')
        buttons.append(primary)
        buttons.append(reset)
        box.append(buttons)
        root.append(box)
        window.set_content(root)
        if watch:
            self.watch_label, self.watch_button = label, primary
            primary.connect('clicked', lambda *_: self._toggle_watch())
            reset.connect('clicked', lambda *_: (self.stopwatch.reset(), self._update_watch()))
            window.connect('notify::visible', self._visibility_changed)
        else:
            primary.connect('clicked', lambda *_: label.set_text(
                f'{bpm:.1f} BPM' if (bpm := self.tempo.tap()) is not None else 'Tap again'))
            reset.connect('clicked', lambda *_: (self.tempo.taps.clear(), label.set_text('Tap to measure BPM')))
        window.connect('close-request', self._hide)
        return window

    def _hide(self, window):
        window.set_visible(False)
        if window is self.windows.get('stopwatch'):
            self._visibility_changed(window, None)
        return True

    def _toggle_watch(self):
        self.stopwatch.toggle()
        self._remove_timer()
        self._update_watch()
        if self.stopwatch.started is not None:
            self.timer = GLib.timeout_add(100, self._update_watch)

    def _update_watch(self):
        seconds = max(0, self.stopwatch.elapsed())
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(int(minutes), 60)
        self.watch_label.set_text((f'{hours:02d}:' if hours else '') + f'{minutes:02d}:{seconds:04.1f}')
        self.watch_button.set_label('Pause' if self.stopwatch.started is not None else 'Start')
        return GLib.SOURCE_CONTINUE

    def _remove_timer(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0

    def _visibility_changed(self, window, _param):
        if not window.get_visible():
            self.stopwatch.pause()
            self._remove_timer()
            if hasattr(self, 'watch_label'):
                self._update_watch()

    def close(self):
        self.stopwatch.pause()
        self._remove_timer()
        windows, self.windows = self.windows, {}
        for window in windows.values():
            window.destroy()
