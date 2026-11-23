# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The demo's simulation: a made-up team, and the communication plane as ONE grange (this Mac's,
djp3) sees it. `python main.py --demo` runs the app on it (demo.py maps it onto model.Snapshot),
because today's grange cannot fill every row of the Stack.

Every name, address and number is invented; nothing is read from Docker, Tailscale or a
grange. The simulation follows the grange's real rules where they shape what a viewer would
see: the neighbour choice of R6.10 (successor plus sha256-ranked picks, same code), push on a
content change and forward-once (R6.11, R6.12), one repair every 3 s (R6.13), the 10 s
heartbeat carried only in digests (R6.2), failed marks cleared by discovery (R6.20), expiry at
60 s and the forget map (R6.4 to R6.6), farm_stale (R6.3), and the 409 / 403 / 503 refusals.

What the demo may pass on is what the local grange and its sidecar could tell a viewer:

  sim.me                  this grange's own record and counters          (GET /health today)
  sim.records             peer records as our Store holds them           (GET /v1/agents today)
  sim.links               every discovered address: binding, hint, mark,
                          role, gossip counters, the sidecar's view       (GET /v1/mesh, docs/openapi.yaml)
  sim.events              every exchange and state change, newest last   (GET /v1/mesh/events)
  sim.history             one Sample per peer per second, 300 s           (an app keeps this itself)
  sim.rate                gossip bytes per second, out and in, 300 s      (an app keeps this itself)
  sim.reach               who is known to hold our last own change        (from repair digests)
  sim.overlay()           every grange's neighbours, recomputed locally   (R6.10 is deterministic)

sim.team is the truth about every grange, which no viewer has; demo.py never reads it.

The scenario loops every LOOP seconds (times are seconds into the loop):

   20  djp3's farm reports a change: pushed to the 5 neighbours, the reach curve fills
   35  frank closes his lid: pushes to him time out, he is marked, re-enters at each
       discovery refresh, expires at ~95; headscale calls him offline at ~185; back at 210
   50  tobias's home router drops UDP: his path falls back to DERP; direct again at 140
   60  judy starts a build with GRANGE_GROUP=proto-judy: her exchanges are refused 409
   80  carol joins the team (discovered at the next refresh); she runs make down at 235
   88  headscale is unreachable: tailscaled warns, tunnels already up keep working; back at 108
  112  the grange loses the sidecar's LocalAPI socket: discovery keeps its last list, every
       incoming exchange fails the whois check (403), outgoing still works; back at 132
  110  kenji's farm goes quiet; farm_stale flips at 170; his farm is back at 200
  125  bob restarts his grange: a leaving body, then a new incarnation at 131
  150  an untagged device reaches the gossip port: refused 403 by the whois check
  160  mira's image goes stale (the tag moved): 503 on everything; she updates at 230
  196  this Mac's sidecar container stops: 127.0.0.1:7788 goes with it, so nothing can read
       the grange; make up at 211 recreates both containers (new incarnation, cursor from 1)

The code below is the design mock-ups' simulation, kept as it was but for one thing: who the
agents are. It only counted them; each farm now also has its sessions by name and state, made
up from a generator of their own (farm_rng), so the same seed still plays the same events.
"""
from __future__ import annotations

import hashlib
import heapq
import random
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Callable, Optional

# ---- the grange's constants (config.py defaults) -------------------------------------------
ME = "djp3"
GOSSIP_PORT = 7789
FANOUT, HEARTBEAT, REPAIR_S, DISCOVERY_S = 5, 10.0, 3.0, 30.0
EXPIRE_S, FARM_STALE_S, FORGET_S, CONNECT_TIMEOUT = 60.0, 60.0, 600.0, 1.0
LOOP = 240.0
START = 100.0                    # where the demo starts, so there is history to draw

# ---- who the agents are: made up, in the shape of agent-farm's session names -------------------
PROJECTS = ("web", "api", "infra", "docs", "ios", "billing")
TASKS = ("fix tests", "docs", "refactor", "login bug", "release", "flaky ci", "migration", "onboarding",
         "perf probe", "review", "search index", "export", "dark mode", "rate limiter", "nightly", "lint",
         "cache layer", "i18n", "upgrade deps", "telemetry", "retry logic", "invoice pdf", "a11y pass",
         "webhooks", "feature flag", "db backup")
STATES = ("busy", "waiting", "idle")                 # agent-farm's words; a grange passes any word on (R4.1)
MOVES = {"busy": ("waiting", "idle", "idle"), "waiting": ("busy",), "idle": ("busy",)}


# ---- R6.10, exactly as grange/gossip.py does it ------------------------------------------------
def hash_rank(own_id: str, addr: str) -> bytes:
    return hashlib.sha256(f"{own_id}/{addr}".encode("utf-8")).digest()


def choose_neighbours(own_id: str, own_addr: str, live: list, fanout: int = FANOUT) -> list:
    if not live or fanout < 1:
        return []
    ordered = sorted(live)
    successor = next((a for a in ordered if a > own_addr), ordered[0])
    others = sorted((a for a in ordered if a != successor), key=lambda a: hash_rank(own_id, a))
    return [successor] + others[:fanout - 1]


# ---- the made-up team --------------------------------------------------------------------------
@dataclass
class G:
    """The truth about one grange, which the local grange only partly sees."""
    gid: str
    octet: int
    where: str                   # office · home · travel: shapes latency and path
    agents: int
    access_ms: float             # one-way latency from its laptop to the office core
    relayed: bool = False        # its sidecar reaches us only through DERP
    present: bool = True         # its device is in the tailnet (discoverable by tag)
    running: bool = True         # the grange process answers
    hint: bool = True            # what headscale (via our sidecar) says: online
    group: str = "default"
    stale_image: bool = False    # the kill-switch tag moved past its build: 503 on everything
    incarnation: int = 0
    rev: int = 1
    counter: int = 1
    farm_alive: bool = True
    last_farm: float = 0.0
    farm_stale: bool = False
    hb_phase: float = 0.0
    roster: list = field(default_factory=list)   # its farm's sessions, (name, state): as many as `agents`

    @property
    def ip(self) -> str:
        return f"100.64.0.{self.octet}"

    @property
    def addr(self) -> str:
        return f"{self.ip}:{GOSSIP_PORT}"

    @property
    def in_mesh(self) -> bool:
        return self.present and self.running and not self.stale_image and self.group == "default"


TEAM = [  # gid, octet, where, agents, access ms, relayed
    ("bob", 1, "office", 3, 1.5, False), ("dave", 2, "home", 2, 11.0, False),
    ("erin", 3, "office", 4, 1.2, False), ("grace", 4, "office", 1, 1.8, False),
    ("djp3", 5, "office", 6, 1.0, False), ("alice", 6, "office", 5, 1.4, False),
    ("heidi", 7, "home", 2, 14.0, False), ("ivan", 8, "travel", 1, 38.0, True),
    ("kenji", 9, "office", 4, 1.6, False), ("mira", 10, "home", 3, 9.0, False),
    ("tobias", 11, "home", 2, 12.0, False), ("frank", 12, "office", 3, 1.3, False),
    ("carol", 13, "home", 2, 16.0, False), ("judy", 14, "office", 1, 1.7, False),
]
DERP_MS = 31.0                   # extra one-way delay through the relay
REKEY_S = 120.0                  # WireGuard re-keys a session this old when it next sends
UNTAGGED_IP = "100.64.0.40"      # a teammate's second laptop, joined without the tag


# ---- what the local grange holds and sees ---------------------------------------------------
@dataclass
class Rec:
    """A peer record as our Store holds it (R4.5, R5.9)."""
    gid: str
    incarnation: int
    counter: int
    rev: int
    seen_at: float               # sim time of the last liveness refresh
    farm_age_at_seen: Optional[float]
    farm_stale: bool
    agents: int
    first_at: float              # when this incarnation was first accepted
    roster: tuple = ()           # the agents in the body last accepted, (name, state)


@dataclass
class Link:
    """One discovered address: the gossip core's view plus the sidecar's view of the peer."""
    addr: str
    ip: str
    hostname: str
    gid: Optional[str] = None    # binding, from the `from` of the last good exchange (R6.19)
    hint: bool = True            # online hint from the sidecar's status
    failed: bool = False         # R6.20 mark
    discovered_at: float = 0.0
    # sidecar (tailscale status, ping)
    path: str = "direct"         # direct · derp
    cur_addr: str = ""           # the peer's UDP endpoint when direct
    latency_ms: float = 0.0
    rx: int = 0
    tx: int = 0
    handshake_at: float = 0.0
    # gossip core
    last_ok: Optional[float] = None
    last_fail: Optional[float] = None
    last_status: Optional[int] = None    # 200 / 204 / 403 / 409 / 503 / None for a timeout
    sent: Counter = field(default_factory=Counter)      # push · repair · probe
    recv: Counter = field(default_factory=Counter)      # push · repair
    fails: int = 0
    bytes_out: int = 0
    bytes_in: int = 0
    lag: Optional[int] = 0       # records it held older than ours at the last repair
    role: str = "outside"        # successor · neighbour · pool · outside


@dataclass
class Ev:
    """One line of what happened, as the local grange would log or count it."""
    t: float
    kind: str      # push-out push-in repair-out repair-in probe refused discover expire
                   # leave readmit restart join farm stale path neighbours fresh mark
    peer: str      # a grange id, or an address when no binding exists
    ok: bool = True
    status: Optional[int] = None
    out_b: int = 0
    in_b: int = 0
    rtt_ms: Optional[float] = None
    note: str = ""
    bodies: tuple = ()           # ((gid, rev, outcome), ...) outcome: new · dup · own · live

    @property
    def lane(self) -> str:
        return self.peer


@dataclass
class Sample:
    t: float
    seen_age: Optional[float]    # None: no record held
    farm_age: Optional[float]
    farm_stale: bool
    hint: bool
    failed: bool
    live: bool
    role: str
    path: str
    latency_ms: float
    beat: bool                   # its counter moved in this second (a heartbeat arrived)
    change: bool                 # its rev moved in this second


@dataclass
class Me:
    gid: str = ME
    addr: str = f"100.64.0.5:{GOSSIP_PORT}"
    incarnation: int = 1795040341941
    rev: int = 1
    counter: int = 1
    cursor: int = 1
    agents: int = 6
    last_farm: float = 0.0
    farm_stale: bool = False
    rejected: Counter = field(default_factory=lambda: Counter(proto=0, group=0, identity=0, overflow=0))
    sent: Counter = field(default_factory=Counter)
    outstanding: int = 0
    duplicate_id: bool = False
    corrections: int = 0
    resumes: int = 0
    fresh_polls: int = 0
    fresh_unchanged: int = 0
    last_fresh: float = 0.0
    built: str = "bf6e87e"
    tag_commit: str = "bf6e87e"
    home_derp: str = "headscale"
    derp_ms: float = 9.0
    health: tuple = ()
    sidecar: str = "ok"          # ok · no-control · no-socket · logged-out · down (7788 unreachable)
    sidecar_since: Optional[float] = None
    restarts: int = 0            # make up after the sidecar stopped
    farm_pushes: int = 0
    longpolls: int = 1           # frolic's long-poll held open
    last_gossip: Optional[float] = None
    roster: list = field(default_factory=list)   # this Mac's farm's sessions, (name, state)


class Sim:
    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)
        self.farm_rng = random.Random(f"farms {seed}")   # who the agents are; never the events' generator
        self._pr = 40                    # the last made-up pull request number
        self.now = 0.0
        self._q: list = []
        self._seq = 0
        self.team = {gid: G(gid, o, w, a, ms, rel, incarnation=1795040000000 + o * 7919,
                            hb_phase=(o * 1.37) % HEARTBEAT)
                     for gid, o, w, a, ms, rel in TEAM}
        self.me = Me()
        for farm in (self.me, *(g for g in self.team.values() if g.gid != ME)):
            farm.roster = [(self._session(), self.farm_rng.choices(STATES, (5, 1, 4))[0]) for _ in range(farm.agents)]
        self.records: dict = {}
        self.forget: dict = {}
        self.links: dict = {}            # addr -> Link
        self.neighbours: list = []       # addresses
        self.events: deque = deque(maxlen=2000)
        self.history: dict = {}          # gid -> deque[Sample]
        self.rate: deque = deque(maxlen=300)   # (t, out_bytes, in_bytes) per second
        self.path_bytes = Counter()      # direct · derp, sidecar totals
        self.reach: dict = {}            # gid -> seconds after our last own change it was confirmed
        self.reach_rev = 0
        self.reach_at: Optional[float] = None
        self.prop: deque = deque(maxlen=60)     # (t, origin, seconds until it reached us, copies)
        self._sec_out = 0
        self._sec_in = 0
        self._last_second = -1
        self._beats: set = set()
        self._changes: set = set()
        self._logged_refusals: set = set()
        self._pending: dict = {}         # (origin, inc, rev) -> copies seen
        self.listeners: list = []

        # judy and carol are not around at t=0
        self.team["carol"].present = self.team["carol"].running = False
        self.team["judy"].present = self.team["judy"].running = False
        self.team["judy"].group = "proto-judy"
        # start: everyone known, as if the mesh had been up a while
        for g in self.team.values():
            g.last_farm = -self.rng.uniform(0, 25)
            if g.gid == ME or not g.present:
                continue
            self.records[g.gid] = Rec(g.gid, g.incarnation, g.counter, g.rev, 0.0, 5.0, False,
                                      g.agents, -3600.0, tuple(g.roster))
        self._discover(quiet=True)
        for link in self.links.values():
            link.gid = link.hostname[len("grange-"):]
        self._recompute(quiet=True)
        self._schedule_all()

    # ------------------------------------------------------------------ scheduling
    def at(self, t: float, fn: Callable, *args) -> None:
        self._seq += 1
        heapq.heappush(self._q, (t, self._seq, fn, args))

    def every(self, start: float, period: float, fn: Callable, *args) -> None:
        def run(*a):
            fn(*a)
            self.at(self.now + period, run, *a)
        self.at(start, run, *args)

    def advance(self, to: float) -> None:
        while self._q and self._q[0][0] <= to:
            t, _, fn, args = heapq.heappop(self._q)
            self._tick_seconds(t)
            self.now = t
            fn(*args)
        self._tick_seconds(to)
        self.now = to

    def _tick_seconds(self, t: float) -> None:
        while self._last_second + 1 <= t:
            self._last_second += 1
            self.now = float(self._last_second)
            self._second()

    def _schedule_all(self) -> None:
        self.every(0.7, REPAIR_S, self._my_repair_tick)
        self.every(0.0, DISCOVERY_S, self._my_discovery_tick)
        self.every(4.0, 30.0, self._fresh_poll)
        self.every(2.0, 30.0, self._my_farm_heartbeat)
        for g in self.team.values():
            if g.gid != ME:
                self.every(g.hb_phase, HEARTBEAT, self._heartbeat, g.gid)
                self.every(1.3 + (g.octet * 0.61) % REPAIR_S, REPAIR_S, self._their_repair_tick, g.gid)
                self.every((g.octet * 2.3) % 30.0, 30.0, self._their_farm_heartbeat, g.gid)
            self.at(self.rng.expovariate(1 / 50.0), self._farm_change, g.gid)
        self.every(self.me.incarnation % 10 / 1.0, HEARTBEAT, self._my_heartbeat)
        for base in range(0, 40):
            o = base * LOOP
            for t, fn, args in [
                (20, self._farm_change, (ME, True)), (35, self._lid, ("frank", False)),
                (185, self._hint, ("frank", False)), (210, self._lid, ("frank", True)),
                (50, self._path, ("tobias", True)), (140, self._path, ("tobias", False)),
                (60, self._judy, (True,)), (225, self._judy, (False,)),
                (80, self._carol, (True,)), (235, self._carol, (False,)),
                (110, self._farm_quiet, ("kenji", True)), (200, self._farm_quiet, ("kenji", False)),
                (125, self._restart_leave, ("bob",)), (131, self._restart_back, ("bob",)),
                (150, self._untagged, ()), (160, self._stale, ("mira", True)),
                (88, self._sidecar, ("no-control",)), (108, self._sidecar, ("ok",)),
                (112, self._sidecar, ("no-socket",)), (132, self._sidecar, ("ok",)),
                (196, self._container, (False,)), (211, self._container, (True,)),
                (230, self._stale, ("mira", False)),
            ]:
                self.at(o + t, fn, *args)

    # ------------------------------------------------------------------ helpers
    def log(self, ev: Ev) -> Ev:
        self.events.append(ev)
        self._sec_out += ev.out_b
        self._sec_in += ev.in_b
        link = self.link_for(ev.peer)
        if link is not None:
            link.bytes_out += ev.out_b
            link.bytes_in += ev.in_b
            overhead = 1.18
            link.tx += int(ev.out_b * overhead) + 64
            link.rx += int(ev.in_b * overhead) + 64
            self.path_bytes[link.path] += int((ev.out_b + ev.in_b) * overhead) + 128
            if ev.ok and (ev.out_b or ev.in_b) and ev.t - link.handshake_at > REKEY_S:
                link.handshake_at = ev.t              # traffic on an old session re-keys it
        for fn in self.listeners:
            fn(ev)
        return ev

    def link_for(self, who: str) -> Optional[Link]:
        if who in self.links:
            return self.links[who]
        for link in self.links.values():
            if link.gid == who or link.hostname == f"grange-{who}":
                return link
        return None

    def lat(self, a: str, b: str) -> float:
        """One-way seconds between two granges."""
        ga, gb = self.team[a], self.team[b]
        ms = ga.access_ms + gb.access_ms + (DERP_MS if (ga.relayed or gb.relayed) else 0.0)
        return ms / 1000.0

    def _session(self) -> str:
        """A made-up session name no farm has yet: project, pull request, what it is about."""
        self._pr += self.farm_rng.randrange(1, 9)
        return f"{self.farm_rng.choice(PROJECTS)}-pr-{self._pr} {self.farm_rng.choice(TASKS)}"

    def _farm_says(self, roster: list, agents: int) -> None:
        """A farm's report changed and it now counts `agents`. When the count stayed as it was,
        and half the time anyway, one of its sessions changes state; then they are brought to
        that many: one starts, or an idle one ends. Only farm_rng is drawn from."""
        rng = self.farm_rng
        if roster and (len(roster) == agents or rng.random() < 0.5):
            i = rng.randrange(len(roster))
            name, state = roster[i]
            roster[i] = (name, rng.choice(MOVES[state]))
        while len(roster) < agents:
            roster.append((self._session(), "busy"))
        while len(roster) > agents:
            idle = [i for i, (_, state) in enumerate(roster) if state == "idle"]
            roster.pop(rng.choice(idle) if idle else rng.randrange(len(roster)))

    def digest_bytes(self) -> int:
        return 48 + 52 * (len(self.records) + 1)

    def body_bytes(self, agents: int) -> int:
        return 190 + 64 * agents

    def live_addrs(self) -> list:
        ids = set(self.records)
        return sorted(a for a, l in self.links.items()
                      if not l.failed and ((l.gid in ids) or l.hint))

    def _recompute(self, quiet: bool = False) -> None:
        live = self.live_addrs()
        new = choose_neighbours(ME, self.me.addr, live)
        if new != self.neighbours:
            gone = [a for a in self.neighbours if a not in new]
            came = [a for a in new if a not in self.neighbours]
            self.neighbours = new
            if not quiet and (gone or came):
                name = lambda a: self.links[a].gid or a  # noqa: E731
                self.log(Ev(self.now, "neighbours", ME, note=" ".join(
                    [f"+{name(a)}" for a in came] + [f"−{name(a)}" for a in gone])))
        for a, l in self.links.items():
            if a == (new[0] if new else None):
                l.role = "successor"
            elif a in new:
                l.role = "neighbour"
            elif a in live:
                l.role = "pool"
            else:
                l.role = "outside"

    def overlay(self) -> dict:
        """Every grange's neighbours as R6.10 picks them, assuming each sees our live set.
        Keys and values are grange ids; computed from addresses and bindings alone."""
        live = [a for a in self.live_addrs()] + [self.me.addr]
        by_addr = {a: (self.links[a].gid if a in self.links else ME) for a in live}
        out = {}
        for a in live:
            gid = by_addr[a]
            if gid is None:
                continue
            out[gid] = [by_addr[n] for n in choose_neighbours(gid, a, [x for x in live if x != a])]
        return out

    def _true_neighbours(self, gid: str) -> list:
        """What a grange really picks, from the granges really in the mesh."""
        mesh = [g for g in self.team.values() if g.in_mesh or g.gid == ME]
        own = self.team[gid]
        live = [g.addr for g in mesh if g.gid != gid]
        by_addr = {g.addr: g.gid for g in mesh}
        return [by_addr[a] for a in choose_neighbours(gid, own.addr, live)]

    def _status_from(self, gid: str) -> Optional[int]:
        """What an exchange to this grange gets: None is a timeout."""
        g = self.team[gid]
        if not g.present or not g.running:
            return None
        if g.stale_image:
            return 503
        if g.group != "default":
            return 409
        return 200

    # ------------------------------------------------------------------ discovery & marks
    def _discover(self, quiet: bool = False) -> None:
        seen = set()
        for g in self.team.values():
            if g.gid == ME or not g.present:
                continue
            seen.add(g.addr)
            link = self.links.get(g.addr)
            if link is None:
                link = Link(g.addr, g.ip, f"grange-{g.gid}", discovered_at=self.now,
                            handshake_at=self.now - self.rng.uniform(0, REKEY_S))
                self.links[g.addr] = link
                if not quiet:
                    self.log(Ev(self.now, "discover", g.gid, note=f"{g.ip} tagged, online"))
            if link.hint != g.hint and not quiet:
                self.log(Ev(self.now, "discover", link.gid or g.gid,
                            note=f"hint {'online' if g.hint else 'offline'}"))
            link.hint = g.hint
            if g.hint and link.failed:
                link.failed = False              # R6.20: the refresh reporting it online
            link.path = "derp" if g.relayed else "direct"
            link.cur_addr = "" if g.relayed else f"192.168.{1 if g.where == 'office' else 40 + g.octet}.{20 + g.octet}:41641"
            link.latency_ms = 2 * self.lat(ME, g.gid) * 1000 + self.rng.uniform(0.1, 0.6)
        for a in [a for a in self.links if a not in seen]:
            del self.links[a]

    def _mark(self, link: Link, status: Optional[int]) -> None:
        was = link.failed
        link.failed = True
        link.fails += 1
        link.last_fail = self.now
        link.last_status = status
        if not was:
            self.log(Ev(self.now, "mark", link.gid or link.addr, ok=False, status=status,
                        note="timeout" if status is None else f"answered {status}"))
            self._recompute()

    def _ok(self, link: Link, gid: str, status: int) -> None:
        link.failed = False
        link.last_ok = self.now
        link.last_status = status
        if link.gid != gid:
            link.gid = gid
        self.me.last_gossip = self.now

    # ------------------------------------------------------------------ our timers
    @property
    def down(self) -> bool:
        return self.me.sidecar == "down"

    def _my_discovery_tick(self) -> None:
        if self.down:
            return
        if self.me.sidecar == "no-socket":           # a failed refresh keeps the last list
            self.log(Ev(self.now, "discover", ME, ok=False,
                        note="refresh failed: the sidecar's LocalAPI does not answer; kept the last list"))
        else:
            self._discover()
        self._recompute()
        outside = [a for a, l in self.links.items() if l.role == "outside"]
        if outside:
            self._repair_to(self.rng.choice(outside), "probe")

    def _my_repair_tick(self) -> None:
        if self.down:
            return
        live = self.live_addrs()
        if not live:
            outside = list(self.links)
            if outside:
                self._repair_to(self.rng.choice(outside), "probe")
            return
        pool = [a for a in live if a not in self.neighbours] or live
        self._repair_to(self.rng.choice(pool), "repair")

    def _my_heartbeat(self) -> None:
        if not self.down:
            self.me.counter += 1

    def _my_farm_heartbeat(self) -> None:
        if not self.team[ME].farm_alive or self.down:
            return
        self.me.last_farm = self.now
        self.me.farm_pushes += 1

    def _fresh_poll(self) -> None:
        if self.down:
            return
        self.me.fresh_polls += 1
        self.me.fresh_unchanged += 1
        self.me.last_fresh = self.now
        self.log(Ev(self.now, "fresh", ME, status=304, note="tag unchanged (304, free)"))

    # ------------------------------------------------------------------ exchanges we start
    def _repair_to(self, addr: str, kind: str) -> None:
        link = self.links.get(addr)
        if link is None:
            return
        g = next((x for x in self.team.values() if x.addr == addr), None)
        if g is None:
            return
        link.sent[kind] += 1
        self.me.sent[kind] += 1
        req = self.digest_bytes()
        status = self._status_from(g.gid)
        if status is None:
            self.at(self.now + CONNECT_TIMEOUT, self._repair_timeout, link, kind)
            return
        rtt = 2 * self.lat(ME, g.gid) + self.rng.uniform(0.002, 0.009)
        if status != 200:
            self.at(self.now + rtt, self._repair_refused, link, kind, status, req, rtt)
            return
        self.at(self.now + rtt, self._repair_back, link, g.gid, kind, req, rtt)

    def _repair_timeout(self, link: Link, kind: str) -> None:
        self.log(Ev(self.now, "probe" if kind == "probe" else "repair-out", link.gid or link.addr,
                    ok=False, out_b=0, rtt_ms=CONNECT_TIMEOUT * 1000, note="timeout"))
        self._mark(link, None)

    def _repair_refused(self, link: Link, kind: str, status: int, req: int, rtt: float) -> None:
        self.log(Ev(self.now, "probe" if kind == "probe" else "repair-out", link.gid or link.addr,
                    ok=False, status=status, out_b=req, in_b=180, rtt_ms=rtt * 1000,
                    note={409: "group 'proto-judy'", 503: "image out of date"}.get(status, "")))
        self._mark(link, status)

    def _repair_back(self, link: Link, gid: str, kind: str, req: int, rtt: float) -> None:
        self._ok(link, gid, 200)
        bodies, lag = self._sync_with(gid)
        resp = self.digest_bytes() + sum(self.body_bytes(self.team[b].agents) for b, _, _ in bodies)
        link.lag = lag
        ev = self.log(Ev(self.now, "probe" if kind == "probe" else "repair-out", gid, status=200,
                         out_b=req, in_b=resp, rtt_ms=rtt * 1000, bodies=tuple(bodies),
                         note=f"{len(bodies)} body(ies)" if bodies else ""))
        if lag:
            held = [x for x in self.records if x != gid][:lag] + [ME]
            self.at(self.now + 0.004, self._push_to, link.addr, held, "follow-up")
        return ev

    def _sync_with(self, peer: str):
        """Liveness from the peer's digest, plus any body it holds newer: what a repair with a
        running peer brings back (R5.10, R5.18)."""
        bodies = []
        for g in self.team.values():
            if g.gid == ME or not g.in_mesh:
                continue
            if g.gid == peer or self._knows(peer, g.gid):
                r = self.records.get(g.gid)
                if r is None or (g.incarnation, g.rev) > (r.incarnation, r.rev):
                    outcome = self._accept(g.gid, source=peer)
                    if outcome in ("new", "readmit", "restart"):
                        bodies.append((g.gid, g.rev, outcome))
                elif g.incarnation == r.incarnation and g.counter > r.counter:
                    self._refresh(g.gid)
        if self.reach_at is not None and self.reach_rev == self.me.rev and peer not in self.reach:
            self.reach[peer] = self.now - self.reach_at     # its digest shows our latest rev
        r = self.records.get(peer)
        # a grange that restarted moments ago still lacks most of the board: the follow-up
        lag = len(self.records) if r is not None and self.now - r.first_at < 4.0 else 0
        return bodies, lag

    def _knows(self, holder: str, gid: str) -> bool:
        """Does `holder` hold gid's current counter? Close enough: if both are in the mesh."""
        h = self.team.get(holder)
        return h is not None and h.in_mesh

    def _push_to(self, addr: str, gids: list, why: str = "") -> None:
        link = self.links.get(addr)
        if link is None:
            return
        g = next((x for x in self.team.values() if x.addr == addr), None)
        if g is None:
            return
        link.sent["push"] += 1
        self.me.sent["push"] += 1
        size_ = 60 + sum(self.body_bytes(self.team[x].agents if x != ME else self.me.agents) for x in gids)
        status = self._status_from(g.gid)
        revs = tuple((x, (self.me.rev if x == ME else self.team[x].rev), "") for x in gids)
        if status is None:
            self.at(self.now + CONNECT_TIMEOUT, self._push_failed, link, None, size_, revs, why)
            return
        rtt = 2 * self.lat(ME, g.gid) + self.rng.uniform(0.001, 0.004)
        if status != 200:
            self.at(self.now + rtt, self._push_failed, link, status, size_, revs, why)
            return
        self.at(self.now + rtt, self._push_done, link, g.gid, size_, revs, rtt, why)

    def _push_done(self, link, gid, size_, revs, rtt, why) -> None:
        self._ok(link, gid, 204)
        self.log(Ev(self.now, "push-out", gid, status=204, out_b=size_, rtt_ms=rtt * 1000,
                    bodies=revs, note=why))
        if self.reach_at is not None and any(x == ME for x, _, _ in revs) and gid not in self.reach:
            self.reach[gid] = self.now - self.reach_at

    def _push_failed(self, link, status, size_, revs, why) -> None:
        self.log(Ev(self.now, "push-out", link.gid or link.addr, ok=False, status=status,
                    out_b=0 if status is None else size_, bodies=revs,
                    rtt_ms=CONNECT_TIMEOUT * 1000 if status is None else None,
                    note="timeout" if status is None else {409: "group mismatch", 503: "image out of date"}.get(status, why)))
        self._mark(link, status)

    # ------------------------------------------------------------------ the Store
    def _accept(self, gid: str, source: str) -> str:
        g = self.team[gid]
        r = self.records.get(gid)
        if r is None:
            fg = self.forget.get(gid)
            if fg is not None and (g.incarnation, g.counter) <= fg[0]:
                return "ignored"
            kind = "new" if fg is None else ("restart" if g.incarnation > fg[0][0] else "readmit")
            if gid in self.forget:
                del self.forget[gid]
            self.records[gid] = Rec(gid, g.incarnation, g.counter, g.rev, self.now,
                                    self.now - g.last_farm, g.farm_stale, g.agents, self.now, tuple(g.roster))
            self.me.cursor += 1
            self._changes.add(gid)
            if kind == "new" and fg is None and gid in ("carol",):
                self.log(Ev(self.now, "join", gid, note=f"first body, via {source}"))
            elif kind != "new":
                self.log(Ev(self.now, kind, gid, note=f"incarnation {'new' if kind == 'restart' else 'same'}, via {source}"))
            self._recompute()
            return kind
        if (g.incarnation, g.rev) > (r.incarnation, r.rev):
            restarted = g.incarnation > r.incarnation
            r.incarnation, r.rev, r.counter = g.incarnation, g.rev, g.counter
            r.seen_at = self.now
            r.farm_age_at_seen = self.now - g.last_farm
            r.farm_stale, r.agents, r.roster = g.farm_stale, g.agents, tuple(g.roster)
            if restarted:
                r.first_at = self.now
                self.log(Ev(self.now, "restart", gid, note=f"new incarnation, via {source}"))
            self.me.cursor += 1
            self._changes.add(gid)
            self._beats.add(gid)
            return "new"
        return "dup"

    def _refresh(self, gid: str) -> None:
        g, r = self.team[gid], self.records.get(gid)
        if r is None or g.incarnation != r.incarnation or g.counter <= r.counter:
            return
        r.counter = g.counter
        r.seen_at = self.now
        r.farm_age_at_seen = self.now - g.last_farm
        self._beats.add(gid)

    # ------------------------------------------------------------------ their timers
    def _heartbeat(self, gid: str) -> None:
        g = self.team[gid]
        if g.running and not g.stale_image:
            g.counter += 1

    def _their_farm_heartbeat(self, gid: str) -> None:
        g = self.team[gid]
        if g.running and g.farm_alive:
            g.last_farm = self.now

    def _their_repair_tick(self, gid: str) -> None:
        """Another grange's repair tick; it lands on us when we are its random pick."""
        g = self.team[gid]
        if self.down:
            return
        if not g.present or not g.running or g.stale_image:
            return
        if g.group != "default":
            # judy's build: she sees us by tag, her exchanges are refused here (R5.11)
            if self.rng.random() < 0.18:
                self._refused(g.ip, "group", 409, "group 'proto-judy' is not 'default'")
            return
        mesh = [x for x in self.team.values() if (x.in_mesh or x.gid == ME) and x.gid != gid]
        nbrs = set(self._true_neighbours(gid))
        pool = [x for x in mesh if x.gid not in nbrs] or mesh
        if not pool or self.rng.choice(pool).gid != ME:
            return
        link = self.link_for(gid)
        if link is None or self.down:
            return
        if self.me.sidecar == "no-socket":
            self._refused(g.ip, "identity", 403, "whois failed: the sidecar's LocalAPI does not answer")
            return
        link.recv["repair"] += 1
        rtt = 2 * self.lat(ME, gid)
        self._ok(link, gid, 200)
        bodies, _ = self._sync_with(gid)
        self.log(Ev(self.now, "repair-in", gid, status=200, in_b=self.digest_bytes(),
                    out_b=self.digest_bytes(), rtt_ms=rtt * 1000, bodies=tuple(bodies)))

    def _farm_change(self, gid: str, scripted: bool = False) -> None:
        if not scripted:
            self.at(self.now + self.rng.expovariate(1 / (55.0 if gid != ME else 70.0)), self._farm_change, gid)
        if gid == ME:
            if not self.team[ME].farm_alive or self.down:
                return
            self.me.rev += 1
            self.me.counter += 1
            self.me.cursor += 1
            self.me.last_farm = self.now
            self.me.agents = max(1, min(9, self.me.agents + self.rng.choice((-1, 1))))
            self._farm_says(self.me.roster, self.me.agents)
            self.log(Ev(self.now, "farm", ME, note=f"farm: {self.me.agents} agent(s), content change, rev {self.me.rev}"))
            self.reach = {}
            self.reach_rev = self.me.rev
            self.reach_at = self.now
            for a in list(self.neighbours):
                self._push_to(a, [ME], "own change")
            self._spread(ME)
            return
        g = self.team[gid]
        if not g.in_mesh or not g.farm_alive:
            return
        g.rev += 1
        g.counter += 1
        g.last_farm = self.now
        g.agents = max(0, min(9, g.agents + self.rng.choice((-1, 0, 1))))
        self._farm_says(g.roster, g.agents)
        self._spread(gid)

    def _spread(self, origin: str) -> None:
        """Forward-once over every grange's real neighbours (Dijkstra on per-hop latency).
        The copies that reach us arrive as push-in events: the first is new, the rest dups."""
        g0 = self.team[origin]
        key = (origin, g0.incarnation if origin != ME else self.me.incarnation,
               g0.rev if origin != ME else self.me.rev)
        mesh = {g.gid for g in self.team.values() if g.in_mesh} | {ME}
        nb = {x: self._true_neighbours(x) for x in mesh}
        arr = {origin: self.now}
        pred = {origin: None}
        todo = [(self.now, origin)]
        while todo:
            t, u = heapq.heappop(todo)
            if t > arr.get(u, 1e18):
                continue
            for v in nb.get(u, []):
                if v not in mesh or v == pred.get(u):
                    continue
                tv = t + self.lat(u, v) + self.rng.uniform(0.003, 0.02)
                if tv < arr.get(v, 1e18):
                    arr[v] = tv
                    pred[v] = u
                    heapq.heappush(todo, (tv, v))
        if origin == ME:
            for v, tv in arr.items():
                if v != ME:
                    self.at(tv + self.rng.uniform(0.2, 3.2), self._confirm, v, key[2])
            return
        if ME not in arr:
            return
        copies = []
        for u in sorted(mesh):
            if u == ME or u not in arr or ME not in nb.get(u, []):
                continue
            if pred.get(u) == ME:
                continue
            copies.append((arr[u] + self.lat(u, ME) + self.rng.uniform(0.002, 0.01), u))
        copies.sort()
        self._pending[key] = 0
        for t, u in copies:
            self.at(t, self._push_in, u, origin, key, arr[ME] - self.now if ME in arr else None)

    def _confirm(self, gid: str, rev: int) -> None:
        if self.reach_rev == rev and self.reach_at is not None and gid not in self.reach:
            self.reach[gid] = self.now - self.reach_at

    def _push_in(self, sender: str, origin: str, key, _arrival) -> None:
        link = self.link_for(sender)
        if link is None or self.down:
            return
        if self.me.sidecar == "no-socket":
            self._refused(self.team[sender].ip, "identity", 403, "whois failed: the sidecar's LocalAPI does not answer")
            return
        link.recv["push"] += 1
        self._ok(link, sender, 204)
        self._pending[key] = self._pending.get(key, 0) + 1
        outcome = self._accept(origin, sender) if self._pending[key] == 1 else "dup"
        g = self.team[origin]
        if outcome == "dup":
            r = self.records.get(origin)
            if r is not None and r.rev < g.rev:
                outcome = self._accept(origin, sender)
        self.log(Ev(self.now, "push-in", sender, status=204, in_b=60 + self.body_bytes(g.agents),
                    rtt_ms=self.lat(sender, ME) * 1000, bodies=((origin, g.rev, outcome),)))
        if outcome in ("new", "restart", "readmit"):
            for a in list(self.neighbours):
                if self.links[a].gid != sender:
                    self._push_to(a, [origin], f"forward {origin}")

    # ------------------------------------------------------------------ the scenario
    def _lid(self, gid: str, open_: bool) -> None:
        g = self.team[gid]
        g.running = open_
        if open_:
            g.hint = True
            g.counter += 3
            g.last_farm = self.now          # its farm wakes with it
            self.at(self.now + 0.4, self._spread, gid)

    def _hint(self, gid: str, online: bool) -> None:
        self.team[gid].hint = online

    def _path(self, gid: str, relayed: bool) -> None:
        g = self.team[gid]
        g.relayed = relayed
        link = self.link_for(gid)
        if link:
            link.path = "derp" if relayed else "direct"
            link.cur_addr = "" if relayed else f"192.168.{40 + g.octet}.{20 + g.octet}:41641"
            link.latency_ms = 2 * self.lat(ME, gid) * 1000
            link.handshake_at = self.now
        self.log(Ev(self.now, "path", gid, ok=not relayed,
                    note="direct UDP lost: relayed through DERP 'headscale'" if relayed else "direct again"))

    def _judy(self, on: bool) -> None:
        g = self.team["judy"]
        g.present = g.running = on
        g.hint = on

    def _carol(self, on: bool) -> None:
        g = self.team["carol"]
        if on:
            g.present = g.running = g.hint = True
            g.incarnation = int(1795040000000 + self.now * 1000)
            g.rev, g.counter = 1, 1
            g.last_farm = self.now
            self.at(self.now + 0.6, self._spread, "carol")
        else:
            g.running = False
            self.at(self.now + 0.2, self._leave_spread, "carol")
            self.at(self.now + 12, self._hint, "carol", False)

    def _farm_quiet(self, gid: str, quiet: bool) -> None:
        g = self.team[gid]
        g.farm_alive = not quiet
        if not quiet:
            g.last_farm = self.now
            if g.farm_stale:
                g.farm_stale = False
                g.rev += 1
                g.counter += 1
                self._spread(gid)

    def _restart_leave(self, gid: str) -> None:
        g = self.team[gid]
        g.running = False
        self._leave_spread(gid)

    def _leave_spread(self, gid: str) -> None:
        """A leaving body, forwarded to us by whoever had it first."""
        r = self.records.get(gid)
        if r is None or self.down:
            return
        via = next((x for x in self._true_neighbours(gid) if x != ME and self.team[x].in_mesh), gid)
        if self.me.sidecar == "no-socket":
            self._refused(self.team[via].ip, "identity", 403, "whois failed: the sidecar's LocalAPI does not answer")
            return
        link = self.link_for(via)
        if link:
            link.recv["push"] += 1
            self.log(Ev(self.now, "push-in", via, status=204, in_b=220,
                        bodies=((gid, r.rev + 1, "leaving"),)))
        del self.records[gid]
        self.forget[gid] = ((r.incarnation, r.counter + 1), self.now)
        self.me.cursor += 1
        self.log(Ev(self.now, "leave", gid, note=f"leaving body via {via}"))
        self._recompute()

    def _restart_back(self, gid: str) -> None:
        g = self.team[gid]
        g.running = True
        g.incarnation = int(1795040000000 + self.now * 1000)
        g.rev, g.counter = 1, 1
        self.at(self.now + 0.5, self._spread, gid)

    def _sidecar(self, state: str) -> None:
        """This Mac's tailscaled: the coordination server lost, or its socket lost to the grange."""
        was, self.me.sidecar = self.me.sidecar, state
        self.me.sidecar_since = self.now
        if state == "no-control":
            self.me.health = ("can't reach the coordination server",)
            self.log(Ev(self.now, "sidecar", ME, ok=False,
                        note="tailscaled: can't reach the coordination server; tunnels already up keep working"))
        elif state == "no-socket":
            self.log(Ev(self.now, "sidecar", ME, ok=False,
                        note="the sidecar's LocalAPI socket does not answer: no discovery, no whois"))
        else:
            self.me.health = ()
            self.log(Ev(self.now, "sidecar", ME, note={"no-control": "coordination server reachable again",
                                                       "no-socket": "the sidecar's LocalAPI answers again"}.get(was, "ok")))

    def _container(self, up: bool) -> None:
        """The sidecar container stops, taking the published port with it; make up brings both back."""
        me = self.me
        if not up:
            me.sidecar, me.sidecar_since = "down", self.now
            return
        me.sidecar, me.sidecar_since, me.health = "ok", self.now, ()
        me.restarts += 1
        me.incarnation = int(1795040000000 + self.now * 1000)
        me.rev = me.counter = me.cursor = 1
        me.rejected = Counter(proto=0, group=0, identity=0, overflow=0)
        me.sent = Counter()
        me.last_gossip = None
        me.fresh_polls = me.fresh_unchanged = 0
        self.records.clear()
        self.forget.clear()
        self.links.clear()
        self.neighbours = []
        self.reach, self.reach_at = {}, None
        self._logged_refusals.clear()
        self._discover(quiet=True)                    # startup: discovery once, then repairs
        self._recompute(quiet=True)
        self.log(Ev(self.now, "restart", ME, note="make up: both containers recreated; new incarnation, cursor from 1"))
        for a in list(self.neighbours):
            self._repair_to(a, "repair")

    def _untagged(self) -> None:
        self._refused(UNTAGGED_IP, "identity", 403, "device does not carry tag:grange")

    def _refused(self, source: str, kind: str, status: int, msg: str) -> None:
        self.me.rejected[kind] += 1
        first = (kind, source) not in self._logged_refusals
        self._logged_refusals.add((kind, source))
        # the grange knows only the source address, and a binding if an exchange ever made one
        link = next((l for l in self.links.values() if l.ip == source), None)
        peer = (link.gid or link.addr) if link is not None else source
        self.log(Ev(self.now, "refused", peer, ok=False, status=status, in_b=self.digest_bytes(),
                    note=msg + ("" if first else " (counted, logged once)")))

    def _stale(self, gid: str, stale: bool) -> None:
        g = self.team[gid]
        if stale:
            g.stale_image = True            # nothing says so until something answers 503
        else:
            g.stale_image = False
            g.incarnation = int(1795040000000 + self.now * 1000)
            g.rev, g.counter = 1, 1
            self.at(self.now + 0.8, self._spread, gid)

    # ------------------------------------------------------------------ once a second
    def _second(self) -> None:
        now = self.now
        if self.down:                                 # nobody can read the grange: no samples
            self.rate.append((now, 0, 0))
            self._beats.clear()
            self._changes.clear()
            return
        # the ticker: expiry, farm_stale, forget map
        for gid in [gid for gid, r in self.records.items() if now - r.seen_at > EXPIRE_S]:
            r = self.records.pop(gid)
            self.forget[gid] = ((r.incarnation, r.counter), now)
            self.me.cursor += 1
            self.log(Ev(now, "expire", gid, ok=False, note=f"seen {int(now - r.seen_at)}s ago"))
            self._recompute()
        for gid in [g for g, (_, at) in self.forget.items() if now - at > FORGET_S]:
            del self.forget[gid]
        for g in self.team.values():
            if g.gid == ME:
                continue
            if g.running and not g.farm_alive and not g.farm_stale and now - g.last_farm > FARM_STALE_S:
                g.farm_stale = True
                g.rev += 1
                g.counter += 1
                self._spread(g.gid)
        # history
        live = set(self.live_addrs())
        for g in self.team.values():
            if g.gid == ME:
                continue
            r = self.records.get(g.gid)
            link = self.link_for(g.gid)
            if r is None and link is None and g.gid not in self.history:
                continue
            h = self.history.setdefault(g.gid, deque(maxlen=300))
            h.append(Sample(
                now,
                None if r is None else now - r.seen_at,
                None if r is None or r.farm_age_at_seen is None else r.farm_age_at_seen + (now - r.seen_at),
                bool(r and r.farm_stale),
                bool(link and link.hint), bool(link and link.failed), bool(link and link.addr in live),
                link.role if link else "gone", link.path if link else "",
                (link.latency_ms + self.rng.uniform(-0.3, 0.3) * (link.latency_ms / 10)) if link else 0.0,
                g.gid in self._beats, g.gid in self._changes))
        self._beats.clear()
        self._changes.clear()
        self.rate.append((now, self._sec_out, self._sec_in))
        self._sec_out = self._sec_in = 0
        for l in self.links.values():     # disco pings and WireGuard keepalives
            if l.hint and not l.failed:
                self.path_bytes[l.path] += 96

    # ------------------------------------------------------------------ for the mock-ups
    def gid_of(self, addr: str) -> str:
        l = self.links.get(addr)
        return (l.gid or l.hostname) if l else addr

    def seen_age(self, gid: str) -> Optional[float]:
        r = self.records.get(gid)
        return None if r is None else self.now - r.seen_at

    def farm_age(self, gid: str) -> Optional[float]:
        if gid == ME:
            return self.now - self.me.last_farm
        r = self.records.get(gid)
        return None if r is None or r.farm_age_at_seen is None else r.farm_age_at_seen + (self.now - r.seen_at)

    def rate_last(self, seconds: int = 60):
        """(out, in) bytes per second, averaged over the last `seconds`."""
        rows = list(self.rate)[-seconds:]
        if not rows:
            return 0.0, 0.0
        return sum(r[1] for r in rows) / len(rows), sum(r[2] for r in rows) / len(rows)

    def recent(self, seconds: float, kinds: Optional[set] = None) -> list:
        return [e for e in self.events if e.t >= self.now - seconds and (kinds is None or e.kind in kinds)]

    def next_scenario(self) -> float:
        """Seconds until the next scripted moment, for an `e` key that jumps to it."""
        marks = (20, 35, 50, 60, 80, 88, 108, 110, 112, 125, 131, 132, 140, 150, 160, 170, 185, 196, 200, 210,
                 211, 225, 230, 235)
        pos = self.now % LOOP
        nxt = next((m for m in marks if m > pos + 0.5), marks[0] + LOOP)
        return nxt - pos - 0.5
