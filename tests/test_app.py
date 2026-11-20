# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The window: it draws every moment of the demo at several sizes, its keys work, and it
freezes on the last picture when nothing answers."""
from __future__ import annotations

import pytest

import main
from demo import DemoSource
from model import Snapshot
from standins import Scripted, drive


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
        before = source.snapshot().clock
        await pilot.press("e")
        assert source.snapshot().clock != before


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
async def test_before_the_grange_has_ever_answered_it_waits():
    source = Scripted(Snapshot(reachable=False, ever_reached=False, port=17981))
    app = main.GrangeViz(source, live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        assert not app.columns
        assert "waiting for the grange on 127.0.0.1:17981" in text_of(app, "#grid")
        assert "is the grange running" in text_of(app, "#head")


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
