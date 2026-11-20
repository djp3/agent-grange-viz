# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
agent-grange-viz: watch agent-grange's communication plane, and see where it breaks.

One window, read top to bottom:

- The top line: this Mac's grange (id, tailnet address, group, incarnation, cursor), how many
  granges were found and are live, and the clock.
- Two panels. Left, liveness: every record's seen age as a dot on a 0–60 s axis against the
  expiry wall. Right, tailnet: direct vs DERP, round trips, path changes, tailscaled's health.
- The Stack (stack.py): a column for every grange, a row for every layer, a verdict under each
  column. Hovering any cell says what it shows, what its mark means and where it comes from.
- The diagnosis: every column that breaks or degrades, in plain words; enter opens the
  selected column's full values beside it.

It reads and never writes: GET /health, /v1/agents and /version on the grange's local port,
and, when allowed, `tailscale status` and `tailscale ping` in the sidecar through the Docker
socket. It shows no agent's name or state and no pull request: those are agent-farm's and
agent-frolic's.

    python main.py                 the grange on 127.0.0.1:7788 (settings: grange_port)
    python main.py --port 17981    another port
    python main.py --demo          a made-up team on a four-minute loop, every row filled

Keys: ← → pick a column, shift+← → (or the wheel) scroll the columns, enter open / close the
detail, t order by role or by address, q quit. In the demo also: space pause, e next scripted
moment, + / - speed.

The terminal's title (the Mac app's title bar) reads 🐓 agent-grange-viz and the version, then
one 📡 for each teammate's grange that is bound and still on the board, up to ten.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

import stack
from apppaths import APP_NAME, LOG_NAME, app_version, log_dir
from fmt import age
from model import DEFAULT_PORT, Snapshot
from panels import Panel, liveness, tailnet
from theme import CRIT, DIVIDER, INK, INK2, MUTED, PLANE, RAISED, SURFACE, WAITING


log = logging.getLogger("agent_grange_viz")
# The terminal's title: the Mac app shows it in its title bar (and so in the Dock, Mission
# Control and the window switcher), iTerm2 in its tab. After the rooster, the name and the
# version, a radar dish for each teammate's grange that is bound: how many this one reaches.
VERSION = app_version()
WINDOW_TITLE = f"🐓 {APP_NAME} v{VERSION}" if VERSION else f"🐓 {APP_NAME}"
DISH = "📡"
MAX_DISHES = 10                          # a team past this many reads as this many


def title_sequence(title: str) -> str:
    """OSC 0 sets the terminal's window title; Textual never sets one itself. An empty
    title hands the choice back to the terminal.

    The terminal ends the sequence at the first BEL or ESC, and prints what follows into the
    screen. So no control character goes inside it, whatever the version file or the
    environment held: a line break is written as a space, and the rest are left out."""
    words = " ".join(title.splitlines())
    clean = "".join(ch for ch in words if not (ch < " " or "\x7f" <= ch <= "\x9f"))
    return f"\x1b]0;{clean}\x07"


def clear_title_left_behind(app, out) -> None:
    """After the app has ended: if it did not get to clear its title (it raised, and Textual
    ends such an app without going through exit), clear it now, so a terminal's tab does not
    go on showing dishes for a viewer that is gone. The Mac app takes its own title back
    whenever the viewer ends; this is for a viewer run in a terminal."""
    if not app.title_shown:
        return
    try:
        out.write(title_sequence(""))
        out.flush()
    except Exception:                    # no terminal there to tell
        pass
    app.title_shown = ""


def window_title(bound: int) -> str:
    return f"{WINDOW_TITLE} {DISH * min(bound, MAX_DISHES)}" if bound > 0 else WINDOW_TITLE


def bound_nodes(snap: Snapshot) -> int:
    """How many teammates' granges this one reaches: those that are bound (an exchange has
    named who answers at their address, R6.19) and whose record is still held. A binding is
    never cleared, so by itself it outlives the grange it named; the record is what expires
    when a teammate goes away. Only GET /v1/mesh says who is bound. Where a grange has not answered it,
    a record held is all there is to go by, and is enough. While nothing answers on
    the local port, nothing is known to be bound; and while this grange's image is out of
    date it answers 503 to everything and has stopped gossiping (R5.16), so it reaches nobody
    whatever the last picture said.

    Granges are counted, not columns: a teammate is one grange however many devices carry
    its name or addresses are bound to its id (a sidecar registered again beside its old
    self), so the count is of distinct names."""
    if not snap.reachable or snap.me.image_stale:
        return 0
    if snap.has_mesh:
        return len({p.name for p in snap.peers if p.bound and p.record is not None})
    return len({p.name for p in snap.peers if p.record is not None})


class GridView(Static):
    """The grid. Whatever the pointer rests on says what it is, as a tooltip; the wheel and
    the trackpad scroll the columns."""

    def __init__(self, tip_for, on_scroll, **kw):
        super().__init__(**kw)
        self.tip_for = tip_for
        self.on_scroll = on_scroll
        self._tip: Optional[str] = None

    def on_mouse_move(self, event: events.MouseMove) -> None:
        tip = event.style.meta.get("tip")
        if tip != self._tip:
            self._tip = tip
            self.tooltip = self.tip_for(tip) if tip else None

    def on_leave(self, event: events.Leave) -> None:
        self._tip = None
        self.tooltip = None

    def _scroll(self, event, d: int) -> None:
        event.stop()
        self.on_scroll(d)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._scroll(event, 1)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._scroll(event, -1)

    def on_mouse_scroll_right(self, event: events.MouseScrollRight) -> None:
        self._scroll(event, 1)

    def on_mouse_scroll_left(self, event: events.MouseScrollLeft) -> None:
        self._scroll(event, -1)


class GrangeViz(App):
    TITLE = "agent-grange-viz"
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #head {{ height: 2; padding: 0 1; border-bottom: solid {DIVIDER}; }}
    #panels {{ height: {stack.PANEL_H}; border-bottom: double {DIVIDER}; }}
    #liveness {{ width: 1fr; padding: 0 1; border-right: solid {DIVIDER}; }}
    #tailnet {{ width: 1fr; padding: 0 1; }}
    #grid {{ height: auto; }}
    Tooltip {{ max-width: 64; background: {RAISED}; color: {INK2}; border: round {DIVIDER}; padding: 0 1; }}
    #bottom {{ height: 1fr; border-top: solid {DIVIDER}; }}
    #diag {{ width: 1fr; padding: 0 1; }}
    #detail {{ width: 58; padding: 0 1; background: {SURFACE}; display: none; }}
    #foot {{ height: 1; padding: 0 1; color: {MUTED}; }}
    """
    BINDINGS = [("left", "step(-1)", "column"), ("right", "step(1)", "column"),
                ("shift+left", "scroll_cols(-1)", "scroll"), ("shift+right", "scroll_cols(1)", "scroll"),
                ("enter", "detail", "detail"), ("t", "order", "order"), ("space", "pause", "pause"),
                ("e", "next", "next moment"), ("plus,equals_sign", "speed(4)", "faster"),
                ("minus", "speed(1)", "slower"), ("q", "quit", "quit")]

    def __init__(self, source, live: bool = True):
        super().__init__()
        self.source = source
        self.live = live                              # False: one frame, nothing moves (headless shots, tests)
        self.snap: Snapshot = source.snapshot()
        self.shown: Snapshot = self.snap              # the last one the grange answered for: what is drawn
        self.columns: list = []
        self.view = stack.View()
        self.show_detail = False
        self.title_shown: Optional[str] = None       # the terminal title last written
        self.leaving = False

    def compose(self) -> ComposeResult:
        yield Static(id="head")
        with Horizontal(id="panels"):
            yield Panel(lambda w, h: self.panel(liveness, "liveness", w, h), id="liveness")
            yield Panel(lambda w, h: self.panel(tailnet, "tailnet", w, h), id="tailnet")
        yield GridView(lambda key: stack.tip_for(self.shown, self.columns, key), self.action_scroll_cols, id="grid")
        with Horizontal(id="bottom"):
            yield Static(id="diag")
            yield Static(id="detail")
        yield Static(id="foot")

    def on_mount(self) -> None:
        if self.live:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGHUP):    # the Mac app stops the viewer this way: leave cleanly
                try:
                    loop.add_signal_handler(sig, self.exit)
                except (NotImplementedError, RuntimeError, ValueError):
                    pass
            self.source.start()
            self.set_interval(0.25, self.tick)
            log.info("started: %s", "the demo" if self.snap.demo else f"the grange on 127.0.0.1:{self.snap.port}")
        self.redraw()

    def on_unmount(self) -> None:
        if self.live:
            self.source.stop()

    def _handle_exception(self, error: Exception) -> None:
        """Textual prints the traceback on the terminal as the app ends, and nowhere else: the
        log the Mac app points at would say only that it exited. Put it there first."""
        log.error("%s crashed", APP_NAME, exc_info=error)
        super()._handle_exception(error)

    def set_window_title(self, title: str) -> None:
        """Write the terminal's title when it has changed (the redraw runs four times a second,
        and an escape on every tick would be wasted). Once the app is quitting it is left cleared."""
        driver = self._driver
        if driver is None or self.leaving or title == self.title_shown:
            return
        try:
            driver.write(title_sequence(title))
            self.title_shown = title
        except Exception:                 # headless, or already out of application mode
            log.debug("window title not set", exc_info=True)

    def exit(self, *args, **kwargs) -> None:
        self.set_window_title("")         # the Mac app goes back to its own title, iTerm2's tab to its
        self.leaving = True
        super().exit(*args, **kwargs)

    def on_resize(self) -> None:
        self.redraw()

    def tick(self) -> None:
        before, self.snap = self.snap, self.source.snapshot()
        self.note_changes(before, self.snap)
        self.redraw()

    @staticmethod
    def note_changes(before: Snapshot, now: Snapshot) -> None:
        """The log gets what changed about reaching the grange and its sidecar, never who is on the board."""
        if now.demo:
            return
        if before.reachable != now.reachable or before.ever_reached != now.ever_reached:
            log.info("the grange on 127.0.0.1:%s %s", now.port,
                     "answers" if now.reachable else "does not answer")
        if before.sidecar.state != now.sidecar.state:
            log.info("sidecar: %s (was %s)%s", now.sidecar.state, before.sidecar.state,
                     f": {', '.join(now.sidecar.health)}" if now.sidecar.health else "")
        if before.has_mesh != now.has_mesh and not now.refusing:
            log.info("GET /v1/mesh %s", "answers: gossip, rev and counter are live" if now.has_mesh else
                     "has not answered")
        if before.me.image_stale != now.me.image_stale:
            log.info("the grange's image is %s", "out of date (503)" if now.me.image_stale else "current again")
        if before.note != now.note and now.note:
            log.info("%s", now.note)

    # ---------------------------------------------------------------- actions
    def action_step(self, d: int) -> None:
        keys = [c.key for c in self.columns]
        if not keys:
            return
        i = keys.index(self.view.sel_key) if self.view.sel_key in keys else 0
        self.view.sel_key = keys[(i + d) % len(keys)]
        self.view.follow = True
        self.redraw()

    def action_scroll_cols(self, d: int) -> None:
        """Scroll the columns without moving the selection (shift+arrows, the wheel, the trackpad)."""
        self.view.view_start += d
        self.redraw()

    def action_detail(self) -> None:
        self.show_detail = not self.show_detail
        self.query_one("#detail").display = self.show_detail
        self.redraw()

    def action_order(self) -> None:
        self.view.by_address = not self.view.by_address
        self.view.follow = True
        self.redraw()

    def action_pause(self) -> None:
        if self.snap.demo:
            self.source.paused = not self.source.paused
            self.redraw()

    def action_next(self) -> None:
        if self.snap.demo:
            self.source.next_moment()
            self.tick()

    def action_speed(self, s: int) -> None:
        if self.snap.demo:
            self.source.speed = s
            self.redraw()

    # ---------------------------------------------------------------- drawing
    def redraw(self) -> None:
        width, height = self.size.width or 150, self.size.height or 46
        snap = self.snap
        self.set_window_title(window_title(bound_nodes(snap)))
        down = not snap.reachable
        dim = 0.35 if down and (self.columns or snap.peers) else 1.0
        for wid in ("#liveness", "#tailnet", "#grid"):
            w = self.query_one(wid)
            if w.styles.text_opacity != dim:
                w.styles.text_opacity = dim
        if not down:                                  # nothing to read keeps the last picture, dimmed
            self.shown = snap
            self.columns = stack.build(snap, self.view.by_address)
        elif self.columns:
            self.columns = stack.build(self.shown, self.view.by_address)
        elif snap.peers:                              # started while it was down: the source's last picture
            self.shown = snap
            self.columns = stack.build(snap, self.view.by_address)
        if self.view.sel_key not in {c.key for c in self.columns}:
            self.view.sel_key = "me"
        band, lines, gap = stack.fit_height(height)
        panels = self.query_one("#panels")
        if panels.styles.height is None or panels.styles.height.value != band:
            panels.styles.height = band
        for p in self.query(Panel):
            p.refresh()
        diag_width = width - 2 - (60 if self.show_detail else 0)
        self.query_one("#head", Static).update(self.head(width))
        if self.columns:
            self.query_one("#grid", Static).update(stack.grid(self.columns, width, lines, gap, self.view))
        else:
            self.query_one("#grid", Static).update(self.waiting())
        self.query_one("#diag", Static).update(stack.unreachable(snap, diag_width) if down else
                                               stack.not_running(snap, diag_width) if snap.refusing else
                                               stack.diagnosis(self.columns, snap, diag_width))
        sel = next((c for c in self.columns if c.key == self.view.sel_key), None)
        self.query_one("#detail", Static).update(stack.detail(sel) if sel else Text(""))
        self.query_one("#foot", Static).update(self.foot())

    def head(self, width: int) -> Text:
        snap, me = self.snap, self.shown.me
        t = Text()
        t.append("agent-grange-viz", style=f"bold {INK}")
        t.append("  stack   ", style=MUTED)
        if snap.demo:
            t.append("demo", style=f"bold {INK2}")
            t.append("   ", style=MUTED)
        right = Text()
        if not snap.reachable:
            gone = age(snap.unreachable_for) if snap.unreachable_for is not None else "a while"
            t.append("✗ ", style=CRIT)
            if snap.ever_reached:
                t.append(f"nothing answers on 127.0.0.1:{snap.port} for {gone}", style=f"bold {INK}")
                t.append(f" · connection refused · showing what the grange said {gone} ago", style=INK2)
            else:
                t.append(f"nothing answers on 127.0.0.1:{snap.port}", style=f"bold {INK}")
                t.append(" · is the grange running?", style=INK2)
        elif snap.refusing:                           # it answers, and only to say no
            since = f" for {age(snap.me.stale_for)}" if snap.me.stale_for is not None else ""
            t.append("✗ ", style=CRIT)
            t.append(f"the grange on 127.0.0.1:{snap.port} is not running", style=f"bold {INK}")
            t.append(f" · image out of date · make update · 503 to every request{since}", style=INK2)
        else:
            t.append(me.gid or "?", style=f"bold {INK}")
            bits = ([f" @ {me.address}"] if me.address else []) + \
                   [x for x in (f"group {me.group}" if me.group else "",
                                f"inc …{str(me.incarnation)[-6:]}" if me.incarnation else "",
                                f"cursor {me.cursor}") if x]
            t.append((bits[0] if me.address else "") + "".join(f" · {b}" for b in bits[1 if me.address else 0:]), style=INK2)
            if width >= 140:
                found = me.peers_discovered if me.peers_discovered is not None else len(self.shown.peers)
                right.append(f"{found} found" + (f" · {me.peers_live} live" if me.peers_live is not None else ""), style=MUTED)
                if not snap.has_mesh and not snap.demo:
                    right.append(" · /v1/mesh: no answer", style=MUTED)
                right.append("   ", style=MUTED)
        right.append(snap.clock, style=INK2)
        if snap.demo:
            paused = getattr(self.source, "paused", False)
            right.append("  ❚❚" if paused else f"  ▶ {getattr(self.source, 'speed', 1)}×", style=WAITING if paused else MUTED)
        pad = max(1, width - 2 - t.cell_len - right.cell_len)
        if pad == 1 and t.cell_len + right.cell_len + 3 > width:
            t.truncate(max(0, width - 3 - right.cell_len), overflow="ellipsis")
            pad = max(1, width - 2 - t.cell_len - right.cell_len)
        t.append(" " * pad)
        t.append_text(right)
        return t

    def panel(self, draw, title: str, w: int, h: int) -> list:
        """A panel, or only its name until the grange has answered once."""
        if not self.columns:
            return [Text(title, style=f"bold {MUTED}"), Text("  nothing to show yet", style=MUTED)]
        return draw(self.shown, w, h)

    def waiting(self) -> Text:
        """The grid's place before the grange has ever answered."""
        t = Text("\n")
        t.append(f"   waiting for the grange on 127.0.0.1:{self.snap.port}\n", style=f"bold {INK2}")
        t.append("   it answers GET /health there once it is up: cd docker && make up, in agent-grange\n", style=MUTED)
        t.append("   another port: --port N, or grange_port in the settings · a made-up team: --demo\n", style=MUTED)
        return t

    def foot(self) -> Text:
        t = Text()
        keys = [("←→", "column"), ("⇧←→", "scroll"), ("enter", "detail"),
                ("t", "by address" if not self.view.by_address else "by role")]
        if self.snap.demo:
            keys += [("space", "pause"), ("e", "next moment"), ("+ -", "speed")]
        keys.append(("q", "quit"))
        for key, what in keys:
            t.append(key, style=INK2)
            t.append(f" {what}  ", style=MUTED)
        t.append(" ● holds ◐ degraded ✗ breaks", style=MUTED)
        if self.snap.demo:
            t.append(" · made-up data", style=MUTED)
        return t


# ---- starting ------------------------------------------------------------------------------------------
def make_source(args):
    """The demo's simulation, or the real grange and its sidecar."""
    if args.demo:
        from demo import DemoSource
        return DemoSource(start=args.at if args.at is not None else 100.0)
    import settings as settings_module
    from live import LiveSource
    cfg = settings_module.load()
    return LiveSource(port=args.port or cfg.grange_port, docker_socket=cfg.docker_socket,
                      use_docker=cfg.use_docker and not args.no_docker)


def parse(argv) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="agent-grange-viz", description="Watch agent-grange's communication plane.")
    ap.add_argument("--version", action="store_true", help="print the version and exit")
    ap.add_argument("--selfcheck", action="store_true", help="check that this copy of the app is whole, and exit")
    ap.add_argument("--demo", action="store_true", help="a made-up team on a four-minute loop; nothing real is read")
    ap.add_argument("--port", type=int, default=None, help=f"the grange's local port (default {DEFAULT_PORT}, or the settings)")
    ap.add_argument("--no-docker", action="store_true", help="do not ask the Docker socket about the sidecar")
    ap.add_argument("--shot", metavar="FILE.svg", help="draw one frame headless, write it, and exit")
    ap.add_argument("--at", type=float, default=None, help="with --demo: seconds into the scenario to start at")
    ap.add_argument("--size", default="150x46", help="with --shot: columns x rows")
    return ap.parse_args(argv)


def shoot(source, path: str, size: str, settle: float) -> None:
    w, h = (int(x) for x in size.split("x"))

    async def go():
        app = GrangeViz(source, live=False)
        async with app.run_test(size=(w, h)) as pilot:
            if settle:                                # the real grange: give the readers a moment
                source.start()
                await asyncio.sleep(settle)
                app.tick()
                source.stop()
            await pilot.pause(0.3)
            svg = app.export_screenshot()
        with open(path, "w") as f:
            f.write(svg)

    asyncio.run(go())
    print(path)


LOG_BYTES = 2_000_000                    # the log starts afresh past this size,
LOG_KEPT = 3                             # and this many before it are kept beside it


def start_log(folder: Path) -> logging.Handler:
    """The log, agent-grange-viz.log in `folder`: what changed about reaching the grange and
    its sidecar, and why anything failed. Past LOG_BYTES it starts afresh, with the LOG_KEPT
    before it kept, so a viewer left open for months does not fill the disk."""
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / LOG_NAME, maxBytes=LOG_BYTES, backupCount=LOG_KEPT, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return handler


def selfcheck() -> int:
    """For the build (scripts/build_app.sh): prove this copy of the app is whole, and say so.

    It loads every part a packaged copy could be missing, the live readers included, and draws
    the demo without a screen. It reads no grange and asks no Docker. Returns 0 when the Stack
    drew with its rows and a verdict for every column."""
    import grangeapi  # noqa: F401  only that they load
    import live       # noqa: F401
    import sidecar    # noqa: F401
    from demo import DemoSource

    async def look() -> str:
        app = GrangeViz(DemoSource(start=165.0), live=False)
        async with app.run_test(size=(150, 46)) as pilot:
            await pilot.pause()
            if not app.columns or any(len(c.cells) != len(stack.LAYERS) for c in app.columns):
                return "the Stack has no columns"
            shown = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
            missing = [word for word in ("headscale", "tailnet", "gossip", "record", "farm", "verdict", "diagnosis")
                       if word not in shown]
            return f"not drawn: {', '.join(missing)}" if missing else ""

    trouble = asyncio.run(look())
    print(f"{APP_NAME} {app_version() or '(no version)'}: "
          + (f"self-check FAILED: {trouble}" if trouble else "self-check passed"))
    return 1 if trouble else 0


def main(argv=None) -> int:
    """Run the viewer. `--version` prints the version and `--selfcheck` checks a packaged copy;
    both return without starting it. The return value is the exit code: not zero after a
    crash, so the Mac app knows to say so instead of closing."""
    args = parse(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(app_version() or "(no version)")
        return 0
    if args.selfcheck:
        return selfcheck()
    source = make_source(args)
    if args.shot:
        shoot(source, args.shot, args.size, settle=0.0 if args.demo else 4.0)
        return 0
    start_log(log_dir())
    app = GrangeViz(source)
    try:
        app.run()
        return app.return_code or 0
    except Exception:                    # trouble outside Textual's own handling: starting, the terminal
        log.exception("%s crashed", APP_NAME)
        raise
    finally:
        clear_title_left_behind(app, sys.__stdout__)
        log.info("exiting (return code %s)", app.return_code)
        logging.shutdown()


if __name__ == "__main__":
    sys.exit(main())
