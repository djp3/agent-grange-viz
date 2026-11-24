# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The bar at the foot of the window: its keys drawn as agent-farm's footer and
agent-frolic's hints are, fitted to the window, with what the marks mean at its far end."""
from __future__ import annotations

import pytest
from rich.cells import cell_len

import hints
import main
import theme
from demo import DemoSource
from standins import Scripted, drive, healthy, today

LIVE = "←→ Column  ⇧←→ Scroll  enter Detail  t By address  q Quit"
DEMO = "←→ Column  ⇧←→ Scroll  enter Detail  t By address  space Pause  e Next moment  + - Speed  q Quit"
LEGEND = "● holds ◐ degraded ✗ breaks"
# The key each of the window's bindings is shown as in the bar.
SHOWN_AS = {"left": "←→", "right": "←→", "shift+left": "⇧←→", "shift+right": "⇧←→", "enter": "enter", "t": "t",
            "space": "space", "e": "e", "plus": "+ -", "equals_sign": "+ -", "minus": "+ -", "q": "q"}


def drawn(line) -> list:
    """Each styled run of a line: its text and its style."""
    return [(line.plain[span.start:span.end], str(span.style)) for span in line.spans]


# ---- how a hint is drawn -------------------------------------------------------------------------
def test_a_hints_key_is_lit_and_its_words_are_plain():
    """As agent-farm's footer and agent-frolic's hints are: the key bold and in the key's
    color, the words beside it, two cells between one hint and the next."""
    line = hints.hint_text([("q", "Quit"), ("⇧←→", "Scroll")])
    assert line.plain == "q Quit  ⇧←→ Scroll"
    assert drawn(line) == [("q", f"bold {theme.KEY}"), (" Quit", theme.INK2),
                           ("⇧←→", f"bold {theme.KEY}"), (" Scroll", theme.INK2)]


def test_the_keys_color_is_no_color_that_already_means_something():
    """Not a status, not the yellow of what is yours, not a path of the tailnet, not a rule,
    not the ink of words."""
    taken = (theme.GOOD, theme.WARN, theme.SERIOUS, theme.CRIT, theme.WAITING, theme.DIVIDER, *theme.UNITS,
             theme.INK, theme.INK2, theme.MUTED, theme.AXIS)
    assert theme.KEY.lower() not in {color.lower() for color in taken}


def test_what_a_key_does_is_said_with_a_capital_and_the_way_out_comes_last():
    for demo in (False, True):
        every = hints.hints(demo=demo)
        assert all(does[0].isupper() for _, does in every), every
        assert every[-1] == ("q", "Quit")


def test_every_key_the_window_answers_to_has_a_hint():
    """A key added to the window and not to the bar would be one nobody is told of."""
    bound = [key for keys, _, _ in main.GrangeViz.BINDINGS for key in keys.split(",")]
    assert set(bound) == set(SHOWN_AS)
    assert {SHOWN_AS[key] for key in bound} == {key for key, _ in hints.hints(demo=True)}


def test_the_demos_keys_are_hinted_only_in_the_demo():
    assert hints.hint_text(hints.hints()).plain == LIVE
    assert hints.hint_text(hints.hints(demo=True)).plain == DEMO


def test_t_says_the_order_it_would_change_to():
    assert ("t", "By address") in hints.hints(by_address=False)
    assert ("t", "By role") in hints.hints(by_address=True)


# ---- the bar as it fits ---------------------------------------------------------------------------
def test_a_wide_bar_has_every_hint_at_the_left_and_what_the_marks_mean_at_the_far_end():
    for demo, keys, note in ((False, LIVE, LEGEND), (True, DEMO, f"{LEGEND} · made-up data")):
        line = hints.foot(False, demo, 160)
        assert line.cell_len == 160 and line.plain.startswith(keys + "   ") and line.plain.endswith(" " + note)
        assert line.plain[len(keys):-len(note)].strip() == "", "nothing but room between the two"


def test_the_note_at_the_far_end_is_quieter_than_the_hints():
    """It is no key: plain, and in the faintest ink."""
    line = hints.foot(False, True, 160)
    assert drawn(line)[-1] == (f"{LEGEND} · made-up data", theme.MUTED)


@pytest.mark.parametrize("demo, width, note", [
    (False, 87, LEGEND),                               # the hints, three cells, and the note: 57 + 3 + 27
    (False, 86, ""),                                   # one cell short: the note goes, whole
    (True, 141, f"{LEGEND} · made-up data"),           # 96 + 3 + 42
    (True, 140, "made-up data"),                       # the demo keeps "made-up data" and lets the marks go
    (True, 111, "made-up data"),                       # 96 + 3 + 12
    (True, 110, ""),
])
def test_short_of_room_the_note_goes_before_any_hint(demo, width, note):
    line = hints.foot(False, demo, width).plain
    keys = DEMO if demo else LIVE
    assert line.startswith(keys) and line[len(keys):].strip() == note
    assert cell_len(line) == (width if note else cell_len(keys)), "the note is pushed to the far end"


@pytest.mark.parametrize("demo", [False, True])
def test_narrower_than_its_hints_the_bar_leaves_out_the_least_needed_and_keeps_the_way_out(demo):
    every = [f"{key} {does}" for key, does in hints.hints(demo=demo)]
    full = hints.HINT_GAP.join(every)
    assert hints.foot(False, demo, cell_len(full)).plain == full
    for width in range(cell_len("q Quit"), cell_len(full) + 1):
        line = hints.foot(False, demo, width).plain
        shown = line.split(hints.HINT_GAP)
        assert cell_len(line) <= width and line.endswith("q Quit"), width
        assert shown == [hint for hint in every if hint in shown], width         # in their order
    assert hints.foot(False, demo, 3).plain == "q Quit", "narrower than that it is cut as text is"


def test_the_hints_go_in_the_order_they_are_least_needed():
    assert set(hints.LEFT_OUT_FIRST) == {key for key, _ in hints.hints(demo=True)} - {"q"}
    assert hints.foot(False, True, 95).plain == DEMO.replace("  + - Speed", "")
    assert hints.foot(False, True, 84).plain == "←→ Column  ⇧←→ Scroll  enter Detail  t By address  space Pause  q Quit"
    assert hints.foot(False, False, 56).plain == "←→ Column  enter Detail  t By address  q Quit"
    assert hints.foot(False, False, 20).plain == "←→ Column  q Quit"


# ---- in the window ---------------------------------------------------------------------------------
def foot_of(app):
    """The window's last row, as it is drawn."""
    return list(app.screen._compositor.render_strips())[-1]


def hexes(color) -> str:
    return color.get_truecolor().hex.lower()


@drive
async def test_the_windows_foot_is_a_bar_with_its_keys_lit():
    """A bar from edge to edge, a shade lighter than the window, and in it the keys, and only
    the keys, bold and in the key's color."""
    app = main.GrangeViz(DemoSource(start=165.0), live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        foot = foot_of(app)
        assert foot.text == f" {DEMO}{' ' * 10}{LEGEND} · made-up data "
        assert {hexes(seg.style.bgcolor) for seg in foot} == {theme.RAISED.lower()}, "one bar, the whole width"
        lit = "".join(seg.text if seg.style.bold and hexes(seg.style.color) == theme.KEY.lower() else " " * len(seg.text)
                      for seg in foot)
        assert lit.split() == "←→ ⇧←→ enter t space e + - q".split()
        above = list(app.screen._compositor.render_strips())[-2]
        assert theme.RAISED.lower() not in {hexes(seg.style.bgcolor) for seg in above if seg.style.bgcolor}


@drive
async def test_the_windows_foot_follows_the_order_and_the_width():
    app = main.GrangeViz(DemoSource(start=165.0), live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        assert "t By address" in foot_of(app).text
        await pilot.press("t")
        await pilot.pause()
        assert "t By role" in foot_of(app).text and "By address" not in foot_of(app).text
        await pilot.press("t")
        await pilot.resize_terminal(113, 46)                     # the note's marks go first
        await pilot.pause()
        assert foot_of(app).text == f" {DEMO}   made-up data "
        await pilot.resize_terminal(90, 46)                      # then the hints, the least needed first
        await pilot.pause()
        assert foot_of(app).text.strip() == DEMO.replace("  + - Speed", "") and "…" not in foot_of(app).text


@drive
async def test_a_real_granges_foot_has_no_key_of_the_demos_and_calls_nothing_made_up():
    app = main.GrangeViz(Scripted(today([healthy("alice")])), live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        foot = foot_of(app).text
        assert foot == f" {LIVE}{' ' * (148 - cell_len(LIVE) - cell_len(LEGEND))}{LEGEND} "
        assert "Pause" not in foot and "made-up" not in foot
