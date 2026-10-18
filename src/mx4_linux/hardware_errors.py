"""Readable hardware failures with enough protocol detail for diagnosis."""

from __future__ import annotations


def format_hardware_error(error: Exception) -> str:
    """Translate structured HID++ replies without guessing their root cause.

    Keep Solaar optional until a worker actually needs it, and leave ordinary
    exceptions alone. In particular, Logitech's vendor error does not tell us
    that a device is asleep or that its hardware is broken.
    """
    fallback = str(error) or type(error).__name__
    try:
        from logitech_receiver.exceptions import FeatureCallError
        from logitech_receiver.hidpp20_constants import ErrorCode
    except ImportError:
        return fallback
    if not isinstance(error, FeatureCallError):
        return fallback

    code = error.error
    if isinstance(code, int) and not isinstance(code, bool):
        messages = {
            ErrorCode.UNKNOWN: "The mouse reported an unknown error.",
            ErrorCode.INVALID_ARGUMENT: "The mouse rejected a request parameter.",
            ErrorCode.OUT_OF_RANGE: "The mouse rejected a value outside its supported range.",
            ErrorCode.HARDWARE_ERROR: "The mouse reported a hardware error.",
            ErrorCode.LOGITECH_ERROR: "The mouse rejected this request with a device-specific error.",
            ErrorCode.INVALID_FEATURE_INDEX: "The mouse did not recognize the requested feature.",
            ErrorCode.INVALID_FUNCTION: "The mouse did not recognize the requested operation.",
            ErrorCode.BUSY: "The mouse is busy. Try again.",
            ErrorCode.UNSUPPORTED: "The mouse does not support this request.",
        }
        message = messages.get(code, "The mouse rejected this request.")
        details = [f"HID++ error 0x{code:02X}"]
    else:
        message = error.msg if isinstance(error.msg, str) and error.msg else "The mouse request failed."
        details = []
    request = error.request
    if isinstance(request, int) and not isinstance(request, bool):
        details.append(f"request 0x{request:04X}")
    return message + (" (" + ", ".join(details) + ")" if details else "")
