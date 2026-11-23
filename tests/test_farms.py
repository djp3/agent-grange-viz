# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""farms.Watch: GET /v1/agents is the whole board every time, so what changed is worked out by
comparing one board with the next."""
from __future__ import annotations

import pytest

from farms import Watch
from model import Agent


def board(**farms) -> dict:
    """grange id -> its agents, from "name:state" words."""
    return {gid: [Agent(*word.split(":")) for word in words] for gid, words in farms.items()}


def told(watch: Watch, now: float = 0.0) -> list:
    return [(c.grange, c.kind, c.name, c.before, c.after) for c in watch.news(now)]


def test_the_first_board_is_where_it_starts_not_news():
    watch = Watch()
    assert watch.since is None and watch.news(0.0) == [] and watch.board() == {}
    watch.take(board(alice=["docs:busy", "lint:idle"]), now=10.0)
    assert watch.news(12.0) == [] and watch.since == 10.0
    assert watch.board() == {"alice": [Agent("docs", "busy"), Agent("lint", "idle")]}


def test_an_agent_that_starts_ends_or_changes_state_is_one_change_each():
    watch = Watch()
    watch.take(board(alice=["docs:busy", "lint:idle"], bob=["ci:busy"]), now=0.0)
    watch.take(board(alice=["docs:waiting", "tests:busy"], bob=["ci:busy"]), now=5.0)
    assert told(watch) == [("alice", "state", "docs", "busy", "waiting"),
                           ("alice", "started", "tests", "", "busy"),
                           ("alice", "ended", "lint", "idle", "")]
    assert [c.age for c in watch.news(8.0)] == [3.0, 3.0, 3.0], "an age, as of when it is asked"
    watch.take(board(alice=["docs:waiting", "tests:busy"], bob=["ci:busy"]), now=9.0)
    assert len(watch.news(9.0)) == 3, "the same board again is no news"


def test_a_grange_that_comes_or_goes_is_one_change_with_its_agents():
    """Not one "ended" for each agent it had: they did not end, their grange stopped being heard."""
    watch = Watch()
    watch.take(board(alice=["a:busy", "b:idle", "c:busy"]), now=0.0)
    watch.take(board(), now=4.0, why={"alice": "expired"})
    watch.take(board(alice=["a:busy"], carol=["x:idle", "y:idle"]), now=9.0, why={"alice": "back on the board"})
    news = watch.news(9.0)
    assert [(c.grange, c.kind, c.agents, c.note, c.name) for c in news] == [
        ("alice", "off", 3, "expired", ""), ("alice", "on", 1, "back on the board", ""), ("carol", "on", 2, "", "")]
    assert [c.age for c in news] == [5.0, 0.0, 0.0], "oldest first"


def test_a_farm_that_reports_nobody_is_still_on_the_board():
    watch = Watch()
    watch.take(board(alice=[]), now=0.0)
    watch.take(board(alice=["a:busy"]), now=1.0)
    watch.take(board(alice=[]), now=2.0)
    assert told(watch) == [("alice", "started", "a", "", "busy"), ("alice", "ended", "a", "busy", "")]
    assert watch.board() == {"alice": []}


def test_two_sessions_under_one_name_are_still_two():
    watch = Watch()
    watch.take(board(alice=["task:busy", "task:idle"]), now=0.0)
    watch.take(board(alice=["task:busy"]), now=1.0)
    assert told(watch) == [("alice", "ended", "task", "idle", "")]
    watch.take(board(alice=["task:waiting", "task:busy"]), now=2.0)
    assert told(watch)[1:] == [("alice", "state", "task", "busy", "waiting"), ("alice", "started", "task", "", "busy")]


def test_while_the_board_refills_nothing_is_news():
    watch = Watch()
    watch.take(board(alice=["a:busy"], bob=["b:busy"]), now=0.0)
    watch.forget()                                       # the grange restarted: the board starts over
    assert watch.board() == {}
    watch.take(board(), now=20.0, quiet=True)
    watch.take(board(alice=["a:idle"]), now=21.0, quiet=True)
    watch.take(board(alice=["a:idle"], bob=["b:busy"]), now=22.0, quiet=True)
    assert watch.news(22.0) == [], "bob did not leave, alice's agent did not change: nobody was told either"
    watch.take(board(alice=["a:busy"], bob=["b:busy"]), now=30.0)
    assert told(watch) == [("alice", "state", "a", "idle", "busy")], "from there on it is news again"
    assert watch.since == 0.0, "and it has been watching since the first board"


def test_forgetting_keeps_what_already_happened():
    watch = Watch()
    watch.take(board(alice=["a:busy"]), now=0.0)
    watch.take(board(alice=["a:idle"]), now=1.0)
    watch.forget()
    watch.take(board(alice=["a:waiting"]), now=2.0)      # the first board after: where it starts again
    assert told(watch) == [("alice", "state", "a", "busy", "idle")]


def test_only_the_newest_changes_are_kept():
    watch = Watch(kept=3)
    watch.take(board(alice=["a:s0"]), now=0.0)
    for i in range(1, 8):
        watch.take(board(alice=[f"a:s{i}"]), now=float(i))
    assert [c.after for c in watch.news(7.0)] == ["s5", "s6", "s7"]


def test_what_it_hands_out_is_a_copy():
    watch = Watch()
    given = board(alice=["a:busy"])
    watch.take(given, now=0.0)
    given["alice"].append(Agent("b", "busy"))
    watch.board()["alice"].clear()
    assert watch.board() == {"alice": [Agent("a", "busy")]}
    with pytest.raises(Exception):
        watch.board()["alice"][0].state = "idle"         # an Agent is not changed after it is reported
