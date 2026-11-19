# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Docker side: the parsers against output captured from tailscale v1.104.7, and finding
and asking the right two containers through a stand-in Engine."""
import time

import pytest

from sidecar import (DockerError, Engine, Sidecar, demux, find_socket, parse_duration, parse_metrics, parse_ping,
                     parse_status, parse_time)
from standins import METRICS, FakeDocker, frame, rfc3339, status_json


# ---- parsers ---------------------------------------------------------------------------------------
def test_parse_time_reads_nanoseconds_and_knows_never():
    assert parse_time("2026-11-19T01:03:50.482777419Z") == pytest.approx(1795050230.482777, abs=1e-3)
    assert parse_time("2026-11-19T01:03:50Z") == pytest.approx(1795050230.0)
    assert parse_time("2026-11-19T02:03:50.5+01:00") == pytest.approx(1795050230.5)
    assert parse_time("0001-01-01T00:00:00Z") is None
    assert parse_time("") is None and parse_time("yesterday") is None


def test_demux_separates_the_two_streams():
    raw = frame(1, b"hello ") + frame(2, b"oops") + frame(1, b"world")
    assert demux(raw) == (b"hello world", b"oops")
    assert demux(b"") == (b"", b"")
    assert demux(b"plain text from a tty\n") == (b"plain text from a tty\n", b"")
    assert demux(frame(1, b"cut")[:-1]) == (frame(1, b"cut")[:-1], b"")       # a broken frame is not guessed at


def test_parse_duration_reads_what_go_prints():
    assert parse_duration("0s") == 0.0
    assert parse_duration("12ms") == pytest.approx(0.012)
    assert parse_duration("1.5s") == pytest.approx(1.5)
    assert parse_duration("250µs") == pytest.approx(0.00025)
    assert parse_duration("1m2s") == pytest.approx(62.0)
    assert parse_duration("soon") is None


def test_parse_ping_reads_both_paths():
    direct = parse_ping("pong from grange-b (100.64.0.2) via 172.29.207.3:55569 in 4ms\n")
    assert (direct.rtt_ms, direct.endpoint, direct.derp) == (4.0, "172.29.207.3:55569", "")
    relayed = parse_ping("pong from grange-ivan (100.64.0.8) via DERP(headscale) in 31ms\n")
    assert (relayed.rtt_ms, relayed.derp, relayed.endpoint) == (31.0, "headscale", "")
    assert parse_ping("pong from grange-b (100.64.0.2) via 172.29.207.3:55569 in 0s").rtt_ms == 0.0
    assert parse_ping('ping "100.64.0.99" timed out\n') is None
    assert parse_ping("") is None


def test_parse_metrics_adds_both_directions_by_path():
    assert parse_metrics(METRICS) == {"derp": 2000, "direct": 102000}
    assert parse_metrics("unknown command") == {}


def test_parse_status_keeps_tagged_peers_with_an_ipv4():
    import json
    now = 1795050000.0
    raw = json.loads(status_json(peers=[
        {"host": "grange-alice", "ip": "100.64.0.6", "cur_addr": "192.168.1.26:41641", "handshake": now, "rx": 10, "tx": 20},
        {"host": "grange-ivan", "ip": "100.64.0.8", "relay": "nyc"},
        {"host": "laptop-two", "ip": "100.64.0.40", "tags": []},
    ], health=["not connected to the coordination server"]))
    raw["Peer"]["nodekey:9"] = {"HostName": "grange-six", "TailscaleIPs": ["fd7a::9"], "Tags": ["tag:grange"]}
    st = parse_status(raw, "tag:grange")
    assert st["backend"] == "Running" and st["health"] == ["not connected to the coordination server"]
    assert st["self"] == {"hostname": "grange-me", "ip": "100.64.0.5", "relay": "hs", "online": True,
                          "tags": ["tag:grange"]}
    assert [p["hostname"] for p in st["peers"]] == ["grange-alice", "grange-ivan"]
    alice, ivan = st["peers"]
    assert alice["cur_addr"] == "192.168.1.26:41641" and alice["handshake"] == pytest.approx(now)
    assert (alice["rx"], alice["tx"]) == (10, 20)
    assert ivan["cur_addr"] == "" and ivan["relay"] == "nyc" and ivan["handshake"] is None


# ---- the Engine and the two containers -------------------------------------------------------------
@pytest.fixture
def docker():
    d = FakeDocker()
    yield d
    d.close()


def three_pairs(d: FakeDocker) -> None:
    """The headscale test stack: three sidecars, each with a grange in its namespace."""
    for n, port in (("a", 17981), ("b", 17982), ("c", 17983)):
        d.add(f"ts{n}", f"lab-ts-{n}-1", "tailscale", port=port, project="lab", up_for=120)
        d.add(f"gr{n}", f"lab-grange-{n}-1", "grange", network=f"container:ts{n}", project="lab")


def test_find_socket_prefers_what_it_is_told(monkeypatch, tmp_path):
    assert find_socket("/somewhere/docker.sock") == "/somewhere/docker.sock"
    monkeypatch.setenv("DOCKER_HOST", "unix:///elsewhere/docker.sock")
    assert find_socket("") == "/elsewhere/docker.sock"


def test_picks_the_sidecar_that_publishes_the_port_and_its_grange(docker):
    three_pairs(docker)
    side = Sidecar(port=17982, socket_path=docker.path)
    info = side.containers()
    assert info.available and side.reason == ""
    assert (info.sidecar, info.sidecar_state) == ("lab-ts-b-1", "running")
    assert info.sidecar_for == pytest.approx(120, abs=3)
    assert (info.grange, info.grange_state, info.grange_health) == ("lab-grange-b-1", "running", "healthy")


def test_a_stopped_sidecar_is_still_found_and_says_for_how_long(docker):
    three_pairs(docker)
    docker.stop("tsa", down_for=12)
    side = Sidecar(port=17981, socket_path=docker.path)
    info = side.containers()
    assert (info.sidecar, info.sidecar_state) == ("lab-ts-a-1", "exited")
    assert info.sidecar_for == pytest.approx(12, abs=2)
    assert info.grange_state == "running"                  # the grange outlives its network
    assert side.status() is None and "not running" in side.reason


def test_a_recreated_sidecar_finds_its_grange_by_project(docker):
    docker.add("new", "agent-grange-tailscale-1", "tailscale", port=7788)
    docker.add("gr", "agent-grange-grange-1", "grange", network="container:old")
    info = Sidecar(port=7788, socket_path=docker.path).containers()
    assert info.grange == "agent-grange-grange-1"


def test_nobody_publishing_the_port_is_said_plainly(docker):
    three_pairs(docker)
    side = Sidecar(port=7788, socket_path=docker.path)
    info = side.containers()
    assert info.available and info.sidecar == ""
    assert "publishes port 7788" in side.reason and "3 other" in side.reason
    assert side.status() is None and side.ping("100.64.0.2") is None and side.path_bytes() == {}


def test_no_docker_is_a_state_not_an_exception(tmp_path):
    side = Sidecar(port=7788, socket_path=str(tmp_path / "no.sock"))
    info = side.containers()
    assert not info.available and "Docker does not answer" in side.reason
    assert side.status() is None and side.ping("100.64.0.2") is None and side.path_bytes() == {}


def test_a_docker_that_refuses_is_a_state_too(docker):
    three_pairs(docker)
    docker.refuse = True
    side = Sidecar(port=17981, socket_path=docker.path)
    assert not side.containers().available and "500" in side.reason


def test_asks_the_sidecar_with_tailscale(docker):
    three_pairs(docker)
    now = time.time()
    docker.script[("tailscale", "status", "--json")] = (status_json(peers=[
        {"host": "grange-b", "ip": "100.64.0.2", "cur_addr": "172.29.207.3:55569", "handshake": now - 30}]), "", 0)
    docker.script[("tailscale", "ping")] = lambda cmd: (
        f"pong from grange-b ({cmd[-1]}) via 172.29.207.3:55569 in 3ms\n", "", 0)
    docker.script[("tailscale", "metrics", "print")] = (METRICS, "", 0)
    side = Sidecar(port=17981, socket_path=docker.path)
    st = side.status()
    assert st["peers"][0]["hostname"] == "grange-b" and st["peers"][0]["handshake"] == pytest.approx(now - 30, abs=1)
    assert side.ping("100.64.0.2").rtt_ms == 3.0
    assert side.path_bytes() == {"derp": 2000, "direct": 102000}
    ran = [cmd for cid, cmd in docker.execs.values()]
    assert all(cid == "tsa" for cid, _ in docker.execs.values())          # only ever in its own sidecar
    assert ["tailscale", "ping", "--c", "1", "--until-direct=false", "--timeout", "2s", "100.64.0.2"] in ran


def test_a_ping_that_times_out_and_a_tailscale_without_metrics(docker):
    three_pairs(docker)
    docker.script[("tailscale", "ping")] = ('ping "100.64.0.9" timed out\n', "", 1)
    docker.script[("tailscale", "metrics")] = ("", "unknown subcommand: metrics", 1)
    docker.script[("tailscale", "status")] = ("not json", "", 0)
    side = Sidecar(port=17981, socket_path=docker.path)
    assert side.ping("100.64.0.9") is None
    assert side.path_bytes() == {}
    assert side.status() is None and "JSON" in side.reason


def test_the_engine_reports_what_went_wrong(docker):
    engine = Engine(docker.path)
    with pytest.raises(DockerError):
        engine.inspect("nobody")
    three_pairs(docker)
    docker.stop("tsc")
    with pytest.raises(DockerError):
        engine.exec("tsc", ["tailscale", "status"])
    assert rfc3339(0).startswith("1970-01-01T00:00:00")
