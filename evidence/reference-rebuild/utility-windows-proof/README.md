# Native local utility windows

Owner-collected private-demo Wayland check at `83a2607b627f083940a5492d817441bb5cbcdb33`, on 2026-10-19. [Result](result.json), [Stopwatch capture](stopwatch.png), [Tap Tempo capture](tap-tempo.png). Root inspected the sanitized result and both app-only captures.

Both windows mapped. Actual GTK button callbacks advanced/reset the stopwatch and measured/reset Tap Tempo. Hiding and closing the stopwatch removed its timer; controller shutdown destroyed both windows and removed a running timer. Prior exact focus was restored. The proof process exited successfully.

Every saved action was literal `/usr/bin/true`. No synthetic desktop input, HID access, personal clipboard access, sound or bell request occurred. GTK callbacks were emitted by the contained proof rather than physical human taps. The temporary proof source hash is retained in the JSON; its top-level GUI execution script is not shipped as a general replay command. This verifies utility lifecycle at the identified source revision, not the final surrounding application.
