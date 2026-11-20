# 📡 agent-grange-viz 🐓

**See whether your team's granges are talking to each other, and where it breaks when one is not.**

Aaah!  A teammate's agents fell off your board an hour ago. Is their laptop shut? Is their
grange running an old image? Did their farm stop reporting, or is it your own sidecar that lost
the network? The board cannot say: it only shows who is missing. agent-grange-viz is a Mac app
that watches the plane the status travels on, one column for every teammate's grange and one
row for every layer between you and them, so the first thing that fails is the first red mark
you read. (A grange is the hall a farming community meets in.)

It is designed to work with [agent-farm](https://github.com/djp3/agent-farm),
[agent-grange](https://github.com/djp3/agent-grange) and
[agent-frolic](https://github.com/djp3/agent-frolic): see
[below](#made-to-work-with-agent-farm-agent-grange-and-agent-frolic).

**[Download for Mac](https://github.com/djp3/agent-grange-viz/releases/latest)** · macOS 27+ on
Apple silicon · free and open source

<!-- docs/screenshot.png goes here once there is one: the window on the demo, mid-loop -->

### Where it breaks is where the column turns red

Every grange your sidecar can find gets a column, and every layer of the plane a row:
headscale, the tailnet path, gossip, the record your grange holds, their farm. A column is
green down to the first layer that fails. That cell is marked ✗, everything beneath it greys
out as its consequence, and one word under the column names it: `timeout`, `refused`, `503`,
`expired`, `no farm`.

### A closed lid, an old image and a silent farm look different

They break at different heights. A shut laptop: headscale still says online, and every
exchange times out. A build on another group: the tailnet is fine, and gossip is refused with
409. An image behind the tag: 503 on everything. A farm that stopped: the grange is alive and
flags its own farm stale. Below the grid the diagnosis says each one in plain words, with the
evidence, the requirement it comes from, and whose move it is.

### Your own side, too

Your column is the first one. When your sidecar cannot reach headscale it turns amber and
says the tunnels already up still work. When nothing answers on the grange's port at all, the
last picture freezes, dimmed, and the top line says for how long, what Docker knows about the
two containers, and that only `make up` brings them back.

### Every cell explains itself

Hover any cell: the exact numbers, what its mark means, and where the data comes from. Hover a
row's name for the key to that row.

### It only reads. It shows no agents and no pull requests.

Which agents are at work is agent-farm's to show, and which pull request each is on is
agent-frolic's. This app shows the plane between them: at most a count of agents per grange,
never a name or a state. It never pushes anything to the grange, and it changes nothing in
Docker.

## Install

1. Download `agent-grange-viz-<version>.zip` from the
   [latest release](https://github.com/djp3/agent-grange-viz/releases/latest), unzip it and
   drag `agent-grange-viz.app` to Applications.
2. Open it. The app is notarized, so it opens like any other download.

The app does not update itself yet: a new release is a new download from the same page.

You need macOS 27 (Golden Gate) or later on Apple silicon.

**On macOS 26 (Tahoe) or earlier?** The app carries its own copy of Python, and that copy is
built for macOS 27, so the app can't run on an earlier macOS. macOS will refuse to open it
and say it needs macOS 27.0 or later. The viewer itself doesn't need macOS 27, so you can run
it from source in a terminal instead. Install Python 3.14 (python.org's installer runs on earlier
macOS versions), then:

    git clone https://github.com/djp3/agent-grange-viz.git && cd agent-grange-viz
    python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python main.py

Run that way, it has no window or menus of its own (`--demo` and `--port N` stand in for the
Viewer menu), `git pull` brings it up to date, and its settings and log stay in that folder.

To watch something real you also need a grange running on this Mac
([agent-grange](https://github.com/djp3/agent-grange): `cd docker && make up`). Without one
the app says so, and **Viewer ▸ Show the Demo** (⌘D) shows a made-up team instead.

## The window

From top to bottom:

- **The top line**: this Mac's grange (its id, tailnet address, group, incarnation and
  cursor), how many granges were found and how many are live, and the clock.
- **Two panels.** On the left, **liveness**: every record's seen age as a dot on an axis from
  0 to 60 seconds, the expiry wall at its end, with the slow ones named, what has no record
  and why, what expired, left, restarted or joined in the last five minutes, and the oldest
  farm age. On the right, **tailnet**: how many peers are reached directly and how many
  through the DERP relay, by count and by bytes, the home relay, round trips on a log scale
  with the slowest named, recent path changes, and tailscaled's own health.
- **The grid**, under a double rule: a column for each grange, yours first and pinned. Where
  the grange says each one's place in the gossip ring (the demo does; a real grange will with
  `GET /v1/mesh`), the others follow in that order: the ring successor, the neighbours, the
  repair pool, and the addresses outside the live set. The rows are below.
- **The verdict**: one word under each column.
- **The diagnosis**: every column that breaks or degrades, in plain words. Something only
  you can fix is yellow. Sources that are no teammate at all (a device without the grange's
  tag, refused by the identity check) are listed here too.

The title bar reads 🐓 agent-grange-viz and the version (v0.1.1, say), then one 📡 for each
teammate's grange that is bound, up to ten, so the Dock, Mission Control and the window
switcher show how many granges yours reaches. A grange is bound once an exchange has named who
answers at its address. Only `GET /v1/mesh` says that, so until the grange serves it a
teammate whose record is held counts instead; while nothing answers on the grange's port
there are no dishes.

### The rows

| row | first line | second line |
|-----|------------|-------------|
| **headscale** | a mark before each name: ● online, ○ offline, as headscale tells your sidecar; ★ is you | |
| **tailnet** | the path and its round trip in whole ms: ● direct, ○ through DERP, ✗ no path | seconds since the last WireGuard handshake (`>999s` past that) |
| **gossip** | ● how long since the last good exchange; ✗ why the last one failed (`409` another group or protocol, `503` image out of date, `403` not a tagged grange, `t/o` no answer) and how long ago; ○ never tried | exchanges sent ↑ and received ↓ |
| **record** | the seen age as a bar filling toward the 60 second expiry | `r` rev and `c` counter, or ↻ after a restart |
| **farm** | their farm's age as a bar filling toward the 60 seconds at which it is called stale | how many agents it reports |

In your own column the tailnet cell is your sidecar's state: ★ ok, ◐ it cannot reach
headscale, ✗ the grange cannot reach its socket, or it is logged out. While your sidecar
cannot answer, every other tailnet cell is last known, and greys out to say so.

### Keys

| key | action |
|-----|--------|
| ← → | pick a column |
| shift+← shift+→, the wheel, the trackpad | scroll the columns when they do not all fit (yours stays pinned) |
| enter | open or close the selected column's full values, beside the diagnosis |
| t | order the columns by role, or by address (the order the gossip ring walks) |
| hover | what a cell shows, what its mark means, where it comes from |
| q | quit |

In the demo also: space pauses, `e` jumps to the next scripted moment, `+` and `-` change the
speed.

The View menu changes the text size (⌘+ / ⌘-), picks any fixed-pitch font installed on the
Mac, and toggles font smoothing; the window remembers all of that with its size and place.
**Viewer ▸ Restart Viewer** (⌘R) starts it again.

## What it can show today, and what waits for the grange

The grange serves three things on its local port today: `GET /health`, `GET /v1/agents` and
`GET /version`. With those, and the sidecar asked through Docker, the app shows live:

- the **headscale** row and the **tailnet** row (the sidecar's `tailscale status` and `ping`);
- the **record** row's bar (how long since each record was refreshed);
- the **farm** row (farm age, the stale flag, the agent count);
- your own column, the liveness and tailnet panels, and the unreachable screen.

The rest is not in today's API: each peer's **gossip** cell (last exchange, failure code,
counts, its place in the ring), rev and counter, which address is bound to which id, and
whether a record that went away expired or left. Those cells say that they need
`GET /v1/mesh`, which the grange does not serve yet. [docs/openapi.yaml](docs/openapi.yaml)
is the specification of it, written for agent-grange to implement, and
[docs/data-sources.md](docs/data-sources.md) is the survey behind it. The app is written to
fill those cells in the moment the route answers.

**The demo** fills every row now. `--demo` (or **Viewer ▸ Show the Demo**) runs a made-up
team of thirteen granges through a four-minute loop: a lid closes, a path falls back to DERP,
a private build is refused, a teammate joins, a farm goes quiet, a grange restarts, a device
without the tag knocks, an image goes stale, headscale becomes unreachable, the grange loses
its sidecar's socket, and the sidecar container stops. Every name and number in it is
invented, and the screen says so.

## What it reads, and what it never does

- **The grange's local API**, on `127.0.0.1` and the port in the settings (7788 unless you
  changed `GRANGE_PORT`): `GET /health`, `GET /v1/agents`, `GET /version`. It never sends
  `PUT /v1/farm`: that is agent-farm's voice, and the app has none.
- **The Docker socket**, if you allow it (you do by default): it lists the two agent-grange
  containers and their state, and runs `tailscale status` and `tailscale ping` inside the
  sidecar. It starts, stops and changes nothing. Turn it off with `"use_docker": false` in
  the settings or `--no-docker`; the headscale and tailnet rows then have nothing to show
  until the grange serves `GET /v1/mesh`.
- **Nothing else.** It reaches no other machine, sends none of this anywhere, and keeps no
  history on disk: what the panels count is held in memory while the app is open.

## Made to work with agent-farm, agent-grange and agent-frolic

agent-grange-viz is the fourth of four programs for teams where coding agents do much of the
work. Each of the other three works without it:

- **[agent-farm](https://github.com/djp3/agent-farm)** watches the agents on your Mac: which
  are at work, which are idle, and which are waiting for you.
- **[agent-grange](https://github.com/djp3/agent-grange)** carries that between Macs. Each
  person runs one. Their agent-farm reports to it, and the granges of a team pass what they
  know on to each other over a private network.
- **[agent-frolic](https://github.com/djp3/agent-frolic)** puts it on the pull requests:
  whose agents are on which one, beside who each pull request waits on.
- **agent-grange-viz** watches the plane the other three depend on. It shows no agents and no
  pull requests, only whether each grange can be reached, by what path, how fresh its record
  is, and what was refused.

```
   your Mac                                       a teammate's Mac

   agent-farm ──▶ agent-grange ◀──────────────▶ agent-grange ◀── agent-farm
                    │      ▲                          │
                    ▼      └── agent-grange-viz       ▼
               agent-frolic        (reads only)  agent-frolic
```

## Files and logs

| what | where |
|------|-------|
| settings | `~/Library/Application Support/agent-grange-viz/settings.json` |
| log | `~/Library/Logs/agent-grange-viz/agent-grange-viz.log` |

Run from a checkout, both stay next to the code instead (`settings.json`, `logs/`), ignored
by git. **Viewer ▸ Open Log Folder** and **Reveal Settings File** open them.

The settings file holds three things, and no secret (the app has none):

| key | default | meaning |
|-----|---------|---------|
| `grange_port` | `7788` | the grange's local port on 127.0.0.1 (1 to 65535) |
| `use_docker` | `true` | may the app ask the Docker socket about the sidecar |
| `docker_socket` | `""` | the socket's path; empty finds it: `DOCKER_HOST`, then `/var/run/docker.sock`, then `~/.docker/run/docker.sock` |

A key the file gets wrong is ignored, and the default used.

## How it works

It is a Python program (Textual) running inside a native window (SwiftTerm), the way
agent-farm and agent-frolic are, and beyond Textual it uses only Python's standard library.
To run it from source:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-build.txt
.venv/bin/python main.py             # the grange on 127.0.0.1:7788
.venv/bin/python main.py --port 17981
.venv/bin/python main.py --demo      # a made-up team, every row filled
.venv/bin/python -m pytest           # the tests: no network, no real settings
```

**The minimum macOS is 27**, declared in `macos/Info.plist` (`LSMinimumSystemVersion`, which
Finder checks before it opens the app) and in `macos/Package.swift`, which the Swift
executable records as its own. It follows the bundled Python: releases are built with
Homebrew's, which Homebrew builds for the macOS it runs on, so nearly every part of the Python
tree records macOS 27 as its minimum. `scripts/check_min_macos.py`, the check agent-farm and
agent-frolic share, holds the whole app to that before it is signed: the build stops when any
Apple-silicon binary inside needs more than `Info.plist` says, or when the app's own
executable records another minimum than it. When the build Mac moves to a newer macOS and
Homebrew's Python follows, it lists the files that need more; then either raise the minimum
in both files and here, or build with a Python made for older systems (python.org's
installer). `scripts/build_app.sh` also stops at once on a macOS before 27, and says what
works there.

An app built for macOS 27 gets its window's title at the left of the title bar, beside the
buttons, and macOS has no setting for that. `macos/Sources/AgentGrangeViz/CenteredTitle.swift`,
the same file as in the other two apps, hides the system's title and draws a label in the
middle of the bar in its place; the window keeps its title for the Dock, Mission Control and
VoiceOver.

`scripts/build_app.sh` builds the Mac app: PyInstaller makes the Python side, `swift build`
the window, and the result is signed ad-hoc, which is enough to run it on the Mac that built
it (it needs Python 3.14 and Xcode's command line tools, and says so and builds nothing on a
macOS before 27). `scripts/release.sh` cuts a release: the same build from the lock, signed
with a Developer ID under the hardened runtime, notarized by Apple, stapled, zipped, tagged
`v<VERSION>` and published on GitHub with the notes in `release-notes/`. `requirements.lock` pins everything with hashes,
and `scripts/locked_venv.sh` makes a virtual environment holding exactly that. The icon is
drawn in `macos/Resources/icon.svg`; `scripts/render_icon.sh` renders it (it needs
`rsvg-convert`), and a build only checks that the rendering is current.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

The icon is this project's own drawing and is under the same license.
