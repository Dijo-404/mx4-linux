"""Hardware-free tests of discovery, write validation, and worker isolation."""

import enum
import errno
import math
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import pytest

from mx4_linux import device_worker as worker


class Named(int):
    def __new__(cls, value, label):
        obj = super().__new__(cls, value)
        obj.label = label
        return obj

    def __str__(self):
        return self.label


class FakeSetting:
    def __init__(self, name="smart-shift", kind=4, value=40, choices=None, bounds=(0, 100)):
        self.name, self.kind, self.hardware = name, kind, value
        self.label, self.description = name.title(), "Device setting"
        self.choices, self.range = choices, bounds
        self._value = None
        self.reads, self.writes = [], []
        self.accept_write = True
        self.update_hardware = True
        self.fail_readback = False
        self.device = None

    def read(self, cached=True):
        self.reads.append(cached)
        if self.fail_readback and self.writes:
            return self._value  # Mimic Solaar retaining _value on a timeout.
        if self.name == "reprogrammable-keys":
            self._value = {int(key): self.device.keys[self.device.keys.index(key)]._mapped_to for key in self.choices}
        else:
            self._value = self.hardware
        return self._value

    def write(self, value, save=True):
        self.writes.append((value, save))
        self._value = value
        if not self.accept_write:
            return None
        if self.update_hardware:
            self.hardware = value
        return value

    def write_key_value(self, key, value, save=True):
        self.writes.append((key, value, save))
        self.device.keys[self.device.keys.index(key)]._mapped_to = value
        if not self.accept_write:
            return None
        if self.update_hardware:
            self.device.mappings[key] = value
        return value


class KeyList(list):
    def index(self, key):
        return next(index for index, item in enumerate(self) if item.cid == int(key))


class FakeDevice:
    def __init__(self, name="MX Master 4", number=255):
        self.name, self.number, self.online = name, number, True
        self.id = self.unitId = "a1b2c3d4"
        self.close = Mock()
        self.ping = Mock(return_value=True)
        self.battery = Mock(return_value=SimpleNamespace(to_str=lambda: "Battery: 82% (discharging)"))
        self.mappings = {80: 80, 81: 81}
        self.keys = KeyList(SimpleNamespace(cid=cid, _mapped_to=999, _mapping_flags=None) for cid in self.mappings)
        self.feature_request = Mock(side_effect=self._feature_request)

    def _feature_request(self, _feature, function, *data):
        if _feature == 3 and function == 0:
            return b'\0' + bytes.fromhex(self.id) + b'\0' * 8
        assert function == 0x20, "Tests must not issue a physical write"
        cid = int.from_bytes(bytes(data))
        return struct.pack("!HBH", cid, 0, self.mappings[cid])


def fake_api(settings=None, devices=None, receiver=False):
    settings = settings or []
    devices = devices or [FakeDevice()]
    info = SimpleNamespace(path="/dev/hidraw-test", isDevice=not receiver)
    base = SimpleNamespace(
        receivers_and_devices=Mock(return_value=[info]),
        open_path=Mock(return_value=23),
        close=Mock(return_value=True),
    )
    api = SimpleNamespace(
        base=base,
        configuration=SimpleNamespace(persister=Mock(side_effect=AssertionError("Persistent config accessed"))),
        hidpp10=SimpleNamespace(set_configuration_pending_flags=Mock(side_effect=AssertionError("Receiver startup write"))),
        hidpp20_constants=SimpleNamespace(SupportedFeature=SimpleNamespace(REPROG_CONTROLS_V4=0x1B04)),
    )
    templates = [SimpleNamespace(name=setting.name, feature=True, instance=setting) for setting in settings]
    api.settings_templates = SimpleNamespace(SETTINGS=templates, check_feature=Mock(side_effect=lambda dev, template: template.instance))
    for setting in settings:
        setting.device = devices[0]

    class Receiver:
        def count(self):
            return len(devices)

        def __getitem__(self, slot):
            return next((dev for dev in devices if dev.number == slot), None)

        def close(self):
            for dev in devices:
                dev.close()
            api.base.close(23)

    def create_direct(low_level, _info):
        low_level.open_path(info.path)
        dev = devices[0]
        dev.close.side_effect = lambda: low_level.close(23)
        return dev

    def create_receiver(low_level, _info):
        low_level.open_path(info.path)
        api.hidpp10.set_configuration_pending_flags(None, 255)
        return Receiver()

    api.device = SimpleNamespace(create_device=Mock(side_effect=create_direct))
    api.receiver = SimpleNamespace(create_receiver=Mock(side_effect=create_receiver))
    return api, devices[0], info


class WorkerTests(unittest.TestCase):
    def test_isolation_bounds_paired_node_wait_and_restores_on_failure(self):
        api, _, _ = fake_api()
        original = api.base.find_paired_node = Mock(return_value="/dev/paired")
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            with worker._isolated_solaar(api):
                self.assertEqual(api.base.find_paired_node("/dev/receiver", 2, 1), "/dev/paired")
                original.assert_called_once_with("/dev/receiver", 2, .01)
                scoped = api.base.find_paired_node
                with worker._isolated_solaar(api):
                    self.assertEqual(api.base.find_paired_node("/dev/receiver", 2, 1), "/dev/paired")
                self.assertIs(api.base.find_paired_node, scoped)
                api.base.find_paired_node("/dev/receiver", 2, 0)
                self.assertEqual(original.call_args.args[-1], 0)
                raise RuntimeError("injected failure")
        self.assertIs(api.base.find_paired_node, original)

    def test_isolation_uses_distinct_software_id_and_restores_after_error(self):
        api, _, _ = fake_api()
        api.base.SOLAAR_SOFTWARE_ID = 0x0B
        original = api.device.create_device.side_effect

        def check_software_id(*args):
            self.assertEqual(api.base.SOLAAR_SOFTWARE_ID, 0x0C)
            return original(*args)

        api.device.create_device.side_effect = check_software_id
        self.assertTrue(worker.handle_request({"op": "scan"}, api)["ok"])
        self.assertEqual(api.base.SOLAAR_SOFTWARE_ID, 0x0B)
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            with worker._isolated_solaar(api):
                self.assertEqual(api.base.SOLAAR_SOFTWARE_ID, 0x0C)
                with worker._isolated_solaar(api, software_id=0x0D):
                    self.assertEqual(api.base.SOLAAR_SOFTWARE_ID, 0x0D)
                self.assertEqual(api.base.SOLAAR_SOFTWARE_ID, 0x0C)
                raise RuntimeError("test failure")
        self.assertEqual(api.base.SOLAAR_SOFTWARE_ID, 0x0B)

    def test_isolation_does_not_add_unsupported_software_id_attribute(self):
        api, _, _ = fake_api()
        with worker._isolated_solaar(api):
            self.assertFalse(hasattr(api.base, "SOLAAR_SOFTWARE_ID"))
        self.assertFalse(hasattr(api.base, "SOLAAR_SOFTWARE_ID"))

    def test_real_solaar_wire_id_follows_worker_isolation(self):
        from logitech_receiver import base

        api, _, _ = fake_api()
        api.base = base
        original = base._get_next_sw_id()
        with worker._isolated_solaar(api):
            self.assertEqual(base._get_next_sw_id(), 0x0C)
            with worker._isolated_solaar(api, software_id=0x0D):
                self.assertEqual(base._get_next_sw_id(), 0x0D)
            self.assertEqual(base._get_next_sw_id(), 0x0C)
        self.assertEqual(base._get_next_sw_id(), original)

    def request_write(self, api, setting, value, **extra):
        return worker.handle_request({
            "op": "write", "device_id": worker._device_id("/dev/hidraw-test", 255),
            "setting": setting, "value": value, **extra,
        }, api)

    def test_scan_reads_live_values_without_writes_or_persistence(self):
        setting = FakeSetting()
        hidden = FakeSetting("divert-keys", 1, False)
        hidden_wheel = FakeSetting("thumb-scroll-mode", 1, False)
        api, dev, _ = fake_api([setting, hidden, hidden_wheel])
        old_persister = api.configuration.persister
        response = worker.handle_request({"op": "scan"}, api)
        self.assertTrue(response["ok"])
        self.assertEqual(response["devices"][0]["settings"][0]["value"], 40)
        self.assertEqual(response["devices"][0]["battery"], "Battery: 82% (discharging)")
        self.assertEqual(setting.reads, [False])
        self.assertEqual(setting.writes, [])
        self.assertEqual(hidden.reads, [])
        self.assertEqual(hidden_wheel.reads, [])
        self.assertEqual(len(response["devices"][0]["settings"]), 1)
        old_persister.assert_not_called()
        self.assertIs(api.configuration.persister, old_persister)
        dev.close.assert_called_once()
        api.base.close.assert_called_once_with(23)

    def test_permission_denial_is_a_scan_diagnostic(self):
        api, _, _ = fake_api()
        api.device.create_device.side_effect = PermissionError(errno.EACCES, "Permission denied")
        response = worker.handle_request({"op": "scan"}, api)
        self.assertEqual(response["devices"], [])
        self.assertTrue(response["diagnostics"][0]["permission_denied"])

    def test_failed_constructor_handle_is_closed(self):
        api, _, _ = fake_api()

        def failing_factory(low_level, info):
            low_level.open_path(info.path)
            raise RuntimeError("Constructor failed")

        api.device.create_device.side_effect = failing_factory
        response = worker.handle_request({"op": "scan"}, api)
        self.assertIn("Constructor failed", response["diagnostics"][0]["message"])
        api.base.close.assert_called_once_with(23)

    def test_receiver_scan_suppresses_constructor_write_and_closes_children(self):
        devices = [FakeDevice(number=1), FakeDevice("MX Master 3S", number=2)]
        api, _, _ = fake_api(devices=devices, receiver=True)
        original = api.hidpp10.set_configuration_pending_flags
        response = worker.handle_request({"op": "scan"}, api)
        self.assertEqual(len(response["devices"]), 1)
        original.assert_not_called()
        self.assertIs(api.hidpp10.set_configuration_pending_flags, original)
        for dev in devices:
            dev.close.assert_called_once()

    def test_offline_device_has_no_settings_or_battery_reads(self):
        setting = FakeSetting()
        api, dev, _ = fake_api([setting])
        dev.online = False
        response = worker.handle_request({"op": "scan"}, api)
        self.assertFalse(response["devices"][0]["online"])
        self.assertEqual(response["devices"][0]["settings"], [])
        dev.battery.assert_not_called()
        self.assertEqual(setting.reads, [])

    def test_only_mx_master_4_model_names_are_accepted(self):
        for name in ("MX Master 4", "mx master 4", "MX Master 4 for Mac", "Logitech MX Master 4 for Business"):
            self.assertTrue(worker._is_mx4(name), name)
        for name in ("MX Master 3S", "MX Master 40", "MX4", "MX Master 4 Evil", "MX Anywhere 4"):
            self.assertFalse(worker._is_mx4(name), name)

    def test_a_setting_failure_does_not_hide_other_settings(self):
        broken, working = FakeSetting(), FakeSetting("haptic-level", value=60)
        broken.read = Mock(side_effect=RuntimeError("Firmware error"))
        api, _, _ = fake_api([broken, working])
        response = worker.handle_request({"op": "scan"}, api)
        self.assertEqual([item["name"] for item in response["devices"][0]["settings"]], ["haptic-level"])
        self.assertIn("Firmware error", response["diagnostics"][0]["message"])

    def test_range_write_disables_save_and_confirms_live_readback(self):
        setting = FakeSetting()
        api, dev, _ = fake_api([setting])
        response = self.request_write(api, setting.name, 75)
        self.assertEqual(response, {"ok": True, "value": 75})
        self.assertEqual(setting.writes, [(75, False)])
        self.assertEqual(setting.reads, [False, False])
        dev.close.assert_called_once()

    def test_single_setting_write_does_not_build_unrelated_capabilities(self):
        settings = [FakeSetting(name) for name in worker.ALLOWED_SETTINGS]
        api, _, _ = fake_api(settings)
        self.assertTrue(self.request_write(api, "haptic-level", 75)["ok"])
        checked = [call.args[1].name for call in api.settings_templates.check_feature.call_args_list]
        self.assertEqual(checked, ["haptic-level"])
        self.assertTrue(all(not setting.reads for setting in settings if setting.name != "haptic-level"))

    def test_wheel_write_returns_related_live_values_without_a_full_scan(self):
        shift, ratchet, unrelated = FakeSetting(), FakeSetting("scroll-ratchet", value=2), FakeSetting("haptic-level")
        api, _, _ = fake_api([shift, ratchet, unrelated])
        self.assertEqual(self.request_write(api, "smart-shift", 25), {
            "ok": True, "value": 25, "values": {"scroll-ratchet": 2}})
        self.assertEqual(ratchet.reads, [False])
        self.assertFalse(unrelated.reads)
        self.assertFalse(ratchet.writes)

    def test_failed_related_wheel_read_requires_refresh_without_failing_confirmed_write(self):
        for result in (None, RuntimeError("Related wheel read timed out")):
            with self.subTest(result=result):
                shift, ratchet = FakeSetting(), FakeSetting("scroll-ratchet", value=2)
                if isinstance(result, Exception):
                    ratchet.read = Mock(side_effect=result)
                else:
                    ratchet.read = Mock(return_value=result)
                api, _, _ = fake_api([shift, ratchet])
                self.assertEqual(self.request_write(api, "smart-shift", 25), {
                    "ok": True, "value": 25, "refresh_required": True})
                self.assertEqual(shift.writes, [(25, False)])
                self.assertEqual(shift.reads, [False, False])

    def test_failed_wheel_dependency_discovery_requires_refresh_after_confirmed_write(self):
        shift, ratchet = FakeSetting(), FakeSetting("scroll-ratchet", value=2)
        api, _, _ = fake_api([shift, ratchet])

        def check(_dev, template):
            if template.name == "scroll-ratchet":
                raise RuntimeError("Wheel capability query timed out")
            return template.instance

        api.settings_templates.check_feature.side_effect = check
        self.assertEqual(self.request_write(api, "smart-shift", 25), {
            "ok": True, "value": 25, "refresh_required": True})
        self.assertEqual(shift.writes, [(25, False)])

    def test_torque_write_keeps_the_solaar_wheel_mode_dependency(self):
        torque, ratchet = FakeSetting("scroll-ratchet-torque"), FakeSetting("scroll-ratchet", value=2)
        api, dev, _ = fake_api([torque, ratchet])
        original_write = torque.write

        def write(value, save=True):
            self.assertEqual([setting.name for setting in dev._settings], ["scroll-ratchet-torque", "scroll-ratchet"])
            return original_write(value, save=save)

        torque.write = write
        self.assertEqual(self.request_write(api, torque.name, 75), {
            "ok": True, "value": 75, "values": {"scroll-ratchet": 2}})
        self.assertFalse(ratchet.writes)

    def test_targeted_receiver_lookup_skips_unrelated_paths_and_slots(self):
        setting = FakeSetting()
        devices = [FakeDevice(number=1), FakeDevice(number=2)]
        api, _, info = fake_api([setting], devices, receiver=True)
        api.base.receivers_and_devices.return_value = [SimpleNamespace(path="/dev/other", isDevice=False), info]
        original = api.receiver.create_receiver.side_effect
        slots = []

        def create(base, selected):
            self.assertIs(selected, info)
            owner = original(base, selected)

            class Receiver:
                close = owner.close

                def __getitem__(self, slot):
                    slots.append(slot)
                    return owner[slot]

            return Receiver()

        api.receiver.create_receiver.side_effect = create
        response = worker.handle_request({"op": "write", "device_id": worker._device_id(info.path, 2),
                                          "setting": setting.name, "value": 75}, api)
        self.assertTrue(response["ok"])
        self.assertEqual(slots, [2])
        devices[0].ping.assert_not_called()
        devices[1].ping.assert_called_once()

    def test_target_ids_cannot_open_paths_outside_device_enumeration(self):
        for target in ('["/dev/arbitrary",255]', '"ab"', 'null', '{}', '["/dev/hidraw-test",true]'):
            api, _, _ = fake_api([FakeSetting()])
            response = worker.handle_request({"op": "write", "device_id": target,
                                              "setting": "smart-shift", "value": 25}, api)
            self.assertFalse(response["ok"])
            api.device.create_device.assert_not_called()

    def test_invalid_range_values_never_write(self):
        for value in (True, False, -1, 101, 40.0, "40", None):
            with self.subTest(value=value):
                setting = FakeSetting()
                api, dev, _ = fake_api([setting])
                response = self.request_write(api, setting.name, value)
                self.assertFalse(response["ok"])
                self.assertEqual(setting.writes, [])
                dev.close.assert_called_once()

    def test_toggle_requires_boolean_and_false_is_success(self):
        for value, success in ((False, True), (True, True), (0, False), (1, False), ("true", False)):
            with self.subTest(value=value):
                setting = FakeSetting("hires-smooth-invert", kind=1, value=True)
                api, _, _ = fake_api([setting])
                response = self.request_write(api, setting.name, value)
                self.assertEqual(response["ok"], success)
                self.assertEqual(len(setting.writes), int(success))

    def test_choice_checks_firmware_discovered_choices(self):
        choices = [Named(400, "400 DPI"), Named(800, "800 DPI")]
        for value, success in ((400, True), (800, True), (1600, False), (True, False), ("400", False)):
            with self.subTest(value=value):
                setting = FakeSetting("dpi", 2, 800, choices)
                api, _, _ = fake_api([setting])
                response = self.request_write(api, setting.name, value)
                self.assertEqual(response["ok"], success)
                self.assertEqual(len(setting.writes), int(success))

    def test_zero_choice_is_successful(self):
        setting = FakeSetting("scroll-ratchet", 2, 1, [Named(0, "Free"), Named(1, "Ratchet")])
        api, _, _ = fake_api([setting])
        self.assertEqual(self.request_write(api, setting.name, 0), {"ok": True, "value": 0})

    def test_mapping_has_live_values_and_key_specific_choices(self):
        choices = {Named(80, "Back"): [Named(80, "Back"), Named(81, "Forward")]}
        setting = FakeSetting("reprogrammable-keys", 10, choices=choices)
        api, dev, _ = fake_api([setting])
        response = worker.handle_request({"op": "scan"}, api)
        descriptor = response["devices"][0]["settings"][0]
        self.assertEqual(descriptor["value"], {"80": 80})
        self.assertEqual(descriptor["keys"][0]["value"], 80)
        self.assertEqual(descriptor["keys"][0]["choices"][1], {"value": 81, "label": "Forward"})
        self.assertEqual(setting.writes, [])
        self.assertTrue(all(call.args[1] == 0x20 for call in dev.feature_request.call_args_list))

    def test_mapping_write_uses_save_false_and_fresh_hardware_readback(self):
        choices = {Named(80, "Back"): [Named(80, "Back"), Named(81, "Forward")]}
        setting = FakeSetting("reprogrammable-keys", 10, choices=choices)
        api, _, _ = fake_api([setting])
        response = self.request_write(api, setting.name, 81, key=80)
        self.assertEqual(response, {"ok": True, "value": 81})
        self.assertEqual(setting.writes, [(80, 81, False)])

    def test_mapping_write_rejects_bad_keys_and_choices(self):
        for key, value in ((True, 81), ("80", 81), (99, 81), (80, 99), (80, True), (None, 81)):
            with self.subTest(key=key, value=value):
                choices = {Named(80, "Back"): [Named(80, "Back"), Named(81, "Forward")]}
                setting = FakeSetting("reprogrammable-keys", 10, choices=choices)
                api, _, _ = fake_api([setting])
                self.assertFalse(self.request_write(api, setting.name, value, key=key)["ok"])
                self.assertEqual(setting.writes, [])

    def test_mapping_optimistic_cache_cannot_confirm_failed_hardware_change(self):
        choices = {Named(80, "Back"): [Named(80, "Back"), Named(81, "Forward")]}
        setting = FakeSetting("reprogrammable-keys", 10, choices=choices)
        setting.update_hardware = False
        api, _, _ = fake_api([setting])
        response = self.request_write(api, setting.name, 81, key=80)
        self.assertFalse(response["ok"])
        self.assertIn("readback", response["error"])

    def test_no_mapping_reply_does_not_report_cached_defaults(self):
        choices = {Named(80, "Back"): [Named(80, "Back"), Named(81, "Forward")]}
        setting = FakeSetting("reprogrammable-keys", 10, choices=choices)
        api, dev, _ = fake_api([setting])
        dev.feature_request.side_effect = lambda *args: None
        response = self.request_write(api, setting.name, 80, key=80)
        self.assertFalse(response["ok"])
        self.assertEqual(setting.writes, [])

    def test_haptic_play_is_a_command_without_persistent_readback(self):
        setting = FakeSetting("haptic-play", 2, None, [Named(0, "Click"), Named(1, "Pulse")])
        api, _, _ = fake_api([setting])
        response = self.request_write(api, setting.name, 1)
        self.assertEqual(response, {"ok": True, "value": 1})
        self.assertEqual(setting.writes, [(1, False)])
        self.assertEqual(setting.reads, [False])

    def test_force_sensing_adapts_singleton_map_to_range_and_confirms_readback(self):
        # Use Solaar's actual new Settings class: unlike legacy settings, this
        # implementation requires a dict cache even for a scalar range widget.
        try:
            from logitech_receiver import settings_new
        except ImportError:
            self.skipTest("Pinned Solaar dependency is not installed")
        api, dev, _ = fake_api()
        hardware = {"value": 45}

        class Button:
            changeable, min_value, default, max_value = True, 15, 45, 90

            def get_current(self):
                return hardware["value"]

            def acceptable_current(self, value):
                return self.min_value <= value <= self.max_value

            def set_current(self, value):
                hardware["value"] = value

        class Buttons(dict):
            def query_key(self, key):
                return self[key]

        class ForceSetting(settings_new.Settings):
            name, label, description = "force-sensing", "Panel force", "Activation force"
            kind, min_value, max_value = 4, 15, 90
            get, set, acceptable = "get_current", "set_current", "acceptable_current"

        dev.persister = None
        setting = ForceSetting(dev, Buttons({0: Button()}))
        api.settings_templates.SETTINGS = [SimpleNamespace(name="force-sensing", feature=True, instance=setting)]
        scan = worker.handle_request({"op": "scan"}, api)
        descriptor = scan["devices"][0]["settings"][0]
        self.assertEqual((descriptor["kind"], descriptor["value"], descriptor["minimum"], descriptor["maximum"]), ("range", 45, 15, 90))
        self.assertEqual(descriptor["default"], 45)
        self.assertEqual(self.request_write(api, "force-sensing", 70), {"ok": True, "value": 70})
        self.assertFalse(self.request_write(api, "force-sensing", 100)["ok"])

    def test_hidapi_silently_lost_permission_errno_is_recovered(self):
        from unittest.mock import patch

        api, _, _ = fake_api()
        api.base.open_path.return_value = None
        with patch.object(worker.os.path, "exists", return_value=True), patch.object(worker.os, "access", return_value=False):
            response = worker.handle_request({"op": "scan"}, api)
        self.assertEqual(response["devices"], [])
        self.assertTrue(response["diagnostics"][0]["permission_denied"])

    def test_write_timeout_and_readback_timeout_are_errors(self):
        for failure in ("write", "readback"):
            with self.subTest(failure=failure):
                setting = FakeSetting()
                if failure == "write":
                    setting.accept_write = False
                else:
                    setting.fail_readback = True
                api, dev, _ = fake_api([setting])
                response = self.request_write(api, setting.name, 75)
                self.assertFalse(response["ok"])
                dev.close.assert_called_once()

    def test_unknown_device_and_unsupported_setting_never_write(self):
        setting = FakeSetting()
        api, _, _ = fake_api([setting])
        response = worker.handle_request({"op": "write", "device_id": "untrusted:/dev/anything", "setting": setting.name, "value": 75}, api)
        self.assertFalse(response["ok"])
        self.assertEqual(setting.writes, [])
        response = self.request_write(api, "divert-keys", True)
        self.assertFalse(response["ok"])

    def test_write_refuses_other_models_and_offline_devices(self):
        for name, online in (("MX Master 3S", True), ("MX Master 4", False)):
            setting = FakeSetting()
            dev = FakeDevice(name)
            dev.online = online
            api, _, _ = fake_api([setting], [dev])
            self.assertFalse(self.request_write(api, setting.name, 75)["ok"])
            self.assertEqual(setting.writes, [])

    def test_solaar_noise_goes_to_stderr(self):
        api, _, _ = fake_api()
        original = api.device.create_device.side_effect

        def noisy_factory(*args):
            print("Noisy Solaar import or API")
            return original(*args)

        api.device.create_device.side_effect = noisy_factory
        stdout, stderr = io.StringIO(), io.StringIO()
        with worker.contextlib.redirect_stdout(stdout), worker.contextlib.redirect_stderr(stderr):
            self.assertTrue(worker.handle_request({"op": "scan"}, api)["ok"])
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Noisy Solaar", stderr.getvalue())

    def test_invalid_requests_return_json_errors(self):
        for request in (None, [], "scan", {}, {"op": "shell"}):
            response = worker.handle_request(request)
            self.assertFalse(response["ok"])
            self.assertIsInstance(response["error"], str)

    def test_malformed_worker_stdin_returns_exactly_one_json_response(self):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        for raw in (b"{invalid\n", b"\n", b"[]\n", b'{"op":"shell"}\n', b"x" * 65537 + b"\n"):
            with self.subTest(raw=raw[:20]):
                result = subprocess.run([sys.executable, "-m", "mx4_linux.device_worker"], input=raw, capture_output=True, env=env, check=True)
                self.assertEqual(len(result.stdout.splitlines()), 1)
                self.assertFalse(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()


def test_scan_metadata_exact_battery_and_stable_unit():
    dev = FakeDevice()
    dev.id = dev.unitId = 'A1B2C3D4'
    dev.battery.return_value = SimpleNamespace(level=82, to_str=lambda: '82%')
    result = worker.handle_request({'op': 'scan'}, fake_api(devices=[dev])[0])['devices'][0]
    assert result['stable_id'] == 'unit:a1b2c3d4'
    assert result['battery_percent'] == 82
    assert result['battery_observed_at'] > 0


def test_expected_identity_changed_rejects_setting_before_write():
    setting = FakeSetting()
    dev = FakeDevice()
    dev.id = dev.unitId = 'a1b2c3d4'
    result = worker.handle_request({'op': 'write', 'device_id': worker._device_id('/dev/hidraw-test', 255),
                                    'expected_stable_id': 'unit:d1e2f3a4',
                                    'setting': 'smart-shift', 'value': 20}, fake_api([setting], [dev])[0])
    assert not result['ok']
    assert not setting.writes


def test_easy_switch_gets_join_utf8_chunks_without_any_hostname_write():
    dev = FakeDevice()
    dev.features = {0x1814: 10, 0x1815: 11}
    name = ('a' * 13 + 'é').encode()
    calls = []
    def request(feature, function, *args, **kwargs):
        calls.append((feature, function, args, kwargs))
        if feature == 0x1814:
            assert function == 0
            return bytes([3, 0])
        assert feature == 0x1815 and function in (0, 0x10, 0x30)
        if function == 0:
            return bytes([1, 8, 3, 0])
        if function == 0x10:
            return bytes([args[0], int(args[0] < 2), 0, 0, len(name), 24])
        return bytes(args) + name[args[1]:args[1] + 14]
    dev.feature_request.side_effect = request
    result = worker._easy_switch(dev)
    assert result['count'] == 3 and result['current'] == 0
    assert result['hosts'][0]['name'] == name.decode()
    assert [h['paired'] for h in result['hosts']] == [True, True, False]
    assert all(fn not in (0x40,) for _, fn, _, _ in calls)


@pytest.mark.parametrize('value,known', [(0, True), (10, True), (11, True), (100, True),
                                       (True, False), (-1, False), (101, False),
                                       (math.nan, False), (math.inf, False), (None, False)])
def test_battery_percentage_is_exact_and_bounded(value, known):
    dev = FakeDevice()
    dev.battery.return_value = SimpleNamespace(level=value, to_str=lambda: 'battery')
    result = worker._battery(dev)
    assert (result['battery_percent'] is not None) == known
    assert (result['battery_observed_at'] is not None) == known


def test_approximate_battery_enum_is_not_a_percentage():
    class Approx(enum.IntEnum):
        LOW = 10
    dev = FakeDevice()
    dev.battery.return_value = SimpleNamespace(level=Approx.LOW, to_str=lambda: 'low')
    assert worker._battery(dev)['battery_percent'] is None


@pytest.fixture
def hosts_device(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    dev = FakeDevice()
    dev.features = {0x1814: 10, 0x1815: 11}
    calls = []
    def request(feature, function, *args, **kwargs):
        calls.append((feature, function, args, kwargs))
        if feature == 3:
            return b'\0' + bytes.fromhex(dev.id) + b'\0' * 8
        if feature == 0x1814:
            if function == 0:
                return bytes([3, 0])
            assert function == 0x10 and kwargs == {'no_reply': True}
            return None
        assert feature == 0x1815
        if function == 0:
            return bytes([1, 8, 3, 0])
        if function == 0x10:
            return bytes([args[0], int(args[0] < 2), 0, 0, 0, 24])
        raise AssertionError('No hostname writes')
    dev.feature_request.side_effect = request
    return dev, fake_api(devices=[dev])[0], calls


def test_valid_host_switch_is_one_unconfirmed_request_without_readback(hosts_device):
    dev, api, calls = hosts_device
    result = worker.handle_request({'op': 'switch_host', 'device_id': worker._device_id('/dev/hidraw-test', 255),
                                   'index': 1, 'expected_stable_id': 'unit:a1b2c3d4'}, api)
    assert result == {'ok': True, 'requested': True, 'message': 'Switch requested; mouse may disconnect.'}
    assert calls[-1] == (0x1814, 0x10, (1,), {'no_reply': True})
    assert sum(feature == 0x1814 and fn == 0x10 for feature, fn, _, _ in calls) == 1


@pytest.mark.parametrize('index', [True, -1, 2, 3, None, '1'])
def test_invalid_or_unpaired_host_never_writes(hosts_device, index):
    _dev, api, calls = hosts_device
    assert not worker.handle_request({'op': 'switch_host', 'device_id': worker._device_id('/dev/hidraw-test', 255),
                                     'index': index, 'expected_stable_id': 'unit:a1b2c3d4'}, api)['ok']
    assert not any(feature == 0x1814 and fn == 0x10 for feature, fn, _, _ in calls)


def test_scan_never_calls_host_setters(hosts_device):
    _dev, api, calls = hosts_device
    assert worker.handle_request({'op': 'scan'}, api)['ok']
    assert all((feature, function) not in {(0x1814, 0x10), (0x1815, 0x40)} for feature, function, _, _ in calls)


def test_host_switch_refuses_dirty_ownership_and_other_listener(hosts_device):
    from mx4_linux.reporting_state import Ownership
    _dev, api, calls = hosts_device
    request = {'op': 'switch_host', 'device_id': worker._device_id('/dev/hidraw-test', 255),
               'index': 1, 'expected_stable_id': 'unit:a1b2c3d4'}
    with Ownership('unit:a1b2c3d4') as owner:
        assert not worker.handle_request(request, api)['ok']
        owner.touch(416, 0x104, 416, 17)
    assert not worker.handle_request(request, api)['ok']
    assert not any(feature == 0x1814 and fn == 0x10 for feature, fn, _, _ in calls)


def test_host_switch_same_channel_is_noop_and_changed_identity_is_rejected(hosts_device):
    _dev, api, calls = hosts_device
    request = {'op': 'switch_host', 'device_id': worker._device_id('/dev/hidraw-test', 255),
               'index': 0, 'expected_stable_id': 'unit:a1b2c3d4'}
    assert worker.handle_request(request, api)['requested'] is False
    request.update(index=1, expected_stable_id='unit:d1e2f3a4')
    assert not worker.handle_request(request, api)['ok']
    assert not any(feature == 0x1814 and fn == 0x10 for feature, fn, _, _ in calls)
