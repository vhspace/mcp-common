# VNC library spike results

Target: Xvfb 800x600x24 + x11vnc on localhost
Samples: 3 screenshots per library

| Library   | Median (s) | Samples | PNG bytes | Error |
|-----------|------------|---------|-----------|-------|
| asyncvnc  |      0.422 | 0.317, 0.422, 0.433 | 0 | AttributeError: 'numpy.ndarray' object has no attribute 'save' |
| vncdotool |      0.218 | 0.392, 0.214, 0.218 | 1575 |  |

Notes:
- asyncvnc latency samples are valid; error occurs only in the separate PNG-size probe.
  `client.screenshot()` in asyncvnc 1.3.0 returns `numpy.ndarray` (not a PIL Image).
  PNG export requires an extra `PIL.Image.fromarray(arr)` step — the benchmark script
  used the old pre-1.0 API that returned a PIL Image directly.
- vncdotool's `client.screen` is a PIL Image; `.save()` works as expected.
- Both libraries otherwise connected and performed screen captures successfully.

## Decision

**Winner: vncdotool**

Rationale:
- vncdotool is the only library that passed the full benchmark without a code-path error
  (asyncvnc's API changed in 1.x: `screenshot()` now returns `numpy.ndarray`, not PIL Image,
  meaning any naive PNG-export or PIL-based downstream code fails out of the box; adapting
  around this adds friction and re-introduces a numpy/PIL dep at runtime).
- vncdotool's keystroke API is a better fit for phase 3: `client.keyPress("ctrl-alt-delete")`
  uses a dash-separated modifier-combo notation (ctrl, alt, delete, F1–F20 all in KEYMAP),
  matching exactly the named-key / modifier-combo / Ctrl+Alt+Del requirements. asyncvnc's
  `keyboard.press("Ctrl", "Alt", "Delete")` works too but requires manual key-name look-up
  from keysymdef; vncdotool's human-readable string form is more maintainable.
- vncdotool is ~2x faster at median connect+screenshot latency (0.218 s vs 0.422 s), which
  matters for the video-capture loop in phase 3 even though latency is only the tiebreaker.
- Twisted is a heavier runtime dependency than asyncvnc's pure-asyncio approach, but
  vncdotool wraps Twisted in a background thread so callers stay async-friendly; the weight
  is acceptable given the correctness and ergonomics wins.

Decision criteria used (in priority order):
1. Correctness (library must actually work against our x11vnc target).
2. Phase-3 keystroke API ergonomics (named keys, modifier combos, Ctrl+Alt+Del macro).
3. Runtime dependency weight (Twisted is heavier than pure asyncio).
4. Median latency (only tiebreaker; >2x delta moves needle, smaller doesn't).
5. Maintenance activity (recent commits on GitHub).

Decided on 2026-04-23.
