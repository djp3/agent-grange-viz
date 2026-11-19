# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
User settings for agent-grange-viz, persisted as settings.json next to the code, or under
~/Library/Application Support/agent-grange-viz when running as the bundled Mac app
(override the path with AGENT_GRANGE_VIZ_SETTINGS). Add a setting by adding a field with a
default; unknown or malformed keys in the file are ignored so an old file never breaks
startup.

Nothing secret belongs here, and the viewer has none: the grange's local API asks for no
token, and the Docker socket is guarded by its own file permissions.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import ClassVar

from apppaths import data_dir, write_json

log = logging.getLogger("agent_grange_viz.settings")

DEFAULT_PATH = data_dir() / "settings.json"


@dataclass
class Settings:
    grange_port: int = 7788         # the grange's local API on 127.0.0.1: GRANGE_PORT in its .env (1 to PORT_MOST)
    use_docker: bool = True         # may the viewer ask the Docker socket about the two containers, and run
                                    # `tailscale status` and `tailscale ping` in the sidecar. Without it the
                                    # headscale and tailnet rows have nothing to show until the grange
                                    # serves GET /v1/mesh
    docker_socket: str = ""         # the Docker socket's path; "" finds it: DOCKER_HOST, then
                                    # /var/run/docker.sock, then ~/.docker/run/docker.sock

    PORT_DEFAULT: ClassVar[int] = 7788
    PORT_MOST: ClassVar[int] = 65535

    @classmethod
    def path(cls) -> Path:
        return Path(os.environ.get("AGENT_GRANGE_VIZ_SETTINGS") or DEFAULT_PATH)

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        p = cls.path()
        try:
            data = json.loads(p.read_text())
        except FileNotFoundError:
            return s
        except Exception as e:
            log.warning("could not read %s: %r (using defaults)", p, e)
            return s
        if not isinstance(data, dict):
            return s
        for f in fields(cls):
            if f.name in data and type(data[f.name]) is type(getattr(s, f.name)):
                setattr(s, f.name, data[f.name])
        if not 1 <= s.grange_port <= cls.PORT_MOST:          # not a TCP port: nothing could answer there
            log.warning("grange_port %d in %s is not a port (1 to %d); using %d",
                        s.grange_port, p, cls.PORT_MOST, cls.PORT_DEFAULT)
            s.grange_port = cls.PORT_DEFAULT
        return s

    def save(self) -> None:
        write_json(self.path(), asdict(self))


def load() -> Settings:
    """The settings as the file has them, for a caller that holds the module: Settings.load()."""
    return Settings.load()


def save(settings: Settings) -> None:
    settings.save()
