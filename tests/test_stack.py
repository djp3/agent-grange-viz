# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Stack's logic: where each column breaks, what it says about it, and that what is not
known is drawn as not known and never counts as a break."""
from __future__ import annotations

import stack
from demo import DemoSource
from model import Docker, Event, Gone, Gossip, Peer, Record, Sidecar, Snapshot, Tailnet
from standins import healthy, today


def demo_at(t: float) -> Snapshot:
    return DemoSource(start=t).snapshot()


def col(cols: list, name: str) -> stack.Column:
    found = [c for c in cols if c.name == name]
    assert len(found) == 1, f"{name}: {len(found)} columns"
    return found[0]


# ------------------------------------------------------------------------------- the demo's moments
def test_every_column_has_one_cell_per_layer_and_you_come_first():
    cols = stack.build(demo_at(165))
    assert cols[0].key == "me" and cols[0].name == "you"
    assert all(len(c.cells) == len(stack.LAYERS) for c in cols)


def test_each_failure_breaks_at_its_own_height():
    cols = stack.build(demo_at(165))
    frank, judy, mira, kenji = (col(cols, n) for n in ("frank", "judy", "mira", "kenji"))
    assert stack.LAYERS[frank.brk] == "gossip" and frank.verdict == "timeout"
    assert stack.LAYERS[judy.brk] == "gossip" and judy.verdict == "refused"
    assert stack.LAYERS[mira.brk] == "gossip" and mira.verdict == "outdated"
    assert stack.LAYERS[kenji.brk] == "farm" and kenji.verdict == "no farm"
    assert "proto-judy" in judy.diag[2] and "R5.11" in judy.diag[2]
    assert "503" in mira.diag[2]
    assert "lid" in frank.diag[3]
    assert col(cols, "alice").brk is None and col(cols, "alice").verdict == "ok"


def test_a_relayed_peer_is_not_a_problem():
    ivan = col(stack.build(demo_at(165)), "ivan")
    assert ivan.at("tailnet").glyph == "○" and ivan.at("tailnet").tone == "ok"
    assert ivan.verdict == "ok" and ivan.diag is None


def test_an_offline_peer_breaks_at_the_tailnet():
    carol = col(stack.build(demo_at(285)), "carol")
    assert carol.online is False
    assert stack.LAYERS[carol.brk] == "tailnet" and carol.verdict == "offline"


def test_your_sidecar_states():
    assert stack.build(demo_at(70))[0].at("tailnet").glyph == "★"
    control = stack.build(demo_at(95))[0]
    assert control.at("tailnet").tone == "warn" and control.brk is None and control.vtone == "warn"
    assert "coordination server" in control.diag[2]
    socket = stack.build(demo_at(120))
    assert socket[0].at("tailnet").tone == "bad" and socket[0].diag[0] == "you"
    assert all(c.stale and c.at("tailnet").ghost and c.brk != 0 for c in socket[1:]), "stale is not a break"


def test_columns_are_ordered_by_role_then_by_address():
    cols = stack.build(demo_at(165))
    roles = [c.role for c in cols[1:]]
    assert roles == sorted(roles, key=stack.ROLE_ORDER.get)
    by_address = stack.build(demo_at(165), by_address=True)
    assert [c.addr for c in by_address[1:]] == sorted(c.addr for c in by_address[1:])


# ------------------------------------------------------------------------- what today's grange gives
def test_without_the_mesh_gossip_is_not_known_and_breaks_nothing():
    cols = stack.build(today([healthy("alice")]))
    alice = col(cols, "alice")
    assert alice.at("gossip").ghost and alice.at("gossip").tone == "none"
    assert alice.brk is None and alice.verdict == "ok"
    assert alice.at("record").lines[1] == "", "no rev or counter to show"
    assert alice.label == "alice", "unknown binding is not shown as unbound"


def test_without_the_mesh_a_tagged_device_with_no_record_still_reads_as_a_break():
    judy = Peer(key="100.64.0.14:7789", name="judy", address="100.64.0.14:7789", hostname="grange-judy",
                tailnet=Tailnet(online=True, path="direct", rtt_ms=5.0, handshake_age=300.0))
    c = col(stack.build(today([judy])), "judy")
    assert stack.LAYERS[c.brk] == "record" and c.verdict == "no body"
    assert "/v1/mesh" in c.diag[3]


def test_a_record_that_went_says_how():
    gone = Peer(key="frank", name="frank", hostname="grange-frank",
                tailnet=Tailnet(online=True, path="direct", rtt_ms=5.0, handshake_age=200.0),
                gone=Gone(kind="expired", age=30.0))
    c = col(stack.build(today([gone])), "frank")
    assert c.verdict == "expired" and "no heartbeat" in c.diag[3]


def test_no_sidecar_data_is_not_a_break():
    alice = Peer(key="alice", name="alice", record=Record(seen_age=3.0, farm_age=8.0, farm_stale=False, agents=2))
    snap = today([alice])
    snap.has_sidecar, snap.sidecar = False, Sidecar(state="unknown")
    cols = stack.build(snap)
    assert cols[0].at("tailnet").ghost and cols[0].brk is None
    c = col(cols, "alice")
    assert c.online is None and c.at("tailnet").ghost and c.brk is None


def test_your_own_problems_are_yours():
    snap = today()
    snap.me.image_stale = True
    me = stack.build(snap)[0]
    assert me.diag[0] == "you" and "503" in me.diag[2] and me.diag[3] == "make update"
    snap = today()
    snap.me.farm_age, snap.me.farm_stale = 90.0, True
    me = stack.build(snap)[0]
    assert stack.LAYERS[me.brk] == "farm" and me.vtone == "you"
    snap = today()
    snap.me.duplicate_id = True
    assert stack.build(snap)[0].verdict == "dup id"


def test_a_grange_alone_on_the_tailnet_is_not_failing():
    snap = today()
    snap.me.last_gossip_age = None
    me = stack.build(snap)[0]
    assert me.brk is None and me.at("gossip").lines[0] == "alone"


def test_a_stale_farm_is_a_break_and_a_slow_one_a_warning():
    slow = healthy("dave")
    slow.record.farm_age = 50.0
    stale = healthy("kenji")
    stale.record.farm_age, stale.record.farm_stale = 80.0, True
    cols = stack.build(today([slow, stale]))
    assert col(cols, "dave").vtone == "warn" and col(cols, "dave").verdict == "farm"
    assert col(cols, "kenji").vtone == "bad" and col(cols, "kenji").verdict == "no farm"


def test_gossip_failing_means_marked_or_last_thing_failed():
    assert Gossip(role="pool", failed=True).failing
    assert Gossip(role="pool", last_ok_age=10.0, last_fail_age=3.0).failing
    assert not Gossip(role="pool", last_ok_age=3.0, last_fail_age=10.0).failing
    assert not Gossip(role="pool", last_ok_age=3.0).failing


# --------------------------------------------------------------------------------------- drawing
def test_the_grid_draws_every_column_when_they_fit_and_scrolls_when_they_do_not():
    cols = stack.build(demo_at(165))
    view = stack.View()
    cw, shown, before, after = stack.layout(cols, 170, view)
    assert shown == cols and before == after == 0
    cw, shown, before, after = stack.layout(cols, 100, view)
    assert shown[0].key == "me" and before == 0 and after == len(cols) - len(shown)
    view.view_start = 3
    cw, shown, before, after = stack.layout(cols, 100, view)
    assert shown[0].key == "me" and shown[1] is cols[4] and before == 3, "your column stays pinned"
    view.sel_key, view.follow = cols[-1].key, True
    cw, shown, before, after = stack.layout(cols, 100, view)
    assert shown[-1] is cols[-1] and after == 0, "a key brings the selection into view"


def test_every_cell_of_the_grid_has_a_tip():
    snap = demo_at(165)
    cols = stack.build(snap)
    text = stack.grid(cols, 170, 2, 0, stack.View())
    keys = {span.style.meta.get("tip") for span in text.spans if hasattr(span.style, "meta") and span.style.meta}
    for c in cols:
        for kind in ("head", "tailnet", "gossip", "record", "farm", "verdict"):
            assert f"{kind}:{c.key}" in keys
            tip = stack.tip_for(snap, cols, f"{kind}:{c.key}")
            assert tip is not None and tip.plain.strip(), f"{kind}:{c.key}"
    for layer in ("headscale", "tailnet", "gossip", "record", "farm", "verdict"):
        assert f"layer:{layer}" in keys and stack.tip_for(snap, cols, f"layer:{layer}") is not None


def test_tips_say_what_is_not_known_without_the_mesh():
    snap = today([healthy("alice")])
    cols = stack.build(snap)
    key = col(cols, "alice").key
    assert "/v1/mesh" in stack.tip_for(snap, cols, f"gossip:{key}").plain
    assert "through Docker" in stack.tip_for(snap, cols, f"tailnet:{key}").plain
    assert "4ms" in stack.tip_for(snap, cols, f"tailnet:{key}").plain and "40 s" in stack.tip_for(snap, cols, f"tailnet:{key}").plain


def test_a_cell_below_a_break_says_it_is_a_consequence():
    snap = demo_at(165)
    cols = stack.build(snap)
    frank = col(cols, "frank")
    assert "consequence" in stack.tip_for(snap, cols, f"farm:{frank.key}").plain
    assert "consequence" not in stack.tip_for(snap, cols, f"tailnet:{frank.key}").plain


def test_the_diagnosis_lists_breaks_yours_first_and_strangers_last():
    snap = demo_at(120)
    lines = stack.diagnosis(stack.build(snap), snap, 200).plain.splitlines()
    assert lines[0].startswith("diagnosis") and lines[1].split()[1] == "you"
    snap = demo_at(165)
    text = stack.diagnosis(stack.build(snap), snap, 200).plain
    assert "100.64.0.40" in text and "R5.13" in text


def test_the_diagnosis_says_what_today_cannot_show():
    snap = today([healthy("alice")])
    text = stack.diagnosis(stack.build(snap), snap, 200).plain
    assert "every column reads green" in text and "/v1/mesh" in text


def test_unreachable_says_what_docker_knows():
    snap = today(reachable=False, unreachable_for=12.0, port=7788,
                 docker=Docker(available=True, sidecar="agent-grange-tailscale-1", sidecar_state="exited",
                               sidecar_for=12.0, grange="agent-grange-grange-1", grange_state="running",
                               grange_health="healthy"))
    text = stack.unreachable(snap, 200).plain
    assert "127.0.0.1:7788" in text and "exited 12s ago" in text and "make up" in text
    snap.docker = Docker(available=False)
    snap.note = "Docker socket not found"
    assert "Docker socket not found" in stack.unreachable(snap, 200).plain


def test_the_height_budget_keeps_room_for_the_diagnosis():
    for height in range(24, 80):
        band, lines, gap = stack.fit_height(height)
        body = sum(min(lines, stack.MAX_LINES.get(layer, lines)) for layer in stack.LAYERS)
        used = 3 + band + 4 + body + (len(stack.LAYERS) - 1) * gap
        assert band >= stack.PANEL_MIN and lines in (2, 3)
        if height >= 36:
            assert height - used >= stack.DIAG_MIN - 1, height


def test_strangers_are_the_latest_refusal_per_source():
    snap = today()
    snap.strangers = [Event(age=30, kind="refused", peer="100.64.0.40", ok=False, status=403, note="a"),
                      Event(age=5, kind="refused", peer="100.64.0.40", ok=False, status=403, note="b")]
    assert [e.note for e in stack.strangers(snap)] == ["b"]
