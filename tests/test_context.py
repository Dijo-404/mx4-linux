"""Niri context is bounded metadata, without personal window titles."""
import json
import subprocess
import threading
import time

import pytest

from mx4_linux.context import FocusModel, NiriFocusObserver


def test_niri_model_initial_change_close_and_null_focus():
    model = FocusModel()
    assert model.feed({'WindowsChanged': {'windows': [
        {'id': 1, 'app_id': 'editor', 'is_focused': True, 'title': 'private'},
        {'id': 2, 'app_id': 'browser', 'is_focused': False}]}}) == {'window_id': 1, 'app_id': 'editor'}
    assert model.windows == {1: 'editor', 2: 'browser'}
    assert model.feed({'WindowFocusChanged': {'id': 2}}) == {'window_id': 2, 'app_id': 'browser'}
    assert model.feed({'WindowOpenedOrChanged': {'window': {'id': 2, 'app_id': 'web', 'is_focused': True}}}) == {'window_id': 2, 'app_id': 'web'}
    assert model.feed({'WindowClosed': {'id': 2}}) is None
    assert model.feed({'WindowFocusChanged': {'id': None}}) is None


@pytest.mark.parametrize('event', [[], {'WindowsChanged': {'windows': 'bad'}},
    {'WindowsChanged': {'windows': [{'id': True, 'app_id': 'x', 'is_focused': True}]}},
    {'WindowOpenedOrChanged': {'window': {'id': 1, 'app_id': [], 'is_focused': True}}},
    {'WindowFocusChanged': {'id': '1'}}, {'WindowClosed': {}}])
def test_malformed_model_updates_fail_without_mutating_context(event):
    model = FocusModel()
    model.feed({'WindowsChanged': {'windows': [{'id': 1, 'app_id': 'safe', 'is_focused': True}]}})
    with pytest.raises(ValueError):
        model.feed(event)
    assert model.current == {'window_id': 1, 'app_id': 'safe'}


def test_owned_observer_delivers_metadata_and_reaps_on_stop(monkeypatch):
    original = subprocess.Popen
    program = "import json,time; print(json.dumps({'WindowsChanged':{'windows':[{'id':7,'app_id':'test','is_focused':True}]}}),flush=True); time.sleep(30)"
    monkeypatch.setattr('mx4_linux.context.subprocess.Popen', lambda _args, **kw: original(['python3', '-c', program], **kw))
    events = []
    observer = NiriFocusObserver(events.append)
    observer.start()
    deadline = time.monotonic() + 3
    while not events and time.monotonic() < deadline:
        threading.Event().wait(.002)
    assert events[0] == {'type': 'context', 'context': {'window_id': 7, 'app_id': 'test'}}
    process = observer.process
    observer.stop()
    process.wait(timeout=3)
    assert observer.process is None
    assert not observer._deliver(process, observer.generation - 1, {'type': 'context', 'context': None})


def test_unavailable_observer_reports_general_fallback(monkeypatch):
    monkeypatch.setattr('mx4_linux.context.subprocess.Popen', lambda *_a, **_kw: (_ for _ in ()).throw(FileNotFoundError()))
    events = []
    NiriFocusObserver(events.append).start()
    assert events[0]['type'] == 'context-unavailable'
    assert 'General' in events[0]['message']


@pytest.mark.parametrize('line', ['{bad\n', json.dumps([]) + '\n', 'x' * 65537])
def test_bad_observer_output_reports_failure_and_reaps(monkeypatch, line):
    original = subprocess.Popen
    program = 'import sys,time; sys.stdout.write(' + repr(line) + '); sys.stdout.flush(); time.sleep(30)'
    monkeypatch.setattr('mx4_linux.context.subprocess.Popen', lambda _args, **kw: original(['python3', '-c', program], **kw))
    events = []
    observer = NiriFocusObserver(events.append)
    observer.start()
    process = observer.process
    deadline = time.monotonic() + 3
    while not events and time.monotonic() < deadline:
        threading.Event().wait(.002)
    assert events[0]['type'] == 'context-unavailable'
    process.wait(timeout=3)
    observer.stop()


def test_unknown_niri_events_preserve_current_context():
    model = FocusModel()
    model.feed({'WindowsChanged': {'windows': [{'id': 1, 'app_id': 'safe', 'is_focused': True}]}})
    assert model.feed({'CastsChanged': {'arbitrary': 'future event'}}) == {'window_id': 1, 'app_id': 'safe'}
