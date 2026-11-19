# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The grange's local API, read-only: GET /health, /version, /v1/agents and, once the grange has
it, /v1/mesh (docs/openapi.yaml), all on 127.0.0.1:<GRANGE_PORT>.

Three things are told apart, because the viewer draws each differently:

  • nothing answers (refused, reset, timed out): Unreachable is raised. The port is published
    by the sidecar container, so this is what a stopped sidecar looks like;
  • the image is out of date: every route answers 503 with one JSON body naming the built
    commit, the tag's commit and the fix (R5.16). That body comes back as a Reply with
    stale=True; it is a state to show, not an error;
  • anything else that is not a 200 raises ApiError.

The viewer never writes: PUT /v1/farm is agent-farm's and is not here.
"""
from __future__ import annotations

import http.client
import json
import socket
from dataclasses import dataclass
from typing import Any, Optional

from model import DEFAULT_PORT

MAX_BODY = 8 << 20                       # a grange holds at most 1000 records of 200 agents


class Unreachable(OSError):
    """Nothing answered on the local API: refused, reset, or silent past the timeout."""


class ApiError(Exception):
    """The grange answered, with something the viewer has no use for."""

    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


@dataclass
class Reply:
    status: int
    body: Any                            # the parsed JSON
    stale: bool = False                  # the 503 "image out of date" body (R5.16)


class GrangeApi:
    def __init__(self, port: int = DEFAULT_PORT, host: str = "127.0.0.1", timeout: float = 3.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    # ------------------------------------------------------------------ routes
    def health(self) -> Reply:
        return self._get("/health")

    def version(self) -> Reply:
        return self._get("/version")

    def agents(self, after: Optional[int] = None, wait: Optional[float] = None) -> Reply:
        """The whole merged picture. With `after` equal to the grange's cursor the request is
        held up to `wait` seconds (R5.7); the socket waits a little longer than that."""
        query = []
        if after is not None:
            query.append(f"after={int(after)}")
        if wait is not None:
            query.append(f"wait={wait:g}")
        path = "/v1/agents" + ("?" + "&".join(query) if query else "")
        held = wait if (after is not None and wait is not None) else 0.0
        return self._get(path, timeout=self.timeout + max(0.0, held))

    def mesh(self) -> Optional[Reply]:
        """GET /v1/mesh, or None while the grange does not have the route (404)."""
        try:
            return self._get("/v1/mesh")
        except ApiError as e:
            if e.status == 404:
                return None
            raise

    # ------------------------------------------------------------------ plumbing
    def _get(self, path: str, timeout: Optional[float] = None) -> Reply:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout or self.timeout)
        try:
            conn.request("GET", path, headers={"Accept": "application/json", "Connection": "close"})
            resp = conn.getresponse()
            raw = resp.read(MAX_BODY + 1)
            status = resp.status
        except (OSError, http.client.HTTPException, socket.timeout) as e:
            raise Unreachable(f"{self.host}:{self.port}{path}: {type(e).__name__}: {e}") from None
        finally:
            conn.close()
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, ValueError):
            body = None
        if status == 200:
            if body is None:
                raise ApiError(status, f"{path} did not answer JSON")
            return Reply(status, body)
        if status == 503 and isinstance(body, dict) and "built_commit" in body:
            return Reply(status, body, stale=True)
        message = body.get("error", "") if isinstance(body, dict) else ""
        raise ApiError(status, message or raw[:120].decode("utf-8", "replace"))
