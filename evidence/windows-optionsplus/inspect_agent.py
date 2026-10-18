#!/usr/bin/env python3
"""Recover bounded, static protobuf schema metadata from an Options+ agent PE.

This stdlib-only tool reads bytes; it does not load or execute the input. It
recognizes ordinary serialized FileDescriptorProto records ending in proto3.
It is not a general executable unpacker or a protobuf code generator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct


MAX_DESCRIPTOR = 512 * 1024
MAX_FIELDS = 20_000
MAX_DEPTH = 24
TYPE_NAMES = {
    1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32",
    6: "fixed64", 7: "fixed32", 8: "bool", 9: "string", 10: "group",
    11: "message", 12: "bytes", 13: "uint32", 14: "enum",
    15: "sfixed32", 16: "sfixed64", 17: "sint32", 18: "sint64",
}
SELECTED = {
    "logi/common_protocol/message.proto": None,
    "logi/protocol/force_sensing.proto": None,
    "logi/protocol/haptics.proto": None,
    "logi/protocol/radial_menu.proto": None,
    "logi/protocol/card_register.proto": None,
    "logi/protocol/application.proto": {"Configuration"},
    "logi/protocol/devices.proto": {
        "ResourceList", "Resource", "ResourcesList", "Resources", "_Resources",
        "DeviceIDAndModel", "DeviceIDModelAndLayer", "DeviceDepotAndModel",
        "DeviceDepotModelAndLayer", "DevioDeviceInfo", "OptionsDeviceInfo",
        "DivertStateRequest", "SpecialKeysDivertRequest", "TriggerEvent",
    },
    "logi/protocol/profiles_v2.proto": {
        "Card", "Assignment", "Slot", "AssignmentQueryInfo", "DeviceSharedContext",
    },
    "logi/protocol/lps.proto": {
        "HandshakeRequest", "HandshakeResponse", "StartDeviceRequest",
        "StartDeviceResponse", "ExecuteAction", "ExecuteRegisteredAction",
        "Action", "RegisterActionRequest", "RegisterActionResponse",
        "LpsAssignment", "ShowOverlayEventData", "HideOverlayEventData",
        "AdjustmentValueData", "ControlAssignment", "NestedControlAssignments",
        "AssignmentsChangedData", "AssignmentChangedData",
        "AdjustmentValueChangedData", "StartRadialMenuDeviceRequestParameters",
        "SetActionsRingScaleFactor",
    },
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for index in range(10):
        if offset >= len(data):
            raise ValueError("truncated varint")
        byte = data[offset]
        offset += 1
        if index == 9 and byte > 1:
            raise ValueError("varint exceeds uint64")
        value |= (byte & 127) << (7 * index)
        if byte < 128:
            return value, offset
    raise ValueError("overlong varint")


def fields(data: bytes) -> list[tuple[int, int, int | bytes]]:
    result = []
    offset = 0
    while offset < len(data):
        tag, offset = varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if not 1 <= number < 1 << 29:
            raise ValueError("invalid protobuf field number")
        if wire == 0:
            value, offset = varint(data, offset)
        else:
            if wire == 1:
                length = 8
            elif wire == 2:
                length, offset = varint(data, offset)
            elif wire == 5:
                length = 4
            else:
                raise ValueError("unsupported protobuf wire type")
            if length > len(data) - offset:
                raise ValueError("truncated fixed-width or length-delimited field")
            value = data[offset:offset + length]
            offset += length
        result.append((number, wire, value))
        if len(result) > MAX_FIELDS:
            raise ValueError("descriptor field limit exceeded")
    return result


def one(rows: list, number: int, default=None):
    found = [(wire, value) for field, wire, value in rows if field == number]
    if len(found) > 1:
        raise ValueError("duplicate singular descriptor field")
    if not found:
        return default
    wire, value = found[0]
    return value.decode("utf-8") if wire == 2 else value


def enum_schema(data: bytes) -> dict:
    rows = fields(data)
    values = {}
    for number, _, value in rows:
        if number == 2:
            item = fields(value)
            raw = one(item, 2, 0)
            signed = raw & 0xFFFFFFFF
            if signed & 0x80000000:
                signed -= 1 << 32
            values[one(item, 1)] = signed
    return {"name": one(rows, 1), "values": values}


def message_schema(data: bytes, depth: int = 0) -> dict:
    if depth > MAX_DEPTH:
        raise ValueError("descriptor nesting limit exceeded")
    rows = fields(data)
    result = {"name": one(rows, 1), "fields": []}
    nested, enums = [], []
    for number, _, value in rows:
        if number == 2:
            item = fields(value)
            type_id = one(item, 5)
            label = one(item, 4)
            field_number = one(item, 3)
            if type_id not in TYPE_NAMES or label not in (1, 2, 3):
                raise ValueError("invalid field type or label")
            if not isinstance(field_number, int) or not 1 <= field_number < 1 << 29:
                raise ValueError("invalid declared field number")
            field = {"number": field_number, "name": one(item, 1),
                     "type": one(item, 6, TYPE_NAMES[type_id])}
            if type_id in (11, 14):
                field["kind"] = TYPE_NAMES[type_id]
            if label == 3:
                field["repeated"] = True
            if label == 2:
                field["required"] = True
            for key, index in (("default", 7), ("oneof_index", 9),
                               ("json_name", 10), ("proto3_optional", 17)):
                found = one(item, index)
                if found is not None:
                    field[key] = found
            result["fields"].append(field)
        elif number == 3:
            nested.append(message_schema(value, depth + 1))
        elif number == 4:
            enums.append(enum_schema(value))
        elif number == 7:
            options = fields(value)
            if any(field == 7 and wire == 0 and flag == 1
                   for field, wire, flag in options):
                result["map_entry"] = True
    if nested:
        result["nested"] = nested
    if enums:
        result["enums"] = enums
    return result


def pe_identity(data: bytes) -> dict:
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError("input is not a DOS/PE image")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if pe > len(data) - 26 or data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("invalid PE header bounds or signature")
    machine, count, timestamp = struct.unpack_from("<HHI", data, pe + 4)
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    if optional_size < 32 or optional_size > len(data) - pe - 24:
        raise ValueError("invalid optional header size")
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic != 0x20B:
        raise ValueError("only PE32+ images are supported")
    start = optional + optional_size
    if count > 96 or count * 40 > len(data) - start:
        raise ValueError("invalid PE section table bounds")
    sections = []
    for index in range(count):
        row = start + index * 40
        name = data[row:row + 8].split(b"\0")[0].decode("ascii", "replace")
        virtual_size, rva, size, offset = struct.unpack_from("<IIII", data, row + 8)
        if size > len(data) - offset:
            raise ValueError("PE section exceeds file bounds")
        sections.append({"name": name, "rva": hex(rva), "virtual_size": virtual_size,
                         "file_offset": hex(offset), "file_size": size})
    return {"format": "PE32+", "machine": hex(machine),
            "coff_timestamp": timestamp,
            "image_base": hex(struct.unpack_from("<Q", data, optional + 24)[0]),
            "entry_rva": hex(struct.unpack_from("<I", data, optional + 16)[0]),
            "sections": sections}


def descriptors(data: bytes) -> list[dict]:
    recovered = []
    seen = set()
    pattern = rb"logi/(?:protocol|common_protocol)/[A-Za-z0-9_/]{1,256}\.proto"
    for match in re.finditer(pattern, data):
        for start in range(max(0, match.start() - 4), match.start() - 1):
            if data[start] != 10 or start in seen:
                continue
            try:
                size, begin = varint(data, start + 1)
                if begin != match.start() or begin + size != match.end():
                    continue
                if data[match.end():match.end() + 1] != b"\x12":
                    continue
                end = data.find(b"\x62\x06proto3", match.end(),
                                min(len(data), start + MAX_DESCRIPTOR))
                if end < 0:
                    continue
                end += 8
                raw = data[start:end]
                rows = fields(raw)
                if rows[-1] != (12, 2, b"proto3"):
                    continue
                name = one(rows, 1)
                if name != match[0].decode("ascii"):
                    continue
                entry = {"name": name, "package": one(rows, 2),
                         "file_offset": hex(start), "end_offset_exclusive": hex(end),
                         "byte_length": end - start, "sha256": digest(raw),
                         "dependencies": [v.decode("utf-8") for n, _, v in rows if n == 3],
                         "messages": [message_schema(v) for n, _, v in rows if n == 4],
                         "enums": [enum_schema(v) for n, _, v in rows if n == 5]}
                recovered.append(entry)
                seen.add(start)
            except (ValueError, UnicodeError, IndexError, TypeError):
                # A filename or dependency reference alone is not a descriptor.
                continue
    return recovered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--all-descriptors-output", type=Path)
    args = parser.parse_args()
    data = args.exe.read_bytes()
    identity = {"file_name": args.exe.name, "size": len(data), "sha256": digest(data),
                **pe_identity(data)}
    all_descriptors = descriptors(data)
    selected = []
    for descriptor in all_descriptors:
        if descriptor["name"] not in SELECTED:
            continue
        names = SELECTED[descriptor["name"]]
        entry = dict(descriptor)
        entry["messages"] = [m for m in descriptor["messages"]
                             if names is None or m["name"] in names]
        entry["message_selection"] = "all" if names is None else "selected top-level messages"
        entry["total_top_level_messages"] = len(descriptor["messages"])
        selected.append(entry)
    result = {"artifact": identity, "method": "static bounded protobuf descriptor decoding",
              "bounds": {"max_descriptor_bytes": MAX_DESCRIPTOR,
                         "max_fields_per_message": MAX_FIELDS, "max_nesting": MAX_DEPTH,
                         "offset_convention": "original file offsets; end is exclusive"},
              "limitations": [
                  "Schema presence does not establish runtime registration, invocation or MX4 support.",
                  "No executable code, native handler body or HID++ wire encoding is recovered.",
                  "Search covers matching logi protocol/common_protocol proto3 descriptors only.",
                  "Unmatched, packed, encrypted, proto2 or differently named records may exist.",
                  "Service/extension definitions and most descriptor options are omitted.",
                  "Selected schema references may point to messages omitted from this report.",
                  "Enums are decoded as signed int32; offsets and digests identify whole descriptors.",
              ], "recovered_descriptor_count": len(all_descriptors),
              "descriptors": selected,
              "missing_selected_protocols": sorted(set(SELECTED) - {e["name"] for e in selected})}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    if args.all_descriptors_output:
        args.all_descriptors_output.parent.mkdir(parents=True, exist_ok=True)
        args.all_descriptors_output.write_text(json.dumps(
            {"artifact": identity, "descriptors": all_descriptors}, indent=2) + "\n")
    print(f"Recovered {len(all_descriptors)} descriptors; selected {len(selected)} protocols "
          f"in {args.output} ({args.output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
