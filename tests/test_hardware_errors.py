"""HID++ failures remain identifiable without exposing exception dictionaries."""

import pytest
from logitech_receiver.exceptions import FeatureCallError
from logitech_receiver.hidpp20_constants import ErrorCode

from mx4_linux.hardware_errors import format_hardware_error


def test_vendor_error_keeps_diagnostic_without_guessing_hardware_fault():
    error = FeatureCallError(number=2, request=2893, error=5, params=b"\x00")
    message = format_hardware_error(error)
    assert message == (
        "The mouse rejected this request with a device-specific error. "
        "(HID++ error 0x05, request 0x0B4D)"
    )


@pytest.mark.parametrize(("code", "meaning"), [
    (ErrorCode.UNKNOWN, "unknown error"),
    (ErrorCode.INVALID_ARGUMENT, "request parameter"),
    (ErrorCode.OUT_OF_RANGE, "supported range"),
    (ErrorCode.HARDWARE_ERROR, "hardware error"),
    (ErrorCode.INVALID_FEATURE_INDEX, "requested feature"),
    (ErrorCode.INVALID_FUNCTION, "requested operation"),
    (ErrorCode.BUSY, "busy. Try again"),
    (ErrorCode.UNSUPPORTED, "does not support"),
    (0xFE, "rejected this request"),
])
def test_protocol_errors_keep_their_specific_meaning_and_code(code, meaning):
    message = format_hardware_error(FeatureCallError(error=code, request=0x023D))
    assert meaning in message
    assert f"HID++ error 0x{code:02X}" in message
    assert "request 0x023D" in message


def test_missing_reply_message_is_retained_without_exception_dictionary():
    assert format_hardware_error(FeatureCallError(msg="No reply from device.")) == "No reply from device."


def test_incomplete_protocol_error_gets_readable_fallback():
    assert format_hardware_error(FeatureCallError()) == "The mouse request failed."


def test_non_protocol_error_remains_unchanged():
    assert format_hardware_error(ValueError("Control 80 cannot be diverted")) == "Control 80 cannot be diverted"
    assert format_hardware_error(RuntimeError()) == "RuntimeError"
