# Optional native gesture verification tools

This directory preserves the exact source tools used for [the native gesture proof](../native-drag-proof.md). It is verification material, not an application runtime dependency. [tested-tooling.json](tested-tooling.json) records source hashes and the executed client hash. The wrapper is archived with `ROOT_REVIEW_COMPLETE = False` after execution; its historical preparation text does not supersede the completed proof record.

The display-free plan and Python checks can run without launching a pointer client:

```sh
python plan.py --plan
python pure-tests.py
python emission-hook-pure-test.py
```

The last command needs PyGObject, imports only GObject, and checks signal ordering in memory. It does not import GTK/GDK or access the display. The other Python checks use only the standard library and mocked process/socket operations.

To compile the client and run the display-free C parser tests, work in a temporary copy with GCC, AddressSanitizer/UndefinedBehaviorSanitizer, pkg-config, the Wayland client development files and `wayland-scanner` installed:

```sh
python build.py --xml wlr-virtual-pointer-unstable-v1.xml
```

Pass the explicit XML argument to avoid the original preparation machine's cache path. The build generates bindings, two executables and `preparation-result.json`. It executes only the parser tests; it never executes the Wayland client. Compiled hashes depend on the toolchain and generated bindings. Generated files are excluded from source control.

Live execution requires a new source review and exclusive test coordination. It temporarily takes focus and sends native pointer input. The closed gate deliberately prevents replay from an ordinary plan/build/test command. The reviewed harness requires private XDG state, an owned opaque shield, exact output and surface checks, no-op fixtures, per-press native motion confirmation, bounded helper cleanup and exact prior focus restoration. Inspect the full code and current application interfaces before adapting it; this archive is not a general pointer automation interface.

The retained [virtual-pointer protocol XML](wlr-virtual-pointer-unstable-v1.xml) contains its upstream MIT license and copyright notice. It was copied byte-for-byte from `wayland-protocols-wlr` 0.3.12's bundled protocol, SHA-256 `3ff6d540be0bc5228195bf072bde42117ea17945a5c2061add5d3cf97d6bb524`. Its upstream source is [swaywm/wlr-protocols](https://github.com/swaywm/wlr-protocols/blob/master/unstable/wlr-virtual-pointer-unstable-v1.xml).
