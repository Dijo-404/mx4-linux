"""Owned, asynchronous Niri focus metadata; window titles never leave the parser."""

import json
import subprocess
import threading


_MAX_LINE = 65536
_UNAVAILABLE = 'Application profiles unavailable; General assignments remain active.'


class FocusModel:
    def __init__(self):
        self.windows = {}
        self.focused = None

    @property
    def current(self):
        if self.focused not in self.windows:
            return None
        return {'window_id': self.focused, 'app_id': self.windows[self.focused]}

    @staticmethod
    def _id(value):
        if type(value) is not int or value < 0:
            raise ValueError('Invalid window ID')
        return value

    @classmethod
    def _window(cls, window):
        if not isinstance(window, dict):
            raise ValueError('Invalid window metadata')
        wid = cls._id(window.get('id'))
        app = window.get('app_id')
        if app is None:
            app = ''
        if not isinstance(app, str) or len(app) > 4096 or type(window.get('is_focused')) is not bool:
            raise ValueError('Invalid application metadata')
        return wid, app, window['is_focused']

    def feed(self, event):
        if not isinstance(event, dict):
            raise ValueError('Invalid event object')
        if 'WindowsChanged' in event:
            body = event['WindowsChanged']
            windows = body.get('windows') if isinstance(body, dict) else None
            if not isinstance(windows, list) or len(windows) > 4096:
                raise ValueError('Invalid window inventory')
            parsed = [self._window(window) for window in windows]
            if len({wid for wid, _, _ in parsed}) != len(parsed) or sum(focused for _, _, focused in parsed) > 1:
                raise ValueError('Ambiguous window inventory')
            self.windows = {wid: app for wid, app, _ in parsed}
            self.focused = next((wid for wid, _, focused in parsed if focused), None)
        elif 'WindowOpenedOrChanged' in event:
            body = event['WindowOpenedOrChanged']
            wid, app, focused = self._window(body.get('window') if isinstance(body, dict) else None)
            if wid not in self.windows and len(self.windows) >= 4096:
                raise ValueError('Window inventory exceeds limit')
            self.windows[wid] = app
            if focused:
                self.focused = wid
            elif self.focused == wid:
                self.focused = None
        elif 'WindowClosed' in event:
            body = event['WindowClosed']
            wid = self._id(body.get('id') if isinstance(body, dict) else None)
            self.windows.pop(wid, None)
            if self.focused == wid:
                self.focused = None
        elif 'WindowFocusChanged' in event:
            body = event['WindowFocusChanged']
            if not isinstance(body, dict) or 'id' not in body:
                raise ValueError('Invalid focus event')
            self.focused = None if body['id'] is None else self._id(body['id'])
        return self.current


class NiriFocusObserver:
    def __init__(self, callback):
        self.callback = callback
        self.process = None
        self.generation = 0
        self._lock = threading.RLock()

    def _deliver(self, process, generation, event):
        with self._lock:
            if self.process is not process or self.generation != generation:
                return False
            self.callback(event)
            return True

    def start(self):
        with self._lock:
            if self.process is not None:
                return
            self.generation += 1
            generation = self.generation
            try:
                process = subprocess.Popen(['niri', 'msg', '--json', 'event-stream'], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1)
            except OSError:
                self.callback({'type': 'context-unavailable', 'message': _UNAVAILABLE})
                return
            self.process = process

        def read():
            model = FocusModel()
            previous = object()
            try:
                while line := process.stdout.readline(_MAX_LINE + 1):
                    if len(line) > _MAX_LINE:
                        raise ValueError('Context event exceeds limit')
                    event = json.loads(line)
                    context = model.feed(event)
                    if not {'WindowsChanged', 'WindowOpenedOrChanged', 'WindowClosed', 'WindowFocusChanged'} & event.keys():
                        continue
                    if context != previous:
                        if not self._deliver(process, generation, {'type': 'context', 'context': context}):
                            break
                        previous = context
                self._deliver(process, generation, {'type': 'context-unavailable', 'message': _UNAVAILABLE})
            except (OSError, ValueError, UnicodeError):
                self._deliver(process, generation, {'type': 'context-unavailable', 'message': _UNAVAILABLE})
            finally:
                if process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    process.wait()
                process.stdout.close()
                with self._lock:
                    if self.process is process and self.generation == generation:
                        self.process = None

        threading.Thread(target=read, daemon=True, name='mx4-niri-context').start()

    def stop(self):
        with self._lock:
            self.generation += 1
            process, self.process = self.process, None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
