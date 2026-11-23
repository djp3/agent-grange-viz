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
        self._board: Optional[dict] = None           # grange id -> (Agent, …), as last handed in
        self._changes: deque = deque(maxlen=kept)    # (at, FarmChange with no age yet), oldest first
        self.since: Optional[float] = None           # when the first answer was taken

    def forget(self) -> None:
        """The board started over (the grange restarted): the next answer is where the
        comparing starts again. What already happened is still what happened."""
        self._board = None

    def take(self, board: dict, now: float, quiet: bool = False, why: Optional[dict] = None) -> None:
        """One whole board, grange id -> its agents, as of `now` on the source's clock. With
        `quiet` nothing is news (the board is refilling after a restart). `why` may say, for
        a grange that came or went, how: grange id -> a few words."""
        board = {gid: tuple(agents) for gid, agents in board.items()}
        before, self._board = self._board, board
        if self.since is None:
            self.since = now
        if before is None or quiet:
            return
        why = why or {}
        for gid in sorted(board.keys() - before.keys()):
            self._add(now, gid, "on", agents=len(board[gid]), note=why.get(gid, ""))
        for gid in sorted(before.keys() - board.keys()):
            self._add(now, gid, "off", agents=len(before[gid]), note=why.get(gid, ""))
        for gid in sorted(board.keys() & before.keys()):
            if board[gid] == before[gid]:
                continue
            was, now_is = _named(before[gid]), _named(board[gid])
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
        """What every farm said in the last answer: grange id -> [Agent]."""
        return {gid: list(agents) for gid, agents in (self._board or {}).items()}
