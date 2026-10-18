# Local catalog utilities

These are operating Linux counterparts to the reference catalog. Catalog creation, validation, import and dry-run do not read the clipboard or clock, create input devices, show utility windows, play sound or change preferences. The catalog's optional availability hints are separate from executable action documents. Input helpers, application key bindings, Wayland services and audio output can still fail at execution.

## Finite steps

Existing version 1 documents remain valid. New step forms are:

```json
{"type":"date","format":"iso-datetime"}
{"type":"clipboard","operation":"upper"}
{"type":"local","operation":"switch-profile","profile_id":"work"}
{"type":"local","operation":"adapt-to-app","enabled":false}
{"type":"local","operation":"stopwatch"}
{"type":"local","operation":"tap-tempo"}
{"type":"local","operation":"bell"}
```

Date formats are `iso-date` (`YYYY-MM-DD`), `iso-time` (`HH:MM:SS`), `iso-datetime` (local date/time with UTC offset), `day-of-year` (`001`–`366`), `iso-week` (`01`–`53`), and `moon-phase`. They are generated at execution and use the same guarded Unicode insertion path as text. A date result reports the step type, without generated text. The controller applies its input guard to the branch actually selected, including later Multi-toggle branches, and cancels date/clipboard operations when their originating application loses focus.

`moon-phase` inserts the nearest of eight English phase names. It is a mean-lunation approximation using the new Moon at **2000-01-06 18:14 UT** from [NASA's phase table](https://eclipse.gsfc.nasa.gov/phase/phases1901.html), and a **29.530589 day** mean synodic month from [NASA's lunar periodicity description](https://eclipse.gsfc.nasa.gov/LEsaros/LEperiodicity.html). Real lunations vary. This calculation does not predict precise phase times or serve as an astronomical ephemeris.

Clipboard operations are `clear`, `plain`, `lower`, `upper`, `title`, `swapcase`, `sentence-case`, `url-encode`, `url-decode`, `html-encode`, and `html-decode`. They update the **regular clipboard only** and do not paste or touch the primary selection. `plain` rewrites an explicitly offered plain-text MIME; it does not strip markup from HTML. Sentence case lowercases the text and capitalizes letters at the start or immediately after `.`, `!` or `?` followed by whitespace. It is deterministic rather than language-aware.

Clipboard helpers receive literal argv and content through stdin. UTF-8 must be valid; NUL is rejected. Input and output are each limited to 262144 bytes and 65536 characters. Trailing newlines are preserved. URL decode rejects malformed percent escapes or invalid UTF-8; HTML decode requires complete known entities and valid non-NUL Unicode scalar values. Each helper has a two-second execution limit, bounded stdout and cancellation checks; stderr is discarded. Failed/cancelled helper processes are terminated and reaped. Errors and results contain no clipboard contents. A write already accepted by the clipboard service cannot be rolled back; the app never restores stale clipboard contents. Clipboard read/transform/write is not atomic against another application's concurrent clipboard change.

## Local state and utilities

Profile switching and Adapt to App require the native controller. A standalone runner reports that local utilities are unavailable. The controller preflights the selected branch's local assignment steps against the current document before any step executes. It then performs each mutation against a fresh document under the existing configuration file lock. Concurrent editor changes are preserved; deletion or incompatible scope fails before that mutation. If a later step fails after a committed mutation, the completed-step count describes the partial run; there is no rollback claim.

With Adapt enabled, an exact originating app profile can become active even if no exact override was previously selected. A General profile is selectable only while no active exact override hides it. With Adapt disabled, only General profiles can be selected. Actual focused app/window identity is retained for safety and action conditions. The selected action and target remain pinned; changes affect future assignments through the worker's configure/ACK mechanism, without stopping integration or cancelling the requesting routine.

Stopwatch is a small local native window with Start/Pause and Reset. It uses monotonic time and pauses when hidden or closed. Its update source is removed on hide, close and controller shutdown. Tap Tempo is a local BPM counterpart, calculated from the last eight taps; a pause longer than three seconds starts a new measurement. It is not a metronome. The system bell is a request to the desktop; the app never claims that a sound was audible. Play local sound launches literal `pw-play -- /absolute/file`; completion means the process was launched, not confirmed audio playback.

## Session-only Multi-toggle

An action may add `toggle_steps`, containing one to seven additional validated step lists:

```json
{
  "label":"Copy then paste",
  "steps":[{"type":"shortcut","keys":["CTRL","C"]}],
  "toggle_steps":[[{"type":"shortcut","keys":["CTRL","V"]}]]
}
```

Ordinary execution starts with `steps`, then cycles through the extra branches. The index advances only after the entire selected branch succeeds. Preview, skipped conditions, errors, cancellation and adjustment execution do not advance it. Any change to the action definition resets its cycle, including a change made during a running invocation. That invocation may finish its pinned branch, but cannot advance the reset cycle. A ring opened before an edit also retains its earlier effects without advancing a later cycle, even if the old definition is restored or the action is removed and recreated. State belongs to the controller session and is not persisted. Every main, increase, decrease and toggle branch uses the same validator and profile-reference iterator. There are no nested toggles or recursive action graphs. The existing editor preserves unfamiliar utility steps through JSON editing, plus icons, adjustments and toggle branches.
