# Normal run log exports

This directory stores export snapshots of **normal API runs** (sessions written to the `sessions` table).

## Create an export

1. Log in as an admin user in the session viewer.
2. Open `/normal-runs`.
3. Click **Export current normal-run logs**.

An export directory is created under `normal_run/results/`:

```
normal_run/results/normal_runs__<timestamp>/
└── sessions.json
```

The `sessions.json` file includes all session rows (status, URL, timestamps, logs, and JSON-LD result) at export time.

## Sharing exports

Commit exports you want to share for debugging:

```bash
git add normal_run/results/
git commit -m "normal run log export: <context>"
git push
```
