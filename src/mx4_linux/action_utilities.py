"""Finite date and regular-text clipboard operations, evaluated only on execution."""
from datetime import datetime, timezone
import html
from html.entities import html5
import os
import re
import selectors
import signal
import subprocess
import time
from urllib.parse import quote, unquote

DATE_FORMATS = frozenset({'iso-date', 'iso-time', 'iso-datetime', 'day-of-year', 'iso-week', 'moon-phase'})
CLIPBOARD_OPERATIONS = frozenset({'clear', 'plain', 'lower', 'upper', 'title', 'swapcase', 'sentence-case',
                                  'url-encode', 'url-decode', 'html-encode', 'html-decode'})
CLIPBOARD_BYTES = 262144
CLIPBOARD_CHARACTERS = 65536
CLIPBOARD_MIMES = ('text/plain;charset=utf-8', 'text/plain')


def _now():
    return datetime.now().astimezone()


def date_text(format, now=None):
    now = _now() if now is None else now
    if format == 'iso-date':
        return now.date().isoformat()
    if format == 'iso-time':
        return now.strftime('%H:%M:%S')
    if format == 'iso-datetime':
        return now.isoformat(timespec='seconds')
    if format == 'day-of-year':
        return f'{now.timetuple().tm_yday:03d}'
    if format == 'iso-week':
        return f'{now.isocalendar().week:02d}'
    if format == 'moon-phase':
        # Mean-lunation approximation; not an ephemeris or a prediction of phase times.
        epoch = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
        phase = ((now.astimezone(timezone.utc) - epoch).total_seconds() / (29.530589 * 86400)) % 1
        names = ('New Moon', 'Waxing Crescent', 'First Quarter', 'Waxing Gibbous',
                 'Full Moon', 'Waning Gibbous', 'Last Quarter', 'Waning Crescent')
        return names[int(phase * 8 + 0.5) % 8]
    raise RuntimeError('Unsupported date format.')


def _text_bytes(value):
    try:
        raw = value.encode('utf-8')
    except UnicodeError:
        raise RuntimeError('Clipboard text is not valid UTF-8.') from None
    if '\0' in value or len(value) > CLIPBOARD_CHARACTERS or len(raw) > CLIPBOARD_BYTES:
        raise RuntimeError('Clipboard text contains NUL or exceeds the text limit.')
    return raw


def transform_clipboard(operation, payload):
    if len(payload) > CLIPBOARD_BYTES:
        raise RuntimeError('Clipboard text exceeds the byte limit.')
    try:
        value = payload.decode('utf-8', errors='strict')
    except UnicodeError:
        raise RuntimeError('Clipboard text is not valid UTF-8.') from None
    _text_bytes(value)
    if operation in ('plain', 'lower', 'upper', 'title', 'swapcase'):
        result = value if operation == 'plain' else getattr(value, operation)()
    elif operation == 'sentence-case':
        # Deterministic counterpart: lower-case all text, capitalize after .!? plus whitespace.
        result = re.sub(r'(^|[.!?]\s+)([^\W\d_])', lambda m: m[1] + m[2].upper(), value.lower())
    elif operation == 'url-encode':
        result = quote(value, safe='')
    elif operation == 'url-decode':
        if re.search(r'%(?![0-9a-fA-F]{2})', value):
            raise RuntimeError('Clipboard URL escapes are invalid.')
        try:
            result = unquote(value, encoding='utf-8', errors='strict')
        except UnicodeError:
            raise RuntimeError('Clipboard URL text is not valid UTF-8.') from None
    elif operation == 'html-encode':
        result = html.escape(value, quote=True)
    elif operation == 'html-decode':
        entities = re.compile(r'&(#(?:[0-9]+|[xX][0-9a-fA-F]+)|[A-Za-z][A-Za-z0-9]+);')
        if '&' in entities.sub('', value):
            raise RuntimeError('Clipboard HTML escapes are invalid.')
        def entity_value(match):
            entity = match[1]
            if entity.startswith('#'):
                hexadecimal = entity[1:2].lower() == 'x'
                digits = (entity[2:] if hexadecimal else entity[1:]).lstrip('0') or '0'
                if len(digits) > (6 if hexadecimal else 7):
                    raise RuntimeError('Clipboard HTML character is invalid.')
                number = int(digits, 16 if hexadecimal else 10)
                if number == 0 or number > 0x10ffff or 0xd800 <= number <= 0xdfff:
                    raise RuntimeError('Clipboard HTML character is invalid.')
                return f'&#{number};'
            if entity + ';' not in html5:
                raise RuntimeError('Clipboard HTML escapes are invalid.')
            return match[0]
        result = html.unescape(entities.sub(entity_value, value))
    else:
        raise RuntimeError('Unsupported clipboard operation.')
    return _text_bytes(result)


def clipboard_process(argv, check, *, payload=None, limit=0):
    """Bound pipes, elapsed time and cancellation; never expose helper output in errors."""
    process = None
    selector = selectors.DefaultSelector()
    output = bytearray()
    offset = 0
    completed = False
    finish = time.monotonic() + 2
    try:
        check()
        process = subprocess.Popen(argv, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE if limit else subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, shell=False, start_new_session=True)
        if limit:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
        if payload is not None:
            os.set_blocking(process.stdin.fileno(), False)
            if payload:
                selector.register(process.stdin, selectors.EVENT_WRITE)
            else:
                process.stdin.close()
        while selector.get_map() or process.poll() is None:
            check()
            if time.monotonic() >= finish:
                raise RuntimeError('Clipboard helper timed out.')
            for key, _events in selector.select(0.02):
                if key.fileobj is process.stdout:
                    chunk = os.read(key.fd, min(8192, limit + 1 - len(output)))
                    output.extend(chunk)
                    if len(output) > limit:
                        raise RuntimeError('Clipboard helper output exceeds the limit.')
                    if not chunk:
                        selector.unregister(key.fileobj)
                else:
                    offset += os.write(key.fd, payload[offset:offset + 8192])
                    if offset == len(payload):
                        selector.unregister(key.fileobj)
                        process.stdin.close()
            if not selector.get_map() and process.poll() is None:
                time.sleep(0.01)
        if process.wait() != 0:
            raise RuntimeError('Clipboard helper failed.')
        check()
        completed = True
        return bytes(output)
    except OSError:
        raise RuntimeError('Clipboard helper is unavailable or failed.') from None
    finally:
        selector.close()
        if process is not None:
            if not completed:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


def clipboard_operation(operation, check):
    check()
    if operation == 'clear':
        clipboard_process(['wl-copy', '--clear'], check)
    else:
        offered = clipboard_process(['wl-paste', '--list-types'], check, limit=8192).splitlines()
        mime = next((mime for mime in CLIPBOARD_MIMES if mime.encode() in offered), None)
        if mime is None:
            raise RuntimeError('Clipboard does not offer supported plain text.')
        raw = clipboard_process(['wl-paste', '--no-newline', '--type', mime], check, limit=CLIPBOARD_BYTES)
        transformed = transform_clipboard(operation, raw)
        check()  # Commit boundary: never restore an older clipboard after this write.
        clipboard_process(['wl-copy', '--type', CLIPBOARD_MIMES[0]], check, payload=transformed)
