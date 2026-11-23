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

The marks are agent-farm's: ● busy, ◐ waiting, ○ idle. ◐ is yellow only for an agent of
your own farm, since yellow means one thing in this app: yours to act on, on this Mac.

`draw` is a function of a model.Snapshot, the column picked and a size; main.py's window puts
it in a panels.Panel. It draws what Snapshot.farms and Snapshot.farm_news hold and works
nothing out, and it draws neither while the grange refuses every request: nothing keeps up
what it said before.
"""
from __future__ import annotations

from typing import Optional

from rich.text import Text

from fmt import age, one_line, short_age
from model import FarmChange, Snapshot
from stack import GRANGE_DOWN
from theme import GOOD, INK, INK2, MUTED, WAITING

ORDER = {"waiting": 0, "busy": 1, "idle": 2}     # agent-farm's tiers: who needs someone comes first
COLUMN = 72                                      # a column of changes is at least this wide
SAID = 28                                        # room for "busy → waiting for its human"
NAME = 40                                        # the most of an agent's name that is shown
WHO = 7                                          # a grange's name beside a change


def look(state: str, mine: bool) -> tuple:
    """(mark, style) for a state. Any other word than agent-farm's three is passed on as it
    came (R4.1) and gets no mark of its own."""
    if state == "waiting":
        return "◐", WAITING if mine else f"bold {INK}"
    return {"busy": ("●", GOOD), "idle": ("○", INK2)}.get(state, ("·", MUTED))


def mark_of(c: FarmChange, mine: bool) -> tuple:
    if c.kind == "state":
        return look(c.after, mine)
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
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def line(c: FarmChange, me: str, who: bool, name_w: int) -> Text:
    """One change: its age, whose farm (when `who`), its mark, the agent and what happened."""
    mine = c.grange == me
    mark, style = mark_of(c, mine)
    t = Text(no_wrap=True)
    t.append(f"{short_age(c.age):>4}  ", style=MUTED)
    if who:
        t.append(f"{'you' if mine else _cut(one_line(c.grange), WHO):<{WHO + 1}}", style=INK2)
    t.append(f"{mark} ", style=style)
    if c.kind in ("on", "off"):
        t.append(said(c, mine), style=INK2 if c.kind == "on" else MUTED)
    else:
        gone = c.kind == "ended"
        t.append(f"{_cut(c.name, name_w):<{name_w + 1}}", style=INK2 if gone else INK)
        t.append(said(c, mine), style=MUTED if gone else INK2)
    return t


def _title(title: str, subtitle: str) -> Text:
    t = Text(no_wrap=True)
    t.append(title, style=f"bold {INK2}")
    t.append("  " + subtitle, style=MUTED)
    return t


def _now(label: str, farms: dict, gid: str, mine: bool, w: int) -> Text:
    """One grange's title: its agents now, those waiting first, as many as the line holds."""
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
        more = len(rest) - i - 1
        room = w - t.cell_len - (len(f"  +{more} more") if more else 0)
        if 4 + len(agent.name) > room:
            t.append(f"  +{more + 1} more", style=MUTED)
            break
        mark, style = look(agent.state, mine)
        t.append(f"  {mark} ", style=style)
        t.append(agent.name, style=INK2)
    return t


def draw(snap: Snapshot, label: str, gid: Optional[str], w: int, h: int) -> list:
    """The pane as lines of Text, title first, for a pane w wide and h high. `gid` None is
    every grange's news; a grange id is that grange's agents and its own changes, under
    `label` (its column's name)."""
    if snap.refusing:                                 # nothing it said before is known to be true now
        head = _title("farm news", GRANGE_DOWN)
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
    longest = max((len(c.name) for c in shown), default=0)       # no wider than the names in view need
    name_w = max(12, min(longest, NAME, each - 2 - fixed - SAID))
    cells = [line(c, me, gid is None, name_w) for c in shown]
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
                              ("◐", f"bold {INK}", "one began waiting for its human (yellow: for you)"),
                              ("●", GOOD, "one went busy"), ("○", INK2, "one went idle"),
                              ("✕", MUTED, "a grange went off the board, and its agents with it")):
        t.append(f"{mark} ", style=style)
        t.append(what + "\n", style=INK2)
    t.append("From GET /v1/agents: each answer is the whole board, and the viewer compares one with the next. "
             "So only what changed while this viewer was open is here.", style=MUTED)
    if demo:
        t.append(" Every agent in the demo is made up.", style=MUTED)
    return t


__all__ = ["draw", "tip", "look", "mark_of", "said", "line"]
