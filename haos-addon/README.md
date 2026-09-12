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

## API

`POST /api/ingest`

```json
{"events": [{"event_id": "...", "schema_version": 1, "...": "..."}]}
```

The bearer token is configured with `ACTIVITY_WRITE_TOKEN`. Requests are
validated for size, event shape, timestamps, and schema version. Event IDs are
idempotent.

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

```bash
ACTIVITY_WRITE_TOKEN=local-token python -m activity_sink
curl -X POST http://127.0.0.1:8788/api/ingest \
  -H 'Authorization: Bearer local-token' \
  -H 'Content-Type: application/json' \
  -d '{"events":[]}'
```
