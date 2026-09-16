# Heartbeat Threshold Behavior

Operator reference for how `HeartbeatManager` turns a peer's last heartbeat
timestamp into a liveness assessment, and what to configure when tuning for
flaky networks. Written in answer to #921 ("Clarify agent heartbeat timeout
behavior on network partition").

## Scope

This document covers `HeartbeatManager._assess_peer()` in
`beacon_skill/heartbeat.py` — the liveness classification a peer receives
based purely on wall-clock time since its `last_beat`. It does not cover
signature/replay validation at the transport layer (see
[`BEACON_MECHANISM_TEST.md`](BEACON_MECHANISM_TEST.md) for that).

## Actors

- Local agent: runs `HeartbeatManager`, tracks peers in `heartbeats.json`.
- Peer agent: sends periodic signed heartbeat envelopes.
- Operator: configures thresholds in `config["heartbeat"]` for their
  deployment's network characteristics.

## The three configurable values

| Key | Default | Meaning |
|---|---|---|
| `heartbeat.interval_s` (informational, see `DEFAULT_INTERVAL_S`) | `300` (5 min) | How often a healthy agent is expected to send a beat. Not itself enforced by `_assess_peer` — it only affects how quickly age accumulates between beats. |
| `heartbeat.silence_threshold_s` | `900` (15 min) | Age past which a peer flips from `healthy` to `concerning`. |
| `heartbeat.dead_threshold_s` | `3600` (1 hour) | Age past which a peer flips from `concerning` to `presumed_dead`. |

Both thresholds are read fresh on every assessment call from
`self._config.get("heartbeat", {})`, so they can be changed at runtime by
updating the config dict passed into `HeartbeatManager` — no restart-specific
caching to worry about.

`prune_dead()` uses `dead_threshold_s * 3` as its own separate cutoff for
actually deleting a peer record, which is intentionally more generous than
the `presumed_dead` assessment threshold — a peer can sit in `presumed_dead`
for a while and still reappear without losing its history.

## Assessment state machine

`_assess_peer(agent_id)` returns exactly one of:

- `unknown` — no heartbeat record exists yet for this `agent_id`.
- `shutting_down` — the peer's last reported `status` was `"shutting_down"`.
  This check runs **before** the age check, so a peer that cleanly announced
  shutdown is never misreported as `concerning`/`presumed_dead` even if the
  shutdown announcement itself is now old.
- `healthy` — `age_s <= silence_threshold_s`.
- `concerning` — `silence_threshold_s < age_s <= dead_threshold_s`.
- `presumed_dead` — `age_s > dead_threshold_s`.

```text
                shutting_down status?  ── yes ──▶ "shutting_down"
                       │ no
                       ▼
        age ≤ silence_threshold_s?  ── yes ──▶ "healthy"
                       │ no
                       ▼
        age ≤ dead_threshold_s?     ── yes ──▶ "concerning"
                       │ no
                       ▼
                 "presumed_dead"
```

`age_s` is always `now - last_beat`, computed at query time — there is no
separate timer or scheduled state transition; two calls to `peer_status()`
milliseconds apart can return different assessments if a threshold boundary
was crossed in between.

## Answering the original questions

1. **Is there a configurable grace period before an agent is marked
   offline?** Yes — two of them. `silence_threshold_s` is the grace period
   before a peer is even flagged as `concerning` (not yet "offline" in any
   alerting sense), and `dead_threshold_s` is the grace period before it's
   treated as `presumed_dead`. Set both under `config["heartbeat"]`.

2. **Should the protocol distinguish 'network unreachable' from 'agent
   unresponsive' in the beacon log?** Not currently, and this is a real
   limitation worth documenting explicitly: liveness is judged **only** from
   `now - last_beat`, so a DNS resolution spike, a transport outage on the
   *observer's* side, and an actually-crashed peer are indistinguishable —
   all three eventually read as `concerning` then `presumed_dead`. Adding a
   genuine `network_unreachable` / `transport_degraded` state would need
   transport-level evidence (e.g. a failed send/connect on the observer's
   own outbound path) fed into the assessment, which `_assess_peer` does not
   currently receive — it only ever sees the stored peer record. That's a
   separate, larger change than a naming/threshold tweak and is out of scope
   here; flagging it as a known gap so it isn't silently assumed to exist.

3. **Would a `BEACON_GRACE_PERIOD_MS` env var help?** The grace periods
   already exist under `silence_threshold_s` / `dead_threshold_s` in the
   config dict (seconds, not milliseconds). There is currently no
   environment-variable binding for either value — they must be set via the
   `config` passed into `HeartbeatManager`, e.g.:

   ```json
   {
     "heartbeat": {
       "silence_threshold_s": 1800,
       "dead_threshold_s": 7200
     }
   }
   ```

   A new env var would be an alias for the same two numbers unless it also
   introduced the distinct network-partition state described in (2) above.

## Practical tuning note

Operators on flaky/high-latency links (satellite, congested mesh, DNS
resolvers with occasional spikes) who see false `concerning` flags similar
to the one reported in #921 should raise `silence_threshold_s` first — it's
the cheapest lever and only delays the *early* warning, not the eventual
`presumed_dead` classification, which stays governed by `dead_threshold_s`.
