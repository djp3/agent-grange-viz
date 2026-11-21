# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The demo source: the simulation's scripted moments as model.Snapshots, its controls, and
that it passes on only what a grange and its sidecar could know."""
from __future__ import annotations

import dataclasses
import time

import pytest

import demo
import demosim
from demo import DemoSource
from model import DEFAULT_PORT, ROLES, SIDECAR_STATES, Snapshot

ORIGINAL_INCARNATION = 1795040341941


def at(t: float, **kw) -> Snapshot:
    """The snapshot t seconds into the loop; nothing moves by itself (start() is never called)."""
    return DemoSource(start=t, **kw).snapshot()


def peer(snap: Snapshot, name: str):
    found = [p for p in snap.peers if p.name == name]
    assert len(found) == 1, f"{name}: {len(found)} columns"
    return found[0]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------------- the shape
def test_a_snapshot_says_it_is_a_demo_with_everything_filled():
    snap = at(100)
    assert snap.demo and snap.has_mesh and snap.has_sidecar and snap.reachable and snap.ever_reached
    assert snap.port == DEFAULT_PORT and snap.unreachable_for is None
    assert snap.loop == "1m40 into the loop"
    me = snap.me
    assert (me.gid, me.address, me.group) == ("djp3", "100.64.0.5:7789", "default")
    assert me.incarnation == ORIGINAL_INCARNATION and me.cursor > 1
    assert me.rev is not None and me.counter is not None and me.sent is not None
    assert set(me.rejected) == {"proto", "group", "identity", "overflow"}
    assert me.peers_discovered == len(snap.peers) and me.neighbours == 5 and me.longpolls == 1
    assert not me.image_stale and me.built == me.tag_commit
    assert snap.docker.available and snap.docker.sidecar_state == "running"
    assert snap.docker.grange_health == "healthy"
    assert snap.sidecar.home_derp == "headscale" and snap.sidecar.home_region == 999
    assert set(snap.sidecar.path_bytes) <= {"direct", "derp"} and snap.sidecar.path_bytes["direct"] > 0


def test_each_link_is_one_column_with_every_layer():
    snap = at(100)
    assert len({p.key for p in snap.peers}) == len(snap.peers)
    alice = peer(snap, "alice")
    assert alice.key == alice.address == "100.64.0.6:7789" and alice.hostname == "grange-alice"
    assert alice.bound is True
    assert alice.tailnet.online and alice.tailnet.path == "direct" and alice.tailnet.endpoint
    assert alice.tailnet.rtt_ms > 0 and 0 <= alice.tailnet.rtt_age <= demosim.DISCOVERY_S
    assert 0 <= alice.tailnet.handshake_age
    assert alice.gossip.role == "successor" and not alice.gossip.failing
    assert alice.gossip.last_ok_age is not None and alice.gossip.last_fail_age is None
    assert alice.record is not None and alice.gone is None
    assert alice.record.rev >= 1 and alice.record.counter >= 1 and alice.record.incarnation > 0
    ivan = peer(snap, "ivan")
    assert ivan.tailnet.path == "derp" and ivan.tailnet.relay == "headscale" and ivan.tailnet.endpoint == ""
    assert {p.gossip.role for p in snap.peers} <= set(ROLES)
    assert sum(p.gossip.role == "successor" for p in snap.peers) == 1


# ---------------------------------------------------------------------------- scripted moments
def test_frank_times_out_whether_or_not_the_mark_is_on():
    """Lid closed at 35: the hint stays online, exchanges time out, each discovery refresh
    clears the mark and the next attempt sets it again."""
    unmarked, marked = peer(at(61), "frank"), peer(at(70), "frank")
    for frank in (unmarked, marked):
        assert frank.tailnet.online is True                 # headscale has not noticed
        assert frank.gossip.failing and frank.gossip.last_status is None and frank.gossip.last_note == ""
        assert frank.record is not None and frank.record.seen_age > 20      # still on the board, ageing
    assert unmarked.gossip.failed is False and unmarked.gossip.role != "outside"
    assert marked.gossip.failed is True and marked.gossip.role == "outside"
    assert marked.gossip.fails >= 2
    assert any(e.kind == "mark" and e.peer == "frank" and e.status is None for e in at(70).events)


def test_judy_is_unbound_and_refused_409_naming_her_group():
    for t in (70, 165):
        judy = peer(at(t), "judy")
        assert judy.bound is False and judy.hostname == "grange-judy" and judy.key == "100.64.0.14:7789"
        assert judy.tailnet.online is True and judy.tailnet.path == "direct"
        assert judy.gossip.failing and judy.gossip.last_status == 409
        assert judy.gossip.last_note == "group 'proto-judy'"
        assert judy.gossip.last_ok_age is None
        assert judy.record is None and judy.gone is None


def test_no_control_at_95():
    snap = at(95)
    assert snap.reachable and snap.sidecar.state == "no-control"
    assert snap.sidecar.since == pytest.approx(7.0) and snap.sidecar.backend == "Running"
    assert any("coordination" in h for h in snap.sidecar.health)
    assert not any(p.tailnet.stale for p in snap.peers)             # tunnels and status keep working
    assert any(e.kind == "sidecar" and not e.ok for e in snap.events)
    frank = peer(snap, "frank")                                     # expired at 88
    assert frank.record is None and frank.gone.kind == "expired"
    assert frank.gone.age == pytest.approx(7.0) and frank.gone.forget_left == pytest.approx(593.0)
    carol = peer(snap, "carol")                                     # discovered at 90, not yet named
    assert carol.bound is False and carol.record is None and carol.gossip.last_ok_age is None


def test_no_socket_at_120_stales_the_tailnet_and_refuses_at_the_whois_check():
    snap = at(120)
    assert snap.reachable and snap.sidecar.state == "no-socket" and snap.sidecar.since == pytest.approx(8.0)
    assert snap.peers and all(p.tailnet.stale for p in snap.peers)
    assert all(p.tailnet.online is not None for p in snap.peers)    # the last known hint stays
    assert snap.me.rejected["identity"] > 0
    refused = [e for e in snap.events if e.kind == "refused" and e.status == 403]
    assert refused and all("whois" in e.note for e in refused)
    assert snap.strangers == []                                     # every refusal is from a peer
    alice = peer(snap, "alice")                                     # outgoing still works
    assert not alice.gossip.failing and alice.gossip.last_ok_age < 15
    assert alice.tailnet.rtt_age == pytest.approx(30.0)             # taken at 90: the refresh at 120 failed
    assert not any(p.tailnet.stale for p in at(135).peers)          # the socket is back at 132


def test_mira_kenji_bob_and_the_stranger_at_165():
    snap = at(165)
    mira = peer(snap, "mira")
    assert mira.tailnet.online and mira.gossip.failed and mira.gossip.last_status == 503
    assert mira.gossip.last_note == "image out of date" and mira.gossip.last_ok_age > mira.gossip.last_fail_age
    assert mira.record is not None and mira.record.seen_age > 5     # ageing towards expiry
    kenji = peer(snap, "kenji")
    assert kenji.record.farm_stale and kenji.record.farm_age > 50 and not kenji.gossip.failing
    bob = peer(snap, "bob")
    assert bob.back_kind == "restart" and 25 < bob.back_age <= 60
    assert bob.record.held_for == pytest.approx(bob.back_age)       # a new incarnation, held since then
    tobias = peer(snap, "tobias")
    assert tobias.tailnet.path == "direct"
    moved = [e for e in snap.events if e.kind == "path" and e.peer == "tobias"]
    assert [e.ok for e in moved] == [False, True]                   # to DERP at 50, direct again at 140
    assert [(e.peer, e.status) for e in snap.strangers] == [("100.64.0.40", 403)]
    assert snap.sidecar.state == "ok" and not any(p.tailnet.stale for p in snap.peers)


def test_unreachable_at_203():
    before, snap = at(197), at(203)                                 # the sidecar container stops at 196
    assert snap.reachable is False and snap.unreachable_for == pytest.approx(7.0)
    assert snap.sidecar.state == "unknown" and snap.sidecar.state in SIDECAR_STATES
    assert snap.docker.available and snap.docker.sidecar_state == "exited"
    assert snap.docker.sidecar_for == pytest.approx(7.0)
    assert snap.docker.grange_state == "running" and snap.docker.grange_health == "healthy"
    assert snap.docker.sidecar == "agent-grange-tailscale-1" and snap.docker.grange == "agent-grange-grange-1"
    # what the grange last said stays as it was, while nothing can ask it
    assert [p.key for p in snap.peers] == [p.key for p in before.peers]
    assert snap.me.cursor == before.me.cursor and snap.me.incarnation == ORIGINAL_INCARNATION
    assert snap.me.counter == before.me.counter and snap.me.rejected == before.me.rejected
    assert [p.record is not None for p in snap.peers] == [p.record is not None for p in before.peers]
    assert [p.gossip.fails for p in snap.peers] == [p.gossip.fails for p in before.peers]


def test_make_up_at_211_starts_the_grange_afresh():
    before, snap = at(195), at(216)
    assert snap.reachable and snap.unreachable_for is None and snap.sidecar.state == "ok"
    assert snap.me.incarnation != ORIGINAL_INCARNATION
    assert snap.me.cursor < before.me.cursor and snap.me.cursor < 20
    assert snap.me.rev == 1 and sum(snap.me.rejected.values()) == 0
    assert snap.docker.sidecar_state == "running" and snap.docker.sidecar_for == pytest.approx(5.0)
    assert any(e.kind == "restart" and e.peer == "djp3" for e in snap.events)
    unbound = [p for p in snap.peers if p.bound is False]
    assert unbound and all(p.record is None and p.gone is None for p in unbound)
    assert all(p.gossip.last_fail_age is None for p in snap.peers)  # a new grange remembers no failure
    assert all(p.tailnet.rtt_age == pytest.approx(5.0) for p in snap.peers)   # measured at startup
    assert sum(p.bound is True for p in at(240).peers) > len(snap.peers) - len(unbound)


def test_carol_leaves_on_purpose_and_then_goes_offline():
    carol = peer(at(250), "carol")                                  # make down at 235
    assert carol.record is None and carol.gone.kind == "left" and carol.gone.age == pytest.approx(14.8, abs=1)
    carol = peer(at(285), "carol")                                  # the hint catches up at the 270 refresh
    assert carol.tailnet.online is False and carol.tailnet.path is None


# ------------------------------------------------------------------------------- invariants
def test_a_sweep_of_two_loops_holds_together():
    source = DemoSource(start=0)
    for _ in range(0, 485, 5):
        snap = source.snapshot()
        assert len({p.key for p in snap.peers}) == len(snap.peers)
        ages = [e.age for e in snap.events]
        assert ages == sorted(ages, reverse=True) and all(0 <= a <= demo.WINDOW for a in ages)
        assert all(e.kind in demo.KEPT for e in snap.events)
        assert all(e.kind == "refused" for e in snap.strangers)
        names = {x for p in snap.peers for x in (p.name, p.address)}
        assert not any(e.peer in names for e in snap.strangers)
        assert snap.sidecar.state in SIDECAR_STATES
        assert snap.reachable == (snap.unreachable_for is None)
        for p in snap.peers:
            assert p.tailnet is not None and p.gossip is not None and p.gossip.role in ROLES
            assert not (p.record is not None and p.gone is not None)
            assert p.bound or (p.record is None and p.gone is None)
            assert p.tailnet.handshake_age >= 0 and p.tailnet.rtt_age >= 0
            assert (p.tailnet.path is None) == (p.tailnet.online is False)
            assert (p.back_kind == "") == (p.back_age is None)
            if p.record is not None:
                assert 0 <= p.record.seen_age <= demosim.EXPIRE_S + 1
            if p.gossip.last_fail_age is None:
                assert p.gossip.last_status is None and p.gossip.last_note == ""
        source.advance(5)


def test_the_same_seed_plays_the_same_snapshots():
    one, two = DemoSource(start=0), DemoSource(start=0)
    for _ in range(12):
        one.advance(37.5)
        two.advance(37.5)
        assert dataclasses.asdict(one.snapshot()) == dataclasses.asdict(two.snapshot())
    other = DemoSource(start=450, seed=8)
    assert dataclasses.asdict(other.snapshot()) != dataclasses.asdict(one.snapshot())


def test_the_copied_simulation_still_plays_the_mock_ups_events():
    """demosim.py is the design mock-ups' simulation, kept as it was: spot values pin it."""
    sim = demosim.Sim()
    sim.advance(165.0)
    assert sim.me.cursor == 36 and dict(sim.me.rejected) == {"proto": 0, "group": 6, "identity": 25, "overflow": 0}
    assert sorted(sim.records) == ["alice", "bob", "carol", "dave", "erin", "grace", "heidi", "ivan",
                                   "kenji", "mira", "tobias"]


def test_it_never_reads_the_truth_about_other_granges():
    code = open(demo.__file__).read().replace(demo.__doc__, "")
    assert ".team" not in code


# --------------------------------------------------------------------------------- controls
def test_nothing_moves_until_start_and_after_stop():
    clock = Clock()
    source = DemoSource(clock=clock)
    clock.now += 30
    source.snapshot()
    assert source.sim.now == demosim.START
    source.start()
    clock.now += 1.0
    source.snapshot()
    assert source.sim.now == pytest.approx(demosim.START + 1.0)
    source.stop()
    clock.now += 3.0
    source.snapshot()
    assert source.sim.now == pytest.approx(demosim.START + 1.0)
    source.start()                                    # the time it was stopped is not made up
    source.snapshot()
    assert source.sim.now == pytest.approx(demosim.START + 1.0)


def test_speed_pause_and_the_cap_on_one_step():
    clock = Clock()
    source = DemoSource(clock=clock)
    source.start()
    source.speed = 4
    clock.now += 0.25
    source.snapshot()
    assert source.sim.now == pytest.approx(demosim.START + 1.0)
    source.paused = True
    assert source.paused
    clock.now += 60
    source.snapshot()
    assert source.sim.now == pytest.approx(demosim.START + 1.0)
    clock.now += 60
    source.paused = False                             # the pause is not made up either
    source.snapshot()
    assert source.sim.now == pytest.approx(demosim.START + 1.0)
    source.speed = 1
    clock.now += 0.5
    source.paused = True                              # what ran before the switch still counts
    assert source.sim.now == pytest.approx(demosim.START + 1.5)
    source.paused = False
    clock.now += 3600                                 # the Mac slept: one step is capped
    source.snapshot()
    assert source.sim.now == pytest.approx(demosim.START + 1.5 + demo.MAX_STEP)


def test_next_moment_and_advance():
    source = DemoSource()                             # 100 s in: the next scripted moment is 108
    source.next_moment()
    assert source.sim.now == pytest.approx(107.5)
    assert source.snapshot().sidecar.state == "no-control"
    source.advance(1)
    assert source.snapshot().sidecar.state == "ok"
    source.next_moment()
    assert source.sim.now == pytest.approx(109.5)
    source.advance(-5)
    assert source.sim.now == pytest.approx(109.5)     # it never runs backwards
    source.advance(30.5)
    assert source.sim.now == pytest.approx(140.0)


def test_a_snapshot_is_cheap_enough_for_four_a_second():
    source = DemoSource(start=230)                    # late in the loop: the most history to map
    began = time.perf_counter()
    for _ in range(200):
        source.snapshot()
    assert time.perf_counter() - began < 1.0          # 200 calls; it takes a few milliseconds
