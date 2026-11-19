# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The demo source: `python main.py --demo` draws the whole Stack on made-up data, because
today's grange cannot fill every row. It plays demosim.py's simulation and maps what one
grange and its sidecar could tell a viewer onto model.Snapshot, as if GET /v1/mesh existed.

It passes on only what a real grange, its sidecar and Docker could know: the Sim's links,
records, forget map, counters and events. It never reads sim.team, the truth about every
grange, so a closed lid shows as timeouts and a stale image as 503s, not as their cause.

    source = DemoSource()          # 100 s into the loop, so there is history to draw
    source.start()                 # from now on it moves with the wall clock
    source.snapshot()              # cheap; advances the simulation by the time gone by
    source.paused = True           # hold it
    source.speed = 4               # 1 or 4
    source.next_moment()           # jump to the next scripted moment
    source.advance(30)             # move it by hand (tests, headless frames)

Nothing runs in the background: the simulation moves inside snapshot(), by the wall-clock
time since the last call times the speed. Until start(), and after stop(), it moves only
when advance() or next_moment() say so.
"""
from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from typing import Callable, Optional

from demosim import DISCOVERY_S, FORGET_S, LOOP, ME, START, Ev, Link, Sim
from model import (DEFAULT_PORT, Docker, Event, Gone, Gossip, Me, Peer, Record, Sidecar,
                   Snapshot, Tailnet)

KEPT = ("join", "restart", "readmit", "expire", "leave", "path", "refused", "sidecar", "mark")
WINDOW = 300.0                   # how far back Snapshot.events reach
BACK_S = 60.0                    # how long a restart, readmit or join counts as "lately"
MAX_STEP = 5.0                   # the most wall-clock time one snapshot() makes up for
FAILED = ("push-out", "repair-out", "probe", "mark")
BACK = ("restart", "readmit", "join")
GROUP = re.compile(r"group '([^']+)'")
SIDECAR_NAME, GRANGE_NAME = "agent-grange-tailscale-1", "agent-grange-grange-1"


def _age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m{s % 60:02d}" if s < 3600 else f"{s // 3600}h{(s % 3600) // 60:02d}"


class DemoSource:
    def __init__(self, start: float = START, seed: int = 7, clock: Callable[[], float] = time.monotonic):
        self.sim = Sim(seed)
        self.speed = 1
        self._paused = False
        self._clock = clock
        self._running = False
        self._last = clock()
        self._lock = threading.Lock()
        # what a viewer would have kept from the events it was sent
        self._events: deque = deque()            # the KEPT kinds, oldest first
        self._failure: dict = {}                 # peer -> its last failed exchange
        self._gone: dict = {}                    # peer -> its last leave or expire
        self._back: dict = {}                    # peer -> its last restart, readmit or join
        self._group: dict = {}                   # peer -> the group its refusals named
        self._failed_refresh: set = set()        # discovery refreshes the sidecar did not answer
        self._up_at = -3600.0                    # when the containers last came up
        self.sim.listeners.append(self._heard)
        self.sim.advance(float(start))

    # ---------------------------------------------------------------- the Source
    def start(self) -> None:
        with self._lock:
            self._running = True
            self._last = self._clock()

    def stop(self) -> None:
        with self._lock:
            self._running = False

    def snapshot(self) -> Snapshot:
        with self._lock:
            self._catch_up()
            return self._build()

    # ---------------------------------------------------------------- the demo's controls
    @property
    def paused(self) -> bool:
        return self._paused

    @paused.setter
    def paused(self, value: bool) -> None:
        with self._lock:
            self._catch_up()                     # what ran before the switch still counts
            self._paused = bool(value)

    def next_moment(self) -> None:
        """Jump to just before the next scripted moment."""
        with self._lock:
            self.sim.advance(self.sim.now + self.sim.next_scenario())
            self._last = self._clock()

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.sim.advance(self.sim.now + max(0.0, seconds))
            self._last = self._clock()

    def _catch_up(self) -> None:
        now = self._clock()
        gone, self._last = now - self._last, now
        if self._running and not self._paused and gone > 0:
            self.sim.advance(self.sim.now + min(gone, MAX_STEP) * self.speed)

    # ---------------------------------------------------------------- what the viewer heard
    def _heard(self, ev: Ev) -> None:
        if ev.kind in KEPT:
            self._events.append(ev)
        if not ev.ok and ev.kind in FAILED:
            self._failure[ev.peer] = ev
        if ev.kind in ("leave", "expire"):
            self._gone[ev.peer] = ev
        elif ev.kind in BACK:
            self._back[ev.peer] = ev
            if ev.peer == ME:                    # make up: both containers recreated
                self._up_at = ev.t
        elif ev.kind == "discover" and not ev.ok:
            self._failed_refresh.add(math.floor(ev.t / DISCOVERY_S) * DISCOVERY_S)
        found = GROUP.search(ev.note)
        if found and (ev.kind == "refused" or not ev.ok):
            self._group[ev.peer] = found.group(1)

    def _latest(self, index: dict, link: Link) -> Optional[Ev]:
        """The newest indexed event filed under the link's id or its address."""
        hits = [index[k] for k in (link.gid, link.addr) if k and k in index]
        return max(hits, key=lambda e: e.t) if hits else None

    # ---------------------------------------------------------------- the mapping
    def _build(self) -> Snapshot:
        sim, me, now = self.sim, self.sim.me, self.sim.now
        down = sim.down
        since = None if me.sidecar_since is None else now - me.sidecar_since
        while self._events and now - self._events[0].t > WINDOW:
            self._events.popleft()

        known = set()
        for link in sim.links.values():
            known |= {link.gid, link.hostname.removeprefix("grange-"), link.addr, link.ip}
        events, strangers = [], {}
        for ev in self._events:
            event = Event(now - ev.t, ev.kind, ev.peer, ev.ok, ev.status, ev.note)
            events.append(event)
            if ev.kind == "refused" and ev.peer not in known:
                strangers.pop(ev.peer, None)     # the latest from each source, oldest source first
                strangers[ev.peer] = event

        return Snapshot(
            me=self._me(), sidecar=self._sidecar(since),
            peers=[self._peer(link) for link in sim.links.values()],
            events=events, strangers=list(strangers.values()),
            docker=Docker(available=True, sidecar=SIDECAR_NAME,
                          sidecar_state="exited" if down else "running",
                          sidecar_for=since if down else now - self._up_at,
                          grange=GRANGE_NAME, grange_state="running", grange_health="healthy"),
            reachable=not down, unreachable_for=since if down else None, ever_reached=True,
            has_mesh=True, has_sidecar=True, demo=True, port=DEFAULT_PORT,
            clock=f"{_age(now % LOOP)} into the loop")

    def _me(self) -> Me:
        sim, me, now = self.sim, self.sim.me, self.sim.now
        return Me(
            gid=me.gid, address=me.addr, group="default", incarnation=me.incarnation, cursor=me.cursor,
            rev=me.rev, counter=me.counter, farm_age=now - me.last_farm, farm_stale=me.farm_stale,
            agents=me.agents, farm_pushes=me.farm_pushes,
            last_gossip_age=None if me.last_gossip is None else now - me.last_gossip,
            rejected=dict(me.rejected), sent=dict(me.sent),
            peers_discovered=len(sim.links), peers_live=len(sim.live_addrs()),
            neighbours=len(sim.neighbours), longpolls=me.longpolls, duplicate_id=me.duplicate_id,
            image_stale=me.built != me.tag_commit, built=me.built, tag_commit=me.tag_commit,
            fresh_polls=me.fresh_polls, fresh_unchanged=me.fresh_unchanged,
            fresh_summary="tag unchanged (304, free)" if me.fresh_polls else "verified at startup")

    def _sidecar(self, since: Optional[float]) -> Sidecar:
        me = self.sim.me
        state = "unknown" if me.sidecar == "down" else me.sidecar     # down: nothing can be asked
        backend = {"logged-out": "NeedsLogin", "down": ""}.get(me.sidecar, "Running")
        return Sidecar(state=state, since=since, health=tuple(me.health), backend=backend,
                       home_derp=me.home_derp, home_region=999, derp_ms=me.derp_ms,
                       path_bytes=dict(self.sim.path_bytes))

    def _measured_at(self, link: Link) -> float:
        """When the link's round trip was last taken: the last discovery refresh that worked."""
        tick = math.floor(self.sim.now / DISCOVERY_S) * DISCOVERY_S
        while tick in self._failed_refresh and tick > link.discovered_at:
            tick -= DISCOVERY_S
        return max(tick, link.discovered_at)

    def _peer(self, link: Link) -> Peer:
        sim, me, now = self.sim, self.sim.me, self.sim.now
        relayed = link.path == "derp"
        tailnet = Tailnet(
            online=link.hint, path=link.path if link.hint else None,
            endpoint="" if relayed else link.cur_addr, relay=me.home_derp if relayed else "",
            rtt_ms=link.latency_ms, rtt_age=now - self._measured_at(link),
            handshake_age=now - link.handshake_at, tx=link.tx, rx=link.rx,
            stale=me.sidecar in ("no-socket", "logged-out"))

        status, note = None, ""
        if link.last_fail is not None:
            if link.last_ok is None or link.last_fail >= link.last_ok:
                status = link.last_status        # the mark wrote it last
            else:
                failure = self._latest(self._failure, link)
                status = failure.status if failure is not None else None
            group = next((self._group[k] for k in (link.gid, link.addr) if k and k in self._group), None)
            note = {None: "", 409: f"group '{group}'" if group else "another group or protocol",
                    503: "image out of date", 403: "not a tagged grange"}.get(status, "")
        gossip = Gossip(
            role=link.role, failed=link.failed,
            last_ok_age=None if link.last_ok is None else now - link.last_ok,
            last_fail_age=None if link.last_fail is None else now - link.last_fail,
            last_status=status, last_note=note, fails=link.fails,
            sent=dict(link.sent), recv=dict(link.recv), bytes_out=link.bytes_out, bytes_in=link.bytes_in)

        record = gone = None
        rec = sim.records.get(link.gid) if link.gid else None
        if rec is not None:
            record = Record(seen_age=now - rec.seen_at, farm_age=sim.farm_age(link.gid),
                            farm_stale=rec.farm_stale, agents=rec.agents, rev=rec.rev,
                            counter=rec.counter, incarnation=rec.incarnation, held_for=now - rec.first_at)
        elif link.gid and link.gid in sim.forget:
            went = now - sim.forget[link.gid][1]
            last = self._gone.get(link.gid)
            gone = Gone(kind="left" if last is not None and last.kind == "leave" else "expired",
                        age=went, forget_left=max(0.0, FORGET_S - went))

        back = self._back.get(link.gid) if link.gid else None
        lately = back is not None and now - back.t <= BACK_S
        return Peer(
            key=link.addr, name=link.gid or link.hostname.removeprefix("grange-"),
            address=link.addr, hostname=link.hostname, bound=link.gid is not None,
            tailnet=tailnet, gossip=gossip, record=record, gone=gone,
            back_kind=back.kind if lately else "", back_age=now - back.t if lately else None)
