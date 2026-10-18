import json
import os

import pytest

from mx4_linux.reporting_state import Ownership, stable_identity


def test_identity_prefers_public_unit_and_rejects_non_unit_fallbacks():
    class Device:
        id = 'a1B2C3D4'
        unitId = 'a1B2C3D4'
    assert stable_identity(Device()) == 'unit:a1b2c3d4'
    Device.id = Device.unitId = '00000000'
    assert stable_identity(Device()) is None


def test_durable_record_and_exclusive_lifetime_lock(tmp_path):
    with Ownership('unit:a1b2c3d4', tmp_path) as owner:
        owner.touch(416, 0x104, 416, 1)
        assert owner.read()['controls'] == [{'cid': 416, 'flags': 0x104, 'mapping': 416, 'touched': 1}]
        assert owner.path.stat().st_mode & 0o777 == 0o600
        assert 'a1b2c3d4' not in owner.path.name
        with pytest.raises(RuntimeError, match='already'):
            with Ownership('unit:a1b2c3d4', tmp_path):
                pass
    with Ownership('unit:a1b2c3d4', tmp_path) as owner:
        assert owner.read()
        owner.clear()
        assert owner.read() is None


@pytest.mark.parametrize('change', [
    lambda r: r.update(version=True),
    lambda r: r['controls'][0].update(cid=80),
    lambda r: r['controls'][0].update(touched=4),
    lambda r: r['controls'][0].update(mapping=True),
    lambda r: r['controls'].append(dict(r['controls'][0])),
    lambda r: r.update(stable_id='unit:ffffffff'),
])
def test_corrupt_record_never_adopted_or_overwritten(tmp_path, change):
    with Ownership('unit:a1b2c3d4', tmp_path) as owner:
        owner.touch(416, 0x104, 416, 1)
        record = owner.read()
        change(record)
        owner.path.write_text(json.dumps(record))
        before = owner.path.read_bytes()
        with pytest.raises(RuntimeError, match='reporting'):
            owner.read()
        assert owner.path.read_bytes() == before


def test_failed_replace_preserves_previous_record(tmp_path, monkeypatch):
    with Ownership('unit:a1b2c3d4', tmp_path) as owner:
        owner.touch(416, 0x104, 416, 1)
        before = owner.path.read_bytes()
        monkeypatch.setattr(os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('disk')))
        with pytest.raises(OSError):
            owner.touch(416, 0x104, 416, 16)
        assert owner.path.read_bytes() == before


@pytest.mark.parametrize('raw', ['null', '[]', '{}', '"empty"'])
def test_existing_invalid_json_value_never_becomes_missing_state(tmp_path, raw):
    with Ownership('unit:a1b2c3d4', tmp_path) as owner:
        owner.path.write_text(raw)
        with pytest.raises(RuntimeError, match='reporting'):
            owner.read()
        assert owner.path.read_text() == raw


def test_oversized_or_symlink_journal_is_not_adopted(tmp_path):
    with Ownership('unit:a1b2c3d4', tmp_path) as owner:
        owner.path.write_bytes(b' ' * 16385)
        with pytest.raises(RuntimeError):
            owner.read()
        owner.path.unlink()
        owner.path.symlink_to(tmp_path / 'missing')
        with pytest.raises(RuntimeError):
            owner.read()
