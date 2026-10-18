"""Verified THUMB_WHEEL 0x2150 fields and masked temporary diversion."""
from .reporting_state import require_identity

FEATURE = 0x2150
LIVE_BITS = 0x06  # byte 1 touch/proximity, never persistent configuration


def read_status(dev):
    reply = dev.feature_request(FEATURE, 0x10)
    if not isinstance(reply, bytes) or not 2 <= len(reply) <= 16:
        raise RuntimeError('No live two-byte thumb reporting reply.')
    return reply[:2]


def read_info(dev):
    index = dev.features[FEATURE] if dev.features is not None and FEATURE in dev.features else None
    if type(index) is not int or not 0 < index < 256:
        raise RuntimeError('This mouse does not expose thumb-wheel notifications.')
    reply = dev.feature_request(FEATURE, 0x00)
    if not isinstance(reply, bytes) or not 8 <= len(reply) <= 16:
        raise RuntimeError('No valid thumb-wheel capability reply.')
    native, diverted = int.from_bytes(reply[:2]), int.from_bytes(reply[2:4])
    if not native or not diverted or reply[4] not in (0, 1):
        raise RuntimeError('Thumb-wheel resolutions or direction are invalid.')
    return {'feature_index': index, 'native_resolution': native, 'diverted_resolution': diverted,
            'default_direction': -1 if reply[4] == 0 else 1, 'capabilities': reply[5],
            'time_unit': int.from_bytes(reply[6:8])}


def write_owned(dev, enabled, stable_id):
    """Preserve fresh unrelated persistent bits; independently confirm even a lost ACK."""
    before = read_status(dev)
    require_identity(dev, stable_id)
    desired = bytes(((before[0] & ~1) | int(enabled), before[1]))
    if before[0] & 1 != int(enabled):
        require_identity(dev, stable_id)
        try:
            dev.feature_request(FEATURE, 0x20, *desired)
        except Exception:
            pass  # A lost acknowledgement may already have changed firmware.
    after = read_status(dev)
    require_identity(dev, stable_id)
    if (after[0] != desired[0] or after[1] & ~LIVE_BITS != desired[1] & ~LIVE_BITS):
        raise RuntimeError('Thumb reporting mutation/restoration was not confirmed; recovery remains pending.')
    return after
