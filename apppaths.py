# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Where agent-grange-viz keeps its own files.

Run from a checkout (`python main.py`) everything stays next to the code: logs/ and
settings.json, both ignored by git. Run as the bundled Mac app (PyInstaller sets sys.frozen)
the bundle is signed and read-only, so logs go to ~/Library/Logs/agent-grange-viz and the
settings to ~/Library/Application Support/agent-grange-viz.

There are no secrets to keep: the viewer reads the grange's local API on 127.0.0.1, which
asks for none, and the Docker socket, which the system guards by its file permissions.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("agent_grange_viz.files")

APP_NAME = "agent-grange-viz"
LOG_NAME = APP_NAME + ".log"
FROZEN = bool(getattr(sys, "frozen", False))
CODE_DIR = Path(__file__).resolve().parent


def log_dir() -> Path:
    if FROZEN:
        return Path.home() / "Library" / "Logs" / APP_NAME
    return CODE_DIR / "logs"


def log_file() -> Path:
    return log_dir() / LOG_NAME


def data_dir() -> Path:
    if FROZEN:
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return CODE_DIR


def write_json(path: Path, data: object) -> bool:
    """Save `data` to `path` as JSON, whole or not at all: it is written to a file beside
    it, made sure of on disk, then put in its place in one step, so a crash or a full disk
    leaves the last good file. Trouble is logged, never raised: the app goes on with what it
    has in memory. Returns whether it was saved."""
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as e:
        log.warning("could not write %s: %r", path, e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def app_version() -> str:
    """The release number without a leading v, or "" if unknown. The Mac app passes its
    bundle version in AGENT_GRANGE_VIZ_VERSION; a run from a checkout reads the VERSION file
    (the bundled copy carries that file too)."""
    v = os.environ.get("AGENT_GRANGE_VIZ_VERSION", "").strip()
    if not v:
        try:
            v = (CODE_DIR / "VERSION").read_text().strip()
        except OSError:
            v = ""
    return v.removeprefix("v")
