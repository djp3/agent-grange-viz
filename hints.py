# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The bar at the foot of the window: the keys, each with a word or two for what it does, and
at its far end what a column's three marks mean.

It is drawn as agent-farm's footer and agent-frolic's hints are, so the three apps read
alike: a bar a shade lighter than the window (main.py lays it out on RAISED), each key bold
and in the key's color, its words beside it in plain ink with a capital, two cells between
one hint and the next, and the way out last. `hint_text` and `fit_hints` are agent-frolic's.

A window too narrow for all of it loses the note at the far end first, then the hints, the
least needed first. `q Quit` always stays. The bar is fitted to the width it has when it is
drawn (`Bar`), so it follows the window as it is resized.
"""
from __future__ import annotations

from typing import Callable, Sequence

from rich.cells import cell_len
from rich.text import Text
from textual.widget import Widget

from theme import INK2, KEY, MUTED

HINT_GAP = "  "
NOTE_GAP = 3                     # the least between the last hint and the note at the far end
LEGEND = "● holds ◐ degraded ✗ breaks"
MADE_UP = "made-up data"
# When the window is too narrow for every hint, these are left out, the first first, by their
# keys: the demo's own before the ones every window has, and what the wheel does as well
# before what only a key does. The way out is not named here, so it always stays.
LEFT_OUT_FIRST = ["+ -", "e", "⇧←→", "t", "space", "enter", "←→"]


def hints(by_address: bool = False, demo: bool = False) -> list:
    """The keys that do something now, each with what it does, in the order they are drawn.
    `t` says the order it would change to. The demo has three keys more."""
    out = [("←→", "Column"), ("⇧←→", "Scroll"), ("enter", "Detail"), ("t", "By role" if by_address else "By address")]
    if demo:
        out += [("space", "Pause"), ("e", "Next moment"), ("+ -", "Speed")]
    return out + [("q", "Quit")]


def hint_text(hints: Sequence[tuple]) -> Text:
    """Hints as they are drawn: "q Quit", the key lit and bold, its words in plain ink, and a
    gap between one hint and the next."""
    out = Text(no_wrap=True, overflow="ellipsis")
    for at, (key, does) in enumerate(hints):
        out.append(HINT_GAP if at else "")
        out.append(key, style=f"bold {KEY}").append(f" {does}", style=INK2)
    return out


def fit_hints(hints: Sequence[tuple], left_out_first: Sequence[str], width: int) -> Text:
    """The hints that fit in `width` cells, in their order: while they do not, the one whose
    key comes next in `left_out_first` is left out. The keys not named there always stay,
    and when even those do not fit the line is cut as text is."""
    shown = list(hints)
    for key in left_out_first:
        if hint_text(shown).cell_len <= width:
            break
        shown = [hint for hint in shown if hint[0] != key]
    return hint_text(shown)


def notes(demo: bool = False) -> list:
    """What the far end of the bar may say, the fullest first: what the marks mean, and in
    the demo that none of it is real. Short of room the demo keeps the second and lets the
    first go: the marks say what they mean on hover as well."""
    return [f"{LEGEND} · {MADE_UP}", MADE_UP] if demo else [LEGEND]


def foot(by_address: bool, demo: bool, width: int) -> Text:
    """The whole bar as it fits in `width` cells: every hint from the left and the fullest
    note that fits, pushed to the far end; or, when not even the hints all fit, the hints
    that do."""
    every = hints(by_address, demo)
    line = hint_text(every)
    if line.cell_len > width:
        return fit_hints(every, LEFT_OUT_FIRST, width)
    for note in notes(demo):
        room = width - line.cell_len - cell_len(note)
        if room >= NOTE_GAP:
            return line.append(" " * room).append(note, style=MUTED)
    return line


# ---- the widget ------------------------------------------------------------------------------------
class Bar(Widget):
    """Draws one line, `draw(width) -> Text`, for the width it has at that moment. A line
    fitted when the window last redrew would be a resize behind."""

    def __init__(self, draw: Callable[[int], Text], **kw):
        super().__init__(**kw)
        self.draw = draw

    def render(self) -> Text:
        return self.draw(self.size.width)


__all__ = ["Bar", "foot", "hints", "notes", "hint_text", "fit_hints"]
