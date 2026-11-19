# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The colors, in one place: the ones agent-frolic's theme.py settled on, plus this app's own
meanings for two of them.

  • text wears the ink tokens, lightest to faintest: INK, INK2, MUTED;
  • the status colors go on marks only, never on words;
  • WAITING (agent-farm's golden yellow) means one thing: yours to fix, on this Mac;
  • DIVIDER (agent-farm's divider blue) is every rule and line, and nothing else;
  • DIRECT and RELAYED are the tailnet path: straight between two sidecars, or through DERP.
"""
from __future__ import annotations

PLANE, SURFACE, RAISED = "#0d0d0d", "#1a1a19", "#262624"          # backgrounds, back to front
INK, INK2, MUTED, AXIS = "#f0efec", "#c3c2b7", "#898781", "#383835"
GOOD, WARN, SERIOUS, CRIT = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
WAITING = "#ffd75f"
DIVIDER = "#0053AA"
UNITS = ("#19a9b0", "#0b6eff", "#9584e5", "#bf2fb9")              # teal, blue, violet, magenta
DIRECT, RELAYED = UNITS[0], UNITS[2]


def _parts(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def blend(start: str, end: str, part: float) -> str:
    """The color that is `part` of the way from one to the other, 0 to 1."""
    part = min(1.0, max(0.0, part))
    return "#" + "".join(f"{round(a + (b - a) * part):02x}" for a, b in zip(_parts(start), _parts(end)))


GHOST = blend(MUTED, PLANE, 0.45)        # text below a break, and anything last-known
BREAK_BG = blend(CRIT, PLANE, 0.80)      # the cell where a column breaks
YOU_BG = blend(WAITING, PLANE, 0.82)     # the same, when it is yours to fix
