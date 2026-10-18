#!/usr/bin/env python3
"""Reproduce execution-free ZIP/plist/Mach-O observations with Python stdlib.

These are local-tool observations, not REA Evidence records. No archive member
is executed, and no URL found in the archive is contacted.
"""

import argparse
import collections
import hashlib
import json
import pathlib
import plistlib
import re
import struct
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def cstring(data):
    return data.split(b"\0", 1)[0].decode("utf-8", "replace")


def macho_slices(data):
    """Decode declared fat headers, load dependencies and nlist64 symbols only."""
    assert data[:4] == b"\xca\xfe\xba\xbe", "Expected FAT_MAGIC"
    count = struct.unpack_from(">I", data, 4)[0]
    slices = []
    for index in range(count):
        cpu, sub, base, size, align = struct.unpack_from(">IIIII", data, 8 + 20 * index)
        image = data[base:base + size]
        assert len(image) == size and image[:4] == b"\xcf\xfa\xed\xfe"
        magic, image_cpu, image_sub, kind, ncmds, cmdbytes, flags, reserved = struct.unpack_from("<8I", image)
        cursor = 32
        dependencies, sections, symbols, uuid = [], [], [], None
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack_from("<II", image, cursor)
            assert cmdsize >= 8 and cursor + cmdsize <= 32 + cmdbytes
            if cmd in (0xC, 0x80000018, 0x8000001F, 0x80000023):
                name_offset = struct.unpack_from("<I", image, cursor + 8)[0]
                dependencies.append({"command": hex(cmd), "name": cstring(image[cursor + name_offset:cursor + cmdsize])})
            elif cmd == 0x1B:
                uuid = image[cursor + 8:cursor + 24].hex()
            elif cmd == 0x19:
                section_count = struct.unpack_from("<I", image, cursor + 64)[0]
                for j in range(section_count):
                    section = cursor + 72 + 80 * j
                    name, segment, address, length, offset = struct.unpack_from("<16s16sQQI", image, section)
                    sections.append({"segment": cstring(segment), "section": cstring(name), "address": hex(address), "size": length, "file_offset": base + offset})
            elif cmd == 0x2:
                symoff, nsyms, stroff, strsize = struct.unpack_from("<4I", image, cursor + 8)
                for j in range(nsyms):
                    strx, ntype, sect, desc, value = struct.unpack_from("<IBBHQ", image, symoff + 16 * j)
                    assert strx < strsize or strx == 0
                    string_start = stroff + strx
                    string_end = image.find(b"\0", string_start, stroff + strsize)
                    assert string_end >= string_start
                    symbols.append(image[string_start:string_end].decode("utf-8", "replace"))
            cursor += cmdsize
        topic_symbols = {}
        for topic in ("hidpp", "IOHID", "haptic", "waveform", "pointer_speed", "download_compressed_depot", "AppDelegate", "pipeline"):
            matches = [name for name in symbols if topic.casefold() in name.casefold()]
            topic_symbols[topic] = {"count": len(matches), "sample": matches[:12], "sample_limit": 12}
        slices.append({"architecture": {0x1000007: "x86_64", 0x100000C: "arm64"}.get(cpu, hex(cpu)), "cpu_subtype": sub, "offset": base, "size": size, "sha256": digest(image), "uuid": uuid, "dependencies": dependencies, "sections": sections, "symbol_count": len(symbols), "topic_symbols": topic_symbols})
    return slices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    archive = args.archive.read_bytes()
    prefix = "logioptionsplus_installer.app/Contents/"
    with zipfile.ZipFile(args.archive) as z:
        entries = [{"path": i.filename, "size": i.file_size, "compressed_size": i.compress_size, "directory": i.is_dir()} for i in z.infolist()]
        plist_bytes = z.read(prefix + "Info.plist")
        binary = z.read(prefix + "MacOS/logioptionsplus_installer")
        strings = [(m.start(), m.group().decode("ascii")) for m in re.finditer(rb"[\x20-\x7e]{6,}", binary)]
        queries = {
            "hidpp": r"hidpp|hid\+\+",
            "IOHID": r"IOHID",
            "SmartShift": r"smartshift",
            "MX_Master_4": r"\bMX[ _-]*Master[ _-]*4\b|\bMX[ _-]*4\b",
            "haptic_routes": r"^/haptic",
            "mouse_routes": r"^/mouse/",
            "gesture_routes": r"^/gestures/",
            "bootstrapper": r"^Starting downloading updates through pipeline\.$|^Downloading (LZMA-compressed )?depot|^Finished downloading depot|^\-\[AppDelegate _download\]$|^logioptionsplus_(agent|updater)$|^pipeline://logioptionsplus/data/devices\.json$|^https://updates[^/\s]*$",
        }
        hits = {}
        for name, pattern in queries.items():
            regex = re.compile(pattern, re.IGNORECASE)
            rows = [{"file_offset": offset, "text": value} for offset, value in strings if regex.search(value)]
            hits[name] = {"regex": pattern, "count": len(rows), "matches": rows}
        resources = [i for i in z.infolist() if i.filename.startswith(prefix + "Resources/") and not i.is_dir()]
        report = {
            "evidence_kind": "ordinary-python-static-observations",
            "archive": {"source_path": str(args.archive.resolve()), "size": len(archive), "sha256": digest(archive), "entry_count": len(entries), "declared_uncompressed_bytes": sum(i["size"] for i in entries)},
            "entries": entries,
            "resource_summary": {"count": len(resources), "suffix_counts": dict(sorted(collections.Counter(pathlib.PurePosixPath(i.filename).suffix for i in resources).items()))},
            "plist": {"path": prefix + "Info.plist", "sha256": digest(plist_bytes), "parsed": plistlib.loads(plist_bytes)},
            "binary": {"path": prefix + "MacOS/logioptionsplus_installer", "size": len(binary), "sha256": digest(binary), "ascii_string_rule": "all maximal runs of ASCII bytes 0x20-0x7e, minimum length 6", "ascii_string_count": len(strings), "searches": hits, "slices": macho_slices(binary)},
            "limitations": [
                "These records were produced by the accompanying Python stdlib script, not a REA provider.",
                "ZIP member inventory does not interpret bytes outside archive entries.",
                "Mach-O parsing covers this supplied FAT_MAGIC/nlist64 layout, declared dependency loads, sections and symbol names; it performs no disassembly, control-flow or reference recovery.",
                "String matches are byte observations; they do not establish execution, handlers, API availability, device support or hardware protocol semantics.",
                "No-match results apply only to the listed ASCII string rule and regexes; compiled, encoded or obfuscated implementations may leave no matching string.",
                "No target code, installer, network endpoint or hardware command was executed.",
                "Apple code signatures and publisher authenticity were not verified.",
            ],
        }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    report["local_observation_id"] = "local_sha256_" + digest(canonical)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"output": str(args.output), "local_observation_id": report["local_observation_id"], "archive_sha256": report["archive"]["sha256"], "binary_sha256": report["binary"]["sha256"]}))


if __name__ == "__main__":
    main()
