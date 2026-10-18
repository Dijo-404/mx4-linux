#!/usr/bin/env python3
"""Inspect a supplied Options+ artifact copy without executing vendor code.

Only Python's standard library is required. No files are extracted from ASAR,
no resources are evaluated, and no remote service or device is contacted.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct


MAX_HEADER = 16 * 1024 * 1024
MAX_JSON = 16 * 1024 * 1024
MAX_MEMBER = 32 * 1024 * 1024
MAX_REPORT = 50 * 1024
SELECTED_MEMBERS = (
    "package.json", "main.js", "preload.bundle.js", "app.min.js",
    "cc.min.js", "marketplace.min.js", "runtime.min.js", "vendors.min.js",
)
DATA_FILES = (
    "devices.json", "devices/devices_initial.json",
    "devices/devices_0000065.json", "defaults/defaults_control_win.json",
    "defaults/defaults_slot_win.json", "card_presets/card_presets_win.json",
    "card_presets/card_presets_001.json", "strings/en-US.yaml",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(path):
    sha = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(block)
    return {"bytes": path.stat().st_size, "sha256": sha.hexdigest()}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def parse_json(data):
    if len(data) > MAX_JSON:
        raise ValueError("JSON input exceeds inspection limit")
    return json.loads(data, object_pairs_hook=unique_object)


def artifact_file(root, relative):
    """Reject symlink traversal and require files inside the supplied root."""
    path = root
    for component in relative.split("/"):
        if component in ("", ".", "..") or "\\" in component:
            raise ValueError(f"Unsafe resource path: {relative!r}")
        path = path / component
        if path.is_symlink():
            raise ValueError(f"Symlink resource is unsupported: {relative!r}")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError(f"Missing or out-of-root resource: {relative!r}")
    return path


def inspect_asar(path):
    total = path.stat().st_size
    with path.open("rb") as source:
        prefix = source.read(16)
        if len(prefix) != 16:
            raise ValueError("Truncated ASAR header")
        size_pickle, header_size, pickle_payload, json_size = struct.unpack("<4I", prefix)
        if size_pickle != 4 or not 8 <= header_size <= MAX_HEADER:
            raise ValueError("Unsupported ASAR pickle header")
        data_start = 8 + header_size
        if pickle_payload + 4 != header_size or json_size > pickle_payload - 4:
            raise ValueError("Inconsistent ASAR header lengths")
        if data_start > total or 16 + json_size > data_start:
            raise ValueError("ASAR header exceeds file bounds")
        header_bytes = source.read(json_size)
        header = parse_json(header_bytes)
        if not isinstance(header, dict) or not isinstance(header.get("files"), dict):
            raise ValueError("ASAR header has no file tree")
        rows, entries, spans, kinds = [], {}, [], Counter()

        def walk(files, parent="", depth=0):
            if depth > 64 or not isinstance(files, dict):
                raise ValueError("Invalid or excessively nested ASAR tree")
            for name, entry in sorted(files.items()):
                if (not isinstance(name, str) or name in ("", ".", "..")
                        or any(char in name for char in ("/", "\\", "\0"))):
                    raise ValueError("Unsafe ASAR member name")
                if not isinstance(entry, dict):
                    raise ValueError("Invalid ASAR member metadata")
                member = parent + name
                if "files" in entry:
                    walk(entry["files"], member + "/", depth + 1)
                    continue
                if len(rows) >= 100_000:
                    raise ValueError("ASAR member count exceeds inspection limit")
                if "link" in entry:
                    kinds["link_not_followed"] += 1
                    rows.append([member, None, None, 1])
                    continue
                size = entry.get("size")
                if type(size) is not int or size < 0:
                    raise ValueError("Invalid ASAR member size")
                if entry.get("unpacked"):
                    kinds["unpacked_not_read"] += 1
                    rows.append([member, size, None, 2])
                    continue
                offset_text = entry.get("offset")
                if not isinstance(offset_text, str) or not offset_text.isdecimal():
                    raise ValueError("Invalid ASAR member offset")
                offset = data_start + int(offset_text)
                if offset < data_start or offset + size > total:
                    raise ValueError("ASAR member exceeds archive bounds")
                kinds["packed"] += 1
                rows.append([member, size, offset, 0])
                entries[member] = (size, offset, entry)
                if size:
                    spans.append((offset, offset + size))

        walk(header["files"])
        spans.sort()
        if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
            raise ValueError("Overlapping packed ASAR members")
        selected, contents = {}, {}
        for member in SELECTED_MEMBERS:
            if member not in entries:
                raise ValueError(f"Required ASAR member missing or not packed: {member}")
            size, offset, metadata = entries[member]
            if size > MAX_MEMBER:
                raise ValueError("Selected ASAR member exceeds inspection limit")
            source.seek(offset)
            data = source.read(size)
            if len(data) != size:
                raise ValueError("Truncated ASAR member read")
            contents[member] = data
            declared = metadata.get("integrity", {})
            selected[member] = {
                "bytes": size, "sha256": digest(data), "archive_offset": offset,
                "declared_sha256": declared.get("hash") if declared.get("algorithm") == "SHA256" else None,
            }
            selected[member]["declared_hash_matches"] = (
                selected[member]["sha256"] == selected[member]["declared_sha256"]
            )
        inventory = {
            "header_json_sha256": digest(header_bytes), "header_json_bytes": json_size,
            "packed_data_offset": data_start, "member_count": len(rows),
            "kinds": dict(kinds), "extensions": dict(sorted(Counter(Path(row[0]).suffix for row in rows).items())),
            "columns": ["member", "bytes", "archive_offset", "kind"],
            "kind_codes": {"0": "packed", "1": "link_not_followed", "2": "unpacked_not_read"},
            "members": rows, "selected_members": selected,
            "validation": "Names and packed ranges validated; no extraction or link following; packed ranges do not overlap.",
        }
        return inventory, contents


def snippet(data, member, label, anchor, before=0, after=0):
    """Retain byte-addressed literal source, never reconstructed pseudocode."""
    needle = anchor.encode("utf-8")
    offset = data.find(needle)
    if offset < 0:
        raise ValueError(f"Expected static anchor absent: {member}/{label}")
    start, end = max(0, offset - before), min(len(data), offset + len(needle) + after)
    excerpt = data[start:end]
    return {
        "label": label, "member": member, "byte_offset": start,
        "byte_length": len(excerpt), "excerpt_sha256": digest(excerpt),
        "literal_utf8": excerpt.decode("utf-8"),
    }


def inspect_javascript(contents):
    main, preload = contents["main.js"], contents["preload.bundle.js"]
    excerpts = [
        snippet(main, "main.js", "username_md5_pipe_name", "var e=c.userInfo().username", after=158),
        snippet(main, "main.js", "agent_connection_name", 'connectionPath:N("logitech_kiros_agent")', after=22),
        snippet(main, "main.js", "windows_agent_executable", 'process.env.PROGRAMFILES,"/LogiOptionsPlus/logioptionsplus_agent.exe"', before=17, after=7),
        snippet(main, "main.js", "agent_json_protocol", 'u="json",l=t', before=55),
        snippet(main, "main.js", "agent_send_frame", 'e.writeUInt32BE(r.length,0)', before=42, after=226),
        snippet(main, "main.js", "agent_receive_frame", 'case r:w(4)&&(_(4).readUInt32BE(0)', after=222),
        snippet(preload, "preload.bundle.js", "preload_agent_bridge", 'iE.SocketEvent.CREATE_CONNECTION', before=30, after=270),
        snippet(preload, "preload.bundle.js", "preload_exposed_globals", 'r.contextBridge.exposeInMainWorld("electronOn"', after=377),
    ]
    globals_found = re.findall(rb'contextBridge\.exposeInMainWorld\("([^"]+)"', preload)
    package = parse_json(contents["package.json"])
    return {
        "package": {
            "name": package.get("name"), "author": package.get("author"),
            "main": package.get("main"), "version": package.get("version"),
            "electron_dev_dependency": package.get("devDependencies", {}).get("electron"),
            "version_boundary": "Package version is absent if null. Electron is a declared build dependency, not a measured runtime version.",
        },
        "preload_exposed_globals": [name.decode("ascii") for name in globals_found],
        "excerpts": excerpts,
        "static_interpretations": [
            "The agent endpoint helper appends MD5(username) to its base pipe/socket name; Windows uses a named pipe and the alternate path begins /tmp/.",
            "The agent connection uses base name logitech_kiros_agent. The Windows backend launcher refers to PROGRAMFILES/LogiOptionsPlus/logioptionsplus_agent.exe.",
            "The agent writer prefixes a little-endian outer length, big-endian protocol-name length, json bytes, big-endian payload length, then message bytes.",
            "These excerpts identify the agent JSON framing. A separate plugin-service framing implementation also exists and must not be conflated with it.",
            "Preload exposes bridge APIs and sends agent connection/message IPC. Static source establishes code paths, not their execution in a live session.",
        ],
    }


def inspect_envelope(data):
    if len(data) < 12 or data[:4] != bytes.fromhex("21052120"):
        raise ValueError("Candidate resource lacks the observed envelope signature")
    metadata_size = struct.unpack_from("<I", data, 4)[0]
    if metadata_size > MAX_JSON or 12 + metadata_size > len(data):
        raise ValueError("Envelope metadata length exceeds file")
    metadata = parse_json(data[8:8 + metadata_size])
    payload_length_offset = 8 + metadata_size
    payload_size = struct.unpack_from("<I", data, payload_length_offset)[0]
    payload_offset = payload_length_offset + 4
    return {
        "magic_hex": data[:4].hex(), "metadata_length_offset": 4,
        "metadata_offset": 8, "metadata_bytes": metadata_size, "metadata": metadata,
        "payload_length_offset": payload_length_offset, "payload_offset": payload_offset,
        "declared_payload_bytes": payload_size, "actual_payload_bytes": len(data) - payload_offset,
        "payload_length_matches": payload_size == len(data) - payload_offset,
        "boundary": "Opaque encoding not decoded. key-id/file-sha are declarations; hash semantics and MX4 identity are unestablished.",
    }


def pointer_entry(pointer, value):
    return {"json_pointer": pointer, "value": value}


def inspect_data(root):
    data_root = root / "data"
    parsed, opaque, selected = {}, [], {}
    for path in sorted(data_root.rglob("*.json")):
        relative = path.relative_to(root).as_posix()
        safe = artifact_file(root, relative)
        raw = safe.read_bytes()
        try:
            parsed[relative] = parse_json(raw)
        except (ValueError, UnicodeDecodeError):
            opaque.append(relative.removeprefix("data/"))
    for relative in DATA_FILES:
        selected[relative] = identity(artifact_file(root, "data/" + relative))
    devices = parsed["data/devices.json"]
    candidate_uri = "pipeline://logioptionsplus/data/devices/devices_0000065.json"
    references = [pointer_entry(f"/files/{index}", item) for index, item in enumerate(devices["files"]) if item == candidate_uri]
    defaults = parsed["data/defaults/defaults_control_win.json"]["defaults"]
    global_id = "420fd454-0c36-499d-bde4-146823b16147"
    chosen = []
    for group in (global_id, "application_id_adobe_photoshop", "application_id_microsoft_teams"):
        for index, entry in enumerate(defaults[group]["defaultAssignment"]):
            if entry.get("id") in {"82", "83", "86", "195", "196"}:
                chosen.append(pointer_entry(f"/defaults/{group}/defaultAssignment/{index}", entry))
    card = parsed["data/card_presets/card_presets_win.json"]["cards"][159]
    if card.get("id") != "card_global_presets_trigger_haptic_waveform":
        raise ValueError("Expected waveform card changed position")
    pattern = re.compile(r"b042|b048|master[ _-]?4|force.?sens|force.?threshold|haptic", re.I)
    hits = []

    def search(value, filename, pointer=""):
        if isinstance(value, dict):
            for key, child in value.items():
                child_pointer = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
                if pattern.search(key):
                    hits.append({"file": filename, "pointer": child_pointer, "kind": "key"})
                search(child, filename, child_pointer)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                search(child, filename, pointer + "/" + str(index))
        elif (type(value) is int and value == 416) or (isinstance(value, str) and (value == "416" or pattern.search(value))):
            hits.append({"file": filename, "pointer": pointer, "kind": "value", "value": value})

    for filename, value in parsed.items():
        search(value, filename.removeprefix("data/"))
    localization_path = artifact_file(root, "data/strings/en-US.yaml")
    localization_keys = {
        "MACROS_DEVICE_ALERT", "MACROS_DEVICE_ALERT_DEVICE_IS_NOT_ACTIVE",
        "WINDOWS_HAPTIC_TITLE", "WINDOWS_HAPTIC_DESCRIPTION", "WINDOWS_HAPTIC_OPEN_SETTINGS_LINK",
    }
    localization = []
    for number, line in enumerate(localization_path.read_text(encoding="utf-8").splitlines(), 1):
        key, separator, _ = line.partition(":")
        if separator and key in localization_keys:
            localization.append({"line": number, "key": key, "literal": line})
    return {
        "selected_file_identities": selected,
        "json_inventory": {
            "json_named_files": len(parsed) + len(opaque), "parsed_json_files": len(parsed),
            "opaque_files": len(opaque), "opaque_paths": opaque,
            "initial_device_entries": len(parsed["data/devices/devices_initial.json"]["devices"]),
        },
        "candidate_definition": {"file": "devices/devices_0000065.json", "index_references": references,
            "envelope": inspect_envelope(artifact_file(root, "data/devices/devices_0000065.json").read_bytes())},
        "generic_control_defaults": {"file": "defaults/defaults_control_win.json", "entries": chosen,
            "boundary": "Generic/app assignment declarations; not proven MX4 defaults or live settings."},
        "generic_waveform_card": {"file": "card_presets/card_presets_win.json", "entry": pointer_entry("/cards/159", card),
            "boundary": "Presenter-tagged card ID17 is not established as an MX4 physical waveform ID."},
        "decoded_json_search": {
            "case_insensitive_pattern": pattern.pattern, "also_matches_scalar_control_id": 416,
            "hits": hits, "boundary": "Keys/string values and exact scalar CID416 only; excludes opaque resource contents and binary code.",
        },
        "localization": {"file": "strings/en-US.yaml", "lines": localization,
            "boundary": "Packaged UI messages establish labels/concepts, not enabled runtime functionality or HID++ commands."},
    }


def compare_source_hashes(root, known):
    source_file = root / "source-hashes.json"
    if not source_file.exists():
        return {"status": "not_supplied", "comparisons": []}
    source_file = artifact_file(root, "source-hashes.json")
    records = parse_json(source_file.read_bytes())
    mapping = {"resources\\app.asar": "app.asar", "data\\devices.json": "data/devices.json"}
    comparisons = []
    for record in records:
        local = mapping.get(record.get("path"))
        if local:
            actual = known[local]
            comparisons.append({"source_path": record["path"], "local_path": local,
                "declared_sha256": record.get("sha256"), "declared_bytes": record.get("size"),
                "hash_matches": actual["sha256"] == record.get("sha256"),
                "size_matches": actual["bytes"] == record.get("size")})
    return {"status": "compared_supplied_snapshot", "snapshot": identity(source_file),
        "comparisons": comparisons,
        "boundary": "Compares local bytes to operator-supplied remote hash declarations; does not authenticate the publisher or reacquire the remote host."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_directory", type=Path, help="Local copy containing app.asar and data/")
    parser.add_argument("--output", type=Path, required=True, help="New JSON evidence report path outside the artifact copy")
    args = parser.parse_args()
    root = args.artifact_directory.resolve(strict=True)
    if not root.is_dir() or args.output.resolve().is_relative_to(root):
        parser.error("Input must be a directory; output must be outside the artifact copy")
    try:
        archive = artifact_file(root, "app.asar")
        inventory, contents = inspect_asar(archive)
        archive_identity = identity(archive)
        data = inspect_data(root)
        report = {
            "schema_version": 1, "analysis_type": "local_static_stdlib",
            "producer": {"name": "inspect_resources.py", "sha256": digest(Path(__file__).read_bytes())},
            "archive": {"file": "app.asar", **archive_identity, **inventory},
            "javascript": inspect_javascript(contents), "data": data,
            "source_hash_verification": compare_source_hashes(root, {
                "app.asar": archive_identity, "data/devices.json": data["selected_file_identities"]["devices.json"]}),
            "limitations": [
                "No proprietary code execution, extraction, network requests, device commands, runtime observation, or resource decoding is performed by this script.",
                "Complete ASAR member metadata inventory; only selected packed members are read and hashed. Archive integrity declarations are unauthenticated metadata.",
                "Local copied artifacts may differ from another installation/version. Only supplied remote hash snapshot comparisons establish copy agreement.",
                "Static literals/code paths do not prove live execution, enabled features, packet observations, device support, or hardware behavior.",
                "Numbered device resources and card_presets_001 remain opaque. The candidate0000065 filename does not establish MX4 identity, force thresholds, defaults, or waveform mappings.",
                "JSON parsing is bounded to16MiB/file, ASAR headers to16MiB, selected members to32MiB and member count100000. Unsupported/truncated archives raise errors.",
            ],
        }
        encoded = (json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) >= MAX_REPORT:
            raise ValueError(f"Evidence report exceeds50KiB budget: {len(encoded)} bytes")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(encoded)
    except (OSError, ValueError, TypeError, KeyError, struct.error, RecursionError) as error:
        parser.exit(1, f"Inspection failed: {error}\n")
    print(f"Wrote {len(encoded)} bytes; {inventory['member_count']} ASAR members; no vendor code executed.")


if __name__ == "__main__":
    main()
