import struct
import pytest
from mx4_linux import event_worker as worker
from mx4_linux.reporting_state import Ownership
from test_event_worker import setup_session, run, notification, INDEX

THUMB = 0x2150
THUMB_INDEX = INDEX + 1


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))


def thumb_session():
    dev, api, commands, events = setup_session()
    dev.features[THUMB] = THUMB_INDEX
    dev.thumb = b'\x00\x81'
    dev.thumb_writes = []
    original = dev._feature_request
    def request(feature, function, *data):
        if feature != THUMB:
            return original(feature, function, *data)
        if function == 0:
            return bytes.fromhex('00140078000303e8') + b'\0' * 8
        if function == 0x10:
            return dev.thumb + b'\0' * 14
        if function == 0x20:
            owner = Ownership('unit:a1b2c3d4')
            record = owner.read()
            assert record is not None and record['thumb']['touched'] == [1, 0], 'thumb SET preceded durable journal'
            dev.thumb_writes.append(bytes(data))
            dev.thumb = bytes(data)
            return dev.thumb + b'\0' * 14
        raise AssertionError('unknown thumb opcode')
    dev.feature_request.side_effect = request
    return dev, api, commands, events


def test_thumb_acquire_persists_before_set_and_restores_owned_bit():
    dev, api, commands, events = thumb_session()
    before = dev.thumb
    assert run(dev, api, commands, events, thumb_wheel=True)
    assert dev.thumb_writes == [b'\x01\x81', before]
    assert dev.thumb == before
    assert events[0]['thumb_wheel']['diverted'] is True
    assert events[0]['thumb_wheel']['native_resolution'] == 20
    assert events[0]['thumb_wheel']['diverted_resolution'] == 120
    assert events[-1] == {'type': 'stopped', 'restored': True}
    assert Ownership('unit:a1b2c3d4').read() is None


def test_thumb_preexisting_diversion_refuses_before_buttons_mutate():
    dev, api, commands, events = thumb_session()
    dev.thumb = b'\x01\x00'
    assert not run(dev, api, commands, events, thumb_wheel=True)
    assert not dev.writes and not dev.thumb_writes
    assert events[0]['reason'] == 'ownership-conflict'


def test_thumb_live_bits_change_but_restore_preserves_fresh_inversion_and_other_bits():
    dev, api, commands, events = thumb_session()
    def read(*_):
        dev.thumb = b'\x41\x06'  # external inversion changed, touch/proximity live
        commands.put({'op': 'stop'})
    api.base.read.side_effect = read
    assert run(dev, api, commands, events, thumb_wheel=True)
    assert len(dev.thumb_writes) == 2
    assert dev.thumb == b'\x40\x06'
    assert events[-1]['restored'] is True


def test_thumb_lost_ack_restores_without_clearing_record_early():
    dev, api, commands, events = thumb_session()
    original = dev.feature_request.side_effect
    def request(feature, function, *data):
        reply = original(feature, function, *data)
        if feature == THUMB and function == 0x20 and len(dev.thumb_writes) == 1:
            raise OSError('lost ack after mutation')
        return reply
    dev.feature_request.side_effect = request
    assert run(dev, api, commands, events, thumb_wheel=True)
    assert dev.thumb == b'\x00\x81'
    assert events[-1]['restored'] is True


def test_malformed_thumb_info_validated_before_button_mutation():
    dev, api, commands, events = thumb_session()
    original = dev.feature_request.side_effect
    dev.feature_request.side_effect = lambda f, fn, *data: b'\0'*16 if f == THUMB and fn == 0 else original(f, fn, *data)
    assert not run(dev, api, commands, events, thumb_wheel=True)
    assert not dev.writes and not dev.thumb_writes


def test_thumb_notifications_strict_feature_device_address_and_payload():
    events = []
    decoder = worker._ThumbDecoder(255, THUMB_INDEX, events.append)
    decoder.active = True
    payload = struct.pack('!hHBB', -32768, 65535, 2, 3)
    decoder(notification(0, payload, 255, THUMB_INDEX))
    assert events == [{'type': 'thumb', 'rotation': -32768, 'timestamp': 65535, 'status': 2, 'flags': 3}]
    for address, data, number, feature in [(16, payload, 255, THUMB_INDEX), (0,payload,1,THUMB_INDEX),
        (0,payload,255,INDEX), (0,payload[:-1],255,THUMB_INDEX), (0,payload+b'\0',255,THUMB_INDEX),
        (0,payload[:4]+b'\x04\0',255,THUMB_INDEX)]:
        decoder(notification(address,data,number,feature))
    assert len(events) == 1
    decoder.active = False
    decoder(notification(0,payload,255,THUMB_INDEX))
    assert len(events) == 1


def test_context_assignment_native_assignment_changes_only_owned_thumb_bit():
    dev, api, commands, events = thumb_session()
    stages = iter([False, True, None])
    def read(*_):
        value = next(stages)
        if value is None: commands.put({'op':'stop'})
        else: commands.put({'op':'configure','config_id': 1 if not value else 2,
                           'controls':[416], 'ring_button':416,'raw_xy':True,'thumb_wheel':value})
    api.base.read.side_effect = read
    assert run(dev, api, commands, events, thumb_wheel=True)
    configured = [e for e in events if e['type'] == 'configured']
    assert [e['thumb_wheel']['diverted'] for e in configured] == [False, True]
    assert dev.thumb == b'\0\x81'
    assert len(dev.thumb_writes) == 4


def test_thumb_and_buttons_restore_failure_keeps_combined_pending_journal():
    dev, api, commands, events = thumb_session()
    original = dev.feature_request.side_effect
    def request(feature, function, *data):
        if feature == THUMB and function == 0x20 and data[0] == 0:
            raise OSError('restore failed')
        return original(feature, function, *data)
    dev.feature_request.side_effect = request
    assert not run(dev, api, commands, events, thumb_wheel=True)
    record = Ownership('unit:a1b2c3d4').read()
    assert record['controls'] and record['thumb']
    assert events[-1]['restored'] is False
