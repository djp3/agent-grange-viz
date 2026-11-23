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


def yellow(lines) -> list:
    """The rows drawn with the yellow mark, as words."""
    return [" ".join(t.plain.split()) for t in lines if any(WAITING in str(span.style) for span in t.spans)]


WAITS = {"djp3": [Agent("viz-pr-9 farm news", "waiting")],
         "alice": [Agent("pr-7 docs", "waiting"), Agent("pr-11 tests", "busy")]}


def test_yellow_is_only_for_an_agent_of_your_own_farm():
    """Yellow means one thing in this app: yours to act on, on this Mac. Alice's agent waits
    too, for her."""
    assert yellow(news.draw(snap_with(CHANGES, WAITS), "", None, 100, 10)) == [
        "12s you ◐ viz-pr-9 farm news busy → waiting for you"]


# ---- yellow is for now: a wait that is over is what happened, not what to do ---------------------------
def mine(age, name, before, after):
    return FarmChange(age, "djp3", "state", name=name, before=before, after=after)


@pytest.mark.parametrize("after", ["busy", "idle"])
def test_a_wait_that_you_answered_is_no_longer_yellow(after):
    """It began waiting, you answered it, and it went on. The old row kept its yellow, and
    after an hour the pane held a dozen of them with nothing waiting at all."""
    changes = [mine(40.0, "viz-pr-9 farm news", "busy", "waiting"), mine(4.0, "viz-pr-9 farm news", "waiting", after)]
    lines = news.draw(snap_with(changes, {"djp3": [Agent("viz-pr-9 farm news", after)]}), "", None, 100, 10)
    assert yellow(lines) == []
    assert " ".join(lines[2].plain.split()) == "40s you ◐ viz-pr-9 farm news busy → waiting for you", \
        "what happened is still told as it happened"


def test_a_wait_whose_agent_ended_is_no_longer_yellow():
    changes = [mine(40.0, "viz-pr-9 farm news", "busy", "waiting"),
               FarmChange(4.0, "djp3", "ended", name="viz-pr-9 farm news", before="waiting")]
    assert yellow(news.draw(snap_with(changes, {"djp3": []}), "", None, 100, 10)) == []


def test_of_an_agent_that_waited_more_than_once_only_the_newest_wait_is_yellow():
    changes = [mine(90.0, "viz-pr-9 farm news", "busy", "waiting"), mine(60.0, "viz-pr-9 farm news", "waiting", "busy"),
               mine(7.0, "viz-pr-9 farm news", "busy", "waiting")]
    lines = news.draw(snap_with(changes, {"djp3": [Agent("viz-pr-9 farm news", "waiting")]}), "", None, 100, 10)
    assert yellow(lines) == ["7s you ◐ viz-pr-9 farm news busy → waiting for you"]


def test_there_are_never_more_yellow_rows_than_agents_of_yours_waiting_now():
    changes = [mine(90.0, "a", "busy", "waiting"), mine(80.0, "b", "busy", "waiting"), mine(70.0, "c", "idle", "waiting"),
               mine(60.0, "a", "waiting", "busy"), mine(50.0, "a", "busy", "waiting"), mine(40.0, "c", "waiting", "idle")]
    farm = {"djp3": [Agent("a", "waiting"), Agent("b", "waiting"), Agent("c", "idle"), Agent("d", "waiting")]}
    assert yellow(news.draw(snap_with(changes, farm), "", None, 100, 10)) == [
        "50s you ◐ a busy → waiting for you", "1m you ◐ b busy → waiting for you"], "d began before the viewer did"


@pytest.mark.parametrize("farms", [{}, {"djp3": None}], ids=["no record of your farm", "your farm has not reported"])
def test_while_what_your_agents_are_doing_is_not_known_nothing_is_yellow(farms):
    """Your grange has just restarted: for up to 30 s it has not heard from your farm. A row
    is yellow because an agent waits now, and that is not known."""
    assert yellow(news.draw(snap_with([mine(12.0, "viz-pr-9 farm news", "busy", "waiting")], farms), "", None, 100, 10)) == []


def test_in_the_demo_a_row_is_yellow_exactly_where_an_agent_of_yours_still_waits():
    seen = 0
    for t in range(0, 600, 20):
        snap = DemoSource(start=float(t)).snapshot()
        waiting = {a.name for a in snap.farms[snap.me.gid] if a.state == "waiting"}
        began = {c.name for c in snap.farm_news if c.grange == snap.me.gid and c.kind == "state" and c.after == "waiting"}
        rows = yellow(news.draw(snap, "", None, 100, 250))
        assert len(rows) == len(waiting & began) and all(any(name in row for name in waiting) for row in rows), t
        seen += len(rows)
    assert seen, "the demo does show one: this test tests something"


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
    assert news.draw(snap_with(farms={"alice": [Agent("pr-7 docs", "compacting")]}), "alice", "alice", 100, 4)[0].plain \
        == "alice's farm  1 agent now:  · pr-7 docs (compacting)", "one grange's agents now: the word, since no mark says it"


# ---- one grange's agents now: the roll call ---------------------------------------------------------
def roll_call(agents, w=100) -> str:
    return news.draw(snap_with(farms={"alice": list(agents)}), "alice", "alice", w, 4)[0].plain


def test_the_roll_call_says_a_state_in_words_only_where_no_mark_says_it():
    head = roll_call([Agent("pr-7 docs", "busy"), Agent("pr-9 lint", "compacting"), Agent("pr-11 tests", "idle"),
                      Agent("pr-12 ci", "waiting")], w=120)
    assert head == "alice's farm  4 agents now:  ◐ pr-12 ci  ● pr-7 docs  ○ pr-11 tests  · pr-9 lint (compacting)"


def test_the_state_in_words_counts_toward_what_fits():
    """Two agents whose names would both fit, were the word not there."""
    from rich.cells import cell_len
    agents = [Agent("pr-7 docs", "compacting"), Agent("pr-9 lint", "compacting")]
    assert roll_call(agents, w=79) == "alice's farm  2 agents now:  · pr-7 docs (compacting)  · pr-9 lint (compacting)"
    head = roll_call(agents, w=78)
    assert head == "alice's farm  2 agents now:  · pr-7 docs (compacting)  +1 more" and cell_len(head) <= 78


@pytest.mark.parametrize("w", [60, 80, 100, 137, 180])
def test_wide_names_in_the_roll_call_leave_the_count_in_view(w):
    """A name in Japanese takes twice the cells it has characters. Counted in characters, too
    many were let onto the line, and what ran off its end was the count of the rest."""
    from rich.cells import cell_len
    head = roll_call([Agent(f"{WIDE * 3} {i}", "busy") for i in range(6)], w=w)
    shown = head.count("●")
    assert cell_len(head) <= w and 1 <= shown < 6, head
    assert head.endswith(f"+{6 - shown} more"), "every agent is on the line or in the count"


def test_a_first_name_too_long_for_the_line_is_cut_not_left_out():
    """Left out, the line read '2 agents now:  +2 more' and named nobody."""
    from rich.cells import cell_len
    head = roll_call([Agent("x" * 150, "busy"), Agent("pr-2 short", "idle")])
    assert head.startswith("alice's farm  2 agents now:  ● xxxx") and head.endswith("x…  +1 more") and cell_len(head) == 100
    alone = roll_call([Agent(WIDE * 20, "waiting")])
    assert alone.startswith(f"alice's farm  1 agent now:  ◐ {WIDE}") and alone.endswith("…") and cell_len(alone) <= 100
    assert all(ch in WIDE + "…" for ch in alone.split("◐ ")[1]), "and no wide character is cut through"


def test_only_the_first_is_ever_cut_and_a_line_with_no_room_for_it_counts_everyone():
    head = roll_call([Agent("pr-1 short", "busy"), Agent("x" * 150, "busy"), Agent("pr-3 short", "idle")])
    assert head == "alice's farm  3 agents now:  ● pr-1 short  +2 more"
    assert roll_call([Agent("pr-1 short", "busy"), Agent("pr-2 short", "busy")], w=40) == "alice's farm  2 agents now:  +2 more"


def test_a_long_name_is_cut_and_a_long_grange_id_too():
    change = FarmChange(3.0, "lightsail-test", "started", name="x" * 60, after="busy")
    line = plain(news.draw(snap_with([change]), "", None, 100, 4))[1]
    assert "lights…" in line and "x" * 39 + "…" in line and "x" * 40 not in line and line.rstrip().endswith("started · busy")


# ---- widths are terminal cells, not characters -------------------------------------------------------
WIDE = "修正テスト"                                    # five characters, ten cells


def test_a_name_in_wide_characters_is_cut_so_that_what_happened_stays_on_its_row():
    """Forty characters of Japanese are eighty cells. Counted as forty they were not cut, the
    row ran past the pane, and what was trimmed off was what happened."""
    from rich.cells import cell_len
    change = FarmChange(3.0, "alice", "state", name=WIDE * 8, before="busy", after="waiting")
    line = news.draw(snap_with([change]), "", None, 100, 4)[1].plain
    assert cell_len(line) <= 100 and line.rstrip().endswith("busy → waiting for its human")
    assert WIDE * 4 not in line and "…" in line, "the name gave way, to the forty cells a name may have"


def test_rows_with_wide_and_plain_names_line_up():
    from rich.cells import cell_len
    changes = [FarmChange(9.0, "alice", "started", name=WIDE, after="busy"),
               FarmChange(6.0, "東京支社のグレンジ", "started", name="pr-7 docs 🐓", after="busy"),
               FarmChange(3.0, "bob", "started", name="pr-8 plain text", after="busy")]
    lines = [t.plain for t in news.draw(snap_with(changes), "", None, 100, 6)[1:]]
    assert len({cell_len(line[:line.index("started")]) for line in lines}) == 1, lines
    assert len({cell_len(line[:line.index("+ ")]) for line in lines}) == 1, "the grange's name is a field too"
    assert "東京支…" in lines[1] and "東京支社" not in lines[1], "seven cells of it: three characters and the …"


def test_a_wide_character_is_never_cut_through():
    from rich.cells import cell_len
    for width in range(1, 12):
        cut = news._cut(WIDE, width)
        assert cell_len(cut) <= width and all(ch in WIDE + "…" for ch in cut), (width, cut)
        assert cut == WIDE or cut.endswith("…")
    assert news._cut(WIDE, 10) == WIDE and news._cut(WIDE, 9) == "修正テス…" and news._cut(WIDE, 4) == "修…"
    assert news._field("ab", 4) == "ab   " and cell_len(news._field(WIDE, 6)) == 7


def test_a_grange_id_is_drawn_as_one_line_with_no_control_character():
    """An agent's name is cleaned where it comes in (live.py). A grange's id is drawn in the
    pane as it stands in the Snapshot, so the pane cleans that one itself."""
    change = FarmChange(3.0, "ali\x1b[31m\nce", "started", name="pr-7 docs", after="busy")
    lines = news.draw(snap_with([change], {"ali\x1b[31m\nce": []}), "", None, 100, 4)
    assert len(lines) == 2 and " ".join(lines[1].plain.split()) == "3s ali[31… + pr-7 docs started · busy"
    head = news.draw(snap_with(farms={"x": []}), "ali\x1b[31m\nce?", "x", 100, 4)[0].plain
    assert head == "ali[31m ce?'s farm  reports no agents"


def test_while_the_grange_refuses_everything_no_agent_is_drawn():
    """Its image is out of date: nothing keeps up what it said before. Even from a source that
    handed the farms on anyway, the pane says only that it is not known."""
    snap = not_running([healthy("alice")])
    snap.farm_news, snap.farms, snap.farm_watched = list(CHANGES), {"alice": [Agent("pr-7 docs", "busy")]}, 90.0
    for label, gid in (("", None), ("alice", "alice")):
        assert plain(news.draw(snap, label, gid, 120, 8)) == ["farm news  not known: your grange is not running"]


def test_before_the_grange_has_ever_answered_nothing_is_said_of_what_it_holds():
    """Started while the grange is down, the viewer still has the tailnet's devices as
    columns. With no board ever read, 'no farm has reported a change' and 'your grange holds
    no record of it' would both be claims about a grange that has said nothing."""
    snap = today([healthy("alice")], reachable=False, ever_reached=False, unreachable_for=4.0)
    for label, gid in (("", None), ("alice", "alice")):
        assert plain(news.draw(snap, label, gid, 120, 8)) == ["farm news  not known: your grange has not answered yet"]
    assert news.draw(snap, "", None, 30, 8)[0].cell_len <= 30


def test_a_grange_that_answered_and_then_went_quiet_keeps_its_last_news():
    """Not reachable now is another thing than never reached: the last picture is still drawn."""
    snap = snap_with(CHANGES, {"alice": [Agent("pr-7 docs", "busy")]})
    snap.reachable, snap.unreachable_for = False, 6.0
    assert "went off the board · expired" in plain(news.draw(snap, "", None, 120, 8))[1]
    assert plain(news.draw(snap, "alice", "alice", 120, 8))[0] == "alice's farm  1 agent now:  ● pr-7 docs"


def test_the_tip_says_what_the_marks_mean_and_where_it_comes_from():
    tip = news.tip().plain
    assert all(mark in tip for mark in "+−◐●○✕") and "GET /v1/agents" in tip and "made up" not in tip
    assert "only what changed while this viewer was open" in tip
    assert "made up" in news.tip(demo=True).plain
