#!/usr/bin/env python3
"""Temporary Task4a EventSession proof; default/--plan/--self-check have no HID I/O.

Root must review after Task4a spec and quality gates. Live mode additionally
requires --reviewed-commit matching HEAD and unchanged relevant app source.
No GUI, action execution, haptics, input synthesis or host switching is used.
"""
from __future__ import annotations

import argparse
import contextlib
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
from time import monotonic

CONTROLS = (82, 83, 86, 195, 196, 416)
TRUE_STEP = {'type': 'command', 'argv': ['/usr/bin/true']}
SEQUENCE = ((None, ()), (416, ()), (83, ()), (None, (416,)), (None, ()))
INTERRUPTED = threading.Event()


def require_assertions_enabled(optimize=None, environment=None):
    optimize = sys.flags.optimize if optimize is None else optimize
    environment = os.environ if environment is None else environment
    if optimize != 0 or 'PYTHONOPTIMIZE' in environment:
        raise RuntimeError('Live proof and self-check require assertions: omit -O and unset PYTHONOPTIMIZE')


def retention_notice(document):
    """A closed diagnostic pipe must never abandon a live reporting worker."""
    try:
        print(json.dumps(document), flush=True)
        return True
    except BaseException:
        return False


def inert_config(trigger, ordinary):
    actions = {f'noop-{index}': {
        'label': f'No operation {index + 1}', 'steps': [deepcopy(TRUE_STEP)],
        'adjustment': {'increase': [deepcopy(TRUE_STEP)], 'decrease': [deepcopy(TRUE_STEP)]},
    } for index in range(8)}
    return {'version': 1, 'ring': list(actions), 'actions': actions,
            'ring_button': trigger, 'button_bindings': {str(cid): 'noop-0' for cid in ordinary},
            'haptic_feedback': False, 'resume_integration': False, 'autostart': False,
            'background': False, 'adapt_to_app': False}


def assert_inert(config, trigger, ordinary):
    from mx4_linux.actions import validate_config
    validate_config(config)
    assert config == inert_config(trigger, ordinary)
    for action in config['actions'].values():
        assert action['steps'] == [TRUE_STEP]
        assert action['adjustment'] == {'increase': [TRUE_STEP], 'decrease': [TRUE_STEP]}
    return config


def reporting_request(config):
    from mx4_linux.event_session import _reporting_request
    return _reporting_request(config)


def exact_ack(event, kind, session_id, request, config_id=None):
    if event.get('type') != kind or type(event.get('session_id')) is not int or event['session_id'] != session_id:
        return False
    if kind == 'configured' and (type(event.get('config_id')) is not int or event['config_id'] != config_id):
        return False
    return (type(event.get('raw_xy')) is bool and event['raw_xy'] is request['raw_xy']
            and type(event.get('ring_button')) is type(request['ring_button'])
            and event.get('ring_button') == request['ring_button']
            and isinstance(event.get('controls'), list)
            and all(type(cid) is int for cid in event['controls'])
            and event['controls'] == request['controls'])


def desired_snapshot(baseline, request):
    result = deepcopy(baseline)
    for cid in request['controls']:
        result[str(cid)]['flags'] |= 1
        if request['raw_xy'] and cid == request['ring_button']:
            result[str(cid)]['flags'] |= 16
    return result


class Protocol:
    """Bounded callback queue; never retain diagnostic strings or user input."""
    def __init__(self):
        self.events = queue.Queue(maxsize=512)
        self.overflow = threading.Event()
        self.counts = {kind: 0 for kind in ('ready', 'configured', 'stopped', 'button', 'motion', 'error', 'warning', 'other')}
        self.lock = threading.Lock()

    def __call__(self, source):
        kind = source.get('type')
        if kind not in self.counts:
            kind = 'other'
        # Main protocol only: no message, titles, path, stable ID, coordinates or
        # action execution is copied. Button/motion observations are counts only.
        event = {'type': kind}
        if kind in ('ready', 'configured', 'stopped'):
            for key in ('session_id', 'config_id', 'controls', 'ring_button', 'raw_xy', 'restored', 'exit_code'):
                if key in source:
                    event[key] = deepcopy(source[key])
        with self.lock:
            self.counts[kind] += 1
        try:
            self.events.put_nowait(event)
        except queue.Full:
            self.overflow.set()

    def next(self, seconds):
        try:
            return self.events.get(timeout=min(.1, max(0, seconds)))
        except queue.Empty:
            return None


def child_snapshot(request):
    """Addressed GETs, software ID C distinct from production listener ID D."""
    require_assertions_enabled()
    from mx4_linux.device_worker import _device_id, _devices, _isolated_solaar, _load_solaar
    from mx4_linux.event_worker import _reporting
    from mx4_linux.reporting_state import require_identity
    api = _load_solaar()
    api.hidpp20 = importlib.import_module('logitech_receiver.hidpp20')
    assert hasattr(api.base, 'SOLAAR_SOFTWARE_ID'), 'Distinct software IDs unavailable'
    selected = None
    with contextlib.redirect_stdout(sys.stderr), _isolated_solaar(api, software_id=0x0C):
        assert api.base.SOLAAR_SOFTWARE_ID == 0x0C
        diagnostics = []
        with contextlib.closing(_devices(api, diagnostics, request['device_id'])) as devices:
            for dev, path in devices:
                if _device_id(path, dev.number) != request['device_id']:
                    continue
                assert selected is None and dev.online
                identity = require_identity(dev, request['expected_stable_id'])
                controls, capabilities = {}, {}
                for cid in CONTROLS:
                    key = next((key for key in dev.keys if int(key._cid) == cid), None)
                    assert key is not None
                    flags, mapping = _reporting(dev, api, key)
                    controls[str(cid)] = {'flags': flags, 'mapping': mapping}
                    capabilities[str(cid)] = {
                        'divertable': api.hidpp20.KeyFlag.DIVERTABLE in key.flags,
                        'raw_xy': api.hidpp20.KeyFlag.RAW_XY in key.flags}
                require_identity(dev, identity)
                selected = {'stable_id': identity, 'controls': controls,
                            'capabilities': capabilities, 'software_id': 0x0C}
        assert selected is not None and not diagnostics
    print(json.dumps(selected, separators=(',', ':')))


def child_json(argv, payload, timeout=30):
    # This helper is GET-only. A read timeout may terminate its read child; the
    # reporting EventSession is never passed to subprocess.run or killed here.
    result = subprocess.run(argv, input=json.dumps(payload), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout,
        check=True, start_new_session=True)
    result = json.loads(result.stdout)
    assert isinstance(result, dict)
    return result


def app_config_digest():
    config = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'mx4-linux'
    desired = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'mx4-linux/remembered.json'
    digest = hashlib.sha256()
    for root in (config, desired):
        paths = sorted(root.rglob('*')) if root.is_dir() else [root]
        for path in paths:
            assert not path.is_symlink()
            if not path.is_file() or path.name.endswith('.lock'):
                continue
            assert path.stat().st_size <= 2 * 1024 * 1024
            digest.update(str(path.relative_to(root) if path != root else '.').encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def cleanup(record, save, restore):
    """Evidence failures cannot prevent stop/reap/restoration verification."""
    save_failures = []
    def checkpoint():
        try:
            save()
        except BaseException as exc:
            save_failures.append({'stage': record['stage'], 'failure': type(exc).__name__})
    record['stage'] = 'stopping'
    checkpoint()
    try:
        restore()
        record.update(stage='originals-confirmed', restored=True)
    except BaseException as exc:
        record.update(stage='restoration-uncertain', restored=False,
                      restoration_failure=type(exc).__name__)
    if save_failures:
        record.setdefault('evidence_failures', []).extend(save_failures)
    previous = len(save_failures)
    checkpoint()
    if len(save_failures) > previous:
        record.setdefault('evidence_failures', []).extend(save_failures[previous:])
    if record['restored'] is not True:
        raise RuntimeError('Restoration not confirmed') from None
    if save_failures:
        raise OSError('Evidence failed after cleanup') from None


def check_reviewed_source(commit):
    assert re.fullmatch('[0-9a-f]{40}', commit or ''), 'A full reviewed commit is required'
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3).stdout.strip()
    assert commit == head, 'HEAD differs from reviewed commit'
    subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--',
        'src/mx4_linux/actions.py', 'src/mx4_linux/backend.py',
        'src/mx4_linux/device_worker.py', 'src/mx4_linux/event_session.py',
        'src/mx4_linux/event_worker.py', 'src/mx4_linux/reporting_state.py'],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)


def run_live(args):
    require_assertions_enabled()  # Before source checks, imports, or hardware I/O.
    check_reviewed_source(args.reviewed_commit)
    from mx4_linux.event_session import EventSession
    from mx4_linux.reporting_state import OWNED_BITS, Ownership, atomic_json, configuration_lock, valid_identity
    directory = Path(tempfile.mkdtemp(prefix='mx4-session-transition-proof-'))
    os.chmod(directory, 0o700)
    path = directory / 'recovery.json'
    record = {'version': 1, 'reviewed_commit': args.reviewed_commit, 'stage': 'preflight',
              'attempted': False, 'restored': None, 'passed': False, 'transitions': []}
    def save():
        atomic_json(path, record)
    save()
    print(json.dumps({'stage': 'preflight', 'recovery_file': str(path)}), flush=True)
    handlers = {number: signal.signal(number, lambda *_: INTERRUPTED.set())
                for number in (signal.SIGINT, signal.SIGTERM)}
    protocol = Protocol()
    session = EventSession(protocol)
    baseline = identity = device_id = capabilities = initial_digest = None
    stopped = []
    failure = None

    def snapshot():
        result = child_json([sys.executable, str(Path(__file__).resolve()), '--run', '--_snapshot'],
                            {'device_id': device_id, 'expected_stable_id': identity})
        assert result['stable_id'] == identity and result['software_id'] == 0x0C
        assert set(result['controls']) == {str(cid) for cid in CONTROLS}
        if capabilities is not None:
            assert result['capabilities'] == capabilities
        return result

    def wait_ack(kind, request, config_id=None):
        deadline = monotonic() + args.ack_timeout
        while monotonic() < deadline:
            if INTERRUPTED.is_set():
                raise InterruptedError('Interrupted')
            if protocol.overflow.is_set():
                raise RuntimeError('Protocol queue overflow')
            event = protocol.next(deadline - monotonic())
            if not event:
                continue
            if event['type'] in ('error', 'warning', 'stopped'):
                if event['type'] == 'stopped':
                    stopped.append(event)
                raise RuntimeError('Worker stopped or diagnosed before expected ACK')
            if event['type'] in ('ready', 'configured'):
                assert exact_ack(event, kind, session.session_id, request, config_id), 'Mismatched/stale ACK'
                return event
        raise TimeoutError('Held controls or transport prevented matching ACK')

    def stop_and_verify():
        # Always stop before any final evidence I/O or independent GET. A failed
        # save checkpoint in cleanup cannot skip this call.
        session.stop()
        deadline = monotonic() + args.cleanup_timeout
        watchdog = False
        retention_log_failed = False
        if session.process is not None:
            while session.running or not stopped:
                event = protocol.next(.1)
                if event and event['type'] == 'stopped':
                    stopped.append(event)
                if monotonic() >= deadline:
                    if not session.running and not stopped:
                        raise TimeoutError('Exited worker has no final stopped outcome')
                    if session.running and not watchdog:
                        watchdog = True
                        retention_log_failed = not retention_notice(
                            {'stage': 'cleanup-watchdog', 'uncertain': True,
                             'worker_retained': True})
                    # Do not abandon a live child, release its pipe, or SIGKILL.
                    # Its production locks remain held until it actually exits.
            assert session.process.poll() == 0
            assert session.restored is True
            assert len(stopped) == 1 and stopped[0].get('restored') is True
            assert type(stopped[0].get('exit_code')) is int and stopped[0]['exit_code'] == 0
            assert stopped[0].get('session_id') == session.session_id
            record.update(worker_exited=True, stopped_restored=True)
        else:
            record.update(worker_exited=True, stopped_restored=None)
        # The first observed exit/outcome can itself arrive after the deadline,
        # without any earlier live-worker iteration printing the watchdog.
        # Measure elapsed time independently before final verification GETs.
        cleanup_deadline_exceeded = monotonic() >= deadline
        with configuration_lock(identity), Ownership(identity) as owner:
            assert owner.read() is None, 'Reporting journal remains pending'
            after = snapshot()['controls']
            assert after == baseline, 'Exact six-control originals not restored'
        assert app_config_digest() == initial_digest, 'Config/desired settings changed concurrently'
        record.update(all_six_flags_and_mappings_restored=True, journal_clean=True,
                      config_and_desired_unchanged=True,
                      cleanup_deadline_exceeded=cleanup_deadline_exceeded,
                      retention_log_failed=retention_log_failed)

    try:
        configs = [assert_inert(inert_config(trigger, ordinary), trigger, ordinary)
                   for trigger, ordinary in SEQUENCE]
        initial_digest = app_config_digest()
        scan = child_json([sys.executable, '-m', 'mx4_linux.device_worker'], {'op': 'scan'})
        assert scan.get('ok') is True and scan.get('diagnostics') == []
        online = [device for device in scan.get('devices', []) if device.get('online') is True]
        assert len(online) == 1
        device_id, identity = online[0]['id'], online[0]['stable_id']
        assert valid_identity(identity) and not identity.startswith('demo:')
        with configuration_lock(identity), Ownership(identity) as owner:
            assert owner.read() is None
            result = snapshot()
            baseline, capabilities = result['controls'], result['capabilities']
            assert all(not row['flags'] & OWNED_BITS for row in baseline.values())
            assert all(capabilities[str(cid)] == {'divertable': True, 'raw_xy': True} for cid in (83, 416))
            record.update(stage='validated', unit_digest=hashlib.sha256(identity.encode()).hexdigest(),
                          original_controls=baseline, capabilities=capabilities,
                          planned_requests=[reporting_request(config) for config in configs],
                          transition_get_software_id=0x0C, worker_software_id=0x0D)
            save()  # Durable original snapshot before even the idle listener.
        # The production worker acquires these same locks for its whole lifetime.
        # Never hold parent preflight ownership while asking the child to start.
        record.update(stage='start-attempted', attempted=True)
        save()
        try:
            if INTERRUPTED.is_set():
                raise InterruptedError('Interrupted before start')
            session.start(device_id, configs[0], expected_stable_id=identity)
            for index, config in enumerate(configs):
                request = reporting_request(config)
                record.update(stage='awaiting-ack', sequence_index=index, requested=request)
                save()
                if index:
                    config_id = session.configure(config)
                    assert type(config_id) is int and config_id > 0, 'Configure not accepted for sending'
                    ack = wait_ack('configured', request, config_id)
                else:
                    ack = wait_ack('ready', request)
                    assert session.ready
                observed = snapshot()['controls']
                assert observed == desired_snapshot(baseline, request), 'ACK not confirmed by exact live six-control GETs'
                record['transitions'].append({'index': index, 'ack': ack, 'controls': observed})
                record.update(stage='ack-and-live-reporting-confirmed')
                save()
                print(json.dumps({'stage': record['stage'], 'index': index,
                                  'request': request, 'config_id': ack.get('config_id')}), flush=True)
        finally:
            cleanup(record, save, stop_and_verify)
        assert protocol.counts['error'] == protocol.counts['warning'] == 0
        assert not protocol.overflow.is_set()
        assert not record.get('cleanup_deadline_exceeded'), 'Cleanup was confirmed after its expected deadline'
        assert len(record['transitions']) == len(SEQUENCE)
        record.update(stage='complete', passed=True, protocol_counts=protocol.counts)
        save()
    except BaseException as exc:
        failure = type(exc).__name__
        record.update(passed=False, failure=failure, protocol_counts=protocol.counts)
        try:
            save()
        except BaseException as save_exc:
            record.setdefault('evidence_failures', []).append(
                {'stage': 'failure-result', 'failure': type(save_exc).__name__})
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    print(json.dumps({'stage': record['stage'], 'passed': record['passed'],
        'restored': record['restored'], 'transitions_confirmed': len(record['transitions']),
        'recovery_file': str(path), 'failure': failure,
        'evidence_failures': record.get('evidence_failures', [])}), flush=True)
    return int(failure is not None)


def self_check():
    require_assertions_enabled()
    for optimize, environment in ((1, {}), (2, {}), (0, {'PYTHONOPTIMIZE': '1'}),
                                   (0, {'PYTHONOPTIMIZE': '0'})):
        try:
            require_assertions_enabled(optimize, environment)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Optimized/environment-configured execution was accepted')
    require_assertions_enabled(0, {})
    baseline = {str(cid): {'flags': 0x80, 'mapping': cid} for cid in CONTROLS}
    requests = []
    for index, (trigger, ordinary) in enumerate(SEQUENCE):
        config = assert_inert(inert_config(trigger, ordinary), trigger, ordinary)
        request = reporting_request(config)
        requests.append(request)
        observed = desired_snapshot(baseline, request)
        assert all(row['mapping'] == int(cid) for cid, row in observed.items())
        assert all(row['flags'] & 0x80 for row in observed.values())
        event = {'type': 'ready' if index == 0 else 'configured', 'session_id': 7, 'config_id': index, **request}
        assert exact_ack(event, event['type'], 7, request, index)
        assert not exact_ack({**event, 'session_id': 6}, event['type'], 7, request, index)
        assert not exact_ack({**event, 'raw_xy': int(request['raw_xy'])}, event['type'], 7, request, index)
        if index:
            assert not exact_ack({**event, 'config_id': True}, 'configured', 7, request, index)
            assert not exact_ack({**event, 'config_id': index + 1}, 'configured', 7, request, index)
    assert requests == [
        {'controls': [], 'ring_button': None, 'raw_xy': False},
        {'controls': [416], 'ring_button': 416, 'raw_xy': True},
        {'controls': [83], 'ring_button': 83, 'raw_xy': True},
        {'controls': [416], 'ring_button': None, 'raw_xy': False},
        {'controls': [], 'ring_button': None, 'raw_xy': False}]
    assert desired_snapshot(baseline, requests[2])['416'] == baseline['416']
    assert desired_snapshot(baseline, requests[3])['83'] == baseline['83']
    assert desired_snapshot(baseline, requests[3])['416']['flags'] == 0x81
    assert desired_snapshot(baseline, requests[4]) == baseline
    protocol = Protocol()
    protocol({'type': 'error', 'message': 'private', 'stable_id': 'private', 'host_name': 'private'})
    assert protocol.next(0) == {'type': 'error'}
    protocol({'type': 'button', 'control': 416, 'pressed': True, 'session_id': 3})
    assert protocol.next(0) == {'type': 'button'}
    for failure_calls in ({2}, {3}, {2, 3}):
        state = {'saves': 0, 'restores': 0, 'value': 'original'}
        record = {'stage': 'start-attempted', 'restored': False}
        def injected_save():
            state['saves'] += 1
            if state['saves'] in failure_calls:
                raise OSError('Injected evidence failure')
        def injected_restore():
            state['restores'] += 1
            state['value'] = 'original'
        injected_save()  # Durable pre-write intent, then inert mutation.
        state['value'] = 'changed'
        try:
            cleanup(record, injected_save, injected_restore)
        except OSError:
            pass
        else:
            raise AssertionError('Save failure did not fail after cleanup')
        assert state == {'saves': 3, 'restores': 1, 'value': 'original'}
        assert record['restored'] is True and len(record['evidence_failures']) == len(failure_calls)
    assert child_json([sys.executable, '-c', 'import json,sys; print(json.dumps(json.load(sys.stdin)))'], {'inert': True}) == {'inert': True}
    print(json.dumps({'self_check': 'passed', 'hardware_io': False,
                      'sequence_length': 5, 'post_mutation_save_failure_cases': 3}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--self-check', action='store_true')
    modes.add_argument('--run', action='store_true')
    parser.add_argument('--reviewed-commit')
    parser.add_argument('--ack-timeout', type=float, default=20)
    parser.add_argument('--cleanup-timeout', type=float, default=45)
    parser.add_argument('--_snapshot', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.run or args.self_check:
        try:
            require_assertions_enabled()
        except RuntimeError as exc:
            parser.error(str(exc))
    if args._snapshot:
        if not args.run:
            parser.error('Internal snapshot also requires --run')
        child_snapshot(json.load(sys.stdin))
        return 0
    if args.self_check:
        self_check()
        return 0
    if args.run:
        if not re.fullmatch('[0-9a-f]{40}', args.reviewed_commit or ''):
            parser.error('--run requires the full --reviewed-commit after both Task4a gates')
        if not 5 <= args.ack_timeout <= 60 or not 30 <= args.cleanup_timeout <= 90:
            parser.error('Use ACK timeout 5..60s and cleanup timeout 30..90s')
        return run_live(args)
    print(json.dumps({'mode': 'plan-only', 'hardware_io': False,
        'requires_reviewed_run': True, 'sequence': [reporting_request(inert_config(*item)) for item in SEQUENCE],
        'actions_executed': 0, 'haptic_requests': 0, 'host_requests': 0}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
