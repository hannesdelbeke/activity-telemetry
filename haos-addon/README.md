# Activity Sink for Home Assistant OS

Small append-only HTTPS ingest service for privacy-preserving activity events.
It is designed to run as a Home Assistant add-on and stores events in a
separate SQLite database rather than Home Assistant Recorder.

The service accepts device-scoped write tokens and exposes no query endpoint in
the initial version. Keep analytics and read access on a trusted personal
client.

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
