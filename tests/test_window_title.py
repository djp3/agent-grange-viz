# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The terminal's title (the Mac app's title bar, iTerm2's tab) reads the rooster, the name
and the version, then a radar dish for each teammate's grange that is bound, up to ten.
Textual never sets a terminal title, so the app writes OSC 0 itself: on mount, when the count
changes, and cleared as it quits."""
from pathlib import Path

from textual.drivers.headless_driver import HeadlessDriver

import apppaths
import main
from demo import DemoSource
from model import Gone, Me, Peer, Record, Snapshot, Tailnet
from standins import Scripted, drive

ROOT = Path(__file__).resolve().parent.parent


def test_title_sequence_is_osc_zero():
    assert main.title_sequence("🐓 agent-grange-viz") == "\x1b]0;🐓 agent-grange-viz\x07"
    assert main.title_sequence("") == "\x1b]0;\x07"


def test_the_title_carries_the_rooster_and_the_version():
    v = apppaths.app_version()
    assert v                                   # a checkout has a VERSION file
    assert main.WINDOW_TITLE == f"🐓 agent-grange-viz v{v}"


def test_a_dish_for_each_bound_grange_up_to_ten(monkeypatch):
    monkeypatch.setattr(main, "WINDOW_TITLE", "🐓 agent-grange-viz v0.9.1")
    assert main.window_title(0) == "🐓 agent-grange-viz v0.9.1"
    assert main.window_title(1) == "🐓 agent-grange-viz v0.9.1 📡"
    assert main.window_title(3) == "🐓 agent-grange-viz v0.9.1 📡📡📡"
    assert main.window_title(10) == "🐓 agent-grange-viz v0.9.1 " + "📡" * 10
    assert main.window_title(11) == main.window_title(10) == main.window_title(100)


def test_the_mac_app_shows_the_same_words_until_the_viewer_sets_its_own():
    swift = (ROOT / "macos" / "Sources" / "AgentGrangeViz" / "AppDelegate.swift").read_text()
    assert 'return "🐓 agent-grange-viz"' in swift
    assert r'return "🐓 agent-grange-viz v\(v)"' in swift
    assert "📡" not in swift, "the dishes are the viewer's to add"


# ---- which granges count --------------------------------------------------------------------------
def held(name: str, **kw) -> Peer:
    return Peer(key=name, name=name, record=Record(seen_age=3.0, farm_age=8.0, farm_stale=False, agents=1), **kw)


def test_with_the_mesh_a_grange_counts_once_it_is_bound():
    snap = Snapshot(has_mesh=True, peers=[held("alice", bound=True), held("carol", bound=False),
                                         Peer(key="judy", name="judy", bound=False),
                                         Peer(key="frank", name="frank", bound=True, gone=Gone("expired", 30.0))])
    assert main.bound_nodes(snap) == 2, "bound is what counts: alice, and frank whose record has gone"


def test_without_the_mesh_a_record_held_stands_in():
    snap = Snapshot(has_mesh=False, peers=[held("alice"), held("bob"),
                                          Peer(key="judy", name="judy", tailnet=Tailnet(online=True)),
                                          Peer(key="frank", name="frank", gone=Gone("expired", 30.0))])
    assert all(p.bound is None for p in snap.peers)
    assert main.bound_nodes(snap) == 2


def test_a_grange_on_two_devices_is_one_grange():
    """A sidecar registered again leaves its old device listed beside the new one, both named
    grange-alice, and the viewer gives each the record held for alice: two columns, one grange."""
    old = held("alice")
    old.key, old.address, old.hostname = "100.64.0.6:7789", "100.64.0.6:7789", "grange-alice"
    new = held("alice")
    new.key, new.address, new.hostname = "100.64.0.21:7789", "100.64.0.21:7789", "grange-alice"
    snap = Snapshot(has_mesh=False, peers=[old, new, held("bob")])
    assert main.bound_nodes(snap) == 2


def test_a_grange_bound_at_two_addresses_is_one_grange():
    """With the mesh a binding is per address, and two addresses can be bound to one id."""
    snap = Snapshot(has_mesh=True, peers=[held("alice", bound=True), held("alice", bound=True),
                                         held("bob", bound=True), held("carol", bound=False)])
    snap.peers[0].key, snap.peers[1].key = "100.64.0.6:7789", "100.64.0.21:7789"
    assert main.bound_nodes(snap) == 2


def test_nothing_is_bound_while_nothing_answers():
    snap = Snapshot(has_mesh=True, reachable=False, unreachable_for=12.0, peers=[held("alice", bound=True)])
    assert main.bound_nodes(snap) == 0
    assert main.bound_nodes(Snapshot(reachable=False, ever_reached=False)) == 0


def test_nothing_is_bound_while_this_granges_image_is_out_of_date():
    """Every route answers 503 then (R5.16), which is an answer: the viewer keeps the last
    picture. But a grange with a stale image has stopped gossiping, and reaches nobody."""
    snap = Snapshot(has_mesh=False, peers=[held("alice"), held("bob"), held("carol")])
    assert snap.reachable and main.bound_nodes(snap) == 3
    snap.me.image_stale = True
    assert main.bound_nodes(snap) == 0
    with_mesh = Snapshot(has_mesh=True, peers=[held("alice", bound=True)], me=Me(image_stale=True))
    assert main.bound_nodes(with_mesh) == 0


def test_the_demo_counts_its_bound_granges():
    snap = DemoSource(start=165.0).snapshot()
    bound = sum(1 for p in snap.peers if p.bound)
    assert bound > 10 and main.bound_nodes(snap) == bound      # judy is the one that is not
    assert main.window_title(main.bound_nodes(snap)).count("📡") == 10


# ---- what the app writes ----------------------------------------------------------------------------
def titles(monkeypatch) -> list:
    """What the app writes as the terminal's title. Textual writes other escapes too (OSC 22
    cursor shapes, say); only title sequences are kept."""
    writes: list = []
    monkeypatch.setattr(HeadlessDriver, "write",
                        lambda self, data: writes.append(data) if data.startswith("\x1b]0;") else None)
    return writes


def team(n: int, **kw) -> Snapshot:
    return Snapshot(me=Me(gid="djp3", cursor=1), has_mesh=True,
                    peers=[held(f"g{i}", bound=True) for i in range(n)], **kw)


@drive
async def test_the_title_follows_the_count_and_is_cleared_on_quit(monkeypatch):
    writes = titles(monkeypatch)
    source = Scripted(team(2))
    app = main.GrangeViz(source, live=False)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        assert writes == [main.title_sequence(f"{main.WINDOW_TITLE} 📡📡")]
        for _ in range(5):                                         # redraws with the same count:
            app.tick()                                             # no title is written again
        await pilot.pause()
        assert len(writes) == 1
        source.snap = team(3)
        app.tick()
        await pilot.pause()
        assert writes[-1] == main.title_sequence(f"{main.WINDOW_TITLE} 📡📡📡")
        source.snap = team(14)
        app.tick()
        assert writes[-1] == main.title_sequence(f"{main.WINDOW_TITLE} " + "📡" * 10)
        source.snap = team(3, reachable=False, unreachable_for=2.0)
        app.tick()
        assert writes[-1] == main.title_sequence(main.WINDOW_TITLE), "nothing answers: no dishes"
        await pilot.press("q")
    assert writes[-1] == main.title_sequence("")                   # the Mac app's own title comes back


@drive
async def test_alone_the_title_is_the_name_and_the_version(monkeypatch):
    writes = titles(monkeypatch)
    app = main.GrangeViz(Scripted(team(0)), live=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.tick()
        assert writes == [main.title_sequence(main.WINDOW_TITLE)]
        await pilot.press("q")
