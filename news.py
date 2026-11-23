# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The farm news, under the diagnosis: what the farms reported, as it reached this grange.

The rows above say whether each grange can be heard. This says what was heard: an agent
started or ended, one began waiting for its human, went busy or went idle, a grange came onto
the board or went off it with its agents. Newest first, down the first column of the pane and
then down the next when the window is wide enough for two or three.

    farm news  what each grange's farm reported · newest first · ← → picks one grange
      4s  grace   ◐ ios-pr-246 flaky ci      busy → waiting for its human
     12s  alice   − web-pr-141 invoice pdf   ended · was idle
     25s  bob     + web-pr-283 refactor      started · busy
     77s  frank   ✕ 3 agents went off the board · expired

With your own column picked it is every grange's news. With another picked (← →) it is that
grange's agents now, by name, and then its own changes.

The marks are agent-farm's: ● busy, ◐ waiting, ○ idle. Yellow means one thing in this app:
yours to act on, now, on this Mac. So ◐ is yellow only for an agent of your own farm, and only
while that agent still waits: once it is busy or idle again, or has ended, its row is what
happened and no longer something to do, and its ◐ is the plain one a teammate's has.

`draw` is a function of a model.Snapshot, the column picked and a size; main.py's window puts
it in a panels.Panel. It draws what Snapshot.farms and Snapshot.farm_news hold and works
nothing out. It draws neither where there is no board to draw from, and says "not known"
instead of "no news" or "no record of it":

  • while the grange refuses every request: nothing keeps up what it said before;
  • before the grange has answered for the first time: a viewer started while it is down
    still has the tailnet's devices to show, but what the grange holds has never been read.
"""
from __future__ import annotations

from typing import Optional

from rich.cells import cell_len, set_cell_size
from rich.text import Text

from fmt import age, one_line, short_age
from model import FarmChange, Snapshot
from stack import GRANGE_DOWN
from theme import GOOD, INK, INK2, MUTED, WAITING

ORDER = {"waiting": 0, "busy": 1, "idle": 2}     # agent-farm's tiers: who needs someone comes first
# Every width here is in terminal cells, not characters: a character of Chinese, Japanese or
# Korean, and most emoji, take two. A name is measured, cut and padded by what it takes on screen.
COLUMN = 72                                      # a column of changes is at least this wide
SAID = 28                                        # room for "busy → waiting for its human"
NAME = 40                                        # the most of an agent's name that is shown
WHO = 7                                          # a grange's name beside a change
ROLL_MIN = 4                                     # the least of a first agent's name worth showing cut
NEVER_ANSWERED = "not known: your grange has not answered yet"


def look(state: str, yours: bool) -> tuple:
    """(mark, style) for a state; `yours` where it is yours to act on now. Any other word than
    agent-farm's three is passed on as it came (R4.1) and gets no mark of its own: where it is
    drawn, the word is beside it."""
    if state == "waiting":
        return "◐", WAITING if yours else f"bold {INK}"
    return {"busy": ("●", GOOD), "idle": ("○", INK2)}.get(state, ("·", MUTED))


def mark_of(c: FarmChange, yours: bool) -> tuple:
    if c.kind == "state":
        return look(c.after, yours)
    return {"started": ("+", f"bold {INK}"), "on": ("+", f"bold {INK}"),
            "ended": ("−", MUTED), "off": ("✕", MUTED)}.get(c.kind, ("·", MUTED))


def said(c: FarmChange, mine: bool) -> str:
    """The change in words."""
    if c.kind == "started":
        return f"started · {c.after}"
    if c.kind == "ended":
        return f"ended · was {c.before}"
    if c.kind == "state":
        return f"{c.before} → {c.after}" + ((" for you" if mine else " for its human") if c.after == "waiting" else "")
    some = f"{c.agents} agent{'' if c.agents == 1 else 's'}"
    where = "came onto the board" if c.kind == "on" else "went off the board"
    return f"{some} {where}" + (f" · {c.note}" if c.note else "")


def _cut(text: str, width: int) -> str:
    """`text` in at most `width` cells, ending in … where it was cut. A character two cells
    wide is left out whole, never cut through."""
    if cell_len(text) <= width:
        return text
    return set_cell_size(text, max(0, width - 1)).rstrip() + "…"


def _field(text: str, width: int) -> str:
    """`text` cut to `width` cells and padded to one more: a field, and the gap after it."""
    cut = _cut(text, width)
    return cut + " " * (width + 1 - cell_len(cut))


def yours_now(snap: Snapshot) -> set:
    """The changes that are still yours to act on, by id(): for each agent of your own farm
    that waits now, the newest change that says it began to. So there are never more of them
    than agents of yours waiting. While your farm has not reported (None), which of them wait
    is not known, and none is."""
    me = snap.me.gid
    waiting = {a.name for a in snap.farms.get(me) or () if a.state == "waiting"}
    out = set()
    for c in reversed(snap.farm_news):                # newest first
        if c.grange == me and c.kind == "state" and c.after == "waiting" and c.name in waiting:
            waiting.discard(c.name)                   # an older wait of the same agent is over
            out.add(id(c))
    return out


def line(c: FarmChange, me: str, who: bool, name_w: int, yours: bool = False) -> Text:
    """One change: its age, whose farm (when `who`), its mark, the agent and what happened.
    `yours` where it is still yours to act on (yours_now): its mark is the yellow one."""
    mine = c.grange == me
    mark, style = mark_of(c, yours)
    t = Text(no_wrap=True)
    t.append(f"{short_age(c.age):>4}  ", style=MUTED)
    if who:
        t.append(_field("you" if mine else one_line(c.grange), WHO), style=INK2)
    t.append(f"{mark} ", style=style)
    if c.kind in ("on", "off"):
        t.append(said(c, mine), style=INK2 if c.kind == "on" else MUTED)
    else:
        gone = c.kind == "ended"
        t.append(_field(c.name, name_w), style=INK2 if gone else INK)
        t.append(said(c, mine), style=MUTED if gone else INK2)
    return t


def _title(title: str, subtitle: str) -> Text:
    t = Text(no_wrap=True)
    t.append(title, style=f"bold {INK2}")
    t.append("  " + subtitle, style=MUTED)
    return t


def _named(agent) -> str:
    """An agent in the roll call. A mark says busy, waiting or idle; any other state has no
    mark of its own, so it is said in words beside the name."""
    return agent.name if agent.state in ORDER else f"{agent.name} ({agent.state})"


def _now(label: str, farms: dict, gid: str, mine: bool, w: int) -> Text:
    """One grange's title, its roll call: its agents now, those waiting first, as many as the
    line holds and then how many more there are. The count is never pushed off the line: every
    agent is measured in cells, with room kept for the count. The first is cut rather than
    left out, so that a farm with agents never shows none of them."""
    t = Text(no_wrap=True)
    t.append(f"{one_line(label)}'s farm", style=f"bold {INK2}")
    if gid not in farms:
        t.append("  not on the board: your grange holds no record of it", style=MUTED)
        return t
    agents = farms[gid]
    if agents is None:                                # None is not known, never nobody
        t.append("  not known: it has not reported since its grange started", style=MUTED)
        return t
    if not agents:
        t.append("  reports no agents", style=MUTED)
        return t
    t.append(f"  {len(agents)} agent{'' if len(agents) == 1 else 's'} now:", style=MUTED)
    rest = sorted(agents, key=lambda a: ORDER.get(a.state, 3))       # stable: the farm's own order within a tier
    for i, agent in enumerate(rest):
        more = len(rest) - i - 1                                     # those after this one
        room = w - t.cell_len - 4 - (cell_len(f"  +{more} more") if more else 0)
        named = _named(agent)
        if cell_len(named) > room:
            if i or room < ROLL_MIN:                                 # not the first, or no room for any of it
                t.append(f"  +{more + 1} more", style=MUTED)
                break
            named = _cut(named, room)
        mark, style = look(agent.state, mine)
        t.append(f"  {mark} ", style=style)
        t.append(named[:len(agent.name)], style=INK2)
        t.append(named[len(agent.name):], style=MUTED)               # its state in words, where no mark says it
    return t


def draw(snap: Snapshot, label: str, gid: Optional[str], w: int, h: int) -> list:
    """The pane as lines of Text, title first, for a pane w wide and h high. `gid` None is
    every grange's news; a grange id is that grange's agents and its own changes, under
    `label` (its column's name)."""
    unknown = GRANGE_DOWN if snap.refusing else None if snap.ever_reached else NEVER_ANSWERED
    if unknown:                                       # no board to draw from: neither news nor "no news"
        head = _title("farm news", unknown)
        head.truncate(w, overflow="ellipsis")
        return [head]
    me = snap.me.gid
    if gid is None or gid == me:
        gid = None
        head = _title("farm news", "what each grange's farm reported · newest first · ← → picks one grange")
        changes = snap.farm_news[::-1]
    else:
        head = _now(label, snap.farms, gid, False, w)
        changes = [c for c in reversed(snap.farm_news) if c.grange == gid]
    head.truncate(w, overflow="ellipsis")
    out = [head]
    rows = max(0, h - 1)
    if not changes:
        watched = f" in the {age(snap.farm_watched)} this viewer has been watching" if snap.farm_watched is not None else " yet"
        none = Text(("  no farm has reported a change" if gid is None else "  it has reported no change") + watched,
                    style=MUTED, no_wrap=True)
        none.truncate(w, overflow="ellipsis")
        return out + [none][:rows]
    across = max(1, min(3, w // COLUMN))
    each = w // across
    shown = changes[:rows * across]
    fixed = 6 + (WHO + 1 if gid is None else 0) + 2 + 1          # age, whose, mark, the gap after the name
    longest = max((cell_len(c.name) for c in shown), default=0)  # no wider than the names in view need
    name_w = max(12, min(longest, NAME, each - 2 - fixed - SAID))
    act = yours_now(snap) if gid is None else ()                 # another grange's waits are not yours
    cells = [line(c, me, gid is None, name_w, id(c) in act) for c in shown]
    for row in range(min(rows, len(cells))):
        t = Text(no_wrap=True)
        for cell in cells[row::rows]:
            cell.truncate(max(1, each - 2), overflow="ellipsis", pad=True)
            t.append_text(cell)
            t.append("  ")
        t.rstrip()
        t.truncate(w, overflow="ellipsis")
        out.append(t)
    return out


def tip(demo: bool = False) -> Text:
    """What the pane is, what its marks mean and where it comes from, for the pointer."""
    t = Text()
    t.append("farm news", style=f"bold {INK}")
    t.append(" · what each grange's farm reported, with how long ago it reached your grange\n", style=INK2)
    for mark, style, what in (("+", f"bold {INK}", "an agent started, or a grange came onto the board"),
                              ("−", MUTED, "an agent ended"),
                              ("◐", f"bold {INK}", "one began waiting for its human (yellow: yours, and still waiting for you)"),
                              ("●", GOOD, "one went busy"), ("○", INK2, "one went idle"),
                              ("✕", MUTED, "a grange went off the board, and its agents with it")):
        t.append(f"{mark} ", style=style)
        t.append(what + "\n", style=INK2)
    t.append("From GET /v1/agents: each answer is the whole board, and the viewer compares one with the next. "
             "So only what changed while this viewer was open is here.", style=MUTED)
    if demo:
        t.append(" Every agent in the demo is made up.", style=MUTED)
    return t


__all__ = ["draw", "tip", "look", "mark_of", "said", "line", "yours_now"]
