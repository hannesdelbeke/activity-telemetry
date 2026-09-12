# Instructions for agents working on this repository

Read this before changing anything. It is written for an agent working directly
on the Linux machine, where most of what goes wrong here goes wrong.

## What this project is

Two deployables that share one event schema:

- `collector/` — Python daemon on each PC. Samples the focused app and an
  activity state every 30s, spools to local SQLite, POSTs batches to the sink.
- `haos-addon/` — Home Assistant add-on. Accepts those batches, stores them,
  and renders a summary and a timeline.

## Rules that are not negotiable

**Never collect window titles, document names, URLs, keystrokes, screen
contents, message contents or credentials.** Only a coarse activity state and
an application identifier (`org.mozilla.firefox.desktop`, `Safari`). A test in
`collector/tests/test_gnome_extension.py` enforces this against the extension
source; do not weaken it to make something else pass.

**Commit as `claude`, and push straight to `main`. No pull request.**

```bash
git -c user.name=claude -c user.email=noreply@anthropic.com commit -m "..."
```

End every commit message with:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

Stage only the files you touched. Never `git add -A`.

**Never print a token.** The sink write token and the Home Assistant
long-lived token live in `~/.config/activity-collector/env` and
`~/.config/ha/env`, both `chmod 600`. Read them from there; do not echo them,
do not put them in a commit, do not include them in a summary.

## Running the tests

One venv at the repo root serves both packages. Always run the whole file's
suite from the package directory — running a single test file directly fails
with `ModuleNotFoundError: activity_collector`, because the path setup lives in
the package's pytest config.

```bash
cd collector   && ../.venv/bin/pytest tests/ -q     # 69 tests
cd haos-addon  && ../.venv/bin/pytest tests/ -q     # 37 tests
```

If a test fails in a way you cannot explain, delete stale bytecode before
believing it: `find . -name __pycache__ -type d -exec rm -rf {} +`. A restored
file with a stale `.pyc` has already wasted time here once.

### The bar for a new test

A test that passes against the bug it is meant to catch is worse than no test,
because it certifies the bug. Before accepting one: break the code it covers,
run the suite, confirm that test fails, then restore. If it did not fail, the
test does not test what you think.

## Diagnosing the Linux collector

Start here. It prints one sample and says which idle probe produced it, and
exits non-zero when none could:

```bash
cd ~/…/activity-telemetry/collector
PYTHONPATH=src python3 -m activity_collector --diagnose
```

Read the `idle source` line. It is the whole point of the command.

| `idle source` | meaning |
| --- | --- |
| `gnome-extension` | working, Wayland or X11 |
| `x11-screensaver` / `xprintidle` | working, X11 only |
| `mutter-idlemonitor` | last resort; often present but refuses on a locked session |
| `none` | nothing can measure idle; state is reported as `unknown` |

`app` reading `unknown` on Wayland while `idle source` is also `none` means the
GNOME extension is not running:

```bash
gnome-extensions list --enabled | grep activity-collector
journalctl --user -b /usr/bin/gnome-shell | grep -i activitycollector
```

Install it with `sh collector/linux/install-gnome-extension.sh`, then **log out
and back in** — Wayland cannot reload extensions in place, and Alt+F2 `r` is an
X11-only trick.

### Service control

```bash
systemctl --user status  activity-collector
systemctl --user restart activity-collector
journalctl --user -u activity-collector -n 50 --no-pager
```

Config is `~/.config/activity-collector/env`, unit is
`~/.config/systemd/user/activity-collector.service`.

### Reading the spool directly

```bash
sqlite3 ~/.cache/activity-collector/events.db \
  "SELECT json_extract(payload_json,'\$.data.activity_state') AS state, COUNT(*)
     FROM events GROUP BY state;"
```

Rows with `synced_at IS NULL` have not reached the sink yet. That is normal
between flushes and expected while the sink is down; the spool exists so an
outage costs nothing.

## Things that are true and cost time to rediscover

**Never report a state you do not know.** `_activity_state()` returns
`"unknown"` when every probe declines. It used to return `"active"`, and the
one situation that default covered was the situation where nothing was known —
so walking away from the machine recorded a full working day. If you find
yourself adding a fallback that guesses, you are reintroducing this.

**0ms idle is a real reading**, not a missing one. It is what the monitor
reports the instant after a keypress. The extension signals "cannot tell" by
throwing a D-Bus error, never by returning 0. Keep those distinct at both ends.

**GNOME disables extensions on the lock screen.** The extension's bus name
vanishes exactly when the screen locks, so it cannot report the lock. Locking
also resets the idle timer, so the machine looks freshly active. `snapshot()`
therefore checks `org.gnome.ScreenSaver` first — that name is owned by
gnome-shell itself and survives the lock.

## Deploying the Home Assistant add-on

The mechanism is unusual and guessing at it wastes an afternoon:

1. `repository.yaml` at the repo root makes this an **add-on repository**. It
   tracks the **default branch**. Tags and GitHub releases are never consulted.
   Creating a release does nothing.
2. Home Assistant decides an update exists **only** by comparing `version:` in
   `haos-addon/config.yaml` against the installed version. Push without
   bumping it and no update is ever offered.
3. The Supervisor caches its clone. After pushing, reload the store
   (Settings → Add-ons → ⋮ → Check for updates) or it will not see the commit.
4. **Rebuild is not update.** Rebuild rebuilds the version already installed.

So: bump `version:` in `haos-addon/config.yaml` in the same commit as the
change, push to `main`, reload the store, then update.

The Supervisor REST proxy at `/api/hassio/*` rejects long-lived tokens. The
websocket `supervisor/api` command accepts the same token and reaches the same
endpoints. Result shapes are inconsistent — unwrap with
`d = r.get("result", {}); d = d.get("data", d)`. A long build outlives the
response, so the rpc result is not the truth: poll `/addons/<slug>/info` until
`version` matches `version_latest` and `state` is `started`.

### Ingress

The panel is mounted under a generated prefix, so **every href must be
relative**. A leading `/` escapes the prefix and hits Home Assistant's own
endpoints. Ingress authenticates with an `ingress_session` cookie from
`POST /ingress/session`, not a bearer token.

### The SSH add-on cannot see the sink's data

With protection mode on it has no `docker`, an empty `/mnt`, and no access to
another add-on's `/data`. Do not plan a fix that requires reaching the database
from there; add the operation to the sink itself, on the Ingress side, which
Home Assistant authenticates and `config.yaml` never publishes to the LAN. The
ingest port stays append-only on purpose — a leaked device write token must
never be able to delete anyone's events.

## Rendering the timeline

At a 30s sample interval one bar is 0.035% of a day, which is a quarter of a
pixel on a 700px track. Hundreds of them antialias into a grey wash. `_merge()`
fuses touching bars that share a state into one run, which is why the page is
5KB rather than 53KB. Only *touching* bars merge: a gap the cap refused to
charge stays a gap, because that hole is an outage and painting over it would
be indistinguishable from the machine having been busy through it.
