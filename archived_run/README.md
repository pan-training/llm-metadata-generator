# Archived run log exports

This directory stores export snapshots of **archived API runs** (sessions written to the `sessions` table).

## Create an export

1. Log in as an admin user in the session viewer.
2. Open `/archived-runs`.
3. Click **Export current archived-run logs**.

An export directory is created under `archived_run/results/`:

```
archived_run/results/archived_runs__<timestamp>/
└── sessions.json
```

The `sessions.json` file includes all session rows (status, URL, timestamps, logs, and JSON-LD result) at export time.

## Sharing exports

Commit exports you want to share for debugging:

```bash
git add archived_run/results/
git commit -m "Archived run log export: <context>"
git push
```
