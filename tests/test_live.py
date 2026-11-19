# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The live source against a stand-in grange and a stand-in Docker: what a Snapshot holds,
how its ages tick, and what it works out when records, paths and containers change. Each test
runs the source's rounds by hand, on a clock it moves itself."""
import time

import pytest

from live import LiveSource, sidecar_state
from model import FORGET_S
from standins import METRICS, STALE_BODY, FakeClock, FakeDocker, FakeGrange, closed_port, entry, status_json

WALL = 1795050000.0                      # the wall clock the stand-in handshakes are measured against


@pytest.fixture
def grange():
    g = FakeGrange("djp3")
    yield g
    g.close()


@pytest.fixture
def docker(grange):
    d = FakeDocker()
    d.add("ts", "agent-grange-tailscale-1", "tailscale", port=grange.port, up_for=600)
    d.add("gr", "agent-grange-grange-1", "grange", network="container:ts")
    d.script[("tailscale", "metrics", "print")] = (METRICS, "", 0)
    d.script[("tailscale", "ping")] = lambda cmd: (f"pong from x ({cmd[-1]}) via 10.0.0.9:41641 in 7ms\n", "", 0)
    yield d
    d.close()


def devices(docker, *peers, **kw) -> None:
    docker.script[("tailscale", "status", "--json")] = (status_json(peers=list(peers), **kw), "", 0)


def dev(name: str, octet: int, **kw) -> dict:
    return {"host": f"grange-{name}", "ip": f"100.64.0.{octet}", **kw}


def source(grange, docker=None, **kw):
    clock = FakeClock()
    src = LiveSource(port=grange.port, docker_socket=docker.path if docker else "", use_docker=docker is not None,
                     agents_wait=0, clock=clock, wall=lambda: WALL + (clock.t - 1000.0), **kw)
    return src, clock


def by_name(snap) -> dict:
    return {p.name: p for p in snap.peers}


def kinds(snap) -> list:
    return [(e.kind, e.peer) for e in snap.events]


# ---- the picture -----------------------------------------------------------------------------------
def test_records_are_joined_to_devices_by_hostname(grange, docker):
    grange.set_granges([entry("alice", seen=2.0, farm=8.0, agents=3), entry("bob", seen=4.0, farm=None, stale=True, agents=0)])
    grange.health.update(peers_discovered=2, peers_live=2, last_gossip_age_s=0.5)
    devices(docker, dev("alice", 6, cur_addr="192.168.1.26:41641", handshake=WALL - 40, rx=100, tx=200),
            dev("carol", 13, relay="nyc"))
    src, _ = source(grange, docker)
    assert src.poll_api() and src.poll_sidecar()
    snap = src.snapshot()
    assert snap.reachable and snap.ever_reached and snap.unreachable_for is None
    assert snap.has_sidecar and not snap.has_mesh and not snap.demo and snap.port == grange.port
    peers = by_name(snap)
    assert set(peers) == {"alice", "bob", "carol"}

    alice = peers["alice"]                               # a record and a device
    assert alice.key == alice.address == "100.64.0.6:7789" and alice.hostname == "grange-alice"
    assert alice.record.seen_age == pytest.approx(2.0) and alice.record.farm_age == pytest.approx(8.0)
    assert alice.record.agents == 3 and not alice.record.farm_stale
    assert alice.record.rev is None and alice.record.counter is None       # needs /v1/mesh
    assert alice.tailnet.online and alice.tailnet.path == "direct" and alice.tailnet.endpoint == "192.168.1.26:41641"
    assert alice.tailnet.handshake_age == pytest.approx(40.0, abs=0.5) and (alice.tailnet.rx, alice.tailnet.tx) == (100, 200)
    assert alice.bound is None and alice.gossip is None                    # needs /v1/mesh too

    bob = peers["bob"]                                   # a record, and no device by that name
    assert bob.key == "bob" and bob.tailnet is None and bob.address == ""
    assert bob.record.farm_age is None and bob.record.farm_stale and bob.record.agents == 0

    carol = peers["carol"]                               # a device, and no record
    assert carol.record is None and carol.gone is None
    assert carol.tailnet.path == "derp" and carol.tailnet.relay == "nyc" and carol.tailnet.handshake_age is None

    me = snap.me
    assert (me.gid, me.incarnation, me.cursor) == ("djp3", 1000, 2)
    assert me.address == "100.64.0.5:7789" and me.built == "bf6e87e" and not me.image_stale
    assert me.farm_age == pytest.approx(5.0) and me.agents == 1
    assert me.last_gossip_age == pytest.approx(0.5) and me.peers_discovered == 2 and me.peers_live == 2
    assert me.rejected == {"proto": 0, "group": 0, "identity": 0, "overflow": 0}
    assert me.fresh_polls == 3 and me.fresh_unchanged == 3
    assert me.rev is None and me.sent is None and me.longpolls is None

    assert snap.sidecar.state == "ok" and snap.sidecar.backend == "Running" and snap.sidecar.home_derp == "hs"
    assert snap.sidecar.path_bytes == {"derp": 2000, "direct": 102000}
    assert snap.docker.available and snap.docker.sidecar == "agent-grange-tailscale-1"
    assert snap.docker.sidecar_state == "running" and snap.docker.grange_health == "healthy"
    assert snap.events == [] and snap.strangers == [] and snap.note == ""
    assert grange.wrote() == []                          # it reads; farm writes


def test_ages_tick_between_reads(grange, docker):
    grange.set_granges([entry("alice", seen=2.0, farm=8.0)])
    devices(docker, dev("alice", 6, cur_addr="1.2.3.4:5", handshake=WALL - 40))
    src, clock = source(grange, docker)
    src.poll_api(), src.poll_sidecar(), src.poll_ping()
    before = src.snapshot()
    clock.tick(3)
    after = src.snapshot()
    a0, a1 = by_name(before)["alice"], by_name(after)["alice"]
    assert a1.record.seen_age - a0.record.seen_age == pytest.approx(3)
    assert a1.record.farm_age - a0.record.farm_age == pytest.approx(3)
    assert a1.record.held_for - a0.record.held_for == pytest.approx(3)
    assert a1.tailnet.handshake_age - a0.tailnet.handshake_age == pytest.approx(3)
    assert a0.tailnet.rtt_ms == 7.0 and a1.tailnet.rtt_age - a0.tailnet.rtt_age == pytest.approx(3)
    assert after.me.farm_age - before.me.farm_age == pytest.approx(3)
    assert after.me.last_gossip_age - before.me.last_gossip_age == pytest.approx(3)
    assert after.docker.sidecar_for - before.docker.sidecar_for == pytest.approx(3)
    assert after.sidecar.since - before.sidecar.since == pytest.approx(3)


def test_a_heartbeat_resets_the_age_without_moving_the_cursor(grange):
    grange.set_granges([entry("alice", seen=9.0)])
    src, clock = source(grange)
    src.poll_api()
    clock.tick(1)
    grange.touch("alice", seen_age_s=0.2)                # refreshed by a digest: the cursor stays (R5.6)
    src.poll_api()
    assert by_name(src.snapshot())["alice"].record.seen_age == pytest.approx(0.2)


# ---- what it works out: left, expired, readmit, join ------------------------------------------------
def test_a_record_that_goes_is_left_or_expired_by_how_old_it_was(grange):
    grange.set_granges([entry("alice", seen=3.0), entry("bob", seen=5.0)])
    src, clock = source(grange)
    src.poll_api()
    assert src.snapshot().events == []                   # the first picture is not news
    clock.tick(1)
    grange.touch("bob", seen_age_s=58.5)
    src.poll_api()
    clock.tick(1)
    grange.set_granges([entry("alice", seen=1.0)])       # bob, last seen a minute ago, is dropped
    src.poll_api()
    snap = src.snapshot()
    bob = by_name(snap)["bob"]
    assert bob.record is None and bob.gone.kind == "expired" and bob.gone.age == pytest.approx(0)
    assert bob.gone.forget_left is None                  # the forget map needs /v1/mesh
    assert kinds(snap) == [("expire", "bob")] and not snap.events[0].ok

    clock.tick(2)
    grange.set_granges([])                               # alice, seen a moment ago, withdraws
    src.poll_api()
    snap = src.snapshot()
    assert by_name(snap)["alice"].gone.kind == "left"
    assert by_name(snap)["bob"].gone.age == pytest.approx(2)
    assert kinds(snap) == [("expire", "bob"), ("leave", "alice")]


def test_coming_back_is_a_readmit_and_a_first_time_is_a_join(grange):
    grange.set_granges([entry("alice")])
    src, clock = source(grange)
    src.poll_api()
    grange.set_granges([])
    src.poll_api()
    clock.tick(10)
    grange.set_granges([entry("alice"), entry("carol")])
    src.poll_api()
    snap = src.snapshot()
    alice, carol = by_name(snap)["alice"], by_name(snap)["carol"]
    assert (alice.back_kind, carol.back_kind) == ("readmit", "join")
    assert alice.gone is None and alice.record is not None and alice.record.held_for == pytest.approx(0)
    assert kinds(snap) == [("leave", "alice"), ("readmit", "alice"), ("join", "carol")]
    clock.tick(30)
    assert by_name(src.snapshot())["carol"].back_age == pytest.approx(30)
    clock.tick(31)                                       # a minute on, it is no longer "lately"
    assert by_name(src.snapshot())["carol"].back_kind == ""


def test_a_gone_record_is_remembered_ten_minutes_and_events_five(grange):
    grange.set_granges([entry("alice")])
    src, clock = source(grange)
    src.poll_api()
    grange.set_granges([])
    src.poll_api()
    clock.tick(299)
    assert kinds(src.snapshot()) == [("leave", "alice")]
    clock.tick(2)
    snap = src.snapshot()
    assert snap.events == [] and "alice" in by_name(snap)
    clock.tick(FORGET_S)
    assert src.snapshot().peers == []


def test_a_gone_record_stays_while_its_device_is_listed(grange, docker):
    grange.set_granges([entry("alice")])
    devices(docker, dev("alice", 6, cur_addr="1.2.3.4:5"))
    src, clock = source(grange, docker)
    src.poll_api(), src.poll_sidecar()
    grange.set_granges([])
    src.poll_api()
    clock.tick(FORGET_S + 100)
    src.poll_sidecar()
    alice = by_name(src.snapshot())["alice"]
    assert alice.gone.kind == "left" and alice.gone.age == pytest.approx(FORGET_S + 100)
    assert alice.tailnet.online and alice.key == "100.64.0.6:7789"


# ---- nothing answers, the image is stale, the grange restarts --------------------------------------
def test_nothing_answering_freezes_the_last_picture(grange, docker):
    grange.set_granges([entry("alice", seen=2.0, farm=8.0)])
    devices(docker, dev("alice", 6, cur_addr="1.2.3.4:5", handshake=WALL - 10))
    src, clock = source(grange, docker)
    src.api.timeout = 0.5
    src.poll_api(), src.poll_sidecar()
    clock.tick(4)
    grange.close()                                       # the sidecar stops: the port goes with it
    docker.stop("ts", down_for=1)
    assert src.poll_api() is False
    src.poll_sidecar()
    clock.tick(6)
    src.poll_api()
    snap = src.snapshot()
    assert not snap.reachable and snap.ever_reached and snap.unreachable_for == pytest.approx(6)
    alice = by_name(snap)["alice"]
    assert alice.record.seen_age == pytest.approx(6.0)   # 2 s, plus the 4 s before it went: then it stops
    assert alice.record.farm_age == pytest.approx(12.0)
    assert snap.me.farm_age == pytest.approx(9.0)
    assert alice.tailnet.stale and not snap.has_sidecar  # last known: nobody to ask
    assert snap.docker.sidecar_state == "exited" and snap.docker.sidecar_for == pytest.approx(7, abs=2)
    assert snap.docker.grange_state == "running"         # still up, with no network
    assert snap.sidecar.state == "unknown"
    assert ("sidecar", "djp3") in kinds(snap)


def test_a_grange_never_reached_says_so(docker):
    src = LiveSource(port=closed_port(), docker_socket=docker.path, agents_wait=0, clock=(clock := FakeClock()))
    src.api.timeout = 0.5
    assert src.poll_api() is False
    clock.tick(5)
    snap = src.snapshot()
    assert not snap.reachable and not snap.ever_reached and snap.unreachable_for == pytest.approx(5)
    assert snap.peers == [] and snap.me.gid == ""
    src.poll_sidecar()
    assert "publishes port" in src.snapshot().note       # Docker is there; our sidecar is not


def test_a_stale_image_is_shown_not_lost(grange):
    grange.set_granges([entry("alice", seen=2.0)])
    src, clock = source(grange)
    src.poll_api()
    clock.tick(3)
    grange.stale = STALE_BODY                            # the tag moved: every route answers 503 (R5.16)
    assert src.poll_api() is False
    clock.tick(10)
    src.poll_api()
    snap = src.snapshot()
    assert snap.reachable and snap.unreachable_for is None
    assert snap.me.image_stale and snap.me.built == "bf6e87e" and snap.me.tag_commit == "c0ffee1"
    assert snap.me.fresh_summary == "the tag moved past this image"
    assert by_name(snap)["alice"].record.seen_age == pytest.approx(5.0)    # the picture stopped when it did
    assert [(e.kind, e.status) for e in snap.events] == [("refused", 503)]
    grange.stale = None                                  # make update
    src.poll_api()
    snap = src.snapshot()
    assert not snap.me.image_stale and by_name(snap)["alice"].record.seen_age == pytest.approx(2.0)


def test_a_restart_is_one_event_not_a_board_of_joins(grange):
    grange.set_granges([entry("alice"), entry("bob")])
    src, clock = source(grange)
    src.poll_api()
    assert src.snapshot().me.cursor == 2
    src._lost()                                          # make down ...
    clock.tick(20)
    grange.set_granges([entry("alice")], incarnation=2000, cursor=1)       # ... make up: cursor from 1 (R5.8)
    src.poll_api()
    clock.tick(1)
    grange.set_granges([entry("alice"), entry("bob"), entry("carol")], cursor=2)   # the board refilling
    src.poll_api()
    snap = src.snapshot()
    assert snap.reachable and (snap.me.incarnation, snap.me.cursor) == (2000, 2)
    assert kinds(snap) == [("restart", "djp3")]             # bob did not "leave": the board started over
    assert by_name(snap)["bob"].gone is None and by_name(snap)["carol"].back_kind == ""
    clock.tick(10)
    grange.set_granges([entry("alice"), entry("bob"), entry("carol"), entry("dave")])
    src.poll_api()
    assert kinds(src.snapshot())[-1] == ("join", "dave")  # later arrivals are news again


def test_a_corrected_incarnation_is_not_a_restart(grange):
    grange.set_granges([entry("alice")])
    src, _ = source(grange)
    src.poll_api()
    grange.set_granges([entry("alice")], incarnation=1001)                 # R4.8: the cursor carries on
    src.poll_api()
    snap = src.snapshot()
    assert snap.me.incarnation == 1001 and snap.events == []


# ---- the sidecar -----------------------------------------------------------------------------------
def test_sidecar_state_from_tailscaleds_own_words():
    assert sidecar_state("Running", []) == "ok"
    assert sidecar_state("Running", ["Unable to connect to the Tailscale coordination server"]) == "no-control"
    assert sidecar_state("Running", ["not connected to control"]) == "no-control"
    assert sidecar_state("Running", ["some other warning"]) == "ok"
    assert sidecar_state("NeedsLogin", []) == "logged-out"
    assert sidecar_state("Stopped", []) == "logged-out"
    assert sidecar_state("Starting", []) == "unknown"        # passes by at every start, as NoState does
    assert sidecar_state("NoState", []) == "unknown"


def test_a_sidecar_that_changes_state_is_an_event(grange, docker):
    devices(docker, dev("alice", 6))
    src, clock = source(grange, docker)
    src.poll_api(), src.poll_sidecar()
    assert src.snapshot().sidecar.state == "ok" and src.snapshot().events == []
    clock.tick(5)
    devices(docker, dev("alice", 6), health=["Unable to connect to the Tailscale coordination server"])
    src.poll_sidecar()
    clock.tick(2)
    snap = src.snapshot()
    assert snap.sidecar.state == "no-control" and snap.sidecar.since == pytest.approx(2)
    assert snap.sidecar.health == ("Unable to connect to the Tailscale coordination server",)
    assert [(e.kind, e.peer, e.ok) for e in snap.events] == [("sidecar", "djp3", False)]
    devices(docker, backend="NeedsLogin")
    src.poll_sidecar()
    assert src.snapshot().sidecar.state == "logged-out"
    devices(docker, dev("alice", 6))
    src.poll_sidecar()
    assert src.snapshot().sidecar.state == "ok" and src.snapshot().events[-1].ok


def test_a_path_that_changes_is_an_event(grange, docker):
    devices(docker, dev("tobias", 11, cur_addr="192.168.51.31:41641"))
    src, clock = source(grange, docker)
    src.poll_api(), src.poll_sidecar()
    clock.tick(5)
    devices(docker, dev("tobias", 11, cur_addr="", relay="headscale"))
    src.poll_sidecar()
    snap = src.snapshot()
    assert by_name(snap)["tobias"].tailnet.path == "derp"
    assert [(e.kind, e.peer, e.ok) for e in snap.events] == [("path", "tobias", False)]
    assert "headscale" in snap.events[0].note
    clock.tick(5)
    devices(docker, dev("tobias", 11, cur_addr="192.168.51.31:41641"))
    src.poll_sidecar()
    assert [(e.kind, e.ok) for e in src.snapshot().events] == [("path", False), ("path", True)]


def test_a_young_sidecar_and_an_idle_link_are_not_path_news(grange, docker):
    docker.add("ts", "agent-grange-tailscale-1", "tailscale", port=grange.port, up_for=5)     # just started
    devices(docker, dev("alice", 6, cur_addr="", active=True), dev("bob", 1, cur_addr="1.2.3.4:5"))
    src, clock = source(grange, docker)
    src.poll_api(), src.poll_sidecar()
    devices(docker, dev("alice", 6, cur_addr="9.9.9.9:1"), dev("bob", 1, cur_addr="", active=False))
    src.poll_sidecar()                                   # DERP first, then direct: how every start goes
    snap = src.snapshot()
    assert snap.events == []
    assert by_name(snap)["bob"].tailnet.path == "derp"   # shown as what traffic would start on
    docker.add("ts", "agent-grange-tailscale-1", "tailscale", port=grange.port, up_for=600)
    devices(docker, dev("alice", 6, cur_addr="9.9.9.9:1"), dev("bob", 1, cur_addr="1.2.3.4:5"))
    src.poll_sidecar()
    assert src.snapshot().events == []                   # idle and back: nothing was relayed


def test_pings_go_round_the_online_peers(grange, docker):
    devices(docker, dev("alice", 6, cur_addr="1.1.1.1:1"), dev("bob", 1, cur_addr="2.2.2.2:2"),
            dev("frank", 12, online=False))
    src, clock = source(grange, docker)
    src.poll_api(), src.poll_sidecar()
    assert src.poll_ping() and src.poll_ping() and src.poll_ping()
    pinged = [cmd[-1] for _, cmd in docker.execs.values() if cmd[:2] == ["tailscale", "ping"]]
    assert pinged == ["100.64.0.1", "100.64.0.6", "100.64.0.1"]            # in turn, never the offline one
    clock.tick(2)
    peers = by_name(src.snapshot())
    assert peers["alice"].tailnet.rtt_ms == 7.0 and peers["alice"].tailnet.rtt_age == pytest.approx(2)
    assert peers["frank"].tailnet.rtt_ms is None and peers["frank"].tailnet.path is None
    assert not peers["frank"].tailnet.online


def test_without_docker_the_records_still_show(grange):
    grange.set_granges([entry("alice", seen=2.0)])
    src, _ = source(grange)                              # use_docker off
    assert src.poll_api() and src.poll_sidecar() is False and src.poll_ping() is False
    snap = src.snapshot()
    alice = by_name(snap)["alice"]
    assert alice.record.seen_age == pytest.approx(2.0) and alice.tailnet is None and alice.key == "alice"
    assert not snap.has_sidecar and not snap.docker.available and snap.sidecar.state == "unknown"
    assert "Docker is not asked" in snap.note


def test_docker_that_is_not_there_is_a_note(grange, tmp_path):
    src = LiveSource(port=grange.port, docker_socket=str(tmp_path / "none.sock"), agents_wait=0, clock=FakeClock())
    src.poll_api(), src.poll_sidecar()
    snap = src.snapshot()
    assert snap.reachable and not snap.docker.available and not snap.has_sidecar
    assert snap.note.startswith("Docker: Docker does not answer")


# ---- GET /v1/mesh, when the grange has it -----------------------------------------------------------
MESH = {
    "grange": "djp3", "address": "100.64.0.5:7789", "group": "default", "incarnation": 1000, "rev": 14,
    "counter": 912, "cursor": 2, "uptime_s": 5321.4,
    "farm": {"last_push_age_s": 7.2, "pushes": 180, "stale": False},
    "local_api": {"long_polls": 1},
    "gossip": {"sent": {"push": 812, "repair": 1774, "probe": 59}, "outstanding": 0,
               "neighbours": ["100.64.0.6:7789", "100.64.0.9:7789"], "resumes": 0, "corrections": 0},
    "peers": [
        {"address": "100.64.0.6:7789", "id": "alice", "hostname": "grange-alice", "hint": True, "failed": False,
         "role": "successor", "last_ok_age_s": 0.8, "last_fail_age_s": None, "last_status": 204,
         "sent": {"push": 61, "repair": 40, "probe": 0}, "recv": {"push": 58, "repair": 37},
         "bytes_out": 74120, "bytes_in": 69311, "lag": 0,
         "record": {"incarnation": 1795040000047, "rev": 9, "counter": 530, "seen_age_s": 0.8, "farm_age_s": 12.0,
                    "farm_stale": False, "agents": 5},
         "tailnet": {"cur_addr": "192.168.1.26:41641", "relay": "headscale", "rx": 81233, "tx": 86010,
                     "last_handshake_age_s": 61.0, "latency_ms": 2.9}},
        {"address": "100.64.0.14:7789", "id": None, "hostname": "grange-judy", "hint": True, "failed": True,
         "role": "outside", "last_ok_age_s": None, "last_fail_age_s": 20.0, "last_status": 409, "fails": 3,
         "sent": {"probe": 3}, "recv": {}, "bytes_out": 0, "bytes_in": 0,
         "record": None, "tailnet": {"cur_addr": "", "relay": "headscale", "rx": 0, "tx": 0,
                                     "last_handshake_age_s": None, "latency_ms": 5.6}},
    ],
    "forgotten": [{"id": "frank", "removed_age_s": 30.2, "why": "expired"}],
    "rejected": {"proto": 0, "group": 9, "identity": 1, "overflow": 0},
    "tailnet": {"backend": "Running", "home_derp": "headscale", "health": [],
                "bytes": {"direct": 1882113, "derp": 20211}},
}


def test_mesh_slots_in_when_the_grange_has_it(grange):
    grange.set_granges([entry("alice", seen=0.8, agents=5)])
    src, clock = source(grange)
    src.poll_api()
    assert not src.snapshot().has_mesh
    grange.mesh = MESH
    clock.tick(60)                                       # the next probe
    src.poll_api()
    clock.tick(2)
    snap = src.snapshot()
    assert snap.has_mesh and snap.has_sidecar
    peers = by_name(snap)
    assert set(peers) == {"alice", "judy", "frank"}

    alice = peers["alice"]
    assert alice.bound is True and alice.key == "100.64.0.6:7789"
    assert alice.gossip.role == "successor" and not alice.gossip.failing
    assert alice.gossip.last_ok_age == pytest.approx(2.8) and alice.gossip.last_status is None
    assert alice.gossip.sent == {"push": 61, "repair": 40, "probe": 0} and alice.gossip.bytes_out == 74120
    assert (alice.record.rev, alice.record.counter, alice.record.incarnation) == (9, 530, 1795040000047)
    assert alice.record.seen_age == pytest.approx(2.8) and alice.record.agents == 5
    assert alice.tailnet.path == "direct" and alice.tailnet.rtt_ms == 2.9
    assert alice.tailnet.handshake_age == pytest.approx(63.0)

    judy = peers["judy"]                                 # answers, refused: never bound
    assert judy.bound is False and judy.record is None and judy.hostname == "grange-judy"
    assert judy.gossip.failing and judy.gossip.failed and judy.gossip.last_status == 409 and judy.gossip.fails == 3
    assert judy.tailnet.path == "derp" and judy.tailnet.handshake_age is None

    frank = peers["frank"]                               # in the forget map only
    assert frank.gone.kind == "expired" and frank.gone.age == pytest.approx(32.2)
    assert frank.gone.forget_left == pytest.approx(FORGET_S - 32.2)

    me = snap.me
    assert (me.rev, me.counter, me.group, me.address) == (14, 912, "default", "100.64.0.5:7789")
    assert me.sent == {"push": 812, "repair": 1774, "probe": 59} and me.neighbours == 2 and me.longpolls == 1
    assert me.farm_pushes == 180 and me.farm_age == pytest.approx(9.2)
    assert me.rejected == {"proto": 0, "group": 9, "identity": 1, "overflow": 0}
    assert snap.sidecar.state == "ok" and snap.sidecar.home_derp == "headscale"
    assert snap.sidecar.path_bytes == {"direct": 1882113, "derp": 20211}


def test_mesh_says_when_the_grange_lost_its_sidecar(grange):
    grange.mesh = dict(MESH, tailnet=None)
    src, _ = source(grange)
    src.poll_api()
    assert src.snapshot().sidecar.state == "no-socket"   # only the grange can know this one


def test_mesh_is_read_by_the_names_the_openapi_spec_gives(grange):
    """docs/openapi.yaml adds to the sketch: the last failure's own status and words, whether a
    session is active, how a record came to be held, the forget map's time left, refusals of
    sources that are no peer, and the sidecar's state as the grange sees it."""
    mesh = dict(MESH)
    mesh["gossip"] = dict(MESH["gossip"], discovered=13, live=11)
    mesh["peers"] = [
        dict(MESH["peers"][0], last_fail_age_s=40.0, last_fail_status=None, last_error="", fails=1,
             record=dict(MESH["peers"][0]["record"], held_for_s=12.0, admitted="restart"),
             tailnet=dict(MESH["peers"][0]["tailnet"], online=True, active=True, latency_age_s=3.0)),
        dict(MESH["peers"][1], last_status=409, last_fail_status=409, last_error="group 'proto-judy' is not 'default'",
             tailnet=dict(MESH["peers"][1]["tailnet"], online=True, active=False)),
    ]
    mesh["forgotten"] = [{"id": "frank", "removed_age_s": 30.0, "why": "left", "forget_left_s": 570.0}]
    mesh["refusals"] = [
        {"source": "100.64.0.14", "address": "100.64.0.14:7789", "kind": "group", "status": 409, "count": 9,
         "last_age_s": 2.0, "note": "group 'proto-judy' is not 'default'"},
        {"source": "100.64.0.40", "address": None, "kind": "identity", "status": 403, "count": 1,
         "last_age_s": 15.0, "note": "device does not carry tag:grange"},
    ]
    mesh["tailnet"] = {"state": "no-control", "state_age_s": 7.0, "reachable": True, "backend": "Running",
                       "health": ["can't reach the coordination server"], "home_derp": "headscale",
                       "home_derp_id": 999, "derp_latency_ms": None, "bytes": {"direct": 10, "derp": 2}}
    grange.mesh = mesh
    grange.set_granges([entry("alice", seen=0.8, agents=5)])
    src, _ = source(grange)
    src.poll_api()
    snap = src.snapshot()
    peers = by_name(snap)

    alice = peers["alice"]
    assert not alice.gossip.failing and alice.gossip.last_status is None and alice.gossip.fails == 1
    assert alice.tailnet.path == "direct" and alice.tailnet.rtt_age == pytest.approx(3.0, abs=0.5)
    assert alice.back_kind == "restart" and alice.back_age == pytest.approx(12.0, abs=0.5)
    assert alice.record.held_for == pytest.approx(12.0, abs=0.5)

    judy = peers["judy"]
    assert judy.gossip.last_status == 409 and "proto-judy" in judy.gossip.last_note
    assert judy.tailnet.online and judy.tailnet.path is None, "online with no session is idle, not relayed"

    assert peers["frank"].gone.kind == "left" and peers["frank"].gone.forget_left == pytest.approx(570.0, abs=0.5)
    assert [(e.peer, e.status) for e in snap.strangers] == [("100.64.0.40", 403)]
    assert snap.me.peers_discovered == 13 and snap.me.peers_live == 11
    sc = snap.sidecar
    assert sc.state == "no-control" and sc.since == pytest.approx(7.0, abs=0.5) and sc.home_region == 999
    assert sc.path_bytes == {"direct": 10, "derp": 2}

    grange.mesh = dict(mesh, tailnet={"state": "no-socket", "reachable": False, "unreachable_for_s": 8.0})
    src2, _ = source(grange)
    src2.poll_api()
    side = src2.snapshot().sidecar
    assert side.state == "no-socket" and side.since == pytest.approx(8.0, abs=0.5)


# ---- the threads -----------------------------------------------------------------------------------
def test_start_reads_and_stop_stops(grange, docker):
    grange.set_granges([entry("alice", seen=2.0)])
    devices(docker, dev("alice", 6, cur_addr="1.2.3.4:5"))
    src = LiveSource(port=grange.port, docker_socket=docker.path, agents_wait=0.2, status_every=0.2, ping_every=0.1)
    src.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            snap = src.snapshot()
            peers = by_name(snap)
            if snap.reachable and snap.has_sidecar and "alice" in peers and peers["alice"].tailnet \
                    and peers["alice"].tailnet.rtt_ms is not None:
                break
            time.sleep(0.05)
        assert snap.reachable and peers["alice"].tailnet.rtt_ms == 7.0 and peers["alice"].record is not None
    finally:
        src.stop()
    time.sleep(0.5)
    asked = len(grange.requests)
    time.sleep(0.6)
    assert len(grange.requests) <= asked + 1             # a request in flight may land; no more follow
    assert grange.wrote() == []
