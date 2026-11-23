# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The window: it draws every moment of the demo at several sizes, its keys work, and it
freezes on the last picture when nothing answers."""
from __future__ import annotations

import logging
import re

import pytest

import main
from demo import DemoSource
from model import Snapshot
from standins import Breaks, Scripted, drive, healthy, not_running, today


def text_of(app, wid: str) -> str:
    return str(app.query_one(wid).render())


@pytest.mark.parametrize("size", [(150, 46), (120, 36), (100, 30), (190, 60)])
@drive
async def test_it_draws_the_whole_demo_loop(size):
    for t in (0, 40, 70, 95, 120, 133, 165, 203, 216, 240, 285, 480):
        app = main.GrangeViz(DemoSource(start=float(t)), live=False)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            assert app.columns, f"t={t}"
            assert "headscale" in text_of(app, "#grid"), f"t={t}"


@drive
async def test_keys_move_the_selection_scroll_and_open_the_detail():
    app = main.GrangeViz(DemoSource(start=165.0), live=False)
    async with app.run_test(size=(100, 34)) as pilot:
        await pilot.pause()
        assert app.view.sel_key == "me"
        await pilot.press("right")
        assert app.view.sel_key == app.columns[1].key
        await pilot.press("shift+right", "shift+right")
        assert app.view.view_start == 2 and app.view.sel_key == app.columns[1].key
        await pilot.press("enter")
        assert app.query_one("#detail").display and app.columns[1].name in text_of(app, "#detail")
        await pilot.press("t")
        assert app.view.by_address
        await pilot.press("left")
        assert app.view.sel_key in {c.key for c in app.columns}


@drive
async def test_demo_keys_pause_speed_and_jump():
    source = DemoSource(start=100.0)
    app = main.GrangeViz(source, live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        await pilot.press("space")
        assert source.paused
        await pilot.press("plus")
        assert source.speed == 4
        await pilot.press("minus")
        assert source.speed == 1
        before = source.snapshot().loop
        await pilot.press("e")
        assert source.snapshot().loop != before


@drive
async def test_nothing_answering_freezes_the_last_picture_and_says_so():
    source = Scripted(DemoSource(start=165.0).snapshot())
    app = main.GrangeViz(source, live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        names = [c.name for c in app.columns]
        source.snap = DemoSource(start=203.0).snapshot()
        assert not source.snap.reachable
        app.tick()
        await pilot.pause()
        assert [c.name for c in app.columns] == names, "the last picture stays"
        assert app.shown.reachable and not app.snap.reachable
        assert "nothing answers" in text_of(app, "#head")
        assert "make up" in text_of(app, "#diag")
        assert app.query_one("#grid").styles.text_opacity < 1


@drive
async def test_a_grange_that_refuses_everything_is_said_to_be_not_running():
    """Its image is out of date: 503 on every route. The top line and the diagnosis say it is
    not running, and the rows it would fill show nothing, not what it said before."""
    source = Scripted(today([healthy("alice")]))
    app = main.GrangeViz(source, live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        assert "━" in text_of(app, "#grid"), "alice's record, as a bar"
        source.snap = not_running([healthy("alice")])              # a source that hands her record on anyway
        app.tick()
        await pilot.pause()
        head, grid, diag = (text_of(app, wid) for wid in ("#head", "#grid", "#diag"))
        assert "is not running" in head and "503" in head and "make update" in head and "2h13" in head
        assert "djp3" not in head and "cursor" not in head
        assert "━" not in grid and "not known" in grid and "stopped" in grid and "unknown" in grid
        assert "not running" in diag and "make update" in diag and "3f4d4a6" in diag and "4fc66bd" in diag
        assert "every column reads green" not in diag
        assert "not running" in text_of(app, "#liveness") and "alice" not in text_of(app, "#liveness")
        assert app.query_one("#grid").styles.text_opacity == 1, "the tailnet rows are live: nothing is dimmed"
        source.snap = today([healthy("alice")])                    # make update
        app.tick()
        await pilot.pause()
        assert "━" in text_of(app, "#grid") and "djp3" in text_of(app, "#head")


# ---- the farm news, under the diagnosis --------------------------------------------------------------
def screen_of(app) -> list:
    return [strip.text for strip in app.screen._compositor.render_strips()]


@pytest.mark.parametrize("size", [(150, 46), (120, 36), (190, 60), (230, 70)])
@drive
async def test_the_farm_news_is_under_the_diagnosis(size):
    app = main.GrangeViz(DemoSource(start=165.0), live=False)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        lines = screen_of(app)
        diagnosis = next(i for i, line in enumerate(lines) if line.startswith(" diagnosis"))
        title = next(i for i, line in enumerate(lines) if line.startswith(" farm news"))
        assert title > diagnosis and lines[title - 1].strip() == "", "below it, after a blank line"
        newest = app.snap.farm_news[-1]
        assert newest.name in lines[title + 1] and lines[title + 1].split()[0] == "4s"
        assert len(lines) - 1 - title >= 4, "its title and at least three rows, above the keys"
        assert all(len(line) <= size[0] for line in lines)


@drive
async def test_picking_a_column_shows_that_granges_agents_and_its_own_news():
    app = main.GrangeViz(DemoSource(start=165.0), live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        assert text_of(app, "#news").startswith("farm news")
        await pilot.press("right")
        picked = app.columns[1]
        pane = text_of(app, "#news").splitlines()
        agents = app.snap.farms[picked.name]
        assert pane[0].startswith(f"{picked.label}'s farm  {len(agents)} agents now:")
        assert all(a.name in pane[0] for a in agents)
        others = {a.name for gid, farm in app.snap.farms.items() if gid != picked.name for a in farm}
        assert not any(name in "\n".join(pane) for name in others), "nobody else's"
        await pilot.press("left")
        assert text_of(app, "#news").startswith("farm news")


@drive
async def test_the_detail_pane_opens_beside_the_farm_news():
    app = main.GrangeViz(DemoSource(start=165.0), live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        wide = app.query_one("#news").size.width
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one("#detail").display and app.query_one("#news").size.width == wide - 58
        title = next(line for line in screen_of(app) if line.startswith(" farm news"))
        assert "long-poll" in title, "the detail's rows run on beside it, to the right"
        assert all(t.cell_len <= wide - 58 for t in app.farm_news(wide - 58, 9)), "and the news keeps to its side"


@drive
async def test_the_farm_news_says_what_it_is_under_the_pointer():
    app = main.GrangeViz(DemoSource(start=165.0), live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        tip = app.query_one("#news").tooltip
        assert "GET /v1/agents" in tip.plain and "made up" in tip.plain
    app = main.GrangeViz(Scripted(today([healthy("alice")])), live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        assert "made up" not in app.query_one("#news").tooltip.plain


@drive
async def test_the_farm_news_shows_no_agent_while_the_grange_refuses_everything():
    from model import Agent, FarmChange
    snap = today([healthy("alice")])
    snap.farms = {"alice": [Agent("pr-7 docs", "busy")]}
    snap.farm_news = [FarmChange(3.0, "alice", "started", name="pr-7 docs", after="busy")]
    source = Scripted(snap)
    app = main.GrangeViz(source, live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        assert "pr-7 docs" in text_of(app, "#news")
        stopped = not_running([healthy("alice")])                  # a source that hands the farms on anyway
        stopped.farms, stopped.farm_news = snap.farms, snap.farm_news
        source.snap = stopped
        app.tick()
        await pilot.pause()
        assert text_of(app, "#news") == "farm news  not known: your grange is not running"
        assert "pr-7 docs" not in "\n".join(screen_of(app))


@drive
async def test_the_farm_news_freezes_and_dims_with_the_rest_when_nothing_answers():
    source = Scripted(DemoSource(start=165.0).snapshot())
    app = main.GrangeViz(source, live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        newest = text_of(app, "#news").splitlines()[1][:60]
        assert newest.split()[0] == "4s" and app.query_one("#news").styles.text_opacity == 1
        source.snap = DemoSource(start=203.0).snapshot()
        app.tick()
        await pilot.pause()
        assert text_of(app, "#news").splitlines()[1][:60] == newest, "the last picture stays, its ages where they were"
        assert app.query_one("#news").styles.text_opacity < 1


@drive
async def test_a_long_diagnosis_keeps_its_rows_and_the_farm_news_gets_what_is_left():
    """Where it breaks comes first. Twelve broken columns in a short window leave the news no
    row, and nothing is drawn over the diagnosis or the keys."""
    from model import Record
    broken = [healthy(f"peer{i}") for i in range(12)]
    for i, peer in enumerate(broken):
        peer.key = peer.address = f"100.64.0.{20 + i}:7789"
        peer.record = Record(seen_age=3.0, farm_age=200.0, farm_stale=True, agents=1)
    app = main.GrangeViz(Scripted(today(broken)), live=False)
    async with app.run_test(size=(150, 36)) as pilot:
        await pilot.pause()
        lines = screen_of(app)
        assert sum("farm_stale" in line for line in lines) >= 5 and "quit" in lines[-1]
        assert not any(line.startswith(" farm news") for line in lines)


TIME_OF_DAY = re.compile(r"\b\d{1,2}:\d\d(:\d\d)?\b")


@drive
async def test_the_top_line_shows_no_clock():
    """The menu bar has one. The top line ends with what the grange says, and with nothing at
    all when the grange cannot be asked."""
    for snap in (today([healthy("alice")]), not_running([healthy("alice")]),
                 today(reachable=False, unreachable_for=12.0)):
        app = main.GrangeViz(Scripted(snap), live=False)
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            head = text_of(app, "#head")
            assert not TIME_OF_DAY.search(head), head
            assert head == head.rstrip(), "and no gap is left where it was"
    assert not hasattr(Snapshot(), "clock"), "a source has nowhere to put one"


@drive
async def test_the_demo_still_says_where_it_is_in_its_loop():
    app = main.GrangeViz(DemoSource(start=100.0), live=False)
    async with app.run_test(size=(170, 46)) as pilot:
        await pilot.pause()
        head = text_of(app, "#head")
        assert "1m40 into the loop" in head and "▶ 1×" in head


@drive
async def test_the_header_does_not_say_the_mesh_route_is_missing():
    app = main.GrangeViz(Scripted(today([healthy("alice")])), live=False)
    async with app.run_test(size=(170, 46)) as pilot:
        await pilot.pause()
        head = text_of(app, "#head")
        assert "no /v1/mesh" not in head and "/v1/mesh: no answer" in head


@drive
async def test_a_teammate_going_quiet_does_not_end_the_viewer():
    """What 0.1.1 died of: a record still held, and not refreshed for 45 s."""
    alice = healthy("alice")
    source = Scripted(today([alice]))
    app = main.GrangeViz(source, live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        for seen in (30.0, 44.0, 45.0, 50.0, 59.9):
            alice.record.seen_age = seen
            app.tick()
            await pilot.pause()
        assert "silent" in text_of(app, "#grid") and app.return_code is None


@drive
async def test_a_crash_is_written_to_the_log(caplog):
    """Textual prints the traceback on the terminal as the app ends, and nothing else: the Mac
    app then points at a log that would hold only "exiting (return code 1)"."""
    app = main.GrangeViz(Breaks(today()), live=False)
    with caplog.at_level(logging.ERROR, logger="agent_grange_viz"):
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.call_later(app.tick)                           # the second reading raises, inside the app
                await pilot.pause(0.2)
        except RuntimeError as died:                               # Textual hands it back as the test ends
            assert "the source broke" in str(died)
        else:
            raise AssertionError("the source did not break: nothing was tested")
    crashed = [r for r in caplog.records if "crashed" in r.getMessage()]
    assert crashed and crashed[0].exc_info and "the source broke" in str(crashed[0].exc_info[1])
    assert app.return_code == 1


@drive
async def test_before_the_grange_has_ever_answered_it_waits():
    source = Scripted(Snapshot(reachable=False, ever_reached=False, port=17981))
    app = main.GrangeViz(source, live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        assert not app.columns
        assert "waiting for the grange on 127.0.0.1:17981" in text_of(app, "#grid")
        assert "is the grange running" in text_of(app, "#head")
        assert text_of(app, "#news") == "" and app.query_one("#news").tooltip is None


@drive
async def test_the_tip_under_the_pointer_follows_it():
    app = main.GrangeViz(DemoSource(start=165.0), live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        grid = app.query_one("#grid")
        await pilot.hover("#grid", offset=(3, 0))
        assert grid.tooltip is not None and "headscale" in grid.tooltip.plain
        await pilot.hover("#grid", offset=(16, 2))
        assert "your sidecar" in grid.tooltip.plain


def test_the_command_line():
    args = main.parse(["--demo", "--at", "70"])
    assert args.demo and args.at == 70.0 and args.port is None
    assert main.parse(["--port", "17981", "--no-docker"]).port == 17981
    assert isinstance(main.make_source(main.parse(["--demo"])), DemoSource)
