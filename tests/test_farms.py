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


# ---- two sessions under one name are told apart by what they are doing -----------------------------
def after(before: list, now: list) -> list:
    """What is told when one farm's report goes from `before` to `now`."""
    watch = Watch()
    watch.take(board(alice=before), now=0.0)
    watch.take(board(alice=now), now=1.0)
    return [(c.kind, c.name, c.before, c.after) for c in watch.news(1.0)]


def test_two_sessions_under_one_name_are_still_two():
    assert after(["task:busy", "task:idle"], ["task:busy"]) == [("ended", "task", "idle", "")]
    assert after(["task:busy"], ["task:waiting", "task:busy"]) == [("started", "task", "", "waiting")], \
        "the busy one is the one that was busy: the other is new"


def test_the_first_of_two_under_one_name_ending_is_that_one_ending():
    """Paired by where they stood in the list, the idle one took the busy one's place: it read
    "busy → idle" and "ended · was idle". One thing happened."""
    assert after(["task:busy", "task:idle"], ["task:idle"]) == [("ended", "task", "busy", "")]


def test_the_order_a_farm_lists_its_agents_in_is_not_news():
    assert after(["task:busy", "task:idle"], ["task:idle", "task:busy"]) == []
    assert after(["a:busy", "b:idle", "c:waiting"], ["c:waiting", "a:busy", "b:idle"]) == []
    assert after(["task:busy", "x:idle", "task:idle"], ["task:idle", "task:busy", "x:busy"]) == [
        ("state", "x", "idle", "busy")], "and what did change beside a reorder is still told"


def test_what_is_left_over_under_a_name_is_what_changed():
    assert after(["task:busy", "task:idle"], ["task:waiting", "task:idle"]) == [("state", "task", "busy", "waiting")]
    assert after(["task:busy", "task:idle"], ["task:waiting", "task:waiting"]) == [
        ("state", "task", "busy", "waiting"), ("state", "task", "idle", "waiting")]
    assert after(["task:busy", "task:busy", "task:idle"], ["task:busy", "task:waiting"]) == [
        ("state", "task", "busy", "waiting"), ("ended", "task", "idle", "")], "one stayed, one changed, one ended"
    assert after(["task:idle"], ["task:busy", "task:busy", "task:idle"]) == [
        ("started", "task", "", "busy"), ("started", "task", "", "busy")]
    assert after(["task:busy", "task:idle"], []) == [("ended", "task", "busy", ""), ("ended", "task", "idle", "")]


def test_while_the_board_refills_nobody_comes_or_goes():
    watch = Watch()
    watch.take(board(alice=["a:busy"], bob=["b:busy"]), now=0.0)
    watch.forget()                                       # the grange restarted: the board starts over
    assert watch.board() == {}
    watch.take(board(), now=20.0, quiet=True)
    watch.take(board(alice=["a:busy"]), now=21.0, quiet=True)
    watch.take(board(alice=["a:busy"], bob=["b:busy"], dave=["d:idle"]), now=22.0, quiet=True)
    assert watch.news(22.0) == [], "bob did not leave and come back, and dave, new here, is where his farm starts"
    watch.take(board(alice=["a:busy"], bob=["b:busy"], dave=["d:idle"], erin=["e:busy"]), now=30.0)
    assert [(c.grange, c.kind, c.agents) for c in watch.news(30.0)] == [("erin", "on", 1)], "from there on it is news"
    assert watch.since == 0.0, "and it has been watching since the first board"


def test_a_grange_that_is_slow_to_come_back_after_the_restart_did_not_come_onto_the_board():
    """The refill is quiet for a few seconds; a record can take longer. What its farm said
    before the restart is still known, so there is nothing to tell."""
    watch = Watch()
    watch.take(board(alice=["a:busy"], bob=["b:busy"]), now=0.0)
    watch.forget()
    watch.take(board(alice=["a:busy"]), now=20.0, quiet=True)
    watch.take(board(alice=["a:busy"], bob=["b:busy"]), now=45.0)        # long after the quiet
    assert watch.news(45.0) == []


def test_what_really_changed_while_this_grange_was_down_is_told_when_it_is_heard_again():
    watch = Watch()
    watch.take(board(alice=["a:busy", "b:idle"]), now=0.0)
    watch.take(board(alice=["a:idle", "b:idle"]), now=1.0)
    watch.forget()
    watch.take(board(alice=["a:idle"]), now=20.0, quiet=True)            # b ended while it was down
    assert told(watch) == [("alice", "state", "a", "busy", "idle"), ("alice", "ended", "b", "idle", "")]
    assert [c.age for c in watch.news(20.0)] == [19.0, 0.0], "what already happened is still what happened"


# ---- a farm that has not reported is not a farm with nobody in it -----------------------------------
def test_a_farm_that_has_not_reported_changes_nothing():
    """A teammate's grange restarts: its record comes with no agents and no farm age, and a
    few seconds later its farm reports the same agents again. Nobody ended, nobody started."""
    watch = Watch()
    watch.take(board(alice=["a:busy", "b:idle"]), now=0.0)
    watch.take({"alice": None}, now=5.0)
    assert watch.news(5.0) == [] and watch.board() == {"alice": None}
    watch.take(board(alice=["a:busy", "b:idle"]), now=12.0)
    assert watch.news(12.0) == []
    watch.take({"alice": None}, now=20.0)
    watch.take(board(alice=["a:waiting"]), now=28.0)     # this time something did change meanwhile
    assert told(watch) == [("alice", "state", "a", "busy", "waiting"), ("alice", "ended", "b", "idle", "")]


def test_the_first_report_of_a_farm_never_heard_is_where_it_starts():
    """The viewer opens a moment after the grange did: its own farm has not pushed yet."""
    watch = Watch()
    watch.take({"me": None, "alice": None}, now=0.0)
    watch.take({"me": [Agent("x", "busy"), Agent("y", "idle")], "alice": None}, now=20.0)
    watch.take(board(me=["x:busy", "y:idle"], alice=["a:busy"]), now=25.0)
    assert watch.news(25.0) == [], "nobody started: they were there before anyone was told"
    watch.take(board(me=["x:waiting", "y:idle"], alice=["a:busy"]), now=30.0)
    assert told(watch) == [("me", "state", "x", "busy", "waiting")]


def test_a_grange_that_comes_onto_the_board_is_told_when_its_farm_is_first_heard():
    watch = Watch()
    watch.take(board(alice=["a:busy"]), now=0.0)
    watch.take({**board(alice=["a:busy"]), "carol": None}, now=5.0, why={"carol": "first record"})
    assert watch.news(5.0) == [], "her grange is there; what her farm says is not known yet"
    watch.take(board(alice=["a:busy"], carol=["x:idle", "y:idle"]), now=14.0)
    assert [(c.grange, c.kind, c.agents, c.note, c.age) for c in watch.news(14.0)] == [
        ("carol", "on", 2, "first record", 0.0)], "one line, with her agents, and how she came"
    watch.take(board(alice=["a:busy"]), now=20.0, why={"carol": "left"})
    watch.take({**board(alice=["a:busy"]), "carol": None}, now=30.0, why={"carol": "back on the board"})
    watch.take(board(alice=["a:busy"], carol=["x:idle"]), now=41.0)
    assert [(c.kind, c.agents, c.note) for c in watch.news(41.0)][1:] == [("off", 2, "left"), ("on", 1, "back on the board")]


def test_a_grange_whose_farm_was_never_heard_goes_without_a_word():
    watch = Watch()
    watch.take(board(alice=["a:busy"]), now=0.0)
    watch.take({**board(alice=["a:busy"]), "carol": None}, now=5.0)
    watch.take(board(alice=["a:busy"]), now=9.0, why={"carol": "left"})
    assert watch.news(9.0) == [], "no agents of hers were ever on the board to go off it"


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
