# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
What the farms say, and what changed.

Gossip exists to carry each grange's farm report: its agents, each with a name and a state.
GET /v1/agents answers with the whole board every time, never with a difference, so the news
is worked out here: a source hands every answer to a Watch, which compares it with the one
before and keeps what changed.

    watch = Watch()
    watch.take({"alice": [Agent("pr-7 docs", "busy")], …}, now)    # the first one is where it starts
    watch.take({…}, later)                                          # every change since is news
    watch.news(now)                                                 # [FarmChange], oldest first
    watch.board()                                                   # what every farm says now

What counts as a change:

  started   an agent that was not in its farm's last report
  ended     one that was, and no longer is
  state     one whose state is another word than before
  on        a grange came onto the board, and its agents with it: one change, not one each
  off       a grange went off the board, and its agents with it

A farm that has not reported is not a farm with no agents. A grange that has just started
lists its farm with nobody in it and no farm age, until the farm next pushes (within 30 s,
R5.2); a board says so with None in place of that farm's agents. Nothing is made of that
silence: what the farm last said is remembered, and its next report is compared with that. So
a grange restarting, this one or a teammate's, ends and starts nobody. A change is news only
where the viewer knew what was there before:

  • a farm heard from for the first time is where its comparing starts, not news;
  • a grange that comes onto the board is told when its farm is first heard, with its agents;
  • after this grange restarts (forget), who is on the board is not known until it has
    refilled, so nobody is told to have come or gone meanwhile; what each farm last said
    is still known, and what really changed while it was down is told as it is heard again.

It only knows what changed while it was being handed answers: nothing here asks a grange for
its history, because there is none to ask for.
"""
from __future__ import annotations

from collections import deque
from typing import Iterable, Optional

from model import FarmChange

KEPT = 200                               # the most changes remembered; the oldest go first


def _named(agents: Iterable) -> dict:
    """Each agent under its name and how many of that name came before it, so two sessions a
    farm reports under one name are still two."""
    seen: dict = {}
    out = {}
    for agent in agents:
        nth = seen.get(agent.name, 0)
        seen[agent.name] = nth + 1
        out[(agent.name, nth)] = agent
    return out


class Watch:
    def __init__(self, kept: int = KEPT):
        self._board: Optional[dict] = None           # grange id -> (Agent, …) or None, as last handed in
        self._said: dict = {}                        # grange id -> (Agent, …): what its farm last reported
        self._coming: dict = {}                      # grange id -> how it came onto the board, while
                                                     # its farm has not been heard
        self._changes: deque = deque(maxlen=kept)    # (at, FarmChange with no age yet), oldest first
        self.since: Optional[float] = None           # when the first answer was taken

    def forget(self) -> None:
        """This grange restarted, and its board started over: who is on it is not known until it
        has refilled. What each farm last said is still known, and what already happened is
        still what happened."""
        self._board = None
        self._coming.clear()

    def take(self, board: dict, now: float, quiet: bool = False, why: Optional[dict] = None) -> None:
        """One whole board as of `now` on the source's clock: grange id -> its agents, or None
        for a grange whose farm has not reported. With `quiet` no grange is told to have come
        or gone (the board is refilling after a restart). `why` may say, for a grange that came
        or went, how: grange id -> a few words."""
        board = {gid: None if agents is None else tuple(agents) for gid, agents in board.items()}
        before, self._board = self._board, board
        if self.since is None:
            self.since = now
        why = why or {}
        if before is not None and not quiet:
            for gid in sorted(board.keys() - before.keys()):      # came onto the board: told once its farm is heard
                if gid not in self._said:
                    self._coming[gid] = why.get(gid, "")
            for gid in sorted(before.keys() - board.keys()):      # went off it, with whatever its farm last said
                self._coming.pop(gid, None)
                said = self._said.pop(gid, None)
                if said is not None:
                    self._add(now, gid, "off", agents=len(said), note=why.get(gid, ""))
        for gid in sorted(board):
            agents = board[gid]
            if agents is None:                                    # its farm has said nothing: nothing changed
                continue
            if gid in self._coming:
                self._add(now, gid, "on", agents=len(agents), note=self._coming.pop(gid))
            elif gid in self._said:
                self._compare(now, gid, self._said[gid], agents)
            self._said[gid] = agents                              # else: the first it is known to say

    def _compare(self, now: float, gid: str, before: tuple, after: tuple) -> None:
        """One farm's report against its last one."""
        if before == after:
            return
        was, now_is = _named(before), _named(after)
        for key, agent in now_is.items():
            if key not in was:
                self._add(now, gid, "started", name=agent.name, after=agent.state)
            elif was[key].state != agent.state:
                self._add(now, gid, "state", name=agent.name, before=was[key].state, after=agent.state)
        for key, agent in was.items():
            if key not in now_is:
                self._add(now, gid, "ended", name=agent.name, before=agent.state)

    def _add(self, at: float, gid: str, kind: str, **what) -> None:
        self._changes.append((at, FarmChange(0.0, gid, kind, **what)))

    def news(self, now: float) -> list:
        """Every change kept, oldest first, each with its age as of `now`."""
        return [FarmChange(max(0.0, now - at), c.grange, c.kind, c.name, c.before, c.after, c.agents, c.note)
                for at, c in self._changes]

    def board(self) -> dict:
        """The last answer: grange id -> [Agent], or None for a grange whose farm has not
        reported."""
        return {gid: None if agents is None else list(agents) for gid, agents in (self._board or {}).items()}
