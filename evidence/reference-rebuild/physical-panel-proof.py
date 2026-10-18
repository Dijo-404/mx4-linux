#!/usr/bin/env python3
"""Root-reviewed, temporary MX4 physical reporting proof; default is no I/O.

Run with project .venv/bin/python. --self-check only validates inert config.
--run is the sole mode that starts integration and changes reporting bits.
No test action launches anything except literal /usr/bin/true.
"""
from __future__ import annotations

import argparse
import contextlib
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from time import monotonic, sleep
from types import SimpleNamespace

APP_ID = 'io.github.mx4proof.PhysicalTarget'
CONTROLS = (82, 83, 86, 195, 196, 416)
RING_CID = 416
TRUE_STEP = {'type': 'command', 'argv': ['/usr/bin/true']}


def inert_config():
    actions = {f'physical-proof-{index}': {
        'label': f'No operation {index + 1}', 'app_id': APP_ID,
        'steps': [deepcopy(TRUE_STEP)],
    } for index in range(8)}
    return {'version': 1, 'ring': list(actions), 'actions': actions,
            'ring_button': RING_CID, 'button_bindings': {},
            'haptic_feedback': False, 'autostart': False, 'background': False,
            'resume_integration': False}


def assert_inert(config):
    from mx4_linux.actions import validate_config
    validate_config(config)
    assert config == inert_config(), 'Proof config differs from the reviewed inert document'
    assert len(config['actions']) == len(config['ring']) == 8
    for action in config['actions'].values():
        assert action['steps'] == [TRUE_STEP]
        assert not action.get('adjustment')
    return config


def child_snapshot(device_id):
    """GET only. JSON stdout is private parent IPC, never an evidence artifact."""
    from mx4_linux.device_worker import _device_id, _devices, _isolated_solaar, _load_solaar
    from mx4_linux.event_worker import _reporting
    from mx4_linux.reporting_state import stable_identity
    api = _load_solaar()
    api.hidpp20 = importlib.import_module('logitech_receiver.hidpp20')
    with contextlib.redirect_stdout(sys.stderr), _isolated_solaar(api, software_id=0x0C):
        diagnostics = []
        with contextlib.closing(_devices(api, diagnostics, device_id)) as devices:
            selected = None
            for dev, path in devices:
                if _device_id(path, dev.number) != device_id:
                    continue
                assert selected is None and dev.online
                identity = stable_identity(dev)
                assert identity, 'No stable mouse identity'
                records = {}
                for cid in CONTROLS:
                    key = next((key for key in dev.keys if int(key._cid) == cid), None)
                    assert key is not None
                    flags, mapping = _reporting(dev, api, key)
                    records[str(cid)] = {'flags': flags, 'mapping': mapping}
                selected = {'stable_id': identity, 'controls': records}
            assert selected is not None, 'Selected mouse unavailable'
    # Contains per-unit identity for comparison in memory only. Parent does not
    # print or persist this transport document or private worker diagnostics.
    print(json.dumps(selected, separators=(',', ':')))


def child_target(state_path):
    """Known, inert focus recipient. Stores only two booleans, never input."""
    from mx4_linux.gtk_support import Gio, GLib, Gtk
    path = Path(state_path)
    app = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
    def save(window):
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
            os.chmod(stream.name, 0o600)
            json.dump({'active': window.is_active(), 'mapped': window.get_mapped()}, stream)
        os.replace(stream.name, path)
    def activate(application):
        window = Gtk.ApplicationWindow(application=application,
            title='MX4 physical input proof', default_width=460, default_height=200)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
        label = Gtk.Label(label='MX4 physical ring proof\n\nHold the haptic panel, move toward a ring item, then release.\nEvery ring item performs /usr/bin/true.\nThis window closes automatically.', wrap=True)
        box.append(label)
        window.set_child(box)
        window.connect('notify::is-active', lambda *_: save(window))
        window.connect('map', lambda *_: save(window))
        window.connect('unmap', lambda *_: save(window))
        window.present()
        save(window)
        GLib.timeout_add_seconds(240, lambda: application.quit() or GLib.SOURCE_REMOVE)
    app.connect('activate', activate)
    return app.run([])


def subprocess_json(argv, *, input_document=None, timeout=30):
    result = subprocess.run(argv, input=None if input_document is None else json.dumps(input_document),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=True)
    return json.loads(result.stdout)


def run_live(gesture_timeout):
    # Import GTK only in the explicitly requested live mode. The isolated roots
    # also reach every worker/target child. Runtime dir and DBus stay inherited.
    directory = Path(tempfile.mkdtemp(prefix='mx4-physical-proof-'))
    os.chmod(directory, 0o700)
    old_environment = {key: os.environ.get(key) for key in ('XDG_CONFIG_HOME', 'XDG_STATE_HOME')}
    os.environ['XDG_CONFIG_HOME'] = str(directory / 'config')
    os.environ['XDG_STATE_HOME'] = str(directory / 'state')
    from mx4_linux.actions import DesktopActions, DesktopConfigStore, focused_context, restore_focus
    from mx4_linux.desktop import DesktopController
    from mx4_linux.gtk_support import Adw, Gio, GLib
    from mx4_linux.reporting_state import OWNED_BITS, Ownership
    from mx4_linux.ring import ActionsRing

    context = GLib.MainContext.default()
    interrupted = threading.Event()
    original_signals = {}
    target = controller = app = None
    previous = None
    snapshot = after = None
    attempted = stopped = restored = journal_clean = False
    failure = None
    stage = 'preflight'
    records = {'button_presses': 0, 'button_releases': 0, 'motion_packets': 0,
               'ring_open_count': 0, 'mapped_seen': False, 'action_successes': 0,
               'selected_indices': [], 'worker_errors': 0, 'worker_warnings': 0,
               'unexpected_controls': 0}
    event_stopped = []
    original_execute = DesktopActions.execute
    original_show = ActionsRing.show_ring
    state_path = directory / 'target-state.json'
    def pump():
        while context.pending():
            context.iteration(False)
        if controller and controller.ring and controller.ring.get_mapped():
            records['mapped_seen'] = True
    def until(predicate, seconds, *, abort_on_worker_error=True, allow_interrupt=False):
        deadline = monotonic() + seconds
        while not predicate():
            pump()
            if interrupted.is_set() and not allow_interrupt:
                raise RuntimeError('Interrupted')
            if abort_on_worker_error and records['worker_errors']:
                raise RuntimeError('Worker reported an error')
            if monotonic() > deadline:
                raise TimeoutError('Bounded proof stage timed out')
            sleep(.01)
        pump()
    def target_state():
        try:
            return json.loads(state_path.read_text())
        except (OSError, ValueError):
            return {}
    def target_windows():
        # Niri's raw inventory remains in memory only; titles are discarded.
        windows = subprocess_json(['niri', 'msg', '--json', 'windows'], timeout=3)
        return [item['id'] for item in windows if item.get('app_id') == APP_ID]
    def snapshot_reporting(device_id):
        return subprocess_json([sys.executable, str(Path(__file__).resolve()),
                                '--_snapshot', device_id], timeout=25)
    def journal_paths():
        return list((directory / 'state' / 'mx4-linux' / 'reporting').glob('*.json'))
    def recorded_execute(runner, action_id, *args, **kwargs):
        assert_inert(runner.config)
        assert not runner.dry_run and kwargs.get('adjustment') is None
        assert action_id in runner.config['actions']
        result = original_execute(runner, action_id, *args, **kwargs)
        if result.get('ok') and result.get('completed') == 1 and not result.get('skipped'):
            records['action_successes'] += 1
            records['selected_indices'].append(runner.config['ring'].index(action_id))
        return result
    def recorded_show(ring, config, *args, **kwargs):
        assert_inert(config)
        assert kwargs.get('held') is True and not kwargs.get('preview')
        records['ring_open_count'] += 1
        return original_show(ring, config, *args, **kwargs)
    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            original_signals[number] = signal.signal(number, lambda *_: interrupted.set())
        assert Path('/usr/bin/true').is_file() and os.access('/usr/bin/true', os.X_OK)
        assert not target_windows(), 'Another proof target already exists'
        config = assert_inert(inert_config())
        DesktopConfigStore().save(config)
        assert not journal_paths()
        # Read-only scan is separately bounded and is never printed/persisted.
        scan = subprocess_json([sys.executable, '-m', 'mx4_linux.device_worker'],
                               input_document={'op': 'scan'}, timeout=30)
        online = [device for device in scan.get('devices', []) if device.get('online') is True]
        assert scan.get('ok') and len(online) == 1, 'Need exactly one online MX4'
        current = online[0]
        snapshot = snapshot_reporting(current['id'])
        assert len(snapshot['controls']) == len(CONTROLS)
        # Refuse to adopt/report over another listener's current diversion.
        assert all(not item['flags'] & OWNED_BITS for item in snapshot['controls'].values()), 'Reporting is already diverted'
        stable_id = snapshot['stable_id']
        # Metadata must agree with GET-derived stable identity (Task4 contract).
        assert current.get('stable_id') == stable_id, 'Fresh scan has no matching stable identity'
        ownership = Ownership(stable_id)
        assert ownership.read() is None
        previous = focused_context()
        target = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--_target', str(state_path)],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stage = 'controlled-target'
        until(lambda: target_state().get('active') is True and target_state().get('mapped') is True, 10)
        ids = target_windows()
        assert len(ids) == 1 and type(ids[0]) is int
        window_id = ids[0]
        assert restore_focus(window_id) == {'app_id': APP_ID, 'window_id': window_id}
        app = Adw.Application(application_id='io.github.mx4linux.PhysicalProof', flags=Gio.ApplicationFlags.NON_UNIQUE)
        assert app.register(None)
        window = SimpleNamespace(demo=False, busy=False, current=current, backend=None,
            get_application=lambda: app, update_desktop_status=lambda: None)
        controller = DesktopController(window)
        DesktopActions.execute = recorded_execute
        ActionsRing.show_ring = recorded_show
        original_event = controller.event
        def event(item):
            kind = item.get('type')
            if kind in ('ready', 'configured') and (item.get('controls') != [RING_CID]
                    or item.get('ring_button') != RING_CID or item.get('raw_xy') is not True):
                records['unexpected_controls'] += 1
                controller.stop()
            elif kind == 'button' and not item.get('cancelled'):
                if item.get('control') != RING_CID:
                    records['unexpected_controls'] += 1
                    controller.stop()
                elif item.get('pressed') is True:
                    records['button_presses'] += 1
                elif item.get('pressed') is False:
                    records['button_releases'] += 1
            elif kind == 'motion':
                records['motion_packets'] += 1
            elif kind == 'error':
                records['worker_errors'] += 1
            elif kind == 'warning':
                records['worker_warnings'] += 1
            elif kind == 'stopped':
                event_stopped.append(item.get('restored'))
            result = original_event(item)
            if records['unexpected_controls']:
                controller.stop()
            return result
        controller.event = event
        stage = 'start-reporting'
        attempted = True
        controller.start()
        until(lambda: controller.session.ready, 25)
        assert not records['unexpected_controls']
        assert controller.session.device_id == current['id']
        assert controller.context == {'app_id': APP_ID, 'window_id': window_id}
        print(json.dumps({'stage': 'ready-for-physical-gesture', 'ring_control': RING_CID,
            'actions': 8, 'action_argv': ['/usr/bin/true'], 'haptics_enabled': False,
            'gesture_timeout_seconds': gesture_timeout}), flush=True)
        stage = 'physical-gesture'
        until(lambda: (records['button_presses'] >= 1 and records['button_releases'] >= 1
                      and records['motion_packets'] >= 1 and records['ring_open_count'] >= 1
                      and records['mapped_seen'] and records['action_successes'] >= 1
                      and not controller.action_busy), gesture_timeout)
        assert not records['unexpected_controls']
        assert records['action_successes'] == 1, 'Multiple selections observed'
        stage = 'stop-reporting'
    except BaseException as error:
        # Deliberately do not disclose exception text: device failures can
        # contain paths, IDs or private upstream diagnostic content.
        failure = type(error).__name__
    finally:
        # Never SIGKILL a listener or delete its journal on uncertain cleanup.
        if controller:
            controller.stop()
            try:
                until(lambda: not controller.session.running and not controller.starting,
                      45, abort_on_worker_error=False, allow_interrupt=True)
                until(lambda: bool(event_stopped), 3, abort_on_worker_error=False, allow_interrupt=True)
                stopped = True
                restored = controller.session.restored is True and event_stopped[-1] is True
            except BaseException:
                failure = failure or 'StopUnconfirmed'
            controller.close()
            # Closing also stops the focus processes. Give their callbacks a
            # brief bounded turn before the application disappears.
            deadline = monotonic() + .2
            while monotonic() < deadline:
                pump()
                sleep(.01)
        DesktopActions.execute = original_execute
        ActionsRing.show_ring = original_show
        if attempted and snapshot and stopped:
            try:
                after = snapshot_reporting(current['id'])
                restored = bool(restored and after == snapshot)
            except BaseException:
                restored = False
                failure = failure or 'PostStopReadUnconfirmed'
        journal_clean = not journal_paths()
        if attempted and (not stopped or not restored or not journal_clean):
            failure = failure or 'RestorationUnconfirmed'
        if target:
            target.terminate()
            try:
                target.wait(timeout=3)
            except subprocess.TimeoutExpired:
                target.kill()  # Only the inert GTK target, never event_worker.
                target.wait(timeout=3)
        if previous:
            try:
                restore_focus(previous['window_id'])
            except BaseException:
                pass
        if app:
            app.quit()
        for number, handler in original_signals.items():
            signal.signal(number, handler)
        preserve = attempted and not (stopped and restored and journal_clean)
        output = {'ok': failure is None, 'stage': stage, 'failure_type': failure,
            'physical_gesture_observed': all(records[key] > 0 for key in
                ('button_presses', 'button_releases', 'motion_packets')),
            **records, 'reporting_snapshot_count': len(snapshot['controls']) if snapshot else 0,
            'reporting_equal_after_stop': bool(snapshot and after == snapshot),
            'worker_stopped': stopped, 'worker_restored': restored,
            'reporting_journal_clean': journal_clean, 'haptics_played': 0,
            'preserved_state_directory': str(directory) if preserve else None}
        print(json.dumps(output, sort_keys=True), flush=True)
        if not preserve:
            shutil.rmtree(directory)
        for key, value in old_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return 0 if failure is None else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run', action='store_true', help='ROOT REVIEW REQUIRED: start physical reporting proof')
    mode.add_argument('--self-check', action='store_true', help='Pure configuration checks; no GTK, focus, or hardware')
    mode.add_argument('--_snapshot', metavar='DEVICE_ID', help=argparse.SUPPRESS)
    mode.add_argument('--_target', metavar='STATE_PATH', help=argparse.SUPPRESS)
    parser.add_argument('--gesture-timeout', type=int, default=90)
    args = parser.parse_args()
    if args._snapshot is not None:
        child_snapshot(args._snapshot)
        return 0
    if args._target is not None:
        return child_target(args._target)
    if args.self_check:
        assert_inert(inert_config())
        print(json.dumps({'ok': True, 'mode': 'self-check', 'action_count': 8,
            'all_argv': ['/usr/bin/true'], 'ring_control': RING_CID,
            'button_bindings': 0, 'haptics': False, 'hardware_access': False}))
        return 0
    if args.run:
        if not 15 <= args.gesture_timeout <= 120:
            parser.error('--gesture-timeout must be between 15 and 120 seconds')
        return run_live(args.gesture_timeout)
    print(json.dumps({'mode': 'plan-only', 'hardware_access': False,
        'run_requires_root_review': True, 'self_check': '--self-check',
        'live_mode': '--run --gesture-timeout 90',
        'task4_contract': 'device.stable_id; EventSession.restored; stopped.restored; Ownership.path/read'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
