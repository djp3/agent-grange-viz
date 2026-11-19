# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The Docker side: what Docker says about the two agent-grange containers, and what the
Tailscale sidecar says when asked through `docker exec`.

The grange's local API is all the viewer can reach directly (docs/data-sources.md). The
sidecar's own knowledge (who is online, by what path, how fast, how many bytes) sits behind a
socket inside Docker's VM, so until the grange relays it (GET /v1/mesh) the viewer asks the
`tailscale` command in the sidecar container, through the Docker Engine API on its Unix
socket. Plain HTTP over a Unix socket: http.client with its own connect(), nothing installed.

  Engine    the Engine API: list, inspect, exec (create, start, read the framed stream)
  Sidecar   finds this grange's two containers by label and published port, and runs
              tailscale status --json
              tailscale ping --c 1 --until-direct=false --timeout 2s <ip>
              tailscale metrics print
            in the sidecar

Docker being absent, stopped, or not ours to use is a normal state here, not an error: every
method of Sidecar answers None (or a Docker record with available=False) and leaves one line
in `reason` saying why. Nothing in this file writes to a container, starts one or stops one.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from model import DEFAULT_PORT, Docker

LABEL = "agent-grange.service"           # =tailscale on the sidecar, =grange on the grange (R3.9)
CONTAINER_PORT = "8080/tcp"              # the grange's API port inside the shared namespace
ZERO_TIME = "0001-01-01T00:00:00Z"       # Go's zero time: "never"

STATUS_CMD = ["tailscale", "status", "--json"]
PING_CMD = ["tailscale", "ping", "--c", "1", "--until-direct=false", "--timeout", "2s"]
METRICS_CMD = ["tailscale", "metrics", "print"]


class DockerError(Exception):
    """The Engine could not be asked, or said no. The message is one line for a person."""


# ---- small parsers, kept apart so they can be tested against captured output ---------------
def parse_time(value: str) -> Optional[float]:
    """An RFC 3339 time as Docker and Tailscale print them (nanoseconds, Z) as epoch seconds;
    None for Go's zero time or anything unreadable."""
    if not value or value.startswith("0001-01-01"):
        return None
    m = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)?$", value)
    if not m:
        return None
    frac = (m.group(2) or "")[:7]                # datetime takes microseconds at most
    zone = "+00:00" if m.group(3) in (None, "Z") else m.group(3)
    try:
        return datetime.fromisoformat(m.group(1) + frac + zone).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def demux(raw: bytes) -> tuple:
    """(stdout, stderr) from an exec stream: frames of [stream, 0, 0, 0, length(4)] + payload.
    A stream that is not framed (a TTY) comes back whole as stdout."""
    out, err, i, n = bytearray(), bytearray(), 0, len(raw)
    while i + 8 <= n:
        kind, size = raw[i], int.from_bytes(raw[i + 4:i + 8], "big")
        if kind > 2 or raw[i + 1:i + 4] != b"\x00\x00\x00" or i + 8 + size > n:
            return bytes(raw), b""
        (err if kind == 2 else out).extend(raw[i + 8:i + 8 + size])
        i += 8 + size
    if i != n:
        return bytes(raw), b""
    return bytes(out), bytes(err)


_UNITS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "μs": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(text: str) -> Optional[float]:
    """A Go duration (0s, 12ms, 1.5s, 250µs, 1m2s) in seconds."""
    parts = re.findall(r"(\d+(?:\.\d+)?)(ns|us|µs|μs|ms|s|m|h)", text)
    if not parts or "".join(a + b for a, b in parts) != text:
        return None
    return sum(float(a) * _UNITS[b] for a, b in parts)


@dataclass
class Pong:
    rtt_ms: float
    derp: str = ""                       # the relay's region code when it came back through DERP
    endpoint: str = ""                   # the peer's UDP endpoint when it came back direct


_PONG = re.compile(r"pong from \S+ \([^)]*\) via (?:DERP\(([^)]*)\)|(\S+)) in (\S+)")


def parse_ping(text: str) -> Optional[Pong]:
    """`pong from grange-b (100.64.0.2) via 172.29.207.3:55569 in 4ms`, or `via DERP(nyc) in
    31ms`. None when it timed out or said anything else."""
    m = _PONG.search(text)
    if not m:
        return None
    seconds = parse_duration(m.group(3))
    if seconds is None:
        return None
    return Pong(seconds * 1000.0, derp=m.group(1) or "", endpoint=m.group(2) or "")


_METRIC = re.compile(r'^tailscaled_(?:inbound|outbound)_bytes_total\{path="([^"]+)"\}\s+(\d+(?:\.\d+)?)\s*$')


def parse_metrics(text: str) -> dict:
    """Bytes both ways by path, from the sidecar's user metrics: {"direct": n, "derp": n}.
    A peer relay is a relay too, and is counted with DERP."""
    out: dict = {}
    for line in text.splitlines():
        m = _METRIC.match(line)
        if m:
            key = "direct" if m.group(1).startswith("direct") else "derp"
            out[key] = out.get(key, 0) + int(float(m.group(2)))
    return out


def _ipv4(ips) -> str:
    return next((ip for ip in ips or () if isinstance(ip, str) and ":" not in ip), "")


def parse_status(status: dict, tag: str) -> dict:
    """What the viewer uses of `tailscale status --json`:
        {"backend", "health": [...], "self": {...}, "peers": [{...}, ...]}
    with only the peers that carry the grange tag and have a tailnet IPv4 (R6.8)."""
    me = status.get("Self") or {}
    peers = []
    for peer in (status.get("Peer") or {}).values():
        if tag not in (peer.get("Tags") or ()):
            continue
        ip = _ipv4(peer.get("TailscaleIPs"))
        if not ip:
            continue
        peers.append({"hostname": peer.get("HostName") or "", "ip": ip, "online": bool(peer.get("Online")),
                      "active": bool(peer.get("Active")), "cur_addr": peer.get("CurAddr") or "",
                      "relay": peer.get("Relay") or "", "handshake": parse_time(peer.get("LastHandshake") or ""),
                      "rx": int(peer.get("RxBytes") or 0), "tx": int(peer.get("TxBytes") or 0)})
    return {"backend": status.get("BackendState") or "", "health": list(status.get("Health") or ()),
            "self": {"hostname": me.get("HostName") or "", "ip": _ipv4(me.get("TailscaleIPs")),
                     "relay": me.get("Relay") or "", "online": bool(me.get("Online")),
                     "tags": list(me.get("Tags") or ())},
            "peers": peers}


# ---- the Engine API -----------------------------------------------------------------------------
class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


def find_socket(configured: str = "") -> Optional[str]:
    """A configured path wins; then DOCKER_HOST, then the usual places, as agent-grange's
    tools/query_server.py looks for it."""
    if configured:
        return configured
    host = os.environ.get("DOCKER_HOST", "")
    if host.startswith("unix://"):
        return host[len("unix://"):]
    for path in ("/var/run/docker.sock", os.path.expanduser("~/.docker/run/docker.sock")):
        if os.path.exists(path):
            return path
    return None


@dataclass
class ExecResult:
    code: Optional[int]
    out: str
    err: str


class Engine:
    def __init__(self, socket_path: str = "", timeout: float = 4.0):
        self.configured = socket_path
        self.timeout = timeout

    def request(self, method: str, path: str, body=None, timeout: Optional[float] = None) -> tuple:
        """(status, raw body). DockerError when the socket is missing or does not answer."""
        sock = find_socket(self.configured)
        if sock is None:
            raise DockerError("Docker socket not found (is Docker running?)")
        conn = _UnixConnection(sock, timeout or self.timeout)
        try:
            payload = None if body is None else json.dumps(body).encode()
            headers = {"Host": "localhost"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read()
        except (OSError, http.client.HTTPException) as e:
            reason = "permission denied" if isinstance(e, PermissionError) else f"{type(e).__name__}: {e}"
            raise DockerError(f"Docker does not answer on {sock}: {reason}") from None
        finally:
            conn.close()

    def get(self, path: str):
        status, raw = self.request("GET", path)
        if status != 200:
            raise DockerError(f"Docker answered {status} to {path.split('?')[0]}")
        try:
            return json.loads(raw)
        except ValueError:
            raise DockerError(f"Docker's answer to {path.split('?')[0]} was not JSON") from None

    def containers(self, service: str) -> list:
        """Every container, running or not, labelled agent-grange.service=<service>."""
        filters = urllib.parse.quote(json.dumps({"label": [f"{LABEL}={service}"]}))
        return self.get(f"/containers/json?all=1&filters={filters}")

    def inspect(self, cid: str) -> dict:
        return self.get(f"/containers/{cid}/json")

    def exec(self, cid: str, cmd: list, timeout: float = 6.0) -> ExecResult:
        """Run a command in a running container and wait for it: exec create, then start
        attached, then read its exit code."""
        status, raw = self.request("POST", f"/containers/{cid}/exec",
                                   {"AttachStdout": True, "AttachStderr": True, "Tty": False, "Cmd": cmd})
        if status != 201:
            raise DockerError(f"Docker would not run {cmd[0]} in the sidecar ({status})")
        try:
            eid = json.loads(raw)["Id"]
        except (ValueError, KeyError, TypeError):
            raise DockerError("Docker's exec answer was not understood") from None
        status, raw = self.request("POST", f"/exec/{eid}/start", {"Detach": False, "Tty": False}, timeout=timeout)
        if status != 200:
            raise DockerError(f"Docker would not start {cmd[0]} in the sidecar ({status})")
        out, err = demux(raw)
        code = None
        try:
            code = self.get(f"/exec/{eid}/json").get("ExitCode")
        except DockerError:
            pass
        return ExecResult(code, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


# ---- this grange's two containers ---------------------------------------------------------------
def _state(inspected: dict, now: float) -> tuple:
    """(state, how long in it, health) from an inspected container."""
    st = inspected.get("State") or {}
    state = st.get("Status") or ""
    health = (st.get("Health") or {}).get("Status") or ""
    since = parse_time(st.get("StartedAt") or "") if st.get("Running") and state != "paused" else None
    if state in ("exited", "dead"):
        since = parse_time(st.get("FinishedAt") or "")
    return state, (None if since is None else max(0.0, now - since)), health


def _publishes(inspected: dict, port: int) -> bool:
    bindings = ((inspected.get("HostConfig") or {}).get("PortBindings") or {}).get(CONTAINER_PORT) or []
    return any(str(b.get("HostPort")) == str(port) for b in bindings)


def _name(inspected: dict) -> str:
    return (inspected.get("Name") or "").lstrip("/")


def _project(inspected: dict) -> str:
    return ((inspected.get("Config") or {}).get("Labels") or {}).get("com.docker.compose.project", "")


class Sidecar:
    def __init__(self, port: int = DEFAULT_PORT, socket_path: str = "", tag: str = "tag:grange",
                 engine: Optional[Engine] = None):
        self.port = port
        self.tag = tag
        self.engine = engine or Engine(socket_path)
        self.reason = ""                 # why the last thing asked came back empty
        self._sidecar_id: Optional[str] = None
        self._sidecar_running = False

    # ------------------------------------------------------------------ containers
    def containers(self) -> Docker:
        """Find the sidecar that publishes 127.0.0.1:<port> and the grange in its namespace,
        and say what state each is in. Works when they are stopped: the bindings and the
        finish time are still on the container."""
        now = time.time()
        self._sidecar_id, self._sidecar_running = None, False
        try:
            sidecars = [self.engine.inspect(c["Id"]) for c in self.engine.containers("tailscale")]
        except DockerError as e:
            self.reason = str(e)
            return Docker(available=False)
        mine = next((c for c in sidecars if _publishes(c, self.port)), None)
        if mine is None:
            self.reason = (f"no container labelled {LABEL}=tailscale publishes port {self.port}"
                           + (f" ({len(sidecars)} other sidecar(s) found)" if sidecars else ""))
            return Docker(available=True)
        self.reason = ""
        state, since, _ = _state(mine, now)
        self._sidecar_id, self._sidecar_running = mine.get("Id"), state == "running"
        info = Docker(available=True, sidecar=_name(mine), sidecar_state=state, sidecar_for=since)
        try:
            granges = [self.engine.inspect(c["Id"]) for c in self.engine.containers("grange")]
        except DockerError as e:
            self.reason = str(e)
            return info
        shares = f"container:{mine.get('Id')}"
        grange = next((g for g in granges if (g.get("HostConfig") or {}).get("NetworkMode") == shares), None)
        if grange is None:                       # recreated sidecar: the id moved on; fall back to the project
            same = [g for g in granges if _project(g) and _project(g) == _project(mine)]
            grange = same[0] if len(same) == 1 else None
        if grange is not None:
            g_state, _, g_health = _state(grange, now)
            info.grange, info.grange_state, info.grange_health = _name(grange), g_state, g_health
        return info

    # ------------------------------------------------------------------ the tailscale command
    def _run(self, cmd: list, timeout: float = 6.0) -> Optional[ExecResult]:
        if self._sidecar_id is None:
            self.containers()
        if self._sidecar_id is None:
            return None
        if not self._sidecar_running:
            self.reason = "the sidecar container is not running"
            return None
        try:
            return self.engine.exec(self._sidecar_id, cmd, timeout)
        except DockerError as e:
            self.reason = str(e)
            return None

    def status(self) -> Optional[dict]:
        """parse_status() of the sidecar's `tailscale status --json`, or None."""
        res = self._run(STATUS_CMD)
        if res is None:
            return None
        try:
            return parse_status(json.loads(res.out), self.tag)
        except (ValueError, AttributeError):
            self.reason = "tailscale status did not answer JSON" + (f": {res.err.strip()[:80]}" if res.err else "")
            return None

    def ping(self, ip: str) -> Optional[Pong]:
        """One Tailscale-layer ping; None when it timed out or could not be run."""
        res = self._run(PING_CMD + [ip], timeout=5.0)
        return None if res is None else parse_ping(res.out)

    def path_bytes(self) -> dict:
        """{"direct": n, "derp": n} from the sidecar's user metrics; {} when it has none."""
        res = self._run(METRICS_CMD)
        return {} if res is None or res.code not in (0, None) else parse_metrics(res.out)
