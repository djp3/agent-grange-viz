# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The Stack: where in the stack does a peer break?

Every discovered grange gets a column and every layer of the communication plane a row, from
headscale at the top to the teammate's farm at the bottom. A column reads top to bottom like a
trace: green marks for as long as the layer holds, one clear break where the first layer
fails, and everything beneath it greyed as the consequence. A closed lid, a private build, a
stale image and a silent farm break at different heights, so the question answered is not "who
is missing" (agent-frolic shows that) but "why, and whose move is it".

    headscale  the column heads: ★ you, ● online, ○ offline, as headscale tells your sidecar
    tailnet    ● direct, ○ relayed through DERP, ✗ no path, beside the round trip in whole ms;
               under it the seconds since the last WireGuard handshake. Your column: your own
               sidecar (★ ok, ◐ no control, ✗ no socket or logged out)
    gossip     ● last exchange good, ✗ failing, ○ never tried, beside its age or the failure's
               code; under it exchanges sent ↑ and received ↓
    record     the seen age as a bar against the 60 s expiry; under it rev and counter
    farm       the farm age as a bar against the 60 s farm_stale line; under it the agent count
    verdict    one word: where the column breaks, or that it holds

This module is the logic and the drawing, as functions of a model.Snapshot; main.py is the
window around it. Whatever a Snapshot does not know (None) is drawn as not known, greyed, and
never counts as a break: each peer's gossip cell, rev and counter where the grange has not
answered GET /v1/mesh (docs/openapi.yaml), and every row the grange fills while its image is
out of date. It then answers 503 to every request and gossips with nobody (R5.16), so nothing
it said before is drawn: nothing keeps it up, and an age that stands still reads as live.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Optional

from rich.style import Style
from rich.text import Text

from fmt import age, counts, fit, ms, ms_int, secs, short_age, size
from model import EXPIRE_S, FARM_STALE_S, Event, Peer, Snapshot
from theme import (AXIS, BREAK_BG, CRIT, DIVIDER, GHOST, GOOD, INK, INK2, MUTED, RAISED, WAITING, WARN,
                   YOU_BG)

LAYERS = ("tailnet", "gossip", "record", "farm")
MAX_LINES = {"tailnet": 2, "gossip": 2, "record": 2, "farm": 2}   # rows a layer needs at most
SOURCE = {"tailnet": "sidecar", "gossip": ":7789", "record": "", "farm": ""}   # under the row's name
ROLE_ORDER = {"you": 0, "successor": 1, "neighbour": 2, "pool": 3, "outside": 4, "": 5}
ROLE_TIP = {"successor": ("ring successor", "the next live address after yours in sorted order (R6.10); "
                                             "your changes are pushed to it, and its edge keeps the ring whole"),
            "neighbour": ("neighbor", "one of four picked by a hash of your id and its address (R6.10); "
                                      "your changes are pushed to it at once (R6.11)"),
            "pool": ("repair pool", "live but not a neighbor; every 3 s one of these gets a repair exchange, "
                                    "which carries heartbeats and anything a push missed (R6.13)"),
            "outside": ("outside the live set", "a failed mark, or no record and headscale says offline; "
                                                "only the probe tries it, every 30 s (R6.10, R6.20)")}
TONE = {"ok": GOOD, "warn": WARN, "bad": CRIT, "none": MUTED, "you": WAITING}
MARK = {"ok": "●", "warn": "◐", "bad": "✗", "none": "○", "you": "✗"}
GUTTER = 11
PANEL_H = 11                                   # the liveness and tailnet band, its rule included
PANEL_MIN = 7
DIAG_MIN = 7                                   # the diagnosis keeps its rule, title and five rows
SHORT = {"refused": "refuse", "timeout": "t/o", "offline": "off", "expired": "gone", "no farm": "farm",
         "outdated": "503", "no body": "body?", "no sock": "sock?", "log out": "logout", "stopped": "stop",
         "unknown": "?"}
SIDECAR_WORD = {"ok": ("ok",), "no-control": ("no control", "no ctrl", "ctrl?"),
                "no-socket": ("no socket", "no sock", "sock?"), "logged-out": ("logged out", "log out", "logout"),
                "unknown": ("unknown", "?")}
MESH = "GET /v1/mesh"                           # gossip per address, bindings, rev and counter come from it
NO_MESH = f"not known: {MESH} has not answered"
GRANGE_DOWN = "not known: your grange is not running"
UNKNOWN = ("fit", "not known", "unknown", "?")  # what a cell reads when nothing can vouch for it


@dataclass
class Cell:
    tone: str                                  # ok · warn · bad · none · you
    lines: list                                # str, ("fit", variants…) or ("bar", fraction, tone)
    glyph: Optional[str] = None                # a mark of its own instead of the tone's
    ghost: bool = False                        # stale or not known: drawn like the cells below a break


@dataclass
class Column:
    key: str
    name: str
    bound: Optional[bool]
    role: str
    addr: str
    peer: Optional[Peer] = None                # None for your own column
    cells: list = field(default_factory=list)
    brk: Optional[int] = None
    verdict: str = "ok"
    vtone: str = "ok"
    diag: Optional[tuple] = None               # (tone, layer, evidence, advice)
    detail: list = field(default_factory=list)
    online: Optional[bool] = True              # headscale's hint, via the sidecar; None: not known
    stale: bool = False                        # the hint and the tailnet values are last known

    def at(self, layer: str) -> Cell:
        return self.cells[LAYERS.index(layer)]

    @property
    def label(self) -> str:
        """The name, with a ? while no exchange has said who answers there."""
        return self.name + "?" if self.bound is False else self.name


@dataclass
class View:
    """What the window remembers between redraws."""
    sel_key: str = "me"
    view_start: int = 0                        # the first scrolled column beside yours
    follow: bool = True                        # bring the selection into view at the next layout
    by_address: bool = False


def agents(n: int) -> tuple:
    return ("fit", f"{n} agent" + ("" if n == 1 else "s"), f"{n} agt" + ("" if n == 1 else "s"), f"{n} ag")


def via(snap: Snapshot) -> str:
    """How the sidecar's answers reach the viewer."""
    return "relayed by the grange" if snap.has_mesh else "asked of the sidecar through Docker"


def _unknown() -> Cell:
    return Cell("none", [UNKNOWN, ""], glyph="·", ghost=True)


# ---- from a Snapshot to columns ----------------------------------------------------------------
def build(snap: Snapshot, by_address: bool = False) -> list:
    """Your column first, then the peers: successor, neighbours, repair pool, outside, by
    address within each; or all by address, which is the ring R6.10 walks."""
    cols = [_peer(snap, p) for p in snap.peers]
    if by_address:
        cols.sort(key=lambda c: (c.addr or "~", c.name))
    else:
        cols.sort(key=lambda c: (ROLE_ORDER.get(c.role, 5), c.addr or "~", c.name))
    return [_me(snap)] + cols


def _judge(col: Column) -> None:
    for i, cell in enumerate(col.cells):
        if cell.tone in ("bad", "you"):
            col.brk = i
            break
    layer = LAYERS[col.brk] if col.brk is not None else None
    c = col.at
    if layer == "tailnet":
        col.verdict, col.vtone = "offline", "bad"
    elif layer == "gossip":
        first = c("gossip").lines[0]
        word = (first[-1] if isinstance(first, tuple) else first).split()[0]   # the status code
        col.verdict = {"t/o": "timeout", "409": "refused", "403": "refused", "503": "outdated"}.get(word, word)
        col.vtone = "bad"
    elif layer == "record":                            # the cell says why in a word (expired, left, no body),
        first = c("record").lines[0]                   # or is the bar of a record held and not heard from
        col.verdict, col.vtone = (first if isinstance(first, str) else "silent"), "bad"
    elif layer == "farm":
        col.verdict, col.vtone = "no farm", "bad"
    elif c("record").tone == "warn":
        col.verdict, col.vtone = "quiet", "warn"
    elif c("farm").tone == "warn":
        col.verdict, col.vtone = "farm", "warn"
    if col.brk is not None and col.cells[col.brk].tone == "you":
        col.vtone = "you"


def _peer(snap: Snapshot, p: Peer) -> Column:
    stopped = snap.refusing
    if stopped:                                        # what the grange said before is last known at best
        p = replace(p, bound=None, gossip=None, record=None, gone=None, back_kind="", back_age=None)
    col = Column(p.key, p.name, p.bound, p.gossip.role if p.gossip else "", p.address, peer=p)
    tn, g, rec = p.tailnet, p.gossip, p.record
    col.online = tn.online if tn else None
    col.stale = bool(tn and tn.stale)

    # tailnet
    if tn is None:
        col.cells.append(Cell("none", [("fit", "no sidecar", "unknown", "?"), ""], glyph="·", ghost=True))
    elif tn.stale:                                     # your sidecar can't say: last known, greyed
        col.cells.append(Cell("none", ["stale", secs(tn.handshake_age)], glyph="·", ghost=True))
    elif tn.online is False:                           # headscale says offline: no path to it
        col.cells.append(Cell("bad", ["—", secs(tn.handshake_age)]))
    elif tn.path is None:                              # online, and no session to it yet
        col.cells.append(Cell("none", ["idle", secs(tn.handshake_age)], glyph="·"))
    else:
        col.cells.append(Cell("ok", [ms_int(tn.rtt_ms), secs(tn.handshake_age)],
                              glyph="○" if tn.path == "derp" else "●"))

    # gossip
    if g is None:
        col.cells.append(_unknown())
    else:
        updown = f"↑{sum(g.sent.values())} ↓{sum(g.recv.values())}"
        code = "t/o" if g.last_status is None else str(g.last_status)
        if g.failing:                                  # why it failed, and how long ago; never clipped
            ago = g.last_fail_age or 0.0
            col.cells.append(Cell("bad", [("fit", f"{code} {age(ago)}", f"{code} {short_age(ago)}", code), updown]))
        elif g.last_ok_age is not None:                # the mark says ok; the age is enough
            col.cells.append(Cell("ok", [age(g.last_ok_age), updown]))
        else:
            col.cells.append(Cell("none", ["untried", updown]))

    # record
    if stopped:
        col.cells.append(_unknown())
    elif rec is not None:
        tone = "ok" if rec.seen_age < 20 else "warn" if rec.seen_age < 45 else "bad"
        if p.back_age is not None and p.back_age < 60:
            second = f"↻ {age(p.back_age)}"
        else:
            second = f"r{rec.rev} c{rec.counter}" if rec.rev is not None and rec.counter is not None else ""
        col.cells.append(Cell(tone, [("bar", rec.seen_age / EXPIRE_S, tone), second]))
    elif p.gone is not None:
        ago = p.gone.age
        col.cells.append(Cell("bad", [p.gone.kind, ("fit", f"{age(ago)} ago", f"{short_age(ago)} ago", short_age(ago))]))
    else:
        col.cells.append(Cell("none" if p.bound is False else "bad",
                              ["no body", "unbound" if p.bound is False else ""]))

    # farm
    if rec is not None:
        fa = rec.farm_age
        tone = "bad" if rec.farm_stale else ("warn" if fa is not None and fa > 45 else "ok")
        col.cells.append(Cell(tone, [("bar", (fa or 0) / FARM_STALE_S, tone), agents(rec.agents)]))
    else:
        col.cells.append(_unknown() if stopped else Cell("none", ["—", ""]))

    _judge(col)
    if stopped and col.brk is None:                    # only the tailnet row is there to judge by
        col.verdict, col.vtone = "unknown", "none"
    _diagnose(snap, col, p)
    col.detail = _detail_peer(snap, p)
    return col


def _rec_state(p: Peer) -> str:
    if p.record is not None:
        return f"record seen {age(p.record.seen_age)} ago"
    if p.gone is not None:
        return f"record {p.gone.kind} {age(p.gone.age)} ago"
    return "no record held"


def _hint(p: Peer) -> str:
    online = p.tailnet.online if p.tailnet else None
    return {True: "hint says online", False: "headscale says offline", None: "tailnet not known"}[online]


def _diagnose(snap: Snapshot, col: Column, p: Peer) -> None:
    g, rec = p.gossip, p.record
    rec_state, hint = _rec_state(p), _hint(p)
    if snap.refusing:
        rec_state = "what your grange held for it is not known"
    layer = LAYERS[col.brk] if col.brk is not None else None
    if layer == "tailnet":
        col.diag = ("bad", "tailnet", f"headscale says offline · no path · {rec_state}",
                    "its sidecar is down or the laptop is off; nothing to do here")
    elif layer == "gossip" and g is not None:
        code = "t/o" if g.last_status is None else str(g.last_status)
        fails = f"{g.fails} failed, last {age(g.last_fail_age)} ago"
        if code == "t/o":
            if p.gone is not None and p.gone.kind == "left" and p.gone.age < 120:
                col.diag = ("bad", "gossip", f"{hint} · exchanges time out ({fails}) · sent a leaving body "
                                             f"{age(p.gone.age)} ago (R6.16)", "make down; it is gone on purpose")
            else:
                col.diag = ("bad", "gossip", f"{hint} · every exchange times out ({fails}) · {rec_state} "
                                             f"(R6.20, R6.4)",
                            "lid probably closed; the hint lags" if p.tailnet and p.tailnet.online else
                            "its sidecar is down or the laptop is off; nothing to do here")
        elif code == "409":
            m = re.search(r"group '([^']+)'", g.last_note)
            what = f"group '{m.group(1)}'" if m else (g.last_note or "another group or protocol")
            col.diag = ("bad", "gossip", f"tailnet fine · refused 409: {what} (R5.11) · "
                                         f"{snap.me.rejected.get('group', 0)} of theirs refused here",
                        "on a private build (R6.21)")
        elif code == "503":
            col.diag = ("bad", "gossip", f"tailnet fine · answers 503: image out of date (R5.16, R6.18) · {rec_state}",
                        "they need make update")
        elif code == "403":
            col.diag = ("bad", "gossip", "refused 403 at the identity check (R5.13)", "check its tag")
        else:
            col.diag = ("bad", "gossip", f"answers {code} ({fails})", "")
    elif layer == "record":
        if rec is not None:                            # held, and nothing has refreshed it for 45 s
            col.diag = ("bad", "record", f"{hint} · {rec_state} · expires in "
                                         f"{max(0.0, EXPIRE_S - rec.seen_age):.0f} s (R6.4)",
                        "its grange has stopped, or no longer reaches yours")
        elif p.gone is not None:
            col.diag = ("bad", "record", f"{hint} · {rec_state} (R6.4, R6.6)",
                        {"left": "make down; it is gone on purpose (R6.16)",
                         "expired": "no heartbeat reached you for 60 s"}.get(
                            p.gone.kind, f"expired or left: {MESH} says which, and has not answered"))
        else:
            col.diag = ("bad", "record", f"{hint} · {rec_state}",
                        "a body should follow within a repair (R5.10)" if snap.has_mesh else
                        f"its grange is not reaching yours, or runs another group; {MESH} says which, and has "
                        f"not answered")
    elif layer == "farm" and rec is not None:
        col.diag = ("bad", "farm", f"farm silent {age(rec.farm_age)} · farm_stale (R6.3)",
                    "grange alive, its farm is not; agents stay on boards, flagged")
    elif col.at("record").tone == "warn":
        col.diag = ("warn", "record", f"{rec_state}; expires at {int(EXPIRE_S)}s (R6.4)", "no heartbeat lately")
    elif col.at("farm").tone == "warn" and rec is not None:
        fa = rec.farm_age or 0
        col.diag = ("warn", "farm", f"farm last pushed {age(fa)} ago; " +
                    (f"stale at {int(FARM_STALE_S)}s (R6.3)" if fa < FARM_STALE_S
                     else "its grange has not flagged it stale yet (R6.3)"),
                    "a farm pushes every 30s at least (R5.2)")


def _detail_peer(snap: Snapshot, p: Peer) -> list:
    tn, g, rec = p.tailnet, p.gossip, p.record
    why = GRANGE_DOWN if snap.refusing else NO_MESH
    rows = [("address", " · ".join(x for x in (p.address, p.hostname) if x) or "not known"),
            ("binding", {True: p.name, False: "none (no good exchange yet)", None: why}[p.bound]
             + (f" · {g.role}" if g else ""))]
    if tn is None:
        rows.append(("tailnet", "no sidecar data"))
    else:
        rows += [("hint", {True: "online", False: "offline", None: "not known"}[tn.online]
                  + (" · last known" if tn.stale else "")),
                 ("path", " · ".join(x for x in (tn.path or "none", tn.endpoint or (f"via relay {tn.relay}" if tn.relay else ""),
                                                 ms(tn.rtt_ms)) if x)),
                 ("wireguard", f"handshake {age(tn.handshake_age)} ago · tx {size(tn.tx)} rx {size(tn.rx)}")]
    if g is None:
        rows.append(("gossip", why))
    else:
        fail = ("never" if g.last_fail_age is None else
                f"{age(g.last_fail_age)} ago ({'timeout' if g.last_status is None else g.last_status})")
        rows += [("mark", f"{'marked failed' if g.failed else 'no mark'} · {g.fails} fail(s)"),
                 ("last", f"ok {age(g.last_ok_age) + ' ago' if g.last_ok_age is not None else 'never'} · fail {fail}"),
                 ("sent ↑", counts(g.sent)), ("received ↓", counts(g.recv)),
                 ("gossip bytes", f"out {size(g.bytes_out)} · in {size(g.bytes_in)}")]
    if rec is not None:
        if rec.rev is not None:
            rows.append(("record", f"inc …{str(rec.incarnation)[-6:]} · rev {rec.rev} · counter {rec.counter}"))
        rows += [("ages", f"seen {age(rec.seen_age)} · farm {age(rec.farm_age)}"
                          f"{' · farm_stale' if rec.farm_stale else ''}"),
                 ("agents", f"{rec.agents} (a count; who and what is agent-farm's)")]
    elif p.gone is not None:
        more = f", remembered for {age(p.gone.forget_left)} more" if p.gone.forget_left is not None else ""
        rows.append(("record", f"{p.gone.kind} {age(p.gone.age)} ago{more}"))
    elif snap.refusing:
        rows.append(("record", GRANGE_DOWN))
    return rows


def sidecar_cell(snap: Snapshot) -> Cell:
    sc = snap.sidecar
    word = ("fit", *SIDECAR_WORD.get(sc.state, (sc.state,)))
    since = short_age(sc.since) if sc.since is not None else ""
    if sc.state == "ok":
        return Cell("ok", [word, ""], glyph="★")
    if sc.state == "no-control":
        return Cell("warn", [word, since])
    if sc.state == "unknown":
        return Cell("none", [word, ""], glyph="·", ghost=True)
    return Cell("bad", [word, since])


def _me(snap: Snapshot) -> Column:
    me, sc = snap.me, snap.sidecar
    stopped = snap.refusing
    col = Column("me", "you", True, "you", me.address)
    alone = me.peers_discovered == 0 and not snap.peers
    quiet = not alone and (me.last_gossip_age is None or me.last_gossip_age > 30)
    last = age(me.last_gossip_age) if me.last_gossip_age is not None else ("alone" if alone else "never")
    fa = me.farm_age
    if me.duplicate_id:
        record = Cell("you", ["dup id", ""])
    elif me.rev is not None and me.counter is not None:
        record = Cell("ok", [f"rev {me.rev}", f"ctr {me.counter}"])
    else:
        record = Cell("ok", [("fit", "on board", "ok"), ""])
    if fa is None:
        farm = Cell("you" if me.farm_stale else "warn", [("fit", "no farm yet", "never"), agents(me.agents)])
    else:
        stale = me.farm_stale or fa > FARM_STALE_S
        farm = Cell("you" if stale else ("warn" if fa > 30 else "ok"),
                    [("bar", fa / FARM_STALE_S, "ok" if fa <= 30 else "warn"), agents(me.agents)])
    gossip = Cell("you" if quiet else ("none" if alone and me.last_gossip_age is None else "ok"),
                  [last, f"rej {sum(me.rejected.values())}"])
    if stopped:                                        # it says nothing now, and what it said is not kept up
        gossip, record, farm = Cell("you", ["stopped", "503"]), _unknown(), _unknown()
    col.cells = [sidecar_cell(snap), gossip, record, farm]
    _judge(col)
    since = age(sc.since) if sc.since is not None else "a while"
    if col.brk is not None:
        layer = LAYERS[col.brk]
        col.verdict = {"tailnet": SIDECAR_WORD.get(sc.state, ("tailnet",))[-1],
                       "gossip": "stopped" if stopped else "fix me",
                       "record": "dup id", "farm": "no farm"}.get(layer, "fix me")
        socket_lost = (f"the grange can't reach its sidecar's LocalAPI for {since}: discovery is frozen on its last "
                       f"list and every incoming exchange fails the whois check "
                       f"({me.rejected.get('identity', 0)} refused, R5.13); outgoing gossip still works")
        col.diag = ("you", layer,
                    {"tailnet": socket_lost if sc.state == "no-socket" else
                                f"tailscaled is logged out for {since}: no tailnet at all",
                     "gossip": (f"your grange is not running: its image is out of date (built from "
                                f"{me.built or '?'}, the tag is at {me.tag_commit or '?'}), so it answers 503 to "
                                f"every request and gossips with nobody (R5.16)") if stopped else
                               "your grange has not gossiped in a while",
                     "record": "another grange runs with your GRANGE_ID (R4.8)",
                     "farm": "your farm has not pushed in a minute (R6.3)" if fa is not None else
                             "your farm has never pushed to this grange (R6.3)"}[layer],
                    {"tailnet": "docker compose logs tailscale; make up if it stays wedged"
                                if sc.state == "no-socket" else
                                "was the node removed on headscale (R9.5)? ask the admin, then make up",
                     "gossip": "make update" if stopped else "docker compose logs grange",
                     "record": "pick another GRANGE_ID", "farm": "is agent-farm running?"}[layer])
    elif sc.state == "no-control":
        col.verdict, col.vtone = "ctrl?", "warn"
        col.diag = ("warn", "tailnet", f"tailscaled can't reach the coordination server (headscale) for {since}; "
                                       f"tunnels already up keep working, online marks stop updating",
                    "nothing to do unless it lasts; nobody new can join meanwhile")
    else:
        col.verdict = "you"
    if stopped:
        col.detail = [("address", me.address or "not known"),
                      ("grange", "not running: 503 to every request"
                                 + (f" for {age(me.stale_for)}" if me.stale_for is not None else "")),
                      ("image", f"built from {me.built or '?'} · the tag is at {me.tag_commit or '?'}"),
                      ("why", me.fresh_summary or "the image is out of date"),
                      ("fix", "cd docker && make update, in agent-grange"),
                      ("home DERP", f"{sc.home_derp or '?'} · {ms(sc.derp_ms)}"),
                      ("health", ", ".join(sc.health) or "no warnings")]
        return col
    col.detail = [("address", me.address or "not known"), ("group", me.group or NO_MESH),
                  ("incarnation", str(me.incarnation)),
                  ("rev · counter", f"{me.rev} · {me.counter}" if me.rev is not None else NO_MESH),
                  ("cursor", str(me.cursor)),
                  ("farm", (f"pushed {age(fa)} ago" if fa is not None else "never pushed")
                           + (f" · {me.farm_pushes} pushes" if me.farm_pushes is not None else "")),
                  ("frolic", f"{me.longpolls} long-poll(s) held" if me.longpolls is not None else NO_MESH),
                  ("sent", counts(me.sent) if me.sent is not None else NO_MESH),
                  ("rejected", counts(me.rejected)),
                  ("duplicate id", "yes" if me.duplicate_id else "no"),
                  ("image", f"{me.built or '?'} · tag {me.tag_commit or '?'} · {me.fresh_unchanged} polls unchanged"),
                  ("home DERP", f"{sc.home_derp or '?'} · {ms(sc.derp_ms)}"),
                  ("health", ", ".join(sc.health) or "no warnings")]
    return col


def strangers(snap: Snapshot) -> list:
    """The latest refusal from each source that is no discovered address."""
    out: dict = {}
    for e in snap.strangers:
        out[e.peer] = e
    return list(out.values())


# ---- the height budget -----------------------------------------------------------------------------
def fit_height(height: int) -> tuple:
    """(panel band, lines per cell, blank line between layers): the most the window can hold
    while leaving the diagnosis at least a few rows."""
    rest = height - 3 - DIAG_MIN                      # the top line, its rule, and the keys
    for lines, gap in ((3, 1), (3, 0), (2, 1), (2, 0)):
        body = sum(min(lines, MAX_LINES.get(layer, lines)) for layer in LAYERS)
        if PANEL_H + 4 + body + (len(LAYERS) - 1) * gap <= rest:
            return PANEL_H, lines, gap
    return max(PANEL_MIN, rest - 4 - 2 * len(LAYERS)), 2, 0


def layout(cols: list, width: int, view: View) -> tuple:
    """(column width, columns shown, hidden on the left, hidden on the right). Your column
    stays pinned at the left; the others scroll beside it."""
    avail = width - GUTTER
    cw = max(9, min(13, avail // max(1, len(cols))))
    if avail // cw >= len(cols):
        view.view_start = 0
        return cw, cols, 0, 0
    room = max(1, (avail - 5) // cw - 1)             # beside the pinned column, leaving room for "n ›"
    rest = cols[1:]
    keys = [c.key for c in rest]
    if view.follow and view.sel_key in keys:         # a key moved the selection: bring it into view
        i = keys.index(view.sel_key)
        if i < view.view_start:
            view.view_start = i
        elif i >= view.view_start + room:
            view.view_start = i - room + 1
    view.follow = False
    view.view_start = max(0, min(view.view_start, len(rest) - room))
    shown = cols[:1] + rest[view.view_start:view.view_start + room]
    return cw, shown, view.view_start, max(0, len(rest) - view.view_start - room)


# ---- drawing -----------------------------------------------------------------------------------------
def _bar(fraction: float, width: int, tone: str) -> Text:
    fill = max(0, min(width, round(fraction * width)))
    t = Text()
    t.append("━" * fill, style=TONE[tone])
    t.append("─" * (width - fill), style=AXIS)
    return t


def rule(grid_width: int, width: int, note: Optional[Text] = None) -> Text:
    t = Text()
    if note is not None:
        t.append("─", style=DIVIDER)
        t.append_text(note)
        t.append("─" * max(0, GUTTER - 2 - note.cell_len), style=DIVIDER)
    else:
        t.append("─" * (GUTTER - 1), style=DIVIDER)
    t.append("┼" + "─" * max(0, min(grid_width, width - GUTTER)), style=DIVIDER)
    t.append("\n")
    return t


def _cell(c: Column, li: int, row: int, cw: int, selected: bool) -> Text:
    cell = c.cells[li]
    below = (c.brk is not None and li > c.brk) or cell.ghost
    at_break = c.brk == li
    back = (YOU_BG if cell.tone == "you" else BREAK_BG) if at_break else (RAISED if selected else None)
    on = f" on {back}" if back else ""
    plain = on.strip() or None
    t = Text()
    value = cell.lines[row] if row < len(cell.lines) else ""
    if isinstance(value, tuple) and value[0] == "fit":
        value = fit(cw - 3, *value[1:])
    if row == 0:
        mark = "·" if below else (cell.glyph or MARK[cell.tone])
        t.append(" ", style=plain)
        t.append(mark, style=(AXIS if below else TONE[cell.tone]) + on)
        t.append(" ", style=plain)
        if not isinstance(value, tuple):
            t.append(f"{str(value)[:cw - 3]:<{cw - 3}}", style=(GHOST if below else INK) + on)
            return t
    else:
        t.append("   ", style=plain)
    if isinstance(value, tuple):                          # ("bar", fraction, tone)
        b = _bar(value[1], cw - 4, "none" if below else value[2])
        if below:
            b.stylize(AXIS)
        if on:
            b.stylize(plain)
        t.append_text(b)
        t.append(" ", style=plain)
        return t
    t.append(f"{str(value)[:cw - 3]:<{cw - 3}}", style=(GHOST if below else INK2) + on)
    return t


def _tip(key: str) -> Style:
    return Style.from_meta({"tip": key})


def grid(cols: list, width: int, nlines: int, gap: int, view: View) -> Text:
    """The matrix: the headscale row of heads, the layers, the verdicts. Every cell carries a
    "tip" in its style's meta, which names what tip_for() should explain."""
    cw, shown, before, after = layout(cols, width, view)
    out = Text(no_wrap=True, overflow="crop")

    def gutter(label: str = "", style: str = MUTED, tip: Optional[str] = None) -> None:
        start = len(out)
        out.append(f" {label:<{GUTTER - 2}}", style=style)
        if tip:
            out.stylize(_tip(tip), start, len(out))
        out.append("│", style=DIVIDER)

    def bg(c: Column) -> str:
        return f" on {RAISED}" if c.key == view.sel_key else ""

    gutter("headscale", f"bold {INK2}", tip="layer:headscale")
    for c in shown:
        start = len(out)
        out.append(" ", style=bg(c).strip() or None)
        if c.key == "me":
            out.append("★", style=GOOD + bg(c))
        elif c.online is None:
            out.append("·", style=MUTED + bg(c))
        else:
            out.append("●" if c.online else "○", style=(MUTED if c.stale else GOOD if c.online else CRIT) + bg(c))
        out.append(f" {c.name[:cw - 3]:<{cw - 3}}", style=f"bold {INK}" + bg(c))
        out.stylize(_tip(f"head:{c.key}"), start, len(out))
    if after:
        hidden_bad = sum(1 for c in cols[1 + before + len(shown) - 1:] if c.vtone in ("bad", "you"))
        out.append(f" {after} ›", style=Style.parse(MUTED) + _tip(f"more:{after},{hidden_bad}"))
        if hidden_bad:
            out.append(f"✗{hidden_bad}", style=CRIT)
    out.append("\n")
    scrolled = None                               # columns off to the left, and how many of them break
    if before:
        hidden_bad = sum(1 for c in cols[1:1 + before] if c.vtone in ("bad", "you"))
        scrolled = Text(f"‹ {before}", style=Style.parse(MUTED) + _tip(f"more:{-before},{hidden_bad}"))
        if hidden_bad:
            scrolled.append(f" ✗{hidden_bad}", style=CRIT)
    out.append_text(rule(cw * len(shown), width, scrolled))

    for li, layer in enumerate(LAYERS):
        for row in range(min(nlines, MAX_LINES.get(layer, nlines))):
            if row == 0:
                gutter(layer, f"bold {INK2}", tip=f"layer:{layer}")
            elif row == 1:
                gutter(SOURCE[layer], MUTED, tip=f"layer:{layer}")
            else:
                gutter()
            for c in shown:
                t = _cell(c, li, row, cw, c.key == view.sel_key)
                t.stylize(_tip(f"{layer}:{c.key}"))
                out.append_text(t)
            out.append("\n")
        if gap and li < len(LAYERS) - 1:
            gutter()
            for c in shown:
                out.append(" " * cw, style=bg(c).strip() or None)
            out.append("\n")
    out.append_text(rule(cw * len(shown), width))
    gutter("verdict", f"bold {INK2}", tip="layer:verdict")
    for c in shown:
        word = Text()
        word.append(f" {'●' if c.vtone == 'ok' else '·' if c.vtone == 'none' else MARK[c.vtone]} ",
                    style=TONE[c.vtone] + bg(c))
        word.append(f"{fit(cw - 3, c.verdict, SHORT.get(c.verdict, c.verdict)):<{cw - 3}}",
                    style=(INK2 if c.vtone == "ok" else MUTED if c.vtone == "none" else f"bold {INK}") + bg(c))
        word.stylize(_tip(f"verdict:{c.key}"))
        out.append_text(word)
    return out


def diagnosis(cols: list, snap: Snapshot, width: int) -> Text:
    """Every column that breaks or degrades, in plain words; yours first."""
    lines = []
    head = Text()
    head.append("diagnosis", style=f"bold {INK2}")
    head.append("  where each column breaks, read from the top", style=MUTED)
    lines.append(head)
    rows = [c for c in cols if c.diag]
    rows.sort(key=lambda c: ({"you": 0, "bad": 1, "warn": 2}[c.diag[0]], LAYERS.index(c.diag[1]), c.name))
    for c in rows:
        tone, layer, evidence, advice = c.diag
        t = Text()
        t.append(f" {MARK[tone]} ", style=TONE[tone])
        t.append(f"{c.label:<8} ", style=f"bold {WAITING if tone == 'you' else INK}")
        t.append(f"{layer:<8}", style=INK2)
        t.append(evidence, style=WAITING if tone == "you" else INK2)
        if advice:
            t.append(f"  — {advice}", style=MUTED)
        lines.append(t)
    for e in strangers(snap):
        t = Text()
        t.append(f" {MARK['bad']} ", style=CRIT)
        t.append(f"{e.peer:<17}", style=f"bold {INK}")
        req = {409: "R5.11", 403: "R5.13", 413: "R5.17"}.get(e.status, "R5.12")
        t.append(f"refused {e.status} {age(e.age)} ago: {e.note.split(' (')[0]} ({req})", style=INK2)
        t.append("  — no longer a discovered address" if e.status == 409 else
                 "  — not a discovered address; the ACL should have stopped it", style=MUTED)
        lines.append(t)
    if not rows and not snap.strangers:
        t = Text(" ● ", style=GOOD)
        t.append("every column reads green down to the farm", style=INK2)
        lines.append(t)
    if not snap.has_mesh and not snap.demo:
        t = Text(" · ", style=MUTED)
        t.append(f"the gossip row, rev and counter come from {MESH}, which this grange has not answered: "
                 f"they read as not known until it does; --demo shows them on made-up data", style=MUTED)
        lines.append(t)
    if snap.note:
        t = Text(" · ", style=MUTED)
        t.append(snap.note, style=MUTED)
        lines.append(t)
    for t in lines:
        t.truncate(width, overflow="ellipsis")
    return Text("\n").join(lines)


def unreachable(snap: Snapshot, width: int) -> Text:
    """The diagnosis while the local API is gone: what the viewer, and Docker, can tell."""
    d = snap.docker
    gone = age(snap.unreachable_for) if snap.unreachable_for is not None else "a while"
    where = f"127.0.0.1:{snap.port}"
    rows = [("✗", CRIT, where, f"connection refused for {gone}" if snap.ever_reached else
             "nothing has answered yet", INK2)]
    if d.available and d.sidecar:
        side = f"{d.sidecar} {d.sidecar_state or 'state unknown'}" + (
            f" {age(d.sidecar_for)} ago" if d.sidecar_for is not None and d.sidecar_state != "running" else "")
        grange = f" · {_grange_says(d)}" if d.grange else ""
        tail = " · with no network: it lives in the sidecar's" if d.sidecar_state != "running" and d.grange_state == "running" else ""
        rows.append(("", MUTED, "docker", side + grange + tail, INK2))
    elif d.available:
        rows.append(("", MUTED, "docker", "no agent-grange containers found: the grange is not started", INK2))
    else:
        rows.append(("", MUTED, "docker", snap.note or "Docker was not asked, so only the port is known", INK2))
    rows.append(("", MUTED, "so", "farm and frolic can't reach it either, and your teammates' boards drop you "
                                  "within 60 s (R6.4)", INK2))
    rows.append(("▲", WAITING, "fix", "nothing restarts by itself (R8.9): cd docker && make up", WAITING))
    return _facts("the grange can't be reached", rows, width)


def not_running(snap: Snapshot, width: int) -> Text:
    """The diagnosis while the grange refuses every request because its image is out of date:
    what it answers, what follows, what is not shown and why, and the fix. Five rows at most,
    so the fix is in view in the smallest window."""
    me, d = snap.me, snap.docker
    since = f" for {age(me.stale_for)}" if me.stale_for is not None else ""
    rows = [("✗", CRIT, f"127.0.0.1:{snap.port}",
             f"answers 503 to every request{since} · built {me.built or '?'}, tag at {me.tag_commit or '?'} · "
             f"{me.fresh_summary or 'image out of date'}", INK2)]
    if d.available and d.grange:
        rows.append(("", MUTED, "docker", _grange_says(d) + (f" · {d.sidecar} {d.sidecar_state}" if d.sidecar else ""),
                     INK2))
    rows.append(("", MUTED, "so", "it gossips with nobody (R5.16): farm and frolic get 503 too, and your "
                                  "teammates' boards drop you within 60 s (R6.4)", INK2))
    rows.append(("", MUTED, "not shown", "what it held before (gossip, records, farms): nothing keeps it up"
                 + (" · the headscale and tailnet rows are live, from your sidecar" if snap.has_sidecar else ""), INK2))
    rows.append(("▲", WAITING, "fix", "cd docker && make update, in agent-grange", WAITING))
    return _facts("your grange is not running: its image is out of date", rows, width)


def _grange_says(d) -> str:
    """The grange's container as Docker tells it: its state, and its health when it has one."""
    return f"{d.grange} {d.grange_state}" + (f", {d.grange_health}" if d.grange_health else "")


def _facts(subtitle: str, rows: list, width: int) -> Text:
    """A diagnosis that is about the grange itself: (mark, its style, key, text, its style) rows."""
    head = Text()
    head.append("diagnosis", style=f"bold {INK2}")
    head.append("  " + subtitle, style=MUTED)
    lines = [head]
    for mark, mstyle, key, text, tstyle in rows:
        line = Text(f" {mark or ' '} ", style=mstyle)
        line.append(f"{key:<16}", style=f"bold {INK}" if mark else MUTED)
        line.append(text, style=tstyle)
        lines.append(line)
    for line in lines:                                # a row is cut, never wrapped: the fix stays in view
        line.truncate(width, overflow="ellipsis")
    return Text("\n").join(lines)


def detail(c: Column) -> Text:
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append(c.label, style=f"bold {INK}")
    t.append("  " + " · ".join(x for x in (c.role, c.addr) if x) + "\n", style=MUTED)
    for k, v in c.detail:
        t.append(f"{k:<13}", style=MUTED)
        t.append(f"{v}\n", style=INK2)
    return t


# ---- what a hover explains ---------------------------------------------------------------------------
LEGEND = {   # what the marks in each row mean, for the end of every tip in that row
    "head": (("★ ", GOOD), ("you  ", INK2), ("● ", GOOD), ("online  ", INK2), ("○ ", CRIT), ("offline  ", INK2),
             ("● ", MUTED), ("last known  ", INK2), ("· ", MUTED), ("not known", INK2)),
    "tailnet": (("● ", GOOD), ("direct  ", INK2), ("○ ", GOOD), ("via DERP  ", INK2), ("✗ ", CRIT), ("no path  ", INK2),
                ("· ", MUTED), ("stale or not known  ", INK2), ("you: ", MUTED), ("★ ", GOOD), ("ok ", INK2),
                ("◐ ", WARN), ("no control ", INK2), ("✗ ", CRIT), ("no socket", INK2)),
    "gossip": (("● ", GOOD), ("last exchange good  ", INK2), ("✗ ", CRIT), ("failing  ", INK2), ("○ ", MUTED),
               ("never tried  ", INK2), ("· ", MUTED), ("not known", INK2)),
    "record": (("● ", GOOD), ("seen < 20 s  ", INK2), ("◐ ", WARN), ("20–45 s  ", INK2), ("✗ ", CRIT),
               ("> 45 s, gone, or none  ", INK2), ("○ ", MUTED), ("not tied to an id yet  ", INK2), ("· ", MUTED),
               ("not known", INK2)),
    "farm": (("● ", GOOD), ("pushed < 45 s ago  ", INK2), ("◐ ", WARN), ("45–60 s  ", INK2), ("✗ ", CRIT),
             ("farm_stale  ", INK2), ("○ ", MUTED), ("no record  ", INK2), ("· ", MUTED), ("not known", INK2)),
    "verdict": (("● ", GOOD), ("ok  ", INK2), ("◐ ", WARN), ("degraded  ", INK2), ("✗ ", CRIT), ("breaks  ", INK2),
                ("· ", MUTED), ("not known  ", INK2), ("yellow: ", MUTED), ("yours to fix", WAITING)),
}
SOURCE_TIP = {   # where a cell's data comes from, when its tip does not already say
    ("head", "me"): "Source: your grange's GET /health and /version; the top line shows the same.",
    ("gossip", "peer"): f"Source: the grange's gossip core, per address: last exchange, status, sent and received "
                        f"counts, its place in the live set ({MESH}).",
    ("gossip", "me"): f"Source: GET /health (last_gossip_age_s, rejected); sent counts from {MESH}.",
    ("record", "peer"): f"Source: the grange's Store: seen_age_s from GET /v1/agents; rev, counter and incarnation "
                        f"from {MESH}.",
    ("record", "me"): f"Source: GET /health (incarnation, duplicate_id); rev and counter from {MESH}.",
    ("farm", "peer"): "Source: GET /v1/agents: farm_age_s, farm_stale and the agents list, as their grange relays them.",
    ("farm", "me"): "Source: your own entry in GET /v1/agents (farm_age_s, agents), from your farm's pushes.",
}
NO_MESH_TIP = (f"Not known: this comes from {MESH}, which this grange has not answered. The viewer asks again "
               f"every minute and fills it in when it does. Run with --demo to see this row on made-up data.")
STOPPED_TIP = ("Not known: your grange is not running. Its image is out of date, so it answers 503 to every "
               "request and gossips with nobody (R5.16). What it held before is not shown, because nothing keeps "
               "it up. cd docker && make update, in agent-grange, starts it again.")


def tip_for(snap: Snapshot, cols: list, key: str) -> Optional[Text]:
    """The tooltip for a hover target: layer:<name>, head|tailnet|gossip|record|farm|verdict:<column>,
    more:<n>,<bad>."""
    kind, _, which = key.partition(":")
    col = next((c for c in cols if c.key == which), None)
    p = col.peer if col is not None else None
    me, sc = snap.me, snap.sidecar
    stopped = snap.refusing
    t = Text()

    def line(*parts) -> None:
        if t.cell_len:
            t.append("\n")
        for text, style in parts:
            t.append(text, style=style)

    if key == "layer:headscale":
        line(("headscale", f"bold {INK}"), (" · who headscale says is on the tailnet", INK2))
        line(*LEGEND["head"])
        line((f"Source: Peer.Online in your sidecar's tailscale status, {via(snap)}. The grange reads it at each "
              "discovery refresh (every 30 s). Headscale is slow to notice a closed lid.", MUTED))
        line(("The name is the grange id from the last good exchange with that address (R6.19); until there is "
              "one, the sidecar's hostname grange-<id>.", MUTED))
    elif key == "layer:tailnet":
        line(("tailnet", f"bold {INK}"), (" · the WireGuard path to each teammate, from your sidecar", INK2))
        line(*LEGEND["tailnet"])
        line(("first line   round trip of the last tailscale ping, in whole ms", INK2))
        line(("second line  seconds since the last WireGuard handshake (>999s past that; the exact number is in "
              "the cell's tip): a link carrying traffic re-keys about every 120 s, so a busy one stays under "
              "~180 s", INK2))
        line(("Your column is your own sidecar.", MUTED))
    elif key == "layer:gossip":
        line(("gossip", f"bold {INK}"), (" · this grange's exchanges with each address, port 7789", INK2))
        line(*LEGEND["gossip"])
        line(("first line   ● how long since the last good exchange; ✗ why the last one failed and how long ago "
              "(t/o timed out, 409 another group or protocol, 503 image out of date, 403 not a tagged grange)", INK2))
        line(("second line  exchanges sent ↑ and received ↓, pushes, repairs and probes together", INK2))
        line(("Each address's place in the protocol (ring successor, neighbor, repair pool, outside) is in its "
              "cell's tip.", MUTED))
        if stopped:
            line((STOPPED_TIP, MUTED))
        elif not snap.has_mesh:
            line((NO_MESH_TIP, MUTED))
    elif key == "layer:record":
        line(("record", f"bold {INK}"), (" · what your grange holds for each teammate's id", INK2))
        line(*LEGEND["record"])
        line(("first line   the bar: seen age, time since a heartbeat or change for it last reached you, filling "
              "toward the 60 s expiry (R6.4)", INK2))
        line(("second line  r rev, the content version; c counter, the liveness version (heartbeats); ↻ when it "
              "restarted, rejoined or joined in the last minute", INK2))
        line(("No bar: expired or left (and how long ago), or no record tied to the address yet.", MUTED))
        if stopped:
            line((STOPPED_TIP, MUTED))
    elif key == "layer:farm":
        line(("farm", f"bold {INK}"), (" · each teammate's agent-farm, as their grange relays it", INK2))
        line(*LEGEND["farm"])
        line(("first line   the bar: farm age, time since that farm last pushed its list, filling toward the "
              "60 s farm_stale line (R6.3)", INK2))
        line(("second line  how many agents that farm reports: a count only; who and what is agent-farm's and "
              "agent-frolic's business", INK2))
        line(("A farm pushes on every change and at least every 30 s (R5.2).", MUTED))
        if stopped:
            line((STOPPED_TIP, MUTED))
    elif key == "layer:verdict":
        line(("verdict", f"bold {INK}"), (" · one word per column: where it breaks, or that it holds", INK2))
        line(("Worked out by this app, not reported by anything: read each column from the top; the first layer "
              "that fails names the verdict, and the cells below it grey out as its consequence.", INK2))
        line(("The diagnosis below says the same in plain words, with the evidence.", MUTED))
        line(*LEGEND["verdict"])
    elif kind == "more":
        n, bad = (int(x) for x in which.split(","))
        line((f"{abs(n)} more column(s) off to the {'left' if n < 0 else 'right'}", INK2),
             (f" · {bad} of them break" if bad else "", INK2))
        line(("The wheel, the trackpad and shift+← → scroll; ← → moves the selection.", MUTED))
    elif kind == "verdict" and col is not None:
        line((col.label, f"bold {INK}"), (" · verdict ", INK2), (col.verdict, f"bold {INK}"))
        if col.diag is not None:
            tone, layer, evidence, advice = col.diag
            line((f"{'breaks at' if tone in ('bad', 'you') else 'degraded at'} {layer}: ", INK2), (evidence, INK))
            if advice:
                line(("— " + advice, MUTED))
        elif col.vtone == "none":
            line(("only the tailnet row is there to judge by: your grange is not running, so nothing else about "
                  "this column is known", INK2))
        else:
            line(("every layer holds", INK2))
        line(("Source: worked out by this app from the rows above.", MUTED))
        line(*LEGEND["verdict"])
    elif kind == "head" and which == "me":
        line(("★ you", f"bold {INK}"), (f" · {me.address or 'address not known'} · grange-{me.gid}", INK2))
        line(("This Mac's grange; its tailnet address is the sidecar's.", INK2))
        if me.longpolls is not None:
            line(("frolic: ", MUTED), (f"{me.longpolls} long-poll(s) held open on /v1/agents", INK2),
                 (f" · cursor {me.cursor}", MUTED))
    elif kind == "head" and p is not None:
        tn = p.tailnet
        line((col.name, f"bold {INK}"), (" · " + " · ".join(x for x in (p.address, p.hostname) if x), INK2))
        if tn is None:
            line(("· ", MUTED), ("not known", INK), (" — no sidecar data, so nobody has said whether it is online", INK2))
        elif tn.stale:
            line(("● ", MUTED), ("last known: ", INK2), ("online" if tn.online else "offline", INK),
                 (" — your sidecar isn't answering, so this is what it said last", INK2))
        elif tn.online:
            line(("● ", GOOD), ("online", INK), (" — headscale says the device is up", INK2))
        else:
            line(("○ ", CRIT), ("offline", INK), (" — headscale says the device is gone (laptop off, sidecar "
                                                  "stopped, or removed)", INK2))
        if p.bound is True:
            line(("bound", INK2), (f": its exchanges say it is {p.name} (R6.19)", MUTED))
        elif p.bound is False:
            line(("not bound yet", INK2), (": no good exchange has named it; the name is its sidecar's hostname", MUTED))
        elif stopped:
            line(("named by its sidecar's hostname", INK2),
                 ("; your grange is not running, so it cannot say who answers there", MUTED))
        else:
            line(("named by its sidecar's hostname and its record's id", INK2),
                 (f"; whether an exchange has named who answers there is in {MESH}, which this grange has not "
                  f"answered", MUTED))
        line((f"Source: tailscale status (Peer.Online, HostName), {via(snap)}; read every 30 s by the grange's "
              "discovery.", MUTED))
    elif kind == "tailnet" and which == "me":
        since = age(sc.since) if sc.since is not None else "a while"
        text = {"ok": (("★ ", GOOD), ("ok", INK), (" — your sidecar answers and reports no health warnings", INK2)),
                "no-control": (("◐ ", WARN), ("no control", INK), (f" for {since} — tailscaled can't reach headscale; "
                                                                   "tunnels already up keep working", INK2)),
                "no-socket": (("✗ ", CRIT), ("no socket", INK), (f" for {since} — the grange can't reach tailscaled's "
                                                                 "LocalAPI: no discovery, no whois", INK2)),
                "logged-out": (("✗ ", CRIT), ("logged out", INK), (f" for {since} — no tailnet at all", INK2)),
                "unknown": (("· ", MUTED), ("not known", INK), (" — the sidecar could not be asked" +
                                                                (f": {snap.note}" if snap.note else ""), INK2))}
        line(("your sidecar", f"bold {INK}"),
             (f" · home DERP '{sc.home_derp}'" + (f" (region {sc.home_region})" if sc.home_region is not None else "")
              if sc.home_derp else "", INK2))
        line(*text.get(sc.state, (("", ""), (sc.state, INK))))
        if sc.backend:
            line(("backend: ", MUTED), (sc.backend, INK2))
        if sc.health:
            line(("health: ", MUTED), (", ".join(sc.health), INK2))
        line((f"Source: the sidecar's status and health, {via(snap)}. That the grange itself lost the socket "
              f"(no socket) only the grange can say: {MESH}.", MUTED))
    elif kind == "tailnet" and p is not None:
        tn = p.tailnet
        line((col.name, f"bold {INK}"), (f" · tailnet {p.address.rsplit(':', 1)[0]}" if p.address else "", INK2))
        if tn is None:
            line(("· ", MUTED), ("not known", INK), (" — no sidecar data" + (f": {snap.note}" if snap.note else ""), INK2))
        else:
            if tn.stale:
                line(("· ", MUTED), ("stale", INK), (" — your sidecar isn't answering; these are the last values "
                                                     "it gave", INK2))
            elif tn.online is False:
                line(("✗ ", CRIT), ("no path", INK), (" — headscale says the device is offline", INK2))
            elif tn.path == "derp":
                line(("○ ", GOOD), ("relayed", INK), (f" through DERP '{tn.relay or sc.home_derp}': no direct UDP path; "
                                                      "it works, slower, and the relay carries every byte", INK2))
            elif tn.path == "direct":
                line(("● ", GOOD), ("direct", INK), (f": UDP straight to its sidecar at {tn.endpoint}", INK2))
            else:
                line(("· ", MUTED), ("idle", INK), (" — online, and no WireGuard session to it yet", INK2))
            if tn.rtt_ms is not None:
                line((ms_int(tn.rtt_ms), f"bold {INK}"), (" round trip of the last tailscale ping to its sidecar", INK2),
                     (f", {age(tn.rtt_age)} ago" if tn.rtt_age is not None else "", MUTED))
            if tn.handshake_age is not None:
                hs = tn.handshake_age
                line((f"{int(hs)} s", f"bold {INK}"), (f" ({age(hs)})" if hs >= 60 else "", MUTED),
                     (" since the last WireGuard handshake; a link carrying traffic re-keys about every 120 s, so "
                      "more than ~180 s means it has been quiet or can't re-key", INK2))
            else:
                line(("no WireGuard handshake yet", INK2))
            line((f"tx {size(tn.tx)} · rx {size(tn.rx)} over WireGuard so far", MUTED))
        line((f"Source: tailscale status (CurAddr, Relay, LastHandshake, TxBytes, RxBytes) and ping, {via(snap)}.",
              MUTED))
    elif stopped and kind in ("gossip", "record", "farm") and col is not None:
        line((col.label, f"bold {INK}"), (f" · {kind}", INK2))
        if which == "me" and kind == "gossip":
            since = f" for {age(me.stale_for)}" if me.stale_for is not None else ""
            line(("✗ ", WAITING), ("stopped", INK), (f"{since}: built from {me.built or '?'}, the tag is at "
                                                      f"{me.tag_commit or '?'}", INK2))
        line((STOPPED_TIP, INK2))
    elif kind == "gossip" and which == "me":
        line(("you", f"bold {INK}"), (f" · gossip on {me.address or 'the tailnet'}", INK2))
        line(("last good exchange, either direction: ", INK2),
             (f"{age(me.last_gossip_age)} ago" if me.last_gossip_age is not None else "never", INK))
        if me.sent is not None:
            line(("sent: ", INK2), (counts(me.sent), INK))
        line(("refused here: ", INK2), (counts(me.rejected), INK), ("  (/health rejected)", MUTED))
        found = f" of {me.peers_discovered} found" if me.peers_discovered is not None else ""
        if me.peers_live is not None:
            line(((f"{me.neighbours} neighbors · " if me.neighbours is not None else "")
                  + f"{me.peers_live} live{found}", MUTED))
    elif kind == "gossip" and p is not None:
        g = p.gossip
        line((col.name, f"bold {INK}"), (f" · {p.address}" if p.address else "", INK2))
        if g is None:
            line((NO_MESH_TIP, INK2))
        else:
            role, why = ROLE_TIP.get(g.role, (g.role, ""))
            line((role, f"bold {INK}"), (f" — {why}", INK2))
            if g.last_ok_age is not None:
                line(("last good  ", MUTED), (f"{age(g.last_ok_age)} ago", INK))
            if g.last_fail_age is not None:
                code = "timed out" if g.last_status is None else str(g.last_status)
                line(("last fail  ", MUTED), (f"{age(g.last_fail_age)} ago", INK),
                     (f" ({code}{': ' + g.last_note if g.last_note else ''}) · {g.fails} failure(s) in all", MUTED))
            if g.failed:
                line(("marked failed", f"bold {INK}"), (": out of the live set until an exchange succeeds or "
                                                        "discovery says it is online (R6.20)", INK2))
            line(("sent ↑     ", MUTED), (counts(g.sent), INK))
            line(("received ↓ ", MUTED), (counts(g.recv), INK))
            line(("bytes      ", MUTED), (f"out {size(g.bytes_out)} · in {size(g.bytes_in)}", INK))
    elif kind == "record" and which == "me":
        line(("your record", f"bold {INK}"), (" · what your grange publishes about itself", INK2))
        if me.rev is not None:
            line(("rev ", MUTED), (str(me.rev), INK), ("  content version: moves when your farm's list changes", INK2))
            line(("counter ", MUTED), (str(me.counter), INK), ("  liveness version: moves every 10 s heartbeat too", INK2))
        else:
            line(("on the board", INK2),
                 (f"; rev and counter come from {MESH}, which this grange has not answered", MUTED))
        line(("incarnation ", MUTED), (str(me.incarnation), INK), ("  chosen at start; on the top line", INK2))
        if me.duplicate_id:
            line(("✗ ", WAITING), ("another grange runs with your GRANGE_ID (R4.8)", INK))
    elif kind == "record" and p is not None:
        rec = p.record
        line((col.name, f"bold {INK}"), (f" · the record held for id {p.name}" if p.bound is not False else
                                         " · no id bound to this address yet", INK2))
        if rec is not None:
            line((f"seen {rec.seen_age:.0f} s ago", f"bold {INK}"),
                 (f" — expires at 60 s, in {max(0, EXPIRE_S - rec.seen_age):.0f} s unless a heartbeat arrives (R6.4)", INK2))
            if rec.rev is not None:
                line(("rev ", MUTED), (str(rec.rev), INK), ("  counter ", MUTED), (str(rec.counter), INK),
                     ("  incarnation ", MUTED), (str(rec.incarnation), INK))
            else:
                line((f"rev, counter and incarnation come from {MESH}, which this grange has not answered", MUTED))
            if rec.held_for is not None:
                line((f"on the board here for {age(rec.held_for)}", MUTED))
            if p.back_age is not None and p.back_age < 60:
                line((f"↻ {p.back_kind or 'back'} {age(p.back_age)} ago", INK2))
        elif p.gone is not None:
            more = (f" — remembered for {age(p.gone.forget_left)} more, so a lagging copy can't bring it back (R6.6)"
                    if p.gone.forget_left is not None else " — remembered for 10 minutes (R6.6)")
            line((f"{p.gone.kind} {age(p.gone.age)} ago", f"bold {INK}"), (more, INK2))
        elif p.bound is False:
            line(("Records are kept by id; until an exchange names who answers at this address, its record (if any) "
                  "can't be tied to this column.", INK2))
        else:
            line(("no record held for this id", INK2))
    elif kind == "farm" and which == "me":
        line(("your farm", f"bold {INK}"), (" · agent-farm on this Mac, pushing to your grange", INK2))
        if me.farm_age is None:
            line(("never pushed", f"bold {INK}"), (" since this grange started; farm_stale after 60 s (R6.3)", INK2))
        else:
            line((f"last push {me.farm_age:.0f} s ago", f"bold {INK}"),
                 ((f" · {me.farm_pushes} pushes so far" if me.farm_pushes is not None else "")
                  + "; farm_stale at 60 s (R6.3)", INK2))
        line((f"{me.agents} agent(s) reported", INK2))
    elif kind == "farm" and p is not None:
        rec = p.record
        line((col.name, f"bold {INK}"), (" · their agent-farm, as their grange relays it", INK2))
        if rec is None:
            line(("no record held, so nothing to say about their farm", INK2))
        else:
            if rec.farm_age is None:
                line(("their farm has never pushed since their grange started", INK2))
            else:
                line((f"last push {rec.farm_age:.0f} s ago", f"bold {INK}"),
                     ("  (relayed: their grange's age plus how long the record has travelled)", MUTED))
            if rec.farm_stale:
                line(("✗ ", CRIT), ("farm_stale", INK), (": their grange flagged the farm as quiet; its last agents "
                                                         "stay on boards, flagged (R6.3)", INK2))
            line((f"{rec.agents} agent(s) reported", INK2))
    else:
        return None

    if kind in ("head", "tailnet", "gossip", "record", "farm"):
        line(*LEGEND[kind])
        src = SOURCE_TIP.get((kind, "me" if which == "me" else "peer"))
        if src and not (stopped and kind in ("gossip", "record", "farm")):
            line((src, MUTED))
        if col is not None and col.brk is not None and kind in LAYERS and LAYERS.index(kind) > col.brk:
            line(("Greyed: this column already breaks at ", MUTED), (LAYERS[col.brk], INK2),
                 ("; this cell is its consequence, not a separate problem.", MUTED))
    return t


def recent(snap: Snapshot, kinds: tuple, within: float = 300.0) -> list:
    """Events of some kinds in the last while, oldest first."""
    return [e for e in snap.events if e.kind in kinds and e.age <= within]


__all__ = ["Cell", "Column", "View", "build", "grid", "diagnosis", "unreachable", "not_running", "detail", "tip_for",
           "fit_height", "layout", "strangers", "recent", "Event"]
