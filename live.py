# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The live source: Snapshots of a real grange and its sidecar.

  the grange   GET /v1/agents, /health and /version on 127.0.0.1:<port>   (grangeapi.py)
  the sidecar  tailscale status, ping and metrics, through `docker exec`   (sidecar.py)
  Docker       the two containers' state, for when nothing answers         (sidecar.py)

and GET /v1/mesh when the grange answers it (docs/openapi.yaml): it is asked at start and
once a minute, and when it answers it replaces the join below and fills what the three routes
above cannot say (gossip per address, bindings, roles, rev and counter, the forget map).

Without it a column is made by joining two lists that know nothing of each other: the records
in /v1/agents, keyed by grange id, and the tagged devices in the sidecar's status, whose
hostname grange-<id> is a hint of the id (R6.19). A device with no record and a record with no
device each get a column of their own.

How it reads:

  • /v1/agents is long-polled on the cursor with a wait of one second, not thirty: a change
    answers at once, and the ages are fresh every second. A longer wait would not do, because
    heartbeats refresh seen_age_s without moving the cursor (R5.6);
  • /health every 2 s; /version once and again after the grange restarts;
  • the sidecar's status and the containers every 5 s, one ping a second round-robin over the
    online peers, its byte counters every 15 s.

What it works out for itself, having no history to ask for: a record that disappears is
"expired" when it was last seen close to the 60 s limit and "left" otherwise; one that comes
back is a "readmit", a first appearance a "join"; a path that changes between direct and DERP,
and a sidecar that changes state, are events too. Events are kept five minutes.

snapshot() only reads what the threads cached and works every age out as of now, so ages tick
between reads. When nothing answers, the last picture is kept and its ages stop where they
were: the view freezes and dims it.

When the image is out of date, every route answers 503 and the grange gossips with nobody
(R5.16). That is an answer, so the grange is reachable; but nothing keeps up what it said
before, and a record whose age stands still reads as live. So the Snapshot then holds none of
it, whichever route it came from: no record, no farm, no gossip, no count. It holds that the
image is stale and for how long, and what the sidecar still says through Docker.

The poll_*() methods each do one round and are what the threads loop on; tests call them
directly with a clock of their own.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Optional

import sidecar as sidecar_mod
from grangeapi import ApiError, GrangeApi, Unreachable
from model import (DEFAULT_PORT, EXPIRE_S, FORGET_S, GOSSIP_PORT, Docker, Event, Gone, Gossip, Me, Peer,
                   Record, Sidecar, Snapshot, Tailnet)

EVENTS_S = 300.0                         # how long an event is kept
BACK_S = 60.0                            # how long a return counts as "lately"
EXPIRED_NEAR = EXPIRE_S - 15.0           # a record last seen this old that disappears: expired
PATH_SETTLE_S = 30.0                     # a sidecar younger than this is still finding its direct paths
HOSTNAME_PREFIX = "grange-"
LOGGED_OUT = ("NeedsLogin", "NeedsMachineAuth", "Stopped")   # NoState and Starting pass by at every start


def _name_of(hostname: str) -> str:
    return hostname[len(HOSTNAME_PREFIX):] if hostname.startswith(HOSTNAME_PREFIX) else hostname


def _plus(value, extra: float):
    """An age that has grown `extra` seconds since it was read; None stays None."""
    return None if value is None else float(value) + max(0.0, extra)


def sidecar_state(backend: str, health) -> str:
    """ok · no-control · logged-out · unknown, from tailscaled's own words."""
    if backend in LOGGED_OUT:
        return "logged-out"
    if any(w in str(h).lower() for h in health or () for w in ("control", "coordination")):
        return "no-control"
    return "ok" if backend == "Running" else "unknown"


class LiveSource:
    def __init__(self, port: int = DEFAULT_PORT, docker_socket: str = "", use_docker: bool = True,
                 tag: str = "tag:grange", *, host: str = "127.0.0.1", agents_wait: float = 1.0,
                 health_every: float = 2.0, mesh_every: float = 60.0, status_every: float = 5.0,
                 ping_every: float = 1.0, metrics_every: float = 15.0,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 api: Optional[GrangeApi] = None, side: Optional[sidecar_mod.Sidecar] = None):
        self.port = port
        self.use_docker = use_docker
        self.tag = tag
        self.api = api or GrangeApi(port, host)
        self.side = side or sidecar_mod.Sidecar(port, docker_socket, tag)
        self.agents_wait, self.health_every, self.mesh_every = agents_wait, health_every, mesh_every
        self.status_every, self.ping_every, self.metrics_every = status_every, ping_every, metrics_every
        self._clock, self._wall = clock, wall
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list = []
        now = clock()
        # the grange
        self._reachable = False
        self._ever = False
        self._lost_at: Optional[float] = now         # since when nothing answers
        self._was_lost = False                       # nothing answered at some point since the last picture
        self._frozen_at: Optional[float] = None      # since when the picture is not being refreshed
        self._picture: Optional[dict] = None
        self._picture_at = now
        self._cursor: Optional[int] = None
        self._incarnation: Optional[int] = None
        self._health: Optional[dict] = None
        self._health_at = now
        self._health_due = now
        self._version: Optional[dict] = None
        self._stale: Optional[dict] = None           # the 503 body while the image is out of date
        self._stale_at: Optional[float] = None       # since when, as far as this source saw
        self._mesh: Optional[dict] = None
        self._mesh_at = now
        self._mesh_due = now
        self._has_mesh = False
        self._api_note = ""
        # the sidecar and Docker
        self._docker = Docker()
        self._docker_at = now
        self._docker_note = "" if use_docker else "Docker is not asked (use_docker is off): no tailnet rows"
        self._status: Optional[dict] = None
        self._status_at = now
        self._status_ok = False                      # the last attempt to ask the sidecar worked
        self._status_wall = wall()
        self._pings: dict = {}                       # ip -> (rtt ms, at, derp region)
        self._ping_turn = 0
        self._path_bytes: dict = {}
        self._metrics_due = now
        self._side_state = "unknown"
        self._side_since: Optional[float] = None
        # what it works out for itself
        self._held: dict = {}                        # id -> since when its record is on the board
        self._seen: dict = {}                        # id -> (seen_age_s, at): the last reading
        self._gone: dict = {}                        # id -> (kind, at)
        self._back: dict = {}                        # id -> (kind, at)
        self._known: set = set()                     # every id ever on the board
        self._paths: dict = {}                       # ip -> direct · derp
        self._events: deque = deque()                # (at, kind, peer, ok, status, note)
        self._baseline_until = now                   # appearances before this are not news

    # ------------------------------------------------------------------ model.Source
    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        loops = [("grange-api", self._api_loop)]
        if self.use_docker:
            loops += [("sidecar-status", self._sidecar_loop), ("sidecar-ping", self._ping_loop)]
        for name, fn in loops:
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._threads = []

    # ------------------------------------------------------------------ the threads
    def _api_loop(self) -> None:
        while not self._stop.is_set():
            ok = self._guard(self.poll_api)
            if not ok:
                self._stop.wait(1.0)                 # nothing answers, or 503: do not spin

    def _sidecar_loop(self) -> None:
        while not self._stop.is_set():
            self._guard(self.poll_sidecar)
            self._stop.wait(self.status_every)

    def _ping_loop(self) -> None:
        while not self._stop.is_set():
            self._guard(self.poll_ping)
            self._stop.wait(self.ping_every)

    def _guard(self, fn) -> bool:
        try:
            return bool(fn())
        except Exception as e:                       # a reader thread must not die
            with self._lock:
                self._api_note = f"{fn.__name__}: {type(e).__name__}: {e}"
            return False

    # ------------------------------------------------------------------ one round of the grange
    def poll_api(self) -> bool:
        """One read of /v1/agents (held up to agents_wait when nothing changed), then /health,
        /version and /v1/mesh when they are due. False when nothing answered or the image is
        stale, so the loop can pause."""
        try:
            reply = self.api.agents(after=self._cursor, wait=self.agents_wait)
        except Unreachable:
            self._lost()
            return False
        except ApiError as e:
            self._note(f"/v1/agents: {e}")
            return False
        if reply.stale:
            self._on_stale(reply.body)
            return False
        restarted = self._on_picture(reply.body)
        now = self._clock()
        try:
            if now >= self._health_due:
                self._health_due = now + self.health_every
                health = self.api.health()
                if health.stale:
                    self._on_stale(health.body)
                    return False
                with self._lock:
                    self._health, self._health_at = health.body, self._clock()
            if self._version is None or restarted:
                version = self.api.version()
                if not version.stale:
                    with self._lock:
                        self._version = version.body
            if self._has_mesh or now >= self._mesh_due:
                self._mesh_due = now + self.mesh_every
                mesh = self.api.mesh()
                with self._lock:
                    self._has_mesh = mesh is not None and not mesh.stale
                    if self._has_mesh:
                        self._mesh, self._mesh_at = mesh.body, self._clock()
        except Unreachable:
            self._lost()
            return False
        except ApiError as e:
            self._note(str(e))
        else:
            self._note("")
        return True

    def _note(self, text: str) -> None:
        with self._lock:
            self._api_note = text

    def _lost(self) -> None:
        with self._lock:
            now = self._clock()
            if self._reachable or self._lost_at is None:
                self._lost_at = now
            self._reachable = False
            self._was_lost = True
            if self._frozen_at is None and self._picture is not None:
                self._frozen_at = now

    def _on_stale(self, body: dict) -> None:
        with self._lock:
            now = self._clock()
            self._reachable, self._ever, self._lost_at = True, True, None
            if self._stale is None:
                self._stale_at = now
                self._event(now, "refused", self._gid(), False, 503,
                            "this grange's image is out of date: every route answers 503 (R5.16)")
            self._stale = body
            if self._frozen_at is None and self._picture is not None:
                self._frozen_at = now

    def _gid(self) -> str:
        if self._picture:
            return self._picture.get("grange", "")
        return (self._health or {}).get("grange", "")

    def _on_picture(self, body: dict) -> bool:
        """Take a /v1/agents answer; True when the grange behind it has restarted."""
        with self._lock:
            now = self._clock()
            first = self._picture is None
            self._reachable, self._ever, self._lost_at = True, True, None
            self._stale, self._stale_at, self._frozen_at = None, None, None
            incarnation, cursor = body.get("incarnation"), body.get("cursor")
            # A restart starts the cursor again from 1 with a new incarnation (R5.8). A new
            # incarnation with the cursor carrying on, and no gap in contact, is a correction (R4.8).
            changed = not first and incarnation != self._incarnation
            restarted = changed and (self._was_lost or (cursor or 0) < (self._cursor or 0))
            self._was_lost = False
            if restarted:
                self._event(now, "restart", body.get("grange", ""), True, None,
                            "this grange restarted: new incarnation, cursor from 1")
                self._baseline_until = now + 5.0     # the board refilling is not news,
                self._held.clear()                   # and what the old board held did not "leave"
                self._seen.clear()
            me = body.get("grange")
            before = set(self._held)
            present = {}
            for g in body.get("granges") or ():
                if g.get("grange") != me:
                    present[g.get("grange")] = g
            quiet = first or now < self._baseline_until
            for gid in sorted(present.keys() - before):
                self._held[gid] = now
                if not quiet:
                    kind = "readmit" if gid in self._known else "join"
                    self._back[gid] = (kind, now)
                    self._event(now, kind, gid, True, None, "back on the board" if kind == "readmit"
                                else "first record")
                self._gone.pop(gid, None)
                self._known.add(gid)
            for gid in sorted(before - present.keys()):
                seen_age, at = self._seen.get(gid, (0.0, now))
                kind = "expired" if seen_age + (now - at) >= EXPIRED_NEAR else "left"
                self._gone[gid] = (kind, now)
                self._held.pop(gid, None)
                self._back.pop(gid, None)
                self._event(now, "expire" if kind == "expired" else "leave", gid, False, None,
                            f"seen {seen_age + (now - at):.0f}s ago" if kind == "expired" else "its record was withdrawn")
            for gid, g in present.items():
                self._seen[gid] = (float(g.get("seen_age_s") or 0.0), now)
            self._picture, self._picture_at = body, now
            self._cursor, self._incarnation = cursor, incarnation
            return restarted

    # ------------------------------------------------------------------ one round of the sidecar
    def poll_sidecar(self) -> bool:
        """The two containers' state, the sidecar's status, and its byte counters when due."""
        if not self.use_docker:
            return False
        info = self.side.containers()
        note = self.side.reason
        status = self.side.status() if info.sidecar_state == "running" else None
        if info.sidecar_state == "running" and status is None:
            note = self.side.reason
        now = self._clock()
        bytes_ = None
        if status is not None and now >= self._metrics_due:
            self._metrics_due = now + self.metrics_every
            bytes_ = self.side.path_bytes()
        with self._lock:
            now = self._clock()
            self._docker, self._docker_at = info, now
            self._docker_note = ("Docker: " + note) if note else ""
            state = "unknown"
            self._status_ok = status is not None
            if status is not None:
                self._status, self._status_at, self._status_wall = status, now, self._wall()
                state = sidecar_state(status["backend"], status["health"])
                # A sidecar that just started reaches everyone through DERP first, and an idle
                # link has no path in use: neither is a path change worth telling.
                settled = info.sidecar_for is None or info.sidecar_for >= PATH_SETTLE_S
                for p in status["peers"]:
                    path = "direct" if p["cur_addr"] else ("derp" if p["online"] and p["active"] else None)
                    old = self._paths.get(p["ip"])
                    if path and old and path != old and settled:
                        self._event(now, "path", _name_of(p["hostname"]) or p["ip"], path == "direct", None,
                                    "direct again" if path == "direct"
                                    else f"direct UDP lost: relayed through DERP '{p['relay']}'")
                    if path:
                        self._paths[p["ip"]] = path
                if bytes_:
                    self._path_bytes = bytes_
            if state != self._side_state:
                if self._side_since is not None:      # the first reading is not a change
                    self._event(now, "sidecar", self._gid(), state == "ok", None,
                                {"ok": "the sidecar is fine again",
                                 "no-control": "tailscaled can't reach the coordination server",
                                 "logged-out": "tailscaled is logged out",
                                 "unknown": "the sidecar can't be asked"}.get(state, state))
                self._side_state, self._side_since = state, now
        return status is not None

    def poll_ping(self) -> bool:
        """One Tailscale ping to the next online peer in turn."""
        with self._lock:
            status = self._status
            fresh = (status is not None and self._status_ok
                     and self._clock() - self._status_at < 3 * self.status_every)
            ips = sorted(p["ip"] for p in status["peers"] if p["online"]) if fresh else []
            if not ips:
                return False
            ip = ips[self._ping_turn % len(ips)]
            self._ping_turn += 1
        pong = self.side.ping(ip)
        with self._lock:
            if pong is not None:
                self._pings[ip] = (pong.rtt_ms, self._clock(), pong.derp)
            return pong is not None

    # ------------------------------------------------------------------ events
    def _event(self, at: float, kind: str, peer: str, ok: bool, status: Optional[int], note: str) -> None:
        self._events.append((at, kind, peer or "", ok, status, note))
        while self._events and at - self._events[0][0] > EVENTS_S:
            self._events.popleft()

    # ------------------------------------------------------------------ the Snapshot
    def snapshot(self) -> Snapshot:
        with self._lock:
            now = self._clock()
            snap = Snapshot(port=self.port, clock=time.strftime("%H:%M:%S", time.localtime(self._wall())))
            snap.reachable = self._reachable
            snap.ever_reached = self._ever
            snap.unreachable_for = None if self._reachable or self._lost_at is None else now - self._lost_at
            stale = self._stale is not None           # 503 on every route: nothing it said is handed on
            snap.has_mesh = self._has_mesh and self._mesh is not None and not stale
            status_fresh = (self._status is not None and self._status_ok
                            and now - self._status_at < 3 * self.status_every)
            snap.has_sidecar = status_fresh or bool(snap.has_mesh and self._mesh.get("tailnet"))
            # how old the picture's own ages have become; they stop where the picture froze
            pic_extra = (self._frozen_at or now) - self._picture_at
            snap.me = self._me_stale(now) if stale else self._me(now, pic_extra)
            snap.sidecar = self._sidecar(now, snap.has_mesh)
            snap.docker = self._docker_now(now)
            snap.peers = (self._peers_mesh(now) if snap.has_mesh else
                          self._peers_joined(now, pic_extra, status_fresh, records=not stale))
            snap.events = [Event(now - at, kind, peer, ok, status, note)
                           for at, kind, peer, ok, status, note in self._events if now - at <= EVENTS_S]
            if snap.has_mesh:                         # refusals of sources that are no discovered address
                extra = (self._frozen_at or now) - self._mesh_at
                snap.strangers = [Event(_plus(r.get("last_age_s") or 0.0, extra), "refused", r.get("source") or "?",
                                        False, r.get("status"), r.get("note") or r.get("kind") or "")
                                  for r in self._mesh.get("refusals") or () if not r.get("address")]
            snap.note = self._docker_note or self._api_note
            return snap

    def _docker_now(self, now: float) -> Docker:
        d = self._docker
        return Docker(d.available, d.sidecar, d.sidecar_state, _plus(d.sidecar_for, now - self._docker_at),
                      d.grange, d.grange_state, d.grange_health)

    def _me(self, now: float, pic_extra: float) -> Me:
        me = Me()
        pic, health = self._picture or {}, self._health or {}
        me.gid = pic.get("grange") or health.get("grange") or ""
        me.incarnation = int(pic.get("incarnation") or health.get("incarnation") or 0)
        me.cursor = int(pic.get("cursor") or health.get("cursor") or 0)
        mine = next((g for g in pic.get("granges") or () if g.get("grange") == me.gid), None)
        if mine is not None:
            me.farm_age = _plus(mine.get("farm_age_s"), pic_extra)
            me.farm_stale = bool(mine.get("farm_stale"))
            me.agents = len(mine.get("agents") or ())
        if health:
            me.last_gossip_age = _plus(health.get("last_gossip_age_s"), (self._frozen_at or now) - self._health_at)
            me.rejected = dict(health.get("rejected") or {})
            me.peers_discovered = health.get("peers_discovered")
            me.peers_live = health.get("peers_live")
            me.duplicate_id = bool(health.get("duplicate_id"))
            fresh = health.get("freshness") or {}
            me.fresh_polls = int(fresh.get("polls") or 0)
            me.fresh_unchanged = int(fresh.get("consecutive_unchanged_polls") or 0)
            me.fresh_summary = fresh.get("last_summary") or ""
        me.built = ((self._version or {}).get("commit") or "")[:7]
        if self._status is not None and self._status["self"]["ip"]:
            me.address = f"{self._status['self']['ip']}:{GOSSIP_PORT}"
        if self._has_mesh and self._mesh:
            self._me_mesh(me, self._mesh, (self._frozen_at or now) - self._mesh_at)
        return me

    def _me_stale(self, now: float) -> Me:
        """This grange while its image is out of date: who it is, where, what the 503 says,
        and for how long. Nothing it counted or held before."""
        stale = self._stale or {}
        me = Me(gid=self._gid(), image_stale=True,
                stale_for=None if self._stale_at is None else now - self._stale_at,
                built=(stale.get("built_commit") or (self._version or {}).get("commit") or "")[:7],
                tag_commit=(stale.get("tag_commit") or "")[:7], fresh_summary=stale.get("summary") or "")
        if self._status is not None and self._status["self"]["ip"]:
            me.address = f"{self._status['self']['ip']}:{GOSSIP_PORT}"
        return me

    @staticmethod
    def _me_mesh(me: Me, mesh: dict, extra: float) -> None:
        me.address = mesh.get("address") or me.address
        me.group = mesh.get("group") or me.group
        me.rev, me.counter = mesh.get("rev"), mesh.get("counter")
        farm = mesh.get("farm") or {}
        me.farm_pushes = farm.get("pushes")
        if "last_push_age_s" in farm:
            me.farm_age = _plus(farm.get("last_push_age_s"), extra)
        gossip = mesh.get("gossip") or {}
        me.sent = dict(gossip.get("sent") or {}) if "sent" in gossip else None
        if isinstance(gossip.get("neighbours"), list):
            me.neighbours = len(gossip["neighbours"])
        if gossip.get("discovered") is not None:
            me.peers_discovered = gossip["discovered"]
        if gossip.get("live") is not None:
            me.peers_live = gossip["live"]
        me.longpolls = (mesh.get("local_api") or {}).get("long_polls")
        if isinstance(mesh.get("rejected"), dict):
            me.rejected = dict(mesh["rejected"])

    def _sidecar(self, now: float, mesh: bool) -> Sidecar:
        sc = Sidecar(state=self._side_state,
                     since=None if self._side_since is None else now - self._side_since,
                     path_bytes=dict(self._path_bytes))
        if self._status is not None:
            sc.health = tuple(self._status["health"])
            sc.backend = self._status["backend"]
            sc.home_derp = self._status["self"]["relay"]
        if mesh:                                     # the grange's own word on its sidecar, while it gives one
            tailnet = self._mesh.get("tailnet")
            if not tailnet or tailnet.get("reachable") is False or tailnet.get("state") == "no-socket":
                sc.state = "no-socket"               # the grange itself cannot reach its sidecar
                if tailnet and tailnet.get("unreachable_for_s") is not None:
                    sc.since = tailnet["unreachable_for_s"] + (now - self._mesh_at)
            else:
                sc.backend = tailnet.get("backend") or sc.backend
                sc.health = tuple(tailnet.get("health") or ())
                sc.home_derp = tailnet.get("home_derp") or sc.home_derp
                sc.home_region = tailnet.get("home_derp_id", sc.home_region)
                sc.derp_ms = tailnet.get("derp_latency_ms", sc.derp_ms)
                sc.state = tailnet.get("state") or sidecar_state(sc.backend, sc.health)
                if tailnet.get("state_age_s") is not None:
                    sc.since = tailnet["state_age_s"] + (now - self._mesh_at)
                if isinstance(tailnet.get("bytes"), dict):
                    sc.path_bytes = dict(tailnet["bytes"])
        return sc

    # ---- columns without the mesh: records joined to the sidecar's tagged devices
    def _peers_joined(self, now: float, pic_extra: float, status_fresh: bool, records: bool = True) -> list:
        """With `records` False (the image is stale) only the sidecar's devices: no record, no
        mark of one that went, and no column for a grange that was a record and nothing else."""
        pic = self._picture or {}
        me = pic.get("grange")
        peers, used = [], set()
        status_extra = now - self._status_at
        if self._status is not None:
            for dev in self._status["peers"]:
                name = _name_of(dev["hostname"]) or dev["ip"]
                peers.append(Peer(key=f"{dev['ip']}:{GOSSIP_PORT}", name=name, address=f"{dev['ip']}:{GOSSIP_PORT}",
                                  hostname=dev["hostname"],
                                  tailnet=self._tailnet(dev, now, status_extra, not status_fresh)))
                used.add(name)
        if not records:
            return peers
        records = {g.get("grange"): g for g in pic.get("granges") or () if g.get("grange") != me}
        for peer in peers:
            self._fill(peer, peer.name, records.get(peer.name), now, pic_extra)
        for gid, g in records.items():
            if gid not in used:
                peer = Peer(key=gid, name=gid)
                self._fill(peer, gid, g, now, pic_extra)
                used.add(gid)
                peers.append(peer)
        for gid, (kind, at) in list(self._gone.items()):
            if gid in used:
                continue
            if now - at > FORGET_S:
                del self._gone[gid]
                continue
            peers.append(Peer(key=gid, name=gid, gone=Gone(kind, now - at)))
        return peers

    def _fill(self, peer: Peer, gid: str, g: Optional[dict], now: float, pic_extra: float) -> None:
        if g is not None:
            held = self._held.get(gid)
            peer.record = Record(seen_age=_plus(g.get("seen_age_s") or 0.0, pic_extra),
                                 farm_age=_plus(g.get("farm_age_s"), pic_extra),
                                 farm_stale=bool(g.get("farm_stale")), agents=len(g.get("agents") or ()),
                                 held_for=None if held is None else now - held)
        elif gid in self._gone:
            kind, at = self._gone[gid]
            peer.gone = Gone(kind, now - at)
        back = self._back.get(gid)
        if back is not None:
            if now - back[1] <= BACK_S:
                peer.back_kind, peer.back_age = back[0], now - back[1]
            else:
                del self._back[gid]

    def _tailnet(self, dev: dict, now: float, status_extra: float, stale: bool) -> Tailnet:
        online = dev["online"]
        tn = Tailnet(online=online, endpoint=dev["cur_addr"], relay=dev["relay"], tx=dev["tx"], rx=dev["rx"],
                     stale=stale)
        tn.path = "direct" if dev["cur_addr"] else ("derp" if online else None)
        if dev["handshake"] is not None:
            tn.handshake_age = max(0.0, self._status_wall - dev["handshake"]) + status_extra
        ping = self._pings.get(dev["ip"])
        if ping is not None:
            tn.rtt_ms, tn.rtt_age = ping[0], now - ping[1]
        return tn

    # ---- columns from GET /v1/mesh (the sketch in docs/data-sources.md section 5)
    def _peers_mesh(self, now: float) -> list:
        mesh = self._mesh or {}
        extra = (self._frozen_at or now) - self._mesh_at
        peers, by_id = [], {}
        for p in mesh.get("peers") or ():
            gid, host = p.get("id"), p.get("hostname") or ""
            peer = Peer(key=p.get("address") or gid or host, name=gid or _name_of(host) or p.get("address", ""),
                        address=p.get("address") or "", hostname=host, bound=gid is not None)
            if "last_fail_status" in p:                # null is a timeout
                fail_status = p.get("last_fail_status")
            else:                                      # only last_status: a failure when it is one
                last_status = p.get("last_status")
                fail_status = last_status if isinstance(last_status, int) and last_status >= 400 else None
            peer.gossip = Gossip(role=p.get("role") or "outside", failed=bool(p.get("failed")),
                                 last_ok_age=_plus(p.get("last_ok_age_s"), extra),
                                 last_fail_age=_plus(p.get("last_fail_age_s"), extra),
                                 last_status=fail_status,
                                 last_note=p.get("last_error") or "", fails=int(p.get("fails") or 0),
                                 sent=dict(p.get("sent") or {}), recv=dict(p.get("recv") or {}),
                                 bytes_out=int(p.get("bytes_out") or 0), bytes_in=int(p.get("bytes_in") or 0))
            tn = p.get("tailnet")
            if isinstance(tn, dict):
                hint = tn.get("online", p.get("hint"))
                active = tn.get("active", hint)          # without it, an online peer is taken to have a session
                peer.tailnet = Tailnet(online=hint, endpoint=tn.get("cur_addr") or "", relay=tn.get("relay") or "",
                                       path=None if not (hint and active) else
                                       ("direct" if tn.get("cur_addr") else "derp"),
                                       rtt_ms=tn.get("latency_ms"), rtt_age=_plus(tn.get("latency_age_s"), extra),
                                       handshake_age=_plus(tn.get("last_handshake_age_s"), extra),
                                       tx=int(tn.get("tx") or 0), rx=int(tn.get("rx") or 0))
            elif "hint" in p:
                peer.tailnet = Tailnet(online=p.get("hint"), stale=True)
            rec = p.get("record")
            if isinstance(rec, dict):
                held = self._held.get(gid)
                peer.record = Record(seen_age=_plus(rec.get("seen_age_s") or 0.0, extra),
                                     farm_age=_plus(rec.get("farm_age_s"), extra),
                                     farm_stale=bool(rec.get("farm_stale")), agents=int(rec.get("agents") or 0),
                                     rev=rec.get("rev"), counter=rec.get("counter"),
                                     incarnation=rec.get("incarnation"),
                                     held_for=_plus(rec.get("held_for_s"), extra) if "held_for_s" in rec else
                                     (None if held is None else now - held))
                admitted = {"first": "join", "restart": "restart", "readmit": "readmit"}.get(rec.get("admitted"))
                if admitted and peer.record.held_for is not None and peer.record.held_for <= BACK_S:
                    peer.back_kind, peer.back_age = admitted, peer.record.held_for
            back = self._back.get(gid)
            if not peer.back_kind and back is not None and now - back[1] <= BACK_S:
                peer.back_kind, peer.back_age = back[0], now - back[1]
            if gid:
                by_id[gid] = peer
            peers.append(peer)
        for f in mesh.get("forgotten") or ():
            gid = f.get("id")
            age = _plus(f.get("removed_age_s") or 0.0, extra)
            left = f.get("forget_left_s")
            gone = Gone(f.get("why") or "gone", age,
                        max(0.0, FORGET_S - age) if left is None else max(0.0, left - extra))
            if gid in by_id:
                if by_id[gid].record is None:
                    by_id[gid].gone = gone
            elif gid:
                peers.append(Peer(key=gid, name=gid, gone=gone))
        return peers
