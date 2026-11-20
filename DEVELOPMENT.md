# Developing agent-grange-viz

The viewer is a Python [Textual](https://textual.textualize.io) program. The Mac app is a
thin native shell around it: a window with a [SwiftTerm](https://github.com/migueldeicaza/SwiftTerm)
terminal that runs a PyInstaller build of the Python code, plus
[Sparkle](https://sparkle-project.org) for updates. Everything the viewer does happens in
Python, so day-to-day development never touches Swift or the bundle. Beyond Textual it uses
only Python's standard library.

## Layout

| path | what |
|------|------|
| `main.py` | the Textual app: the window, its keys, the top line, the terminal's title, the command line, the log |
| `model.py` | the contract between sources and views: a `Snapshot` of this Mac's grange, its sidecar and every peer. `None` means "not known", and every time is an age in seconds |
| `stack.py` | the Stack: where each column breaks, what it says about it, the grid, the diagnosis, the detail pane and every tooltip, as functions of a `Snapshot` |
| `panels.py` | the liveness and tailnet panels above the grid |
| `live.py` | the live source: reads the grange and the sidecar on threads of its own, and works every age out as of now |
| `grangeapi.py` | the grange's local API, read-only: `GET /health`, `/version`, `/v1/agents`, `/v1/mesh` |
| `sidecar.py` | the Docker side: the two containers' state, and `tailscale status`, `ping` and `metrics` through `docker exec` |
| `demo.py`, `demosim.py` | the demo source, and the simulation of a made-up team behind it |
| `theme.py`, `fmt.py` | the colors, and how numbers are written |
| `settings.py` | persisted settings; a new setting is a dataclass field with a default |
| `apppaths.py` | where the log and the settings live: next to the code from a checkout, under `~/Library` in the app |
| `docs/` | `openapi.yaml`, the specification of `GET /v1/mesh` and its companions; `data-sources.md`, the survey behind it; `screenshot.png`, for the README |
| `macos/` | the SwiftPM package for the host app (`Package.swift`, `Sources/AgentGrangeViz`, `Info.plist`, the icon) |
| `packaging/` | the PyInstaller spec for the Python side |
| `scripts/` | `build_app.sh`, `sign_app.sh`, `release.sh`, `fetch_sparkle_tools.sh`, `locked_venv.sh`, `check_min_macos.py`, `render_icon.sh`, `make_icon.sh` |
| `release-notes/` | one file per release, written for people who use the app |
| `tests/` | plain pytest; `standins.py` holds a stand-in grange and a stand-in Docker |
| `VERSION` | the single source of the version number |

## Running from source

    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-build.txt
    .venv/bin/python main.py             # the grange on 127.0.0.1:7788
    .venv/bin/python main.py --port 17981
    .venv/bin/python main.py --no-docker # the grange only: no headscale or tailnet rows
    .venv/bin/python main.py --demo      # a made-up team, every row filled
    .venv/bin/python main.py --demo --at 165 --shot frame.svg --size 150x46   # one frame, headless
    .venv/bin/python -m pytest           # the tests: no network, no real settings

From a checkout the settings file is `settings.json` next to the code (or
`AGENT_GRANGE_VIZ_SETTINGS`), and the log is `logs/agent-grange-viz.log`.

The tests are plain pytest. The ones that drive the window use Textual's `run_test` with a
source that hands out whatever snapshot it was given (`Scripted` in `tests/standins.py`), and
are run by a small decorator, `drive`, so there is no async plugin to install. No test touches
the real settings or reaches the network: `tests/conftest.py` sees to both.

To look at a state the demo does not reach, build a `Snapshot` by hand, as the tests do
(`today`, `healthy` and `not_running` in `tests/standins.py`), and hand it to `main.shoot`.

## Building the app

    scripts/build_app.sh                 # -> dist/agent-grange-viz.app

PyInstaller makes the Python side and `swift build` the window; Sparkle's framework, which
SwiftPM downloads, is copied into the bundle beside them. The result is signed ad-hoc, which
is enough to run it on the Mac that built it. It needs Python 3.14 and Xcode's command line
tools.

Dependencies are pinned exactly in `requirements.txt` and `requirements-build.txt`, and
`requirements.lock` lists every package, indirect ones included, with the hashes of their
files. `scripts/locked_venv.sh` makes a virtual environment holding exactly that, and a
release is built from one. To upgrade a package, change its pin and regenerate the lock; the
command is at the top of `requirements.txt`.

**The minimum macOS is 27**, declared in `macos/Info.plist` (`LSMinimumSystemVersion`, which
Finder checks before it opens the app) and in `macos/Package.swift`, which the Swift
executable records as its own. It follows the bundled Python: releases are built with
Homebrew's, which Homebrew builds for the macOS it runs on, so nearly every part of the Python
tree records macOS 27 as its minimum. `scripts/check_min_macos.py`, the check agent-farm and
agent-frolic share, holds the whole app to that before it is signed: the build stops when any
Apple-silicon binary inside needs more than `Info.plist` says, or when the app's own
executable records another minimum than it. When the build Mac moves to a newer macOS and
Homebrew's Python follows, it lists the files that need more; then either raise the minimum
in both files and in the README, or build with a Python made for older systems (python.org's
installer). `scripts/build_app.sh` also stops at once on a macOS before 27, and says what
works there.

An app built for macOS 27 gets its window's title at the left of the title bar, beside the
buttons, and macOS has no setting for that. `macos/Sources/AgentGrangeViz/CenteredTitle.swift`,
the same file as in the other two apps, hides the system's title and draws a label in the
middle of the bar in its place; the window keeps its title for the Dock, Mission Control and
VoiceOver.

The viewer writes that title itself (OSC 0): the rooster, the name, the version, and a radar
dish for each teammate's grange it reaches. The Mac app shows the same words until the viewer
sets its own, and takes its title back whenever the viewer ends.

The icon is drawn in `macos/Resources/icon.svg`. `scripts/render_icon.sh` renders it (it
needs `rsvg-convert`), and a build only checks that the rendering is current.

During development `swift run` in `macos/` starts the window alone, and
`AGENT_GRANGE_VIZ_CORE=<an executable>` points it at any viewer to run inside. The assembled
app ignores that variable. A `swift run` has no `Info.plist`, so no feed and no key: it never
checks for updates, and **Check for Updates…** stays greyed out.

Sparkle is named in three places, raised together: the minimum in `macos/Package.swift`, the
version that is linked in `macos/Package.resolved`, and the SHA-256 of that release's tools
in `scripts/fetch_sparkle_tools.sh`.

## Releasing

`scripts/release.sh` builds the app from the lock, signs it with a Developer ID under the
hardened runtime, has Apple notarize it, staples it, zips it, writes a Sparkle appcast signed
with this app's EdDSA key, tags `v<VERSION>` and publishes the GitHub release with the zip
and `appcast.xml` attached. The notes in `release-notes/<VERSION>.md` go into the appcast,
where Sparkle's update window shows them, and onto the GitHub release.

Installed apps poll
`https://github.com/djp3/agent-grange-viz/releases/latest/download/appcast.xml` once a day,
so creating the release is what ships the update. Copies of 0.1.1 and 0.1.2 came before
Sparkle and poll nothing.

### One-time setup

- A "Developer ID Application" certificate in the login keychain.
- Notarization credentials: a `notarytool` keychain profile named `agent-grange-viz`

      xcrun notarytool store-credentials agent-grange-viz --apple-id <id> --team-id <team> --password <app-specific>

  or a profile that already exists under another name (`NOTARY_PROFILE=<name>`), or an App
  Store Connect API key (`NOTARY_KEY_ID`, `NOTARY_ISSUER_ID`, `NOTARY_KEY_FILE`).
- The Sparkle key, in the login keychain under the account `agent-grange-viz`. Its public
  half is `SUPublicEDKey` in `macos/Info.plist`:

      .sparkle-tools/<ver>/bin/generate_keys --account agent-grange-viz -p    # prints that public key

  Apps already installed accept updates signed with this key only, so keep a copy of it
  somewhere safe: `generate_keys --account agent-grange-viz -x <file>` exports it.
  `SPARKLE_PRIVATE_KEY_FILE=<file>` signs with an exported key instead of the keychain's.
  The first time `generate_appcast` reads the key, macOS asks whether it may.
- `gh` logged in with push access to the repository.

### Each release

    echo 0.2.0 > VERSION
    $EDITOR release-notes/0.2.0.md       # what is new, written for people who use the app;
                                         # Sparkle's window is small: short paragraphs and - lists
    git add VERSION release-notes && git commit -m "Release 0.2.0"
    scripts/release.sh

`DRY_RUN=1 scripts/release.sh` builds with the Developer ID, zips and writes the appcast into
`dist/`, then stops: nothing is sent to Apple or GitHub, and nothing is tagged.

The script refuses a working tree that is not clean, a tag that already exists, release
notes that are missing, and an appcast that came out unsigned (which is what Sparkle's tool
writes when the key it was given is not the one in `Info.plist`). The tag is made last and
removed again if publishing fails, so a second run starts clean.

`scripts/fetch_sparkle_tools.sh` downloads the tools of the Sparkle release the app links
into `.sparkle-tools/`, and unpacks them only if the download matches the SHA-256 pinned in
the script: `generate_appcast` is handed the private key every installed copy trusts.

The repository is public: the notes, like everything else in it, use made-up names.
