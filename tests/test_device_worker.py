"""Hardware-free tests of discovery, write validation, and worker isolation."""

import errno
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
        self.close = Mock()
        self.ping = Mock(return_value=True)
        self.battery = Mock(return_value=SimpleNamespace(to_str=lambda: "Battery: 82% (discharging)"))
        self.mappings = {80: 80, 81: 81}
        self.keys = KeyList(SimpleNamespace(cid=cid, _mapped_to=999, _mapping_flags=None) for cid in self.mappings)
        self.feature_request = Mock(side_effect=self._feature_request)

    def _feature_request(self, _feature, function, *data):
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
            changeable, min_value, max_value = True, 15, 90

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
