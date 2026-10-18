"""Private, durable ownership of the listener's two temporary reporting bits."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

CONTROLS = frozenset((82, 83, 86, 195, 196, 416))
OWNED_BITS = 0x11
MAX_BYTES = 16384
MISSING = object()


class OwnershipConflict(RuntimeError):
    """An observable external reporting change requires explicit user intervention."""


def valid_identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'(unit|serial|demo):[a-z0-9][a-z0-9._-]{1,127}', value):
        return False
    payload = value.split(':', 1)[1]
    return not (set(payload) <= {'0'} or set(payload) <= {'f'})


def stable_identity(dev):
    """Use Solaar's public per-unit ID, never its receiver/path/model identity."""
    try:
        value = dev.id
        unit = getattr(dev, 'unitId', None)
        if not isinstance(value, str):
            return None
        value = value.strip().lower()
        result = ('unit:' if unit and str(unit).strip().lower() == value else 'serial:') + value
        return result if valid_identity(result) else None
    except Exception:
        return None


def require_identity(dev, expected=None):
    actual = stable_identity(dev)
    # Public properties are cached by Solaar. Address the unit again immediately
    # before each mutation so a reused receiver slot cannot inherit authorization.
    try:
        reply = dev.feature_request(0x0003, 0x00)
        fresh = 'unit:' + reply[1:5].hex() if isinstance(reply, bytes) and len(reply) >= 13 else None
    except Exception:
        fresh = None
    if not actual or fresh != actual or expected is not None and actual != expected:
        raise RuntimeError('Mouse identity is unavailable or changed; wake and refresh this mouse before continuing.')
    return actual


def private_dir(path):
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError('Private state directory must not be a symbolic link')
    os.chmod(path, 0o700)


def read_json(path, limit):
    """Bounded regular-file read, refusing links and unusual file types."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return MISSING
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Private state must be a regular file')
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Private state exceeds its size limit')
    return json.loads(raw)


def atomic_json(path, document):
    filename = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
            filename = stream.name
            os.chmod(filename, 0o600)
            json.dump(document, stream, allow_nan=False, separators=(',', ':'))
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, path)
        fsync_dir(path.parent)
    finally:
        if filename and os.path.exists(filename):
            os.unlink(filename)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def file_lock(path, blocking=True):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise RuntimeError('Private state lock must be a regular file')
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise RuntimeError('Mouse reporting is already owned by another active listener; stop it first.') from exc
        yield stream


@contextmanager
def configuration_lock(stable_id):
    """Exclude listener/host changes for the duration of a settings profile."""
    owner = Ownership(stable_id)
    private_dir(owner.directory)
    with file_lock(owner.path.with_suffix('.configuration.lock'), blocking=False):
        yield


class Ownership:
    def __init__(self, stable_id, directory=None):
        if not valid_identity(stable_id) or stable_id.startswith('demo:'):
            raise RuntimeError('A verified physical mouse identity is required for reporting ownership')
        self.stable_id = stable_id
        self.directory = Path(directory) if directory else Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'mx4-linux/reporting'
        digest = hashlib.sha256(stable_id.encode()).hexdigest()
        self.path = self.directory / (digest + '.json')
        self.lock_path = self.directory / (digest + '.lock')
        self._lock = None

    def __enter__(self):
        private_dir(self.directory)
        self._lock = file_lock(self.lock_path, blocking=False)
        self._lock.__enter__()
        return self

    def __exit__(self, *args):
        self._lock.__exit__(*args)

    def read(self):
        try:
            record = read_json(self.path, MAX_BYTES)
            if record is MISSING:
                return None
            if (not isinstance(record, dict) or set(record) != {'version', 'stable_id', 'controls'}
                    or type(record['version']) is not int or record['version'] != 1
                    or record['stable_id'] != self.stable_id
                    or not isinstance(record['controls'], list) or len(record['controls']) > 6):
                raise ValueError('invalid record')
            seen = set()
            for item in record['controls']:
                if not isinstance(item, dict) or set(item) != {'cid', 'flags', 'mapping', 'touched'}:
                    raise ValueError('invalid control')
                cid, flags, mapping, touched = (item[key] for key in ('cid', 'flags', 'mapping', 'touched'))
                if (any(type(value) is not int for value in (cid, flags, mapping, touched))
                        or cid not in CONTROLS or cid in seen or not 0 <= flags <= 65535
                        or not 1 <= mapping <= 65535 or touched <= 0 or touched & ~OWNED_BITS):
                    raise ValueError('invalid control values')
                seen.add(cid)
            return record
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise RuntimeError('Previous reporting record is unreadable or invalid; keep it intact and retry recovery for this mouse.') from exc

    def touch(self, cid, flags, mapping, bit):
        record = self.read() or {'version': 1, 'stable_id': self.stable_id, 'controls': []}
        item = next((item for item in record['controls'] if item['cid'] == cid), None)
        if item is None:
            item = {'cid': cid, 'flags': flags, 'mapping': mapping, 'touched': 0}
            record['controls'].append(item)
        elif item['flags'] != flags or item['mapping'] != mapping:
            raise RuntimeError('Original reporting snapshot changed')
        item['touched'] |= bit
        # Validate caller data through the same strict reader without touching hardware.
        if (type(cid) is not int or cid not in CONTROLS or type(flags) is not int or not 0 <= flags <= 65535
                or type(mapping) is not int or not 1 <= mapping <= 65535
                or type(bit) is not int or bit <= 0 or bit & ~OWNED_BITS):
            raise RuntimeError('Invalid reporting ownership snapshot')
        atomic_json(self.path, record)

    def clear(self):
        if self.path.exists():
            self.path.unlink()
            fsync_dir(self.directory)
