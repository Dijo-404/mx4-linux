#!/usr/bin/env python3
"""Build temporary client and run only its display-free parser test executable."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, default=Path(
        "/home/dj/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/"
        "wayland-protocols-wlr-0.3.12/wlr-protocols/unstable/"
        "wlr-virtual-pointer-unstable-v1.xml"))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    for kind, output in (("client-header", "virtual-pointer-client.h"),
                         ("private-code", "virtual-pointer-protocol.c")):
        subprocess.run(["wayland-scanner", kind, str(args.xml), str(root / output)], check=True)
    flags = shlex.split(subprocess.check_output(
        ["pkg-config", "--cflags", "--libs", "wayland-client"], text=True))
    common = ["gcc", "-std=c11", "-Wall", "-Wextra", "-Wpedantic", "-Werror", "-O2"]
    subprocess.run(common + [str(root / "client.c"), str(root / "command.c"),
        str(root / "virtual-pointer-protocol.c"), *flags,
        "-o", str(root / "virtual-pointer-client")], check=True)
    subprocess.run(common + ["-fsanitize=address,undefined", "-fno-omit-frame-pointer",
        str(root / "command.c"), str(root / "parser-test.c"),
        "-o", str(root / "parser-tests")], check=True)
    # This executable links only the pure parser/guard. It has no Wayland/display code.
    subprocess.run([str(root / "parser-tests")], check=True)
    # The client is deliberately never executed, not even with a --help/self-check flag.
    result = {
        "client_compiled": True, "client_executed": False,
        "pure_parser_sanitized_tests_passed": True,
        "protocol_xml": str(args.xml),
        "protocol_xml_sha256": hashlib.sha256(args.xml.read_bytes()).hexdigest(),
        "client_sha256": hashlib.sha256((root / "virtual-pointer-client").read_bytes()).hexdigest(),
    }
    (root / "preparation-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
