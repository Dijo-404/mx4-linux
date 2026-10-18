#!/usr/bin/env python3
"""Pure preparation wrapper. No GTK imports, display access, or child on import/plan.

The owned GTK hook is prepared but unexecuted. The false-by-default review gate
must be changed by root after source review and exclusive-test coordination.
"""
import argparse
import json
from pathlib import Path

ROOT_REVIEW_COMPLETE = False


def plan():
    return {
        "status": "temporary native tooling; root hook-review gate is closed",
        "root_review_gate": ROOT_REVIEW_COMPLETE,
        "layer_order": {"shield": "TOP", "production_ring": "OVERLAY",
                        "same_layer_creation_order_is_assumed": False},
        "animation_policy": {"normal": True, "reduced_motion": False,
                             "effective_property_readback_recorded": True,
                             "original_process_local_value_restored": True},
        "paint_readiness": {"observer_installed_before_each_show": True,
                            "fresh_final_allocation_after_paint_required": True,
                            "natural_frame_preferred": True,
                            "probe_redraw_fallback_separately_recorded": True,
                            "own_gdk_connection_sync_before_client": True},
        "hook": str(Path(__file__).resolve().with_name("owned_gtk_hook.py")),
        "client": str(Path(__file__).resolve().with_name("virtual-pointer-client")),
        "client_was_executed_during_preparation": False,
        "client_argv_template": [
            "virtual-pointer-client", "--run", "--owner-pid", "<owned-harness-pid>",
            "--output", "<exact-wl_output.name>", "--extent", "<logical-width>",
            "<logical-height>", "--bounds", "<logical-left>", "<logical-top>",
            "<logical-width>", "<logical-height>",
        ],
        "stdin_protocol": [
            "M <next-sequence> <output-local-x-times-256> <output-local-y-times-256>",
            "D <next-sequence>", "U <next-sequence>", "Q <next-sequence>",
        ],
        "limits": {
            "commands": 128, "whole_ms": 30000, "idle_ms": 3000,
            "compositor_sync_ms": 700, "release_cleanup_ms": 400,
        },
        "required_before_client_launch": [
            "Root coordinated exclusive focus-sensitive test period",
            "Private XDG config and state established before app imports",
            "Every action and branch validated as type command, argv /usr/bin/true",
            "One explicit current output and matching GDK monitor",
            "Owned opaque shield covers that output above unrelated windows",
            "Exact production ring surface is above shield and mapped on same monitor",
            "Fresh matching after-paint observed for that show; GTK own-display sync completed",
            "Prior exact focus identity saved in memory",
        ],
        "required_before_every_D": [
            "One M sent alone and corresponding compositor-only ACK received",
            "Actual native GTK motion observed at intended local ring coordinate",
            "Current exact ring/shield surface identity, output, stacking and intended hit verified",
            "No intervening context loss, lifecycle change, or unexpected shield event",
        ],
        "ack_scope": "Wayland compositor sync only; never substitutes for GTK event/ownership observation",
        "cleanup": [
            "Request U before destroying any owned surface, even when a case fails",
            "Client attempts BTN_LEFT up/frame/flush before destroy/disconnect on every exit",
            "Do not SIGKILL client or use subprocess timeout kill while button might be down",
            "Close pipe, collect cleanup acknowledgement, reap client, then close owned surfaces",
            "Restore previous exact focus if it still exists; never broad app activation",
        ],
        "integration_hook": "owned_gtk_hook.run_owned_gtk_probe; ROOT_REVIEW_COMPLETE remains False",
        "gesture_constants": {"threshold_logical": 10, "detent_logical": 12, "max_emissions_per_motion": 4},
        "proof_scope": "Gesture callbacks only; no executing runner/controller/HID/clipboard",
        "preview_contract": "Preview callbacks may occur with preview=True; effects always zero",
    }


def integer_list(value):
    values = [int(part) for part in value.split(",")]
    if not values or len(values) != len(set(values)) or any(not 75 <= item <= 150 for item in values):
        raise argparse.ArgumentTypeError("Scales must be distinct integers from 75 to 150")
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--plan", action="store_true", help="Print the pure preparation plan (default)")
    group.add_argument("--run", action="store_true", help="Reserved for future reviewed owned-GTK integration")
    parser.add_argument("--coordinated", action="store_true", help="Root coordinated the exclusive native period")
    parser.add_argument("--project", default="/home/dj/Projects/mx4-linux")
    parser.add_argument("--output", help="Explicit current output connector; required for run")
    parser.add_argument("--expected-ring-sha256", default="", help="Reviewed production ring.py SHA256")
    parser.add_argument("--evidence", default="", help="New sanitized evidence file under /tmp")
    parser.add_argument("--scales", type=integer_list, default=[75, 100, 150])
    parser.add_argument("--cases", default="plain,vertical,right,left,reverse,pinned-root,child,reject,huge,hide-cancel,after-cancel,preview")
    parser.add_argument("--threshold", type=float, default=10)
    parser.add_argument("--detent", type=float, default=12)
    parser.add_argument("--max-emissions", type=int, default=4)
    parser.add_argument("--reduced-motion", action="store_true")
    parser.add_argument("--include-hide-cancel", action="store_true", help="Reviewed intentional-unmap cancellation setup")
    args = parser.parse_args(argv)
    if args.run:
        if not ROOT_REVIEW_COMPLETE:
            parser.error("Root hook-review gate is closed; no display/client/input operation started")
        if not args.coordinated or not args.output or not args.evidence:
            parser.error("Explicit coordination, output and new /tmp evidence are required")
        args.cases = args.cases.split(",")
        args.root_review_complete = ROOT_REVIEW_COMPLETE
        from owned_gtk_hook import ProbeFailure, run_owned_gtk_probe
        try:
            result = run_owned_gtk_probe(args)
        except BaseException as error:
            print(json.dumps({"passed": False, "failure": str(error) if isinstance(error, ProbeFailure) else type(error).__name__}))
            return 1
        print(json.dumps(result, indent=2))
        return 0
    print(json.dumps(plan(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
