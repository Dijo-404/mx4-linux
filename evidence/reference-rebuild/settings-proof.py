#!/usr/bin/env python3
"""Temporary reversible hardware proof. Default and --self-check perform no I/O.

Only --run opens HID devices. Never switches hosts, plays haptics, or diverts
reporting. Production Backend.write is used with remember=False throughout.
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
import signal
import subprocess
import sys
import tempfile
import threading

CONTROLS = (82, 83, 86, 195, 196, 416)
SIMPLE = ('dpi', 'haptic-level', 'force-sensing', 'hires-smooth-invert',
          'hires-smooth-resolution', 'thumb-scroll-invert')
WHEEL = ('smart-shift', 'scroll-ratchet', 'scroll-ratchet-torque')
ALL_NAMES = frozenset((*SIMPLE, *WHEEL, 'reprogrammable-keys', 'haptic-play'))
INTERRUPTED = threading.Event()


def equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(equal(a, b) for a, b in zip(left, right))
    return left == right


def descriptors(device):
    from mx4_linux.device_worker import _validate_value
    items = device.get('settings')
    assert isinstance(items, list) and len(items) == len(ALL_NAMES), 'Incomplete descriptors'
    result = {item['name']: item for item in items}
    assert set(result) == ALL_NAMES, 'Incomplete descriptors'
    for name, item in result.items():
        if name == 'haptic-play':
            continue
        if item['kind'] == 'map_choice':
            assert isinstance(item['value'], dict) and item['value']
            for key, value in item['value'].items():
                assert isinstance(key, str) and key.isdecimal()
                _validate_value(item, value, int(key))
        else:
            _validate_value(item, item['value'], None)
    return result


def nearby(item):
    """Derive a supported alternative; never infer pressure units or presets."""
    from mx4_linux.device_worker import _validate_value
    original = item['value']
    if item['kind'] == 'toggle':
        assert type(original) is bool
        candidate = not original
    elif item['name'] == 'force-sensing':
        assert item['kind'] == 'range'
        choices = [item[key] for key in ('minimum', 'default', 'maximum')]
        assert all(type(value) is int for value in choices)
        candidate = min((value for value in choices if value != original),
                        key=lambda value: (abs(value - original), value), default=None)
    elif item['kind'] == 'choice':
        candidate = min((choice['value'] for choice in item['choices'] if choice['value'] != original),
                        key=lambda value: (abs(value - original), value), default=None)
    elif item['kind'] == 'range':
        assert type(original) is int
        candidate = original + 1 if original < item['maximum'] else original - 1
        if not item['minimum'] <= candidate <= item['maximum']:
            candidate = None
    else:
        raise AssertionError('Unsupported descriptor')
    if candidate is not None:
        _validate_value(item, candidate, None)
        assert not equal(candidate, original)
    return candidate


def wheel_threshold(raw, settings):
    """Only thresholds round-trippable through the reviewed public setter."""
    assert isinstance(raw, list) and len(raw) == 3
    assert all(type(value) is int and 0 <= value <= 255 for value in raw)
    assert raw[0] in (1, 2)
    assert raw[0] == settings['scroll-ratchet']['value']
    assert raw[2] == settings['scroll-ratchet-torque']['value']
    assert {row['value'] for row in settings['scroll-ratchet']['choices']} == {1, 2}
    value = 50 if raw[1] == 255 else raw[1] if 1 <= raw[1] <= 49 else None
    if value is None:
        return None
    from mx4_linux.device_worker import _validate_value
    _validate_value(settings['smart-shift'], value, None)
    assert settings['smart-shift']['value'] == (value if raw[0] == 2 else 1)
    return value


def plan(settings, raw):
    result = []
    for name in SIMPLE:
        alternative = nearby(settings[name])
        result.append({'name': name, 'value': alternative, 'skip': alternative is None})
    threshold = wheel_threshold(raw, settings)
    for name in WHEEL:
        item = deepcopy(settings[name])
        if name == 'smart-shift' and threshold is not None:
            item['value'] = threshold
        result.append({'name': name, 'value': nearby(item) if threshold is not None else None,
                       'skip': threshold is None, 'wheel': True})
    item = settings['reprogrammable-keys']
    key = next((key for key in item['keys'] if key['value'] == 83), None)
    original = item['value'].get('83')
    allowed = {row['value'] for row in key['choices']} if key else set()
    alternative = next((value for value in (86, 82, 195, 196, 416)
                        if value in allowed and value != original), None)
    result.append({'name': 'reprogrammable-keys', 'key': 83,
                   'value': alternative, 'skip': alternative is None})
    return result


def child_snapshot(request):
    """GET-only child IPC, never persisted or printed by the parent."""
    from mx4_linux.device_worker import _device_id, _devices, _isolated_solaar, _load_solaar
    from mx4_linux.event_worker import _reporting
    from mx4_linux.reporting_state import require_identity
    api = _load_solaar()
    api.hidpp20 = importlib.import_module('logitech_receiver.hidpp20')
    with contextlib.redirect_stdout(sys.stderr), _isolated_solaar(api, software_id=0x0C):
        diagnostics = []
        selected = None
        with contextlib.closing(_devices(api, diagnostics, request['device_id'])) as devices:
            for dev, path in devices:
                if _device_id(path, dev.number) != request['device_id']:
                    continue
                assert selected is None and dev.online
                identity = require_identity(dev, request['expected_stable_id'])
                records = {}
                for cid in CONTROLS:
                    key = next((key for key in dev.keys if int(key._cid) == cid), None)
                    assert key is not None
                    flags, mapping = _reporting(dev, api, key)
                    records[str(cid)] = {'flags': flags, 'mapping': mapping}
                feature = api.hidpp20_constants.SupportedFeature.SMART_SHIFT_ENHANCED
                reply = dev.feature_request(feature, 0x10)
                assert isinstance(reply, bytes) and len(reply) >= 3
                require_identity(dev, identity)
                selected = {'stable_id': identity, 'controls': records, 'wheel': list(reply[:3])}
        assert selected is not None and not diagnostics
    print(json.dumps(selected, separators=(',', ':')))


def worker_json(argv, payload):
    """Never SIGKILL a potentially mutating worker on a watchdog timeout.

    Each ordinary operation has a 90s watchdog. If exceeded, retain the parent
    and its configuration lock, wait for the child, then fail into restoration.
    A permanently stuck child therefore requires root intervention; no later
    restore can race it. Child diagnostics and raw IPC are never logged.
    """
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    timed_out = False
    document = json.dumps(payload)
    while True:
        try:
            output, _ = process.communicate(document, timeout=90 if not timed_out else 30)
            break
        except subprocess.TimeoutExpired:
            document = None
            if not timed_out:
                print(json.dumps({'stage': 'worker-watchdog', 'uncertain': True,
                                  'retaining_configuration_lock': True}), flush=True)
            timed_out = True
    if timed_out:
        raise TimeoutError('Worker watchdog expired; child has now exited')
    assert process.returncode == 0, 'Worker failed'
    result = json.loads(output)
    assert isinstance(result, dict), 'Invalid worker result'
    return result


def private_digest(directory):
    """Hash existing app config/desired files without persisting their contents."""
    digest = hashlib.sha256()
    if directory.exists():
        assert directory.is_dir() and not directory.is_symlink()
        for path in sorted(directory.rglob('*')):
            if not path.is_file() or path.name.endswith('.lock'):
                continue
            assert not path.is_symlink() and path.stat().st_size <= 2 * 1024 * 1024
            digest.update(str(path.relative_to(directory)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def cleanup_case(record, save, restore):
    """Evidence failures must never bypass the already-required restoration."""
    save_failures = []

    def checkpoint():
        try:
            save()
        except BaseException as exc:
            save_failures.append({'stage': record['stage'], 'failure': type(exc).__name__})

    record['stage'] = 'restoring'
    checkpoint()
    try:
        restore()
        record.update(stage='originals-confirmed', restored=True)
    except BaseException as exc:
        record.update(stage='restoration-uncertain', restored=False,
                      restoration_failure=type(exc).__name__)
    if save_failures:
        record.setdefault('evidence_failures', []).extend(save_failures)
    # Attempt the cleanup result even when the preceding marker could not save.
    before_final = len(save_failures)
    checkpoint()
    if len(save_failures) > before_final:
        record.setdefault('evidence_failures', []).extend(save_failures[before_final:])
    if record['restored'] is not True:
        raise RuntimeError('Restoration not confirmed') from None
    if save_failures:
        raise OSError('Evidence could not be saved; originals were restored') from None


def run_live():
    from mx4_linux.backend import Backend
    from mx4_linux.reporting_state import OWNED_BITS, Ownership, atomic_json, configuration_lock, valid_identity

    class ProofBackend(Backend):
        def request(self, payload):
            result = worker_json([sys.executable, '-m', 'mx4_linux.device_worker'], payload)
            assert result.get('ok') is True, 'Production worker rejected request'
            return result

    backend = ProofBackend()
    directory = Path(tempfile.mkdtemp(prefix='mx4-settings-proof-'))
    os.chmod(directory, 0o700)
    evidence = directory / 'recovery.json'
    record = {'version': 1, 'stage': 'preflight', 'case': None,
              'attempted': False, 'restored': None, 'passed': False, 'results': []}
    def save():
        atomic_json(evidence, record)
    save()
    print(json.dumps({'stage': 'preflight', 'recovery_file': str(evidence)}), flush=True)
    signals = {number: signal.signal(number, lambda *_: INTERRUPTED.set())
               for number in (signal.SIGINT, signal.SIGTERM)}
    config_dir = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'mx4-linux'
    remembered = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'mx4-linux/remembered.json'
    original_config = private_digest(config_dir)
    original_desired = remembered.read_bytes() if remembered.exists() else None
    identity = None
    baseline = settings = None
    failure = None

    def fresh():
        scan = backend.scan()
        assert scan.get('diagnostics') == []
        online = [device for device in scan.get('devices', []) if device.get('online') is True]
        assert len(online) == 1, 'Need exactly one online MX4'
        device = online[0]
        assert valid_identity(device.get('stable_id')) and not device['stable_id'].startswith('demo:')
        assert identity is None or device['stable_id'] == identity, 'Unit changed'
        descriptors(device)
        return device

    def snapshot(device):
        result = worker_json([sys.executable, str(Path(__file__).resolve()), '--run', '--_snapshot'],
            {'device_id': device['id'], 'expected_stable_id': identity})
        assert result.get('stable_id') == identity
        assert set(result['controls']) == {str(cid) for cid in CONTROLS}
        return {key: result[key] for key in ('controls', 'wheel')}

    def clean_owner():
        # Release before calling production mapping writer, which needs this lock.
        with Ownership(identity) as owner:
            assert owner.read() is None, 'Active/orphan reporting journal'

    def current_pair():
        clean_owner()
        device = fresh()
        return descriptors(device), snapshot(device)

    def write(name, value, key=None, *, restoring=False):
        if INTERRUPTED.is_set() and not restoring:
            raise InterruptedError('Interrupted before next write')
        device = fresh()
        clean_owner()
        item = descriptors(device)[name]
        from mx4_linux.device_worker import _validate_value
        _validate_value(item, value, key)
        result = backend.write(device['id'], name, value, key=key,
                               expected_stable_id=identity, remember=False)
        assert result.get('refresh_required') is not True
        assert equal(result.get('value'), value), 'Write acknowledgment/readback mismatch'
        after, observed = current_pair()
        actual = after[name]['value'] if key is None else after[name]['value'].get(str(key))
        assert equal(actual, value), 'Fresh descriptor did not confirm write'
        assert all(observed['controls'][str(cid)]['flags'] == baseline['controls'][str(cid)]['flags'] for cid in CONTROLS)
        for cid in CONTROLS:
            mapped = value if name == 'reprogrammable-keys' and cid == key else baseline['controls'][str(cid)]['mapping']
            assert observed['controls'][str(cid)]['mapping'] == mapped, 'Unexpected native mapping change'
        return after, observed

    def restore(case):
        name = case['name']
        if case.get('wheel'):
            threshold = wheel_threshold(baseline['wheel'], settings)
            assert threshold is not None
            # A free-spin descriptor hides threshold: expose it during restore.
            write('scroll-ratchet', 2, restoring=True)
            write('smart-shift', threshold, restoring=True)
            write('scroll-ratchet-torque', settings['scroll-ratchet-torque']['value'], restoring=True)
            write('scroll-ratchet', settings['scroll-ratchet']['value'], restoring=True)
        elif case.get('key') is not None:
            write(name, settings[name]['value'][str(case['key'])], case['key'], restoring=True)
        else:
            write(name, settings[name]['value'], restoring=True)
        after, observed = current_pair()
        assert equal(observed, baseline), 'Reporting/wheel originals not restored'
        assert all(equal(after[key]['value'], settings[key]['value']) for key in ALL_NAMES if key != 'haptic-play'), 'Settings originals not restored'

    try:
        device = fresh()
        identity = device['stable_id']
        with configuration_lock(identity):
            clean_owner()
            device = fresh()
            settings = descriptors(device)
            baseline = snapshot(device)
            assert all(not row['flags'] & OWNED_BITS for row in baseline['controls'].values()), 'Preexisting diverted reporting'
            cases = plan(settings, baseline['wheel'])
            # Durable recovery evidence has only capability/value data, no unit ID,
            # device path, host names, labels, window titles or user configuration.
            record.update(unit_digest=hashlib.sha256(identity.encode()).hexdigest(),
                          original_values={name: settings[name]['value'] for name in ALL_NAMES if name != 'haptic-play'},
                          original_descriptors=[{key: value for key, value in item.items() if key not in ('label', 'description')}
                                                for item in settings.values()], baseline=baseline,
                          planned_cases=cases, stage='validated')
            save()
            for case in cases:
                if case['skip']:
                    record['results'].append({'setting': case['name'], 'status': 'skipped-no-roundtrippable-alternative'})
                    save()
                    continue
                if INTERRUPTED.is_set():
                    raise InterruptedError('Interrupted between restored cases')
                before_settings, before = current_pair()
                assert equal(before, baseline)
                assert all(equal(before_settings[key]['value'], settings[key]['value']) for key in ALL_NAMES if key != 'haptic-play')
                record.update(case=case, stage='write-started', attempted=True, restored=False)
                save()  # Persist originals and intent before any setter.
                case_failure = None
                try:
                    expected = deepcopy(baseline)
                    if case['name'] == 'smart-shift' and baseline['wheel'][0] == 1:
                        _, setup = write('scroll-ratchet', 2)
                        expected['wheel'][0] = 2
                        assert equal(setup, expected)
                    _, changed = write(case['name'], case['value'], case.get('key'))
                    if case.get('wheel'):
                        index = WHEEL.index(case['name'])
                        index = (1, 0, 2)[index]
                        expected['wheel'][index] = 255 if index == 1 and case['value'] == 50 else case['value']
                    elif case.get('key'):
                        expected['controls'][str(case['key'])]['mapping'] = case['value']
                    assert equal(changed, expected), 'Unexpected coupled/reporting change'
                    record['stage'] = 'alternative-confirmed'
                    save()
                except BaseException as exc:
                    case_failure = type(exc).__name__
                finally:
                    cleanup_case(record, save, lambda: restore(case))
                record['results'].append({'setting': case['name'], 'status': 'passed' if case_failure is None else 'failed-restored',
                                          'failure': case_failure})
                save()
                print(json.dumps(record['results'][-1]), flush=True)
                if case_failure:
                    raise RuntimeError('Test failed after confirmed restoration')
            after_settings, after = current_pair()
            assert equal(after, baseline)
            assert all(equal(after_settings[key]['value'], settings[key]['value']) for key in ALL_NAMES if key != 'haptic-play')
            assert private_digest(config_dir) == original_config, 'App config changed concurrently'
            assert (remembered.read_bytes() if remembered.exists() else None) == original_desired, 'Desired settings changed concurrently'
            record.update(stage='complete', case=None, restored=True, passed=True,
                          configuration_unchanged=True, desired_unchanged=True,
                          all_six_reporting_and_mappings_restored=True)
            save()
    except BaseException as exc:
        failure = type(exc).__name__
        record.update(failure=failure, passed=False)
        try:
            save()
        except BaseException as save_exc:
            record.setdefault('evidence_failures', []).append(
                {'stage': 'failure-result', 'failure': type(save_exc).__name__})
    finally:
        for number, handler in signals.items():
            signal.signal(number, handler)
    print(json.dumps({'stage': record['stage'], 'passed': record['passed'],
        'restored': record['restored'], 'case_count': len(record['results']),
        'recovery_file': str(evidence), 'failure': failure,
        'evidence_failures': record.get('evidence_failures', [])}), flush=True)
    return int(failure is not None)


def self_check():
    from mx4_linux.backend import DemoBackend
    device = DemoBackend().device
    settings = descriptors(device)
    assert nearby(settings['haptic-level']) == 51
    assert nearby(settings['force-sensing']) == 5644
    assert nearby(settings['hires-smooth-invert']) is True
    assert nearby(settings['dpi']) == 800
    cases = plan(settings, [2, 20, 50])
    assert len(cases) == 10 and not any(case['skip'] for case in cases)
    assert next(case for case in cases if case.get('key')) == {
        'name': 'reprogrammable-keys', 'key': 83, 'value': 86, 'skip': False}
    hidden = deepcopy(settings)
    hidden['smart-shift']['value'] = 1
    hidden['scroll-ratchet']['value'] = 1
    assert wheel_threshold([1, 37, 50], hidden) == 37
    assert next(case for case in plan(hidden, [1, 37, 50]) if case['name'] == 'smart-shift')['value'] == 38
    assert sum(case['skip'] for case in plan(hidden, [1, 0, 50])) == 3
    maximum = deepcopy(settings)
    maximum['smart-shift']['value'] = 50
    assert wheel_threshold([2, 255, 50], maximum) == 50
    assert nearby(maximum['smart-shift']) == 49
    fixed = {'name': 'haptic-level', 'kind': 'range', 'minimum': 9, 'maximum': 9, 'value': 9}
    assert nearby(fixed) is None
    assert not equal(True, 1) and not equal({'x': False}, {'x': 0})
    assert equal({'x': [1, False]}, {'x': [1, False]})
    bad = deepcopy(settings['force-sensing'])
    del bad['default']
    try:
        nearby(bad)
    except KeyError:
        pass
    else:
        raise AssertionError('Guessed a missing pressure default')
    # The inert child exercises private IPC and production watchdog without HID.
    result = worker_json([sys.executable, '-c', 'import json,sys; print(json.dumps(json.load(sys.stdin)))'], {'only': 'inert'})
    assert result == {'only': 'inert'}
    # A durable pre-write record already exists; inject disk failures only after
    # mutation, both before restoration and while recording its confirmed result.
    for fail_calls in ({2}, {3}, {2, 3}):
        state = {'value': 'original', 'restores': 0, 'saves': 0}
        record = {'stage': 'write-started', 'restored': False, 'passed': False}
        persisted = []
        def injected_save():
            state['saves'] += 1
            if state['saves'] in fail_calls:
                raise OSError('Injected evidence failure')
            persisted.append(deepcopy(record))
        def injected_restore():
            state['restores'] += 1
            state['value'] = 'original'
        injected_save()  # Durable baseline/intent precedes the inert mutation.
        state['value'] = 'temporary'
        try:
            cleanup_case(record, injected_save, injected_restore)
        except OSError:
            pass
        else:
            raise AssertionError('Evidence failure did not stop the case')
        assert state == {'value': 'original', 'restores': 1, 'saves': 3}
        assert record['restored'] is True and record['passed'] is False
        assert len(record['evidence_failures']) == len(fail_calls)
        assert persisted[0]['stage'] == 'write-started'
    print(json.dumps({'self_check': 'passed', 'hardware_io': False,
                      'planned_persistent_cases': 10, 'reporting_setters': 0,
                      'post_mutation_save_failure_cases': 3}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--self-check', action='store_true')
    parser.add_argument('--_snapshot', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._snapshot:
        if not args.run:
            parser.error('Internal hardware snapshot also requires --run')
        child_snapshot(json.load(sys.stdin))
        return 0
    if args.self_check:
        self_check()
        return 0
    if args.run:
        return run_live()
    print(json.dumps({'mode': 'plan-only', 'hardware_io': False,
        'requires_explicit_run': True, 'cases': [*SIMPLE, *WHEEL, 'reprogrammable-keys:83'],
        'no_host_switch': True, 'no_haptic_pulse': True, 'no_diversion': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
