"""Optional finite Wayland input at the current cursor; never warp or infer a target."""
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import time


def validate_pointer(step):
    operation = step.get('operation')
    keys = {'click': {'type', 'operation', 'button', 'count'},
            'move': {'type', 'operation', 'dx', 'dy'},
            'scroll': {'type', 'operation', 'horizontal', 'vertical'}}
    if not isinstance(operation, str) or operation not in keys or set(step) != keys[operation]:
        raise RuntimeError('Pointer steps need a finite click, move or scroll operation with its exact fields.')
    if operation == 'click':
        if step['button'] not in ('left', 'right', 'middle') or type(step['count']) is not int or step['count'] not in (1, 2):
            raise RuntimeError('Pointer clicks need left, right or middle and count 1 or 2.')
    else:
        fields, bound = (('dx', 'dy'), 4096) if operation == 'move' else (('horizontal', 'vertical'), 20)
        values = [step[field] for field in fields]
        if any(type(value) is not int or not -bound <= value <= bound for value in values) or not any(values):
            raise RuntimeError(f'Pointer {operation} needs nonzero bounded integer values (±{bound}).')


def native_pointer():
    sibling = Path(sys.executable).parent / 'mx4-pointer'
    return str(sibling) if sibling.is_file() and os.access(sibling, os.X_OK) else shutil.which('mx4-pointer')


def _messages(process, check, timeout):
    """Read bounded NDJSON without buffered readline or unbounded child output."""
    end, pending, total = time.monotonic() + timeout, bytearray(), 0
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            check()
            if time.monotonic() >= end:
                raise RuntimeError('Pointer helper exceeded its response deadline; cleanup is required.')
            if not selector.select(0.02):
                continue
            chunk = os.read(process.stdout.fileno(), 1024)
            if not chunk:
                if pending:
                    raise RuntimeError('Pointer helper returned a truncated response.')
                return
            total += len(chunk)
            pending.extend(chunk)
            if total > 8192 or len(pending) > 2048:
                raise RuntimeError('Pointer helper response exceeded its limit.')
            while b'\n' in pending:
                raw, _, pending = pending.partition(b'\n')
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise RuntimeError('Pointer helper returned an invalid response.') from None
                if not isinstance(value, dict):
                    raise RuntimeError('Pointer helper returned an invalid response.')
                yield value


def pointer_availability():
    helper = native_pointer()
    if not helper:
        return {'available': False, 'reason': 'Build the optional mx4-pointer helper with scripts/setup-pointer.sh.'}
    process = None
    try:
        process = subprocess.Popen([helper, '--check'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
        result = list(_messages(process, lambda: None, 2))
        code = process.wait(timeout=1)
        if (code == 0 and len(result) == 1 and result[0].get('available') is True
                and result[0].get('protocol_version') in (1, 2)):
            return {**result[0], 'reason': 'Current cursor only; compositor sync does not confirm application delivery.'}
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        pass
    finally:
        if process:
            # --check cannot create a virtual pointer or press a button.
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
    return {'available': False, 'reason': 'A compatible Wayland virtual-pointer compositor is unavailable or denied.'}


def pointer_operation(step, check):
    validate_pointer(step)
    check()
    helper = native_pointer()
    if not helper:
        raise RuntimeError('Current-cursor input requires the optional mx4-pointer helper (scripts/setup-pointer.sh).')
    operation = step['operation']
    args = ([step['button'], str(step['count'])] if operation == 'click' else
            [str(step[key]) for key in (('dx', 'dy') if operation == 'move' else ('horizontal', 'vertical'))])
    process = None
    success = False
    try:
        process = subprocess.Popen([helper, '--run', operation, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
        ready, result = False, None
        for message in _messages(process, check, 3):
            if message.get('event') == 'ready' and not ready:
                check()
                process.stdin.write(b'go\n')
                process.stdin.flush()
                ready = True
            elif message.get('event') == 'result' and ready and result is None:
                result = message
            else:
                raise RuntimeError('Pointer helper returned an unexpected response; input may be partial.')
        check()
        code = process.wait(timeout=1)
        success = (code == 0 and result is not None and result.get('ok') is True
                   and result.get('released') is True and result.get('compositor_sync') is True)
        if not success:
            raise RuntimeError('Current-cursor input was not confirmed; input may be partial or release unconfirmed.')
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('Current-cursor helper failed; input may be partial or release unconfirmed.') from None
    finally:
        if process:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()  # EOF is cancellation; the helper releases its own pressed buttons.
            if process.poll() is None:
                process.terminate()  # The finite helper handles TERM through release-first cleanup.
            # Never use timeout/SIGKILL while a virtual button might be down.
            # The project helper bounds every poll/sync and has no descendants.
            process.wait()
            process.stdout.close()
