---
name: ceo-minutes-sync
description: Use when a scheduled or manual CEO Agent task must bring the DingTalk AI minutes archive up to date, including asking for access to minutes this account cannot read yet.
metadata:
  managed_by: ceo-agent-service
---

# CEO Minutes Sync

Bring the local AI 听记 archive up to date in two ordered steps, then report what
each one actually did. Both steps are service commands: run them, read their
receipts, and do not re-implement what they do.

## Why two steps

The read API lists only minutes this account already has access to. A minute
nobody shared stays invisible there, so it can never be archived until its owner
grants access. The 听记 admin console is the only place those minutes appear,
and the minute's own page is the only place a request can be sent. Asking first
means a minute approved since the last run is archived by the same pass.

## Step 1 — ask for the access we do not have

```bash
ceo-agent request-minutes-access
```

Reads the console, asks the provider which of its minutes this account may not
read, and submits a view request on each such minute's page. It prints one line:

```
request-minutes-access discovered=N requested=N already_requested=N readable=N unresolved=N failed=N session_expires_in_days=N
```

- A minute is asked for **once**. The command keeps the asked-for set, because a
  repeat request notifies the same colleague again.
- `unresolved` means the console had not resolved an owner to ask yet. Those are
  retried on the next run; nothing is wrong.
- `session_expires_in_days` counts down the signed-in console session. When the
  command prints `session-renewal-required`, tell Derek to sign in again — the
  session lasts about a month and only he can renew it.
- **A failed step 1 does not cancel step 2.** Access is an improvement to the
  next pass; archiving what we can already read is the job. Run step 2, then
  report the step 1 failure.

## Step 2 — archive everything not archived yet

```bash
ceo-agent sync-minutes-once
```

Lists every scope the provider offers (`all`, `mine`, `shared` — none of them is
complete on its own), fetches summary and transcript for each minute not already
archived, and writes it under the workspace's `AI听记` directory. It prints:

```
sync-minutes-once discovered=N synced=N skipped=N permission_requested=N permission_pending=N failed=N
```

`permission_pending` is a minute whose access was asked for and not granted yet.
It is not a failure and needs no action.

## What to report

One short paragraph: how many minutes were archived, how many access requests
went out, and anything that needs Derek. Escalate only these:

- the console session needs renewing (only he can sign in)
- `sync-minutes-once` failed, with its printed line
- the same minute has failed to archive on several consecutive runs

Do not report a clean run item by item, and do not list minute titles.

## Boundaries

- Never send an access request any other way. `dws minutes +apply-permission`
  answers `requested: true` for a request its owner never receives; only the
  minute's page, read back afterwards, is evidence.
- Never write archive files yourself. `sync-minutes-once` owns the layout, the
  cursor and the deduplication.
- Reading a minute's content for other work goes through `dingtalk-minutes`,
  not this Skill.
