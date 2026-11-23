# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The farm news under the diagnosis: what the pane draws from a Snapshot, for every grange
and for one, and what it does not draw while the grange refuses everything."""
from __future__ import annotations

import pytest

import news
from demo import DemoSource
from model import Agent, FarmChange
from standins import healthy, not_running, today
from theme import WAITING


def snap_with(changes=(), farms=None, watched=90.0):
    snap = today([healthy("alice"), healthy("bob")])
    snap.farm_news, snap.farms, snap.farm_watched = list(changes), farms or {}, watched
    return snap


def plain(lines) -> list:
    return [t.plain for t in lines]


CHANGES = [FarmChange(70.0, "bob", "on", agents=2, note="first record"),
           FarmChange(50.0, "alice", "started", name="pr-11 tests", after="busy"),
           FarmChange(31.0, "alice", "ended", name="pr-8 lint", before="idle"),
           FarmChange(12.0, "djp3", "state", name="viz-pr-9 farm news", before="busy", after="waiting"),
           FarmChange(9.0, "alice", "state", name="pr-7 docs", before="busy", after="waiting"),
           FarmChange(4.0, "bob", "off", agents=2, note="expired")]


def test_every_granges_news_newest_first_in_words():
    lines = plain(news.draw(snap_with(CHANGES), "", None, 100, 10))
    assert lines[0].startswith("farm news  what each grange's farm reported")
    assert [" ".join(line.split()) for line in lines[1:]] == [
        "4s bob ✕ 2 agents went off the board · expired",
        "9s alice ◐ pr-7 docs busy → waiting for its human",
        "12s you ◐ viz-pr-9 farm news busy → waiting for you",
        "31s alice − pr-8 lint ended · was idle",
        "50s alice + pr-11 tests started · busy",
        "1m bob + 2 agents came onto the board · first record"]


def test_yellow_is_only_for_an_agent_of_your_own_farm():
    """Yellow means one thing in this app: yours to act on, on this Mac."""
    lines = news.draw(snap_with(CHANGES), "", None, 100, 10)
    yellow = [t.plain.split()[1] for t in lines if any(WAITING in str(span.style) for span in t.spans)]
    assert yellow == ["you"]


@pytest.mark.parametrize("w, h", [(40, 3), (72, 4), (98, 6), (148, 9), (228, 23), (300, 40)])
def test_it_fits_the_pane_it_is_given(w, h):
    snap = DemoSource(start=165.0).snapshot()
    for label, gid in (("", None), ("alice", "alice"), ("frank", "frank")):
        lines = news.draw(snap, label, gid, w, h)
        assert 1 <= len(lines) <= h and all(t.cell_len <= w for t in lines), (label, [t.cell_len for t in lines])


def test_a_wide_pane_holds_two_and_three_columns_read_downward():
    snap = DemoSource(start=165.0).snapshot()
    newest = snap.farm_news[::-1]
    one, two, three = (news.draw(snap, "", None, w, 5) for w in (100, 150, 230))
    assert len(one) == len(two) == len(three) == 5
    def names(lines):                                    # the agents named in a line, in the order drawn
        return [[c.name for c in newest[:12] if c.name and c.name in t.plain] for t in lines[1:]]
    first = [c.name or f"{c.agents} agent" for c in newest[:12]]
    assert all(first[i] in one[1 + i].plain for i in range(4))
    assert all(first[i] in two[1 + i].plain and first[4 + i] in two[1 + i].plain for i in range(4)), names(two)
    assert all(first[8 + i] in three[1 + i].plain for i in range(4)), names(three)


def test_one_grange_is_its_agents_now_and_its_own_changes():
    farms = {"alice": [Agent("pr-7 docs", "busy"), Agent("pr-11 tests", "idle"), Agent("pr-12 ci", "waiting")]}
    lines = plain(news.draw(snap_with(CHANGES, farms), "alice", "alice", 120, 10))
    assert lines[0] == "alice's farm  3 agents now:  ◐ pr-12 ci  ● pr-7 docs  ○ pr-11 tests", "who needs someone first"
    assert [" ".join(line.split()) for line in lines[1:]] == [
        "9s ◐ pr-7 docs busy → waiting for its human", "31s − pr-8 lint ended · was idle",
        "50s + pr-11 tests started · busy"]


def test_agents_that_do_not_fit_the_line_are_counted():
    farms = {"alice": [Agent(f"proj-{i}-pr-{i} a long session name", "busy") for i in range(9)]}
    head = news.draw(snap_with(farms=farms), "alice", "alice", 100, 4)[0].plain
    assert head.startswith("alice's farm  9 agents now:  ● proj-0-pr-0 a long session name") and head.endswith("more")
    shown = head.count("●")
    assert 1 <= shown < 9 and head.endswith(f"+{9 - shown} more") and len(head) <= 100


def test_a_grange_with_no_record_and_a_farm_with_no_agents_say_so():
    lines = plain(news.draw(snap_with(CHANGES, {"alice": []}), "bob?", "bob", 120, 6))
    assert lines[0] == "bob?'s farm  not on the board: your grange holds no record of it"
    assert "went off the board · expired" in lines[1], "and how its agents went is still told"
    assert plain(news.draw(snap_with(farms={"alice": []}), "alice", "alice", 120, 6)) == [
        "alice's farm  reports no agents", "  it has reported no change in the 1m30 this viewer has been watching"]


def test_a_farm_that_has_not_reported_is_not_known_which_is_not_nobody():
    """Her grange has just restarted: its record lists no agents because it has heard nothing."""
    lines = plain(news.draw(snap_with(CHANGES, {"alice": None}), "alice", "alice", 120, 6))
    assert lines[0] == "alice's farm  not known: it has not reported since its grange started"
    assert "pr-7 docs" in lines[1], "what it reported before is still its news"


def test_no_news_says_for_how_long_it_has_been_watching():
    assert plain(news.draw(snap_with(watched=12.0), "", None, 120, 6))[1] == \
        "  no farm has reported a change in the 12s this viewer has been watching"
    assert plain(news.draw(snap_with(watched=None), "", None, 120, 6))[1] == "  no farm has reported a change yet"


def test_your_own_column_is_every_granges_news():
    snap = snap_with(CHANGES)
    assert plain(news.draw(snap, "djp3", "djp3", 100, 10)) == plain(news.draw(snap, "", None, 100, 10))


def test_a_state_that_is_not_one_of_agent_farms_words_is_passed_on_as_it_came():
    """The grange passes any word on (R4.1): the pane does too, with no mark of its own."""
    change = FarmChange(3.0, "alice", "state", name="pr-7 docs", before="busy", after="compacting")
    line = plain(news.draw(snap_with([change], {"alice": [Agent("pr-7 docs", "compacting")]}), "", None, 100, 4))[1]
    assert " ".join(line.split()) == "3s alice · pr-7 docs busy → compacting"
    assert "· pr-7 docs" in news.draw(snap_with(farms={"alice": [Agent("pr-7 docs", "compacting")]}),
                                      "alice", "alice", 100, 4)[0].plain


def test_a_long_name_is_cut_and_a_long_grange_id_too():
    change = FarmChange(3.0, "lightsail-test", "started", name="x" * 60, after="busy")
    line = plain(news.draw(snap_with([change]), "", None, 100, 4))[1]
    assert "lights…" in line and "x" * 39 + "…" in line and "x" * 40 not in line and line.rstrip().endswith("started · busy")


def test_while_the_grange_refuses_everything_no_agent_is_drawn():
    """Its image is out of date: nothing keeps up what it said before. Even from a source that
    handed the farms on anyway, the pane says only that it is not known."""
    snap = not_running([healthy("alice")])
    snap.farm_news, snap.farms, snap.farm_watched = list(CHANGES), {"alice": [Agent("pr-7 docs", "busy")]}, 90.0
    for label, gid in (("", None), ("alice", "alice")):
        assert plain(news.draw(snap, label, gid, 120, 8)) == ["farm news  not known: your grange is not running"]


def test_the_tip_says_what_the_marks_mean_and_where_it_comes_from():
    tip = news.tip().plain
    assert all(mark in tip for mark in "+−◐●○✕") and "GET /v1/agents" in tip and "made up" not in tip
    assert "only what changed while this viewer was open" in tip
    assert "made up" in news.tip(demo=True).plain
