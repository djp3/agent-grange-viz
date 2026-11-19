# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The two panels above the Stack. Each is a function of a model.Snapshot and a size that returns
lines of rich Text, title line first; `Panel` is the widget that draws one.

  liveness   R6.4: every record's seen age as a dot on a 0–60 s axis with the expiry wall, the
             laggards named, what has no record and why, lifecycle counts for five minutes,
             the oldest farm age and who is farm_stale
  tailnet    the sidecars' paths: direct vs DERP by peers and by bytes, the home relay, round
             trips on a log axis with the slowest named, path changes, tailscaled's health
"""
from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Callable

from rich.text import Text
from textual.widget import Widget

from fmt import age
from model import EXPIRE_S, Snapshot
from theme import AXIS, CRIT, DIRECT, GOOD, INK, INK2, MUTED, RELAYED, SERIOUS, WARN


# ---- drawing helpers ----------------------------------------------------------------------------
def T(*parts) -> Text:
    out = Text()
    for p in parts:
        if isinstance(p, Text):
            out.append_text(p)
        elif isinstance(p, tuple):
            out.append(*p)
        else:
            out.append(str(p))
    return out


def spread(left: Text, right: Text, w: int) -> Text:
    """left, then right flush with the edge; left gives way first."""
    if left.cell_len + right.cell_len + 1 > w:
        left = left.copy()
        left.truncate(max(0, w - right.cell_len - 1), overflow="ellipsis")
    if left.cell_len + right.cell_len + 1 > w:
        right = right.copy()
        right.truncate(max(0, w - left.cell_len - 1), overflow="ellipsis")
    return T(left, " " * max(1, w - left.cell_len - right.cell_len), right)


def clip(t: Text, w: int) -> Text:
    t = t.copy()
    t.truncate(w, overflow="ellipsis")
    return t


def mark(ok: str) -> tuple:
    """A status mark: good · warn · serious · crit · none."""
    return {"good": ("● ", GOOD), "warn": ("▲ ", WARN), "serious": ("▲ ", SERIOUS),
            "crit": ("✗ ", CRIT), "none": ("○ ", MUTED)}[ok]


def stacked(parts, w: int) -> Text:
    """A stacked horizontal bar from [(value, color)], w cells."""
    total = sum(v for v, _ in parts)
    if not total or w <= 0:
        return Text("─" * max(0, w), style=AXIS)
    out, used = Text(), 0
    for i, (v, color) in enumerate(parts):
        cells = round(v / total * w) if i < len(parts) - 1 else w - used
        cells = max(0, min(cells, w - used))
        out.append("█" * cells, style=color)
        used += cells
    return out


def place(labels, w: int, rows: int = 2) -> list:
    """Put (x, Text) labels on up to `rows` lines without overlap; drop what does not fit."""
    lines = [Text(" " * w) for _ in range(rows)]
    ends = [0] * rows
    for x, label in sorted(labels, key=lambda p: p[0]):
        x = max(0, min(x, w - label.cell_len))
        for r in range(rows):
            if x >= ends[r]:
                line = lines[r]
                lines[r] = T(line[:x], label, line[x + label.cell_len:])
                ends[r] = x + label.cell_len + 1
                break
    return lines


def _title(title: str, subtitle: str, right: Text, w: int) -> Text:
    return spread(T((title, f"bold {INK}"), ("  " + subtitle, MUTED)), right, w)


def _label(p) -> str:
    return p.name + "?" if p.bound is False else p.name


# ---- liveness --------------------------------------------------------------------------------------
def liveness(snap: Snapshot, w: int, h: int) -> list:
    seen = {_label(p): p.record.seen_age for p in snap.peers if p.record is not None}
    worst = max(seen.values(), default=0)
    ok = "good" if worst < 20 else ("warn" if worst < 45 else "serious")
    live = snap.me.peers_live
    found = snap.me.peers_discovered if snap.me.peers_discovered is not None else len(snap.peers)
    right = T(mark(ok), (f"{live} live" if live is not None else f"{len(seen)} held", INK),
              (f" of {found} found · {len(seen)} records", MUTED))
    out = [_title("liveness", "R6.4 · seen age, expiry at 60 s", right, w)]
    h -= 1
    aw = max(10, w - 16)
    cols: dict = {}
    for name, a in seen.items():
        cols.setdefault(min(aw - 1, int(a / EXPIRE_S * aw)), []).append((name, a))

    def tone(a):
        return GOOD if a < 15 else (WARN if a < 40 else SERIOUS)
    stack = 3 if h >= 9 else 2                      # three rows of dots whenever the band is full height
    for r in range(stack - 1, -1, -1):
        line = Text()
        for x in range(aw):
            peers = sorted(cols.get(x, []), key=lambda p: p[1])
            if len(peers) > r:
                more = r == stack - 1 and len(peers) > stack
                line.append("+" if more else "●", style=tone(peers[r][1]))
            else:
                line.append(" ")
        out.append(T("  ", line))
    axis = Text()
    for x in range(aw):
        sec = x / aw * EXPIRE_S
        axis.append("┼" if x and int(sec) % 10 == 0 and int((x - 1) / aw * EXPIRE_S) % 10 == 9 else "─", style=AXIS)
    out.append(T(("  ", MUTED), axis, ("┃", CRIT), (" expire 60 s", MUTED)))
    ticks = [(int(sec / EXPIRE_S * aw), T((f"{sec}s", MUTED))) for sec in range(0, 60, 10)]
    out.append(T("  ", place(ticks, aw, 1)[0]))
    laggards = sorted(((n, a) for n, a in seen.items() if a >= 13), key=lambda p: -p[1])[:3]
    if laggards:
        names = [(int(a / EXPIRE_S * aw), T(("▲", tone(a)), (f"{n} {age(a)}", INK2))) for n, a in laggards]
        out.append(T("  ", place(names, aw, 1)[0]))
    elif seen:
        out.append(T(("  every heartbeat within 13 s: repairs every 3 s carry them", MUTED)))
    else:
        out.append(T(("  no records held: this grange is alone on the board", MUTED)))
    no_record = []
    for p in snap.peers:
        if p.record is not None:
            continue
        if p.gone is not None:
            no_record.append((_label(p), f"{p.gone.kind} {age(p.gone.age)} ago"))
        elif p.gossip is not None and p.gossip.last_fail_age is not None and p.gossip.last_ok_age is None:
            code = "timeout" if p.gossip.last_status is None else p.gossip.last_status
            no_record.append((_label(p), f"never answered ({code})"))
        else:
            no_record.append((_label(p), "no record"))
    if no_record:
        out.append(T(("no record   ", MUTED), *[x for name, why in no_record
                                                for x in ((name, INK2), (f" {why}   ", MUTED))]))
    life = Counter(e.kind for e in snap.events if e.age <= 300)
    words = {"expire": "expired", "leave": "left", "gone": "gone", "restart": "restarted",
             "readmit": "readmitted", "join": "joined"}
    shown = [k for k in words if k != "gone" or life["gone"]]
    out.append(T(("last 5 min  ", MUTED), *[x for i, k in enumerate(shown) for x in
                                            ((" · " if i else "", MUTED), (str(life[k]), INK if life[k] else MUTED),
                                             (f" {words[k]}", INK2 if life[k] else MUTED))]))
    farm = [(_label(p), p.record.farm_age, p.record.farm_stale) for p in snap.peers
            if p.record is not None and p.record.farm_age is not None]
    if farm:
        name, fa, _ = max(farm, key=lambda f: f[1])
        stale = [n for n, _, st in farm if st]
        out.append(T(("farm ages   ", MUTED), ("oldest ", MUTED), (f"{name} {age(fa)}", INK2), ("  ", ""),
                     mark("warn") if stale else ("", ""),
                     (f"farm_stale: {', '.join(stale)}" if stale else "none stale", MUTED)))
    return out


# ---- tailnet ---------------------------------------------------------------------------------------
def tailnet(snap: Snapshot, w: int, h: int) -> list:
    sc = snap.sidecar
    known = [p for p in snap.peers if p.tailnet is not None]
    online = [p for p in known if p.tailnet.online]
    direct = sum(1 for p in online if p.tailnet.path == "direct")
    derp = sum(1 for p in online if p.tailnet.path == "derp")
    idle = len(online) - direct - derp
    offline = sum(1 for p in known if p.tailnet.online is False)
    right = T(mark("good" if not derp else "none"), (f"{direct} direct", INK), (f" · {derp} via DERP", MUTED))
    out = [_title("tailnet", "the sidecars' paths · tailscale status and ping", right, w)]
    h -= 1
    if not snap.has_sidecar:
        out.append(T(("  no sidecar data", INK2)))
        out.append(T(("  " + (snap.note or "the sidecar could not be asked"), MUTED)))
        out.append(T(("  paths, round trips and handshakes come from tailscale status and ping", MUTED)))
        return out
    n = direct + derp
    pw = max(4, min(w - 30, max(n, 1) * 3))
    peers_bar = stacked([(direct, DIRECT), (derp, RELAYED)], pw) if n else Text("─" * pw, style=AXIS)
    extra = "".join(f"  · {count} {word}" for count, word in ((offline, "offline"), (idle, "idle")) if count)
    out.append(T(("peers  ", MUTED), peers_bar, ("  ", ""), ("■", DIRECT), (" direct  ", INK2),
                 ("■", RELAYED), (" DERP", INK2), (extra, MUTED)))
    pb = sc.path_bytes
    if pb:
        tb = (pb.get("direct", 0) + pb.get("derp", 0)) or 1
        out.append(T(("bytes  ", MUTED), stacked([(pb.get("direct", 0), DIRECT), (pb.get("derp", 0), RELAYED)], pw),
                     (f"  {pb.get('direct', 0) / tb:.0%} direct · {pb.get('derp', 0) / tb:.0%} relayed", INK2),
                     ("  since start", MUTED)))
    else:
        out.append(T(("bytes  ", MUTED), ("by path: not available from this sidecar", MUTED)))
    rtts = sorted((p.tailnet.rtt_ms, _label(p), p.tailnet.path) for p in online if p.tailnet.rtt_ms is not None)
    relayed = [f"{name} {v:.0f} ms" for v, name, path in rtts if path == "derp"]
    home = (f"'{sc.home_derp}'" + (f" {sc.derp_ms:.0f} ms" if sc.derp_ms is not None else "")) if sc.home_derp else "not known"
    out.append(T(("relay  ", MUTED), ("home DERP ", MUTED), (home, INK2),
                 ("   relayed now ", MUTED), (", ".join(relayed) if relayed else "nobody", INK2)))
    if h >= 11:
        out.append(Text(""))
    if rtts:
        aw = max(10, w - 9)
        lo, hi = math.log10(0.5), math.log10(300)

        def x_of(v):
            return int((math.log10(min(300, max(0.5, v))) - lo) / (hi - lo) * (aw - 1))
        line = [Text(" ") for _ in range(aw)]
        for v, _name, path in rtts:
            line[x_of(v)] = Text("│", style=DIRECT if path == "direct" else RELAYED)
        out.append(T(("rtt    ", MUTED), "  ", *line))
        out.append(T(("       ", MUTED), "  ", ("─" * aw, AXIS)))
        ticks = [(x_of(v), T((lab, MUTED))) for v, lab in ((1, "1 ms"), (3, "3"), (10, "10"), (30, "30"),
                                                           (100, "100"), (300, "300"))]
        out.append(T("         ", place(ticks, aw, 1)[0]))
        med = statistics.median(v for v, _, _ in rtts)
        slow = rtts[-1]
        out.append(T(("       min ", MUTED), (f"{rtts[0][0]:.1f}", INK2), (" · median ", MUTED),
                     (f"{med:.1f}", INK2), (" · max ", MUTED), (f"{slow[0]:.0f} ms ", INK2),
                     (f"{slow[1]}{' via DERP' if slow[2] == 'derp' else ''}", INK)))
    else:
        out.append(T(("rtt    ", MUTED), ("no round trips measured yet", MUTED)))
    moved = [e for e in snap.events if e.kind == "path" and e.age <= 300][-3:]
    changes = " · ".join(f"{e.peer} {'→ DERP' if not e.ok else '→ direct'} {age(e.age)} ago" for e in reversed(moved))
    out.append(T(("moved  ", MUTED), (changes or "no path changes in 5 min", INK2 if changes else MUTED)))
    if sc.state == "unknown":
        out.append(T(("health ", MUTED), mark("none"), ("tailscaled could not be asked", MUTED)))
    else:
        out.append(T(("health ", MUTED), mark("warn" if sc.health else "good"),
                     (", ".join(sc.health) if sc.health else "no warnings from tailscaled", INK2)))
    return out


# ---- the widget ------------------------------------------------------------------------------------
class Panel(Widget):
    """Draws one panel function, `draw(w, h) -> [Text]`, clipped to its size."""

    def __init__(self, draw: Callable[[int, int], list], **kw):
        super().__init__(**kw)
        self.draw = draw

    def render(self) -> Text:
        w, h = self.size.width, self.size.height
        if w < 10 or h < 2:
            return Text("")
        return Text("\n").join(clip(t, w) for t in self.draw(w, h)[:h])
