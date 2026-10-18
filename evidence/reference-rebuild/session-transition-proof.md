# Physical reporting transition verification

At commit `cf2247e996bf161fdad3211cd253a96cabe5c502`, the production
`EventSession` and event worker completed five acknowledged transitions on the
real MX Master 4:

| State | Controls | Ring trigger | RAW XY |
|---|---|---|---|
| Manual only | none | null | false |
| Panel trigger | 416 | 416 | true |
| Back trigger | 83 | 83 | true |
| Ordinary panel assignment | 416 | null | false |
| Idle | none | null | false |

Every matching ready/configured acknowledgement was followed by independent
addressed GETs using software ID C, distinct from listener ID D. All six
controls' complete reporting flags and semantic mappings matched their original
state plus exactly the requested diversion/RAW bits. Removed controls returned
to their originals at each transition.

The worker exited with `restored=true`; final independent GETs confirmed all
six originals, the durable reporting journal was clear, and application
configuration and remembered settings were unchanged. There were one ready,
four configured and one stopped event, with zero warnings/errors or physical
button/motion packets. The process exited successfully within the cleanup
deadline. No routine, synthetic input, haptic or host-switch request ran.

The sanitized result is [session-transition-proof.json](session-transition-proof.json).
The byte-exact reviewed harness is [session-transition-proof.py](session-transition-proof.py),
SHA256 `1017909edfc7ab185398d0c08306c4edaac60d2df7fcaf88b3c9465dbde5e60c`.
Its default and `--self-check` modes do not access hardware. Live mode requires
the full reviewed commit to equal HEAD, unchanged relevant source, ordinary
non-optimized Python, normal production ownership locks, one online stable
unit and no pending recovery journal. The original private recovery record is
retained outside the repository; identifiers, originals and configuration
digests are excluded from the public result.

This establishes reporting transitions and restoration through the production
session/worker. It does not establish controller focus, GUI profile workflows,
native drag delivery, thumb-wheel diversion, reconnect, Bluetooth or host
switching. Semantic mappings are not a byte-exact raw HID reply comparison.
