# Activity Sink for Home Assistant OS

Small append-only ingest service for privacy-preserving activity events.
It is designed to run as a Home Assistant add-on and stores events in a
separate SQLite database rather than Home Assistant Recorder.

The service accepts device-scoped write tokens and exposes no query endpoint in
the initial version. Keep analytics and read access on a trusted personal
client.

## Install

The repository root carries a `repository.yaml`, so this installs as a normal
add-on repository rather than by copying files into `/addons` by hand:

1. Settings → Add-ons → Add-on Store → ⋮ → Repositories.
2. Add `https://github.com/hannesdelbeke/activity-telemetry`.
3. Install **Activity Sink** from the new section, set `write_token` in its
   Configuration tab, and start it.

The add-on builds locally on first install; `amd64` and `aarch64` are declared.
Events land in `/data/activity.db`, inside the add-on's own persistent volume.

Point collectors at the machine's IPv4 address rather than `homeassistant.local`.
The hostname resolves to an IPv6 link-local address first and the published port
is IPv4-only, so clients that prefer IPv6 see the connection reset.

## Reading the data

The add-on ships a read-only panel and registers it with Ingress, so it appears
in the Home Assistant sidebar as **Activity** and is authenticated by Home
Assistant rather than by a token of its own.

It shows, per local day and optionally per machine, time spent in each
application, the active/idle split, and the most recent samples. `GET
/api/events` on the same panel returns the day as JSON for scripting.

Durations are derived, not recorded. Each event says only which application was
in front at one instant, so a sample is charged the gap until the next sample,
capped at three times the median gap for that machine. An outage therefore
costs the report accuracy but never invents hours that were not worked.

The panel listens on `ingress_port` 8099, which `config.yaml` deliberately does
not publish: it is reachable from the Supervisor network only. The ingest port
serves no read endpoint, so a leaked device write token still cannot read
history back out.

## API

`POST /api/ingest`

```json
{"events": [{"event_id": "...", "schema_version": 1, "...": "..."}]}
```

The bearer token is the add-on's `write_token` option, or `ACTIVITY_WRITE_TOKEN`
outside Home Assistant, and is compared in constant time. Requests are validated
for size, event shape, timestamps, and schema version. Event IDs are idempotent.

| Status | Meaning |
| --- | --- |
| 202 | Batch stored; `accepted` counts the events in it |
| 400 | Malformed body, unparseable JSON, or an event that failed validation |
| 401 | Missing, wrong, or unconfigured write token |
| 413 | Body over 256 KiB |
| 503 | Could not write to SQLite; the collector keeps the batch and retries |

`GET /health`

Returns a minimal health response without event data.

## Transport

The service itself speaks plain HTTP on `0.0.0.0:8788`. It terminates no TLS,
so the bearer token and every event cross the network in the clear, and it is
only safe as-is on a trusted LAN.

For anything else, put TLS in front of it rather than in it: publish it through
the Home Assistant reverse proxy or an add-on such as NGINX Proxy Manager, keep
port 8788 unpublished on the host, and point `ACTIVITY_INGEST_URL` on each
collector at the `https://` address the proxy serves.

## Privacy

This service stores only the event payload sent by clients. Do not send
keystrokes, window titles, URLs, screen contents, notifications, message
contents, personal names, employer names, credentials, or health data.

Use separate write tokens per device and rotate them if a device is lost.
Back up and delete the SQLite database according to an explicit retention
policy.

## Local development

`ACTIVITY_DB` matters here: the default is `/data/activity.db`, which only
exists inside the add-on container and fails with a permission error anywhere
else.

```bash
ACTIVITY_WRITE_TOKEN=local-token ACTIVITY_DB=./activity.db python src/activity_sink.py
curl -X POST http://127.0.0.1:8788/api/ingest \
  -H 'Authorization: Bearer local-token' \
  -H 'Content-Type: application/json' \
  -d '{"events":[]}'
```

Tests, from the repository root:

```bash
python -m pytest haos-addon/tests collector/tests
```
