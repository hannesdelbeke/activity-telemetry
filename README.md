# Activity Telemetry

Privacy-preserving activity telemetry for personal devices.

This repository contains two separately deployable components:

- [`collector/`](collector/) — cross-platform Python collector for Linux and
  Windows PCs, with a shared protocol and local SQLite spool.
- [`haos-addon/`](haos-addon/) — append-only HTTPS ingest service for
  Home Assistant OS.

They live together because they must evolve against the same event schema,
batch contract, idempotency behavior, and privacy rules. They remain separate
packages so either component can be deployed, versioned, or replaced
independently later. Split into separate repositories only if release cadence,
ownership, or access-control requirements diverge.

## Privacy boundary

The project intentionally excludes keystrokes, window titles, document names,
URLs, screen contents, notifications, message contents, personal names,
employer names, credentials, and health data. Events should contain only
coarse activity state and allowlisted application identifiers.

Use generic device labels and device-scoped write tokens. Keep analytics and
read access outside the write-only client path.

## Protocol

The collector sends bounded batches to `POST /api/ingest`. The sink validates
schema version, timestamps, required fields, and event IDs, then stores events
idempotently in SQLite. See the component READMEs for local setup.
