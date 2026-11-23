# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
What the viewer draws: one Snapshot of the communication plane, as this Mac's grange and its
Tailscale sidecar report it. Sources build Snapshots (live.py from the real containers, demo.py
from a simulation); the views read Snapshots and nothing else.

Two rules hold everywhere:

  • None means "not known from the sources at hand", never "zero" or "no". GET /health,
    /v1/agents and /version say nothing per peer about gossip, and neither rev nor counter;
    those come from GET /v1/mesh (docs/openapi.yaml). Where a grange has not answered it the
    views draw None as "not known" rather than guessing.
  • Every time is an age in seconds, already worked out by the source at the moment the
    Snapshot was built. Nothing here is a timestamp, so nothing depends on anybody's clock.

Who the agents are is here for one thing, the farm news under the diagnosis: each grange's
agents by name and state (GET /v1/agents serves them), and what changed from one answer to
the next. The Stack itself still needs only how many there are.

A source is anything with:

    start()                 begin reading (threads, timers); returns at once
    stop()                  stop reading
    snapshot() -> Snapshot  cheap, callable several times a second; ages are as of now
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

# The grange's defaults (agent-grange content/grange/config.py); the views measure against them.
EXPIRE_S = 60.0                  # R6.4: a record not refreshed for this long is dropped
FARM_STALE_S = 60.0              # R6.3: a farm silent for this long is flagged farm_stale
FORGET_S = 600.0                 # R6.6: how long a dropped record is remembered
REKEY_S = 120.0                  # WireGuard re-keys a session this old when it next sends
DEFAULT_PORT = 7788              # GRANGE_PORT: the grange's local API on 127.0.0.1
GOSSIP_PORT = 7789               # GRANGE_GOSSIP_PORT, on the tailnet only

ROLES = ("successor", "neighbour", "pool", "outside")           # R6.10
SIDECAR_STATES = ("ok", "no-control", "no-socket", "logged-out", "unknown")


@dataclass
class Record:
    """One grange's record as the local grange holds it (GET /v1/agents)."""
    seen_age: float                      # since its liveness was last refreshed (R5.9)
    farm_age: Optional[float]            # since that grange's farm last pushed; None: never
    farm_stale: bool                     # the origin's own flag (R6.3)
    agents: int                          # how many agents it reports; who they are: Snapshot.farms
    rev: Optional[int] = None            # content version            (/v1/mesh)
    counter: Optional[int] = None        # liveness version           (/v1/mesh)
    incarnation: Optional[int] = None    # chosen at that grange's start (/v1/mesh)
    held_for: Optional[float] = None     # how long this record has been on the board here


@dataclass
class Gone:
    """A record that was held and no longer is."""
    kind: str                            # expired · left · gone (today's API cannot tell which)
    age: float                           # how long ago it went
    forget_left: Optional[float] = None  # how much longer the forget map remembers it (/v1/mesh)


@dataclass
class Tailnet:
    """The local sidecar's view of one peer device (tailscale status, ping)."""
    online: Optional[bool] = None        # headscale's hint, as the sidecar relays it
    path: Optional[str] = None           # direct · derp · None when there is no path to speak of
    endpoint: str = ""                   # the peer's UDP endpoint when direct (CurAddr)
    relay: str = ""                      # the DERP region code when relayed, or its home relay
    rtt_ms: Optional[float] = None       # round trip of the last tailscale ping
    rtt_age: Optional[float] = None      # how long ago that ping was
    handshake_age: Optional[float] = None  # since the last WireGuard handshake; None: never
    tx: int = 0                          # bytes sent to it over WireGuard
    rx: int = 0
    stale: bool = False                  # last known: the sidecar is not answering right now


@dataclass
class Gossip:
    """The local grange's own dealings with one address (/v1/mesh only)."""
    role: str                            # one of ROLES
    failed: bool = False                 # the R6.20 mark
    last_ok_age: Optional[float] = None
    last_fail_age: Optional[float] = None
    last_status: Optional[int] = None    # of the last failure: 409, 503, 403…; None is a timeout
    last_note: str = ""                  # what the refusal said, e.g. "group 'proto-judy'"
    fails: int = 0
    sent: dict = field(default_factory=dict)       # push · repair · probe -> count
    recv: dict = field(default_factory=dict)       # push · repair -> count
    bytes_out: int = 0
    bytes_in: int = 0

    @property
    def failing(self) -> bool:
        """Marked failed, or the last thing that happened was a failure."""
        if self.failed:
            return True
        if self.last_fail_age is None:
            return False
        return self.last_ok_age is None or self.last_fail_age < self.last_ok_age


@dataclass
class Peer:
    """One column: a teammate's grange, by whatever the sources know of it."""
    key: str                             # stable for the life of the column: its address, else its id
    name: str                            # the grange id; before any is known, the hostname's guess
    address: str = ""                    # tailnet ip:port, when known
    hostname: str = ""                   # the sidecar's hostname, grange-<id>
    bound: Optional[bool] = None         # an exchange has named who answers there (R6.19); None: unknown
    tailnet: Optional[Tailnet] = None    # None: no sidecar data at all
    gossip: Optional[Gossip] = None      # None: not known (it comes from /v1/mesh)
    record: Optional[Record] = None      # None: no record held (see gone)
    gone: Optional[Gone] = None
    back_kind: str = ""                  # restart · readmit · join, when that happened lately
    back_age: Optional[float] = None


@dataclass
class Sidecar:
    """This Mac's own Tailscale sidecar."""
    state: str = "unknown"               # one of SIDECAR_STATES
    since: Optional[float] = None        # how long it has been in that state
    health: tuple = ()                   # tailscaled's warnings, in its own words
    backend: str = ""                    # tailscaled's BackendState: Running, NeedsLogin…
    home_derp: str = ""                  # the home relay's region code
    home_region: Optional[int] = None
    derp_ms: Optional[float] = None      # round trip to the home relay
    path_bytes: dict = field(default_factory=dict)   # direct · derp -> bytes, both ways (usermetrics)


@dataclass
class Me:
    """This Mac's grange (GET /health, /version, and its own entry in /v1/agents)."""
    gid: str = ""
    address: str = ""                    # tailnet ip:gossip port, when known
    group: str = ""                      # GRANGE_GROUP, when known
    incarnation: int = 0
    cursor: int = 0
    rev: Optional[int] = None            # /v1/mesh
    counter: Optional[int] = None        # /v1/mesh
    farm_age: Optional[float] = None     # since this Mac's farm last pushed; None: never
    farm_stale: bool = False
    agents: int = 0
    farm_pushes: Optional[int] = None    # /v1/mesh
    last_gossip_age: Optional[float] = None   # last good exchange, either direction; None: never
    rejected: dict = field(default_factory=dict)   # proto · group · identity · overflow -> count
    sent: Optional[dict] = None          # push · repair · probe -> count (/v1/mesh)
    peers_discovered: Optional[int] = None
    peers_live: Optional[int] = None
    neighbours: Optional[int] = None     # /v1/mesh
    longpolls: Optional[int] = None      # long-polls held on /v1/agents (/v1/mesh)
    duplicate_id: bool = False
    image_stale: bool = False            # the kill-switch tag moved: every route answers 503
    stale_for: Optional[float] = None    # how long it has answered 503, as far as the viewer saw
    built: str = ""                      # the commit this image was built from
    tag_commit: str = ""                 # the commit the tag points at, when the grange says
    fresh_polls: int = 0
    fresh_unchanged: int = 0
    fresh_summary: str = ""


@dataclass
class Event:
    """Something that happened, for the panels' counts and the diagnosis's "since"."""
    age: float                           # seconds ago
    kind: str                            # join · restart · readmit · expire · leave · gone · back ·
                                         # path · refused · sidecar · mark
    peer: str                            # a grange id, or an address when none is known
    ok: bool = True                      # path: False is "fell back to DERP"
    status: Optional[int] = None
    note: str = ""


@dataclass(frozen=True)
class Agent:
    """One agent as its farm reports it (GET /v1/agents)."""
    name: str
    state: str                           # agent-farm's words are busy · waiting · idle; a grange
                                         # passes any word on as it came (R4.1)


@dataclass
class FarmChange:
    """One thing a grange's farm reported, as it reached this grange. A source works these out
    by comparing one answer of GET /v1/agents with the next (farms.py)."""
    age: float                           # seconds ago it was seen here
    grange: str                          # whose farm
    kind: str                            # started · ended · state · on · off
    name: str = ""                       # started, ended, state: the agent
    before: str = ""                     # ended, state: the state it was in
    after: str = ""                      # started, state: the state it is in
    agents: int = 0                      # on, off: how many agents came or went with the grange
    note: str = ""                       # on, off: why, when the source can tell (expired, left…)


@dataclass
class Docker:
    """What Docker says about the two containers, when the viewer may ask it."""
    available: bool = False              # the Docker socket answered
    sidecar: str = ""                    # the sidecar container's name
    sidecar_state: str = ""              # running · exited · …
    sidecar_for: Optional[float] = None  # how long it has been in that state
    grange: str = ""
    grange_state: str = ""
    grange_health: str = ""              # healthy · unhealthy · starting · ""


@dataclass
class Snapshot:
    me: Me = field(default_factory=Me)
    sidecar: Sidecar = field(default_factory=Sidecar)
    peers: list = field(default_factory=list)        # [Peer], in no particular order
    events: list = field(default_factory=list)       # [Event], oldest first, the last five minutes
    strangers: list = field(default_factory=list)    # [Event]: refusals of sources that are no peer
    farms: dict = field(default_factory=dict)        # grange id -> [Agent], for every grange on the
                                                     # board, this Mac's included
    farm_news: list = field(default_factory=list)    # [FarmChange], oldest first
    farm_watched: Optional[float] = None             # how long answers have been compared; None: not yet
    docker: Docker = field(default_factory=Docker)
    reachable: bool = True               # the grange answered on its local API
    unreachable_for: Optional[float] = None   # how long nothing has answered
    ever_reached: bool = True            # False until the first answer: "not started" reads differently
    has_mesh: bool = False               # GET /v1/mesh answered: gossip, bindings, rev and counter are real
    has_sidecar: bool = False            # the sidecar could be asked (through /v1/mesh, or Docker)
    demo: bool = False                   # every name and number is made up
    port: int = DEFAULT_PORT
    loop: str = ""                       # the demo's place in its loop, for the top line; else nothing
    note: str = ""                       # one line a source wants shown, e.g. why Docker is unavailable

    @property
    def refusing(self) -> bool:
        """The grange answers, and refuses everything: its image is out of date, so every route
        is 503 and it gossips with nobody (R5.16). It is not running in any way that matters,
        and nothing it said before is known to be true now."""
        return self.reachable and self.me.image_stale


class Source(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def snapshot(self) -> Snapshot: ...
