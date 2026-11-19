# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Stand-ins for the two things the live source talks to, so its tests need no network beyond
loopback and no real Docker:

  FakeGrange   a grange's local API on 127.0.0.1 at a port of its own: /health, /version,
               /v1/agents with the cursor and the long-poll (R5.7), the 503 "image out of
               date" body on every route, and /v1/mesh when given one
  FakeDocker   a Docker Engine on a Unix socket: containers by label, inspect, and exec
               (create, start with the framed stream, exit code) answering from a script

Both record what was asked of them, so a test can say the viewer never wrote anything.
"""
from __future__ import annotations

import json
import os
import shutil
import socketserver
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STALE_BODY = {"error": "image out of date", "summary": "the tag moved past this image",
              "details": "ahead by 2", "built_commit": "bf6e87e21b0775d37e39f5892a95d358f84f73d1",
              "tag": "agent-grange-latest", "tag_commit": "c0ffee1234567890c0ffee1234567890c0ffee12",
              "fix": "make update", "checked": "2026-11-18T22:00:00+00:00"}


def rfc3339(epoch: float) -> str:
    """As Docker and Tailscale print a time: nanoseconds, Z."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"


def entry(gid: str, seen: float = 1.0, farm=5.0, stale: bool = False, agents: int = 1) -> dict:
    """One grange as /v1/agents lists it."""
    return {"grange": gid, "seen_age_s": seen, "farm_age_s": farm, "farm_stale": stale,
            "agents": [{"owner": gid, "name": f"{gid}-pr-{i} task", "state": "busy"} for i in range(agents)]}


class FakeClock:
    """A clock a test moves by hand."""

    def __init__(self, at: float = 1000.0):
        self.t = at

    def __call__(self) -> float:
        return self.t

    def tick(self, seconds: float) -> None:
        self.t += seconds


# ---- the grange ------------------------------------------------------------------------------------
class FakeGrange:
    def __init__(self, gid: str = "me"):
        self.gid = gid
        self.health = {"status": "ok", "grange": gid, "cursor": 1, "incarnation": 1000,
                       "freshness": {"last_polled": None, "last_summary": "current", "recheck_seconds": 30.0,
                                     "polls": 3, "consecutive_unchanged_polls": 3},
                       "peers_discovered": 0, "peers_live": 0, "last_gossip_age_s": 1.5,
                       "rejected": {"proto": 0, "group": 0, "identity": 0, "overflow": 0}, "duplicate_id": False}
        self.version = {"repo": "djp3/agent-grange", "branch": "main",
                        "commit": "bf6e87e21b0775d37e39f5892a95d358f84f73d1", "dirty": False}
        self.picture = {"cursor": 1, "grange": gid, "incarnation": 1000, "granges": [entry(gid, seen=0.0)]}
        self.stale = None                    # a body: every route answers 503 with it
        self.mesh = None                     # a dict: GET /v1/mesh answers it
        self.error = None                    # a status: every route answers it, with no body
        self.requests: list = []             # (method, path with query)
        self._cond = threading.Condition()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, code: int, body) -> None:
                raw = b"" if body is None else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
                self.close_connection = True

            def do_GET(self):
                fake.requests.append(("GET", self.path))
                url = urllib.parse.urlsplit(self.path)
                if fake.error is not None:
                    return self._send(fake.error, None)
                if fake.stale is not None:
                    return self._send(503, fake.stale)
                if url.path == "/health":
                    return self._send(200, fake.health)
                if url.path == "/version":
                    return self._send(200, fake.version)
                if url.path == "/v1/mesh":
                    return self._send(404, {"error": "no route /v1/mesh"}) if fake.mesh is None \
                        else self._send(200, fake.mesh)
                if url.path == "/v1/agents":
                    q = urllib.parse.parse_qs(url.query)
                    after = int(q["after"][0]) if "after" in q else None
                    wait = min(30.0, float(q["wait"][0])) if "wait" in q else 30.0
                    with fake._cond:
                        if after and after == fake.picture["cursor"] and wait > 0:
                            fake._cond.wait_for(lambda: fake.picture["cursor"] != after, wait)
                        body = json.loads(json.dumps(fake.picture))
                    if fake.stale is not None:           # it went stale while we waited
                        return self._send(503, fake.stale)
                    return self._send(200, body)
                self._send(404, {"error": f"no route {url.path}"})

            def do_PUT(self):
                fake.requests.append(("PUT", self.path))
                self._send(204, None)

            do_POST = do_DELETE = do_PUT

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    # what a test changes
    def set_granges(self, others: list, incarnation=None, cursor=None) -> None:
        """A content change: the peers' entries, and the cursor moves (R5.6)."""
        with self._cond:
            mine = self.picture["granges"][0]
            self.picture["granges"] = [mine] + list(others)
            self.picture["cursor"] = cursor if cursor is not None else self.picture["cursor"] + 1
            if incarnation is not None:
                self.picture["incarnation"] = incarnation
                self.health["incarnation"] = incarnation
            self.health["cursor"] = self.picture["cursor"]
            self._cond.notify_all()

    def touch(self, gid: str, **fields) -> None:
        """Change an entry's ages without moving the cursor, as a heartbeat does."""
        with self._cond:
            for g in self.picture["granges"]:
                if g["grange"] == gid:
                    g.update(fields)

    def wrote(self) -> list:
        return [r for r in self.requests if r[0] != "GET"]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def closed_port() -> int:
    """A loopback port nothing listens on."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---- Docker ----------------------------------------------------------------------------------------
def frame(stream: int, data: bytes) -> bytes:
    """One frame of an exec stream: 1 is stdout, 2 is stderr."""
    return bytes([stream, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


def status_json(self_host: str = "grange-me", self_ip: str = "100.64.0.5", peers=(), backend: str = "Running",
                health=(), relay: str = "hs") -> str:
    """`tailscale status --json`, as much of it as the viewer reads. Each peer is a dict with
    host, ip, and optionally online, cur_addr, relay, handshake (epoch), rx, tx, tags, active."""
    out = {"BackendState": backend, "Health": list(health),
           "Self": {"HostName": self_host, "TailscaleIPs": [self_ip, "fd7a::5"], "Relay": relay, "Online": True,
                    "Tags": ["tag:grange"]},
           "Peer": {}}
    for i, p in enumerate(peers):
        out["Peer"][f"nodekey:{i}"] = {
            "HostName": p["host"], "TailscaleIPs": [p["ip"], f"fd7a::{i}"], "Tags": p.get("tags", ["tag:grange"]),
            "Online": p.get("online", True), "Active": p.get("active", True), "CurAddr": p.get("cur_addr", ""),
            "Relay": p.get("relay", relay), "RxBytes": p.get("rx", 0), "TxBytes": p.get("tx", 0),
            "LastHandshake": rfc3339(p["handshake"]) if p.get("handshake") else "0001-01-01T00:00:00Z"}
    return json.dumps(out)


METRICS = '''# TYPE tailscaled_inbound_bytes_total counter
tailscaled_inbound_bytes_total{path="derp"} 900
tailscaled_inbound_bytes_total{path="direct_ipv4"} 50000
tailscaled_inbound_bytes_total{path="direct_ipv6"} 0
tailscaled_inbound_bytes_total{path="peer_relay_ipv4"} 0
tailscaled_outbound_bytes_total{path="derp"} 1100
tailscaled_outbound_bytes_total{path="direct_ipv4"} 52000
tailscaled_outbound_dropped_packets_total{reason="error"} 0
'''


class FakeDocker:
    def __init__(self):
        # A Unix socket's path is short by law (104 bytes on macOS); pytest's tmp_path is not.
        self.dir = tempfile.mkdtemp(prefix="agv")
        self.path = os.path.join(self.dir, "d.sock")
        self.containers: dict = {}           # id -> the inspect body
        self.script: dict = {}               # first words of a command -> (stdout, stderr, exit code) or callable
        self.execs: dict = {}                # exec id -> (container id, cmd)
        self.calls: list = []                # (method, path)
        self.refuse = False                  # every request answers 500
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _json(self, code: int, body) -> None:
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else {}

            def do_GET(self):
                fake.calls.append(("GET", self.path))
                url = urllib.parse.urlsplit(self.path)
                parts = url.path.strip("/").split("/")
                if fake.refuse:
                    return self._json(500, {"message": "no"})
                if url.path == "/containers/json":
                    filters = json.loads(urllib.parse.parse_qs(url.query).get("filters", ["{}"])[0])
                    wanted = [f.split("=", 1) for f in filters.get("label", [])]
                    out = []
                    for cid, c in fake.containers.items():
                        labels = c["Config"]["Labels"]
                        if all(labels.get(k) == v for k, v in wanted):
                            out.append({"Id": cid, "Names": [c["Name"]], "State": c["State"]["Status"], "Labels": labels})
                    return self._json(200, out)
                if len(parts) == 3 and parts[0] == "containers" and parts[2] == "json":
                    c = fake.containers.get(parts[1])
                    return self._json(200, c) if c else self._json(404, {"message": "no such container"})
                if len(parts) == 3 and parts[0] == "exec" and parts[2] == "json":
                    cmd = fake.execs.get(parts[1], (None, []))[1]
                    return self._json(200, {"ExitCode": fake.answer(cmd)[2]})
                self._json(404, {"message": "page not found"})

            def do_POST(self):
                fake.calls.append(("POST", self.path))
                parts = urllib.parse.urlsplit(self.path).path.strip("/").split("/")
                body = self._body()
                if fake.refuse:
                    return self._json(500, {"message": "no"})
                if len(parts) == 3 and parts[0] == "containers" and parts[2] == "exec":
                    c = fake.containers.get(parts[1])
                    if c is None:
                        return self._json(404, {"message": "no such container"})
                    if c["State"]["Status"] != "running":
                        return self._json(409, {"message": "container is not running"})
                    eid = f"e{len(fake.execs) + 1}"
                    fake.execs[eid] = (parts[1], body.get("Cmd") or [])
                    return self._json(201, {"Id": eid})
                if len(parts) == 3 and parts[0] == "exec" and parts[2] == "start":
                    out, err, _ = fake.answer(fake.execs.get(parts[1], (None, []))[1])
                    raw = (frame(1, out.encode()) if out else b"") + (frame(2, err.encode()) if err else b"")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.docker.raw-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(raw)
                    self.close_connection = True
                    return
                self._json(404, {"message": "page not found"})

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True

        self.server = Server(self.path, Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def answer(self, cmd: list) -> tuple:
        """(stdout, stderr, exit code) for a command: the longest scripted prefix wins."""
        for n in range(len(cmd), 0, -1):
            hit = self.script.get(tuple(cmd[:n]))
            if hit is not None:
                return hit(cmd) if callable(hit) else hit
        return "", f"{' '.join(cmd)}: not scripted", 127

    def add(self, cid: str, name: str, service: str, *, state: str = "running", port=None, network: str = "bridge",
            health: str = "healthy", project: str = "agent-grange", up_for: float = 100.0,
            down_for: float = 10.0) -> None:
        """A container as inspect shows it; service is tailscale or grange."""
        now = time.time()
        running = state in ("running", "paused")
        self.containers[cid] = {
            "Id": cid, "Name": "/" + name,
            "State": {"Status": state, "Running": running, "Paused": state == "paused",
                      "StartedAt": rfc3339(now - up_for - (0 if running else down_for)),
                      "FinishedAt": "0001-01-01T00:00:00Z" if running else rfc3339(now - down_for),
                      "Health": {"Status": health} if health else None},
            "Config": {"Labels": {"agent-grange.service": service, "com.docker.compose.project": project}},
            "HostConfig": {"NetworkMode": network,
                           "PortBindings": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]} if port else {}},
        }

    def stop(self, cid: str, down_for: float = 5.0) -> None:
        st = self.containers[cid]["State"]
        st.update(Status="exited", Running=False, FinishedAt=rfc3339(time.time() - down_for))

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)
