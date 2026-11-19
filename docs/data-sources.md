# What the communication plane can tell a viewer

A survey for agent-grange-viz: what the two agent-grange containers expose today, what the
grange already holds and could expose, and what a Mac-side viewer can actually reach.

The tailscale facts below were checked by hand on 2026-11-18. The check ran against
agent-grange's local headscale stack (`docker/compose.headscale.yml`: headscale 0.29.7, three
`tailscale/tailscale:v1.104.7` sidecars, each with a grange in its namespace), called as the
grange's own non-root user (uid 10001) through the shared socket. Code references are to the
`agent-grange` implementer worktree (`docker/content/grange/`) at `bf6e87e`.

## 1. Where a viewer on the Mac sits

```
 macOS                         Docker Desktop VM
 ─────                         ──────────────────────────────────────────────────────────
 agent-grange-viz ──────────▶ 127.0.0.1:7788 ─▶ sidecar netns :8080 ─▶ grange local API
      │                                         │
      │                                         ├─ 127.0.0.1:9002   /healthz   (sidecar netns only)
      │                                         ├─ tailscaled.sock  LocalAPI   (shared volume; grange reads it)
      │                                         └─ 100.64.x.y:7789  gossip     (tailnet only, never published)
      └──── ~/.docker/run/docker.sock ─▶ Docker Engine API (container state, health log, logs, stats, exec)
```

From macOS, two things are reachable:

- **The grange's local API** on `127.0.0.1:7788`. The sidecar publishes the port, and the
  grange answers it from inside the sidecar's network namespace.
- **The Docker Engine socket.** `docker/tools/query_server.py` already uses it to find the
  sidecar by label.

Three things are not reachable from macOS. The tailscaled LocalAPI socket lives in a Docker
volume inside the VM. `:9002` is bound to loopback inside the sidecar's namespace. The gossip
port exists only on the tailnet. **Anything about the tailnet has to be relayed by the grange,
which already holds the socket. The alternative is to go around the grange through
`docker exec`.**

## 2. The tailscale sidecar: what it exposes

| surface | reachable by | what it serves |
|---|---|---|
| `127.0.0.1:7788 → :8080` | the Mac | not tailscale's own: this is the grange's local API (section 3) |
| `127.0.0.1:9002` (`TS_LOCAL_ADDR_PORT`) | the sidecar's namespace, so the grange too | `/healthz` → `ok` once it has a tailnet IP. `/metrics` → 404 (`TS_ENABLE_METRICS` is not set) |
| `tailscaled.sock` LocalAPI | the grange (volume, mode 0666) | below |
| tailnet `100.64.x.y` | tagged peers, `tcp:7789` only (ACL) | gossip, which is the grange's. Peerapi ports are advertised but outside the ACL |
| Docker label `agent-grange.service=tailscale` | the Mac, via Docker | container state, health log |

**LocalAPI, read permission (all answered 200 to uid 10001):**

| route | what is in it that matters for the plane |
|---|---|
| `GET /localapi/v0/status` | `Self` and `Peer{}`: `HostName`, `TailscaleIPs`, `Tags`, `Online`, `Active`, **`CurAddr`** (the direct UDP endpoint; empty when relayed), **`Relay`** (DERP region code), `PeerRelay`, **`RxBytes`/`TxBytes`** per peer, **`LastHandshake`**, `LastWrite`, `LastSeen`, `Created`, `KeyExpiry` (null: tagged nodes do not expire), `InNetworkMap`/`InMagicSock`/`InEngine`, `PeerAPIURL`. Top level: `BackendState`, **`Health[]`**, `Version`, `TUN`, `CurrentTailnet` |
| `GET /localapi/v0/whois?addr=IP` | `Node`: `Endpoints`, `HomeDERP`, `Hostinfo` (client version, OS, container), `Tags`, `Online`, `MachineAuthorized`; `UserProfile` (`tagged-devices`). The grange uses this for R5.13 |
| `POST /localapi/v0/ping?ip=IP&type=disco\|TSMP` | **`LatencySeconds`**, `Endpoint` when direct, or `DERPRegionID`/`DERPRegionCode` when relayed, `PeerAPIPort`. A disco ping took 0.16 ms between lab containers |
| `GET /localapi/v0/usermetrics` | Prometheus text: `tailscaled_{inbound,outbound}_{bytes,packets}_total{path=direct_ipv4\|direct_ipv6\|derp\|peer_relay_ipv4\|peer_relay_ipv6}`, `tailscaled_outbound_dropped_packets_total{reason}`, `tailscaled_health_messages{type}`, `tailscaled_home_derp_region_id` |
| `GET /localapi/v0/watch-ipn-bus?mask=N` | A stream of JSON lines: `State`, `Health` warnings and, with engine updates (mask bit 1), roughly every 2 s: `RBytes`, `WBytes`, `NumLive`, `LiveDERPs`, `LivePeers{nodekey: TxBytes, RxBytes, LastHandshake}` |
| `GET /localapi/v0/debug-peer-endpoint-changes?ip=IP` | The history of one peer's endpoint and best-address changes: when a path flipped between direct and DERP |
| `GET /localapi/v0/derpmap`, `/prefs`, `/tka/status`, `/serve-config` | Relay regions (the embedded relay: region 999), control URL, and so on: mostly static |

**Refused to a non-root caller (403):** `/metrics` (client metrics), `/debug-derp-region`,
`/goroutines`, `/logtap`, `/dns-osconfig`, `/check-prefs`. There is no `/netcheck` route (404):
netcheck runs in the CLI, not the daemon.

Per peer, the sidecar therefore answers four questions: is it online (as headscale sees it),
is the path direct or relayed, how fast is it, and how many bytes have crossed it. Each
answer can disagree with what gossip sees, and the disagreement is often where the problem
is.

## 3. The grange: what it exposes today

| route | contents |
|---|---|
| `GET /v1/agents[?after&wait]` | `cursor`, `grange`, `incarnation`, and `granges[]`, each with `grange`, `seen_age_s`, `farm_age_s`, `farm_stale` and `agents[]` (owner, name, state). This is frolic's feed |
| `GET /health` | `status`, `grange`, `freshness{last_polled, last_summary, recheck_seconds, polls, consecutive_unchanged_polls}`, `cursor`, `incarnation`, `peers_discovered`, `peers_live`, `last_gossip_age_s`, `rejected{proto, group, identity, overflow}`, `duplicate_id` |
| `GET /version` | `repo`, `branch`, `commit`, `dirty` |
| `PUT /v1/farm` | farm's input. A viewer must never call it |
| every route while stale | 503 with `built_commit`, `tag`, `tag_commit`, `fix`, `checked` |
| stderr (`docker logs`) | startup and repair counts. Also each of these, logged once per peer: refusals, rebinds, hostname disagreements, incarnation corrections, duplicate-id. Plus resume gaps, leaving, and freshness flips |
| Docker health log | why `/health` failed, printed by `healthcheck.py` |

For each peer, today's API answers whether it is in the picture and how stale it is. It says
nothing about why. The live set, neighbours, failed marks, per-exchange outcomes and the
tailnet path are all invisible.

## 4. What the grange holds but does not expose

| module | state that already exists |
|---|---|
| `gossip.Core` | `discovered{addr: online hint}`, `binding{addr: id}`, the `failed` set, the live set, `neighbours` (successor first), `sent{push, repair, probe}`, `outstanding`, `startup_pending/ok`, `stale`, `leaving`, next heartbeat/repair/discovery deadlines; `discovery.hostnames{addr: HostName}` |
| `state.Store` | per peer: body (`incarnation`, `rev`, `counter`, `farm_stale`, `leaving`, agent count), stored liveness counter, `seen_at`. The forget map `{id: ((inc, counter), removed_at)}`. Own `rev`, `counter`, `incarnation`, `last_push_at`. `corrections`, `duplicate_source`, `overflow`. Events `LOCAL` and `FORWARD`, **with the address each body came from** |
| `api_gossip.GossipApp` | per incoming request: source, push or repair, refusal kind and status; `take_push` returns each body's outcome (`accepted` / `refreshed` / `ignored` / `own` / `overflow`) |
| `runtime.HttpTransport` | per outgoing exchange: address, status, request bytes, reply bytes, elapsed time |
| `ticker.Ticker` | `resumes`, the gap, the ids that expired |
| `freshness_watch` | verdict, summary, poll counts |
| local API | farm pushes (time, count, content change or not), long-polls held on the cursor |

Two more things fall out of the design and cost nothing to compute:

- **The whole overlay is computable on one Mac.** R6.10's neighbour choice is a pure function
  of a grange's id, its address and the live set. With the bindings any grange holds, the
  viewer can recompute every grange's expected neighbours, not only its own. This is an
  estimate: each grange's live set may differ briefly.
- **Repair digests reveal what the other side holds.** Each repair response carries the
  peer's full digest. Kept for the latest repair, it says which peers already hold this
  grange's newest rev, which gives a reach curve for its own changes against R6.14's 5 s.

## 5. Proposal: read-only mesh routes on the grange's local API

**The full specification is [`openapi.yaml`](openapi.yaml)** (OpenAPI 3.1). It documents the
routes that exist today exactly as the code serves them, and specifies three to implement:
`GET /v1/mesh`, `GET /v1/mesh/events` and, optionally, `POST /v1/mesh/ping`. Every proposed
field there carries `x-source`, naming where the grange already holds the value, and
`x-new: true` where it needs new bookkeeping. What follows is the short version; where the
two differ, the specification is right.

This has to be a grange change. Section 1 of agent-grange's requirements lists "Observability
is a later, separate app" as a non-goal for v1, so it would need a new R5.x. It keeps the
grange's rules: loopback only, JSON, nothing on disk, no new field crosses the tailnet, and no
protocol change (`proto` stays 1). Agents appear as a count only.

**`GET /v1/mesh`**: a snapshot, answered at once.

```json
{
  "grange": "djp3", "address": "100.64.0.5:7789", "group": "default", "proto": 1,
  "incarnation": 1795040341941, "rev": 14, "counter": 912, "cursor": 381, "uptime_s": 5321.4,
  "image": {"commit": "bf6e87e…", "tag": "agent-grange-latest", "tag_commit": "bf6e87e…",
            "verdict": "fresh"},
  "freshness": {"last_polled": "2026-11-18T22:19:41+00:00", "last_summary": "…",
                "recheck_seconds": 30.0, "polls": 176, "consecutive_unchanged_polls": 175},
  "farm": {"last_push_age_s": 7.2, "pushes": 180, "stale": false, "agents": 6},
  "local_api": {"long_polls": 1},
  "gossip": {"sent": {"push": 812, "repair": 1774, "probe": 59}, "outstanding": 0,
             "neighbours": ["100.64.0.6:7789", "..."], "discovered": 13, "live": 11,
             "last_ok_age_s": 0.8, "discovery_age_s": 12.0, "resumes": 0, "corrections": 0},
  "duplicate_id": false, "duplicate_source": null,
  "peers": [{
    "address": "100.64.0.6:7789", "id": "alice", "hostname": "grange-alice",
    "hint": true, "failed": false, "role": "successor",
    "last_ok_age_s": 0.8, "last_fail_age_s": null, "last_status": 204,
    "last_fail_status": null, "last_error": "", "fails": 0,
    "sent": {"push": 61, "repair": 40, "probe": 0}, "recv": {"push": 58, "repair": 37},
    "bytes_out": 74120, "bytes_in": 69311, "lag": 0,
    "record": {"incarnation": 1795040000047, "rev": 9, "counter": 530, "seen_age_s": 0.8,
               "farm_age_s": 12.0, "farm_stale": false, "leaving": false, "agents": 5,
               "held_for_s": 5318.0, "admitted": "first"},
    "tailnet": {"online": true, "active": true, "cur_addr": "192.168.1.26:41641",
                "relay": "headscale", "rx": 81233, "tx": 86010, "last_handshake_age_s": 61.0,
                "latency_ms": 2.9, "latency_age_s": 3.0}
  }],
  "forgotten": [{"id": "frank", "removed_age_s": 30.2, "why": "expired", "forget_left_s": 569.8,
                 "incarnation": 1795040000091, "counter": 418}],
  "rejected": {"proto": 0, "group": 9, "identity": 1, "overflow": 0},
  "refusals": [{"source": "100.64.0.40", "address": null, "kind": "identity", "status": 403,
                "count": 1, "last_age_s": 15.0, "note": "source device does not carry the grange tag"}],
  "tailnet": {"state": "ok", "state_age_s": 5320.0, "reachable": true, "unreachable_for_s": null,
              "status_age_s": 2.1, "backend": "Running", "version": "1.104.7",
              "home_derp": "headscale", "home_derp_id": 999, "derp_latency_ms": null,
              "health": [], "bytes": {"direct": 1882113, "derp": 20211}}
}
```

Reading it:

- `id` is null until an exchange has named who answers at the address (unbound).
- `last_status` is the status of the most recent exchange, success or failure, and null when
  it got no answer. `last_fail_status` is the status of the last failure, null for a timeout.
- `record` is null when no unexpired record is held for the id; `forgotten` then says why.
  `admitted` says how the record came to be held: `first`, `restart` or `readmit`.
- In a peer's `tailnet`, with `active` true a non-empty `cur_addr` is a direct path and an
  empty one is relayed through DERP. `latency_ms` needs the grange to ping, so it may be null.
- The top-level `tailnet` block is the sidecar itself. `reachable: false` (`state:
  "no-socket"`) is the one state only the grange can report: it cannot reach the daemon's
  socket, so discovery is frozen and every incoming exchange fails the whois check.
- `refusals` with `address: null` are sources that are no discovered address.

**`GET /v1/mesh/events?after=<seq>&wait=30`**: a long-polled ring buffer, about 2000
entries in memory. It uses the same waiting rule as `/v1/agents` (only `after` equal to the
newest `seq` waits), and a restart starts the sequence again.

```json
{"seq": 90211, "first_seq": 88212, "incarnation": 1795040341941, "events": [
  {"seq": 90210, "age_s": 0.4, "kind": "push-in", "peer": "alice", "ok": true, "status": 204,
   "out_b": 0, "in_b": 378, "rtt_ms": null, "bodies": [["carol", 3, "accepted"]], "note": ""},
  {"seq": 90211, "age_s": 0.4, "kind": "push-out", "peer": "erin", "ok": true, "status": 204,
   "out_b": 378, "in_b": 0, "rtt_ms": 2.6, "bodies": [["carol", 3, "forward"]], "note": ""}
]}
```

Event kinds: `push-out`, `push-in`, `repair-out`, `repair-in`, `probe`, `refused`, `mark`,
`neighbours`, `discover`, `join`, `expire`, `leave`, `readmit`, `restart`, `farm`,
`farm-stale`, `path`, `fresh`, `resume`, `sidecar`. A body's outcome is one of `accepted`,
`refreshed`, `ignored`, `own`, `overflow` when received, and `change`, `forward`, `followup`,
`leaving` when sent.

**`POST /v1/mesh/ping?peer=<id or address>`**, optional: one disco ping through the sidecar on
demand, answering the latency and whether the pong came back direct or through DERP.

**Tailnet fields** come from the sidecar socket the grange already reads: `status` per peer,
`usermetrics` for direct-vs-DERP bytes, and `ping` for latency. The specification asks the
grange to read `status` and `usermetrics` on a timer of their own (every 5 s) and serve the
cached result, since discovery's 30 s is too old for byte counters and handshake ages.

**What is not free.** Four values the specification flags with `x-cost`:

- Per-peer `latency_ms` needs active pings; a grange may serve null.
- `derp_latency_ms` has no read-permitted LocalAPI route behind it; serve null.
- `last_error` needs the reply body of a refused request parsed instead of discarded.
- The `no-control` sidecar state is best effort, since `status` gives health warnings as text.

**Exposure.** These routes reveal the team's tailnet addresses and topology to any process on
the Mac. That is the same trust level R9.9 already accepts for `/v1/agents`, which reveals the
whole team's status.

**Without changing the grange,** a viewer can work today from the grange's existing routes
and the Docker socket: `docker exec <sidecar> tailscale status --json` and `tailscale ping`
for the tailnet, `/health` and `/v1/agents` for the rest. That is what agent-grange-viz does
until `/v1/mesh` exists. It sees nothing of per-peer gossip (the live set, neighbours, marks,
exchange outcomes), nor rev and counter, and it cannot tell the "no socket" state.

## 6. What the viewer leaves to the other apps

| app | owns | the viewer shows instead |
|---|---|---|
| agent-farm | each session's state, subagents, shells, plan usage | nothing about sessions |
| agent-frolic | agents on pull requests, who each PR waits on | nothing about PRs |
| agent-grange | the board JSON: agents by grange | an agent **count** per grange at most |

Whatever the viewer shows is about the plane: who is reachable, by what path, how fresh each
record is, what was exchanged, what was refused, and whether changes travel within the
targets (R6.14: 5 s; R6.15: 10 KB/s).

## 7. What one Mac can and cannot know

It knows its own grange's exchanges, records and marks, and its own sidecar's paths. It does
not see exchanges between two other granges. The viewer respects that. Two things it could
still work out are estimates and would be labelled so: the overlay recomputed from R6.10, and
reach confirmed through repair digests. The demo's simulation (`demosim.py`) plays the real
rules (neighbour choice, forward-once, repair cadence, marks, expiry, forget map), but every
name, address and number in it is invented, and `demo.py` passes on only what a grange and its
sidecar could know.
