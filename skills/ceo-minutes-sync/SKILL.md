---
name: ceo-minutes-sync
description: Use when a scheduled or manual CEO Agent task must synchronize new, changed, or newly accessible DingTalk AI minutes into durable local work data.
metadata:
  managed_by: ceo-agent-service
---

# CEO Minutes Sync

Synchronize what DingTalk returns without inventing remote-version evidence.

**REQUIRED SUB-SKILL:** Use `dingtalk-minutes` for current read and pagination contracts.

**CONDITIONAL SUB-SKILL:** Use `dingtalk-minutes-access-request` only under the decision below; restriction alone never authorizes a request.

## Durable state

In `<working-directory>/data/ai-minutes-sync/`, keep per-`taskUuid` directories, an archived manifest, and atomic `content-cursor.json`. Record exact bytes, each `artifact_sha256`, `fetched_at`, outcome, and permission state. Preserve source-returned, artifact-specific `source_version` or `source_updated_at` as optional evidence; never require or synthesize it.

## One run

1. Load the cursor. Discover with `--page-all`; the list is complete only when items come from `data.minutes`, `data.complete=true`, and `meta.pagination.endpoint_exhausted=true`. Follow `meta.pagination.next_token` while present. Retry saved `permission_pending` IDs even if absent from the list.
2. Fresh-read every relevant item needing content. The detail is the top-level envelope: permission failures enter the restricted-item decision first; otherwise require `complete=true`, `failureCount=0`, and successful `basic.result` plus `summary.result`. The transcript outer envelope must be successful and its pagination must reach both `data.complete=true` and `meta.pagination.endpoint_exhausted=true`. Other partial, failed, missing, repeated-cursor, or incomplete results are `failed` with `freshness_unknown`; retain the prior item cursor.
3. Archive `basic.result`, `summary.result`, and transcript `data.paragraphList` as UTF-8 JSON with sorted keys, compact `,`/`:` separators, unescaped Unicode, and no trailing newline; hash those exact archived bytes. Store hashes and `fetched_at` in the manifest. This proves only what was fetched then; it does not establish a remote revision or continued currency after that fetch.
4. New/different complete bytes are `synced`/`fresh_fetch`; complete bytes matching the verified manifest are `skipped`/`unchanged_fetched`. Both may advance the item cursor after the complete fetch.
5. Clear `permission_pending` only after recovery completes that sequence.

## Restricted-item decision

Use trusted visible title, owner, participants, time, and source context:

- CEO-relevant: request only when necessary and explicitly authorized; otherwise `permission_pending`.
- Clearly out-of-scope: no request; `skipped` with auditable metadata, and record the out-of-scope decision in the item cursor.
- Relevance unknown: no request; `permission_pending`/`needs_review`, blocking success.

A submitted access request remains `permission_pending`; submission is not synchronized content.

## Result ledger

The run scope is new list items plus saved pending IDs; `discovered` means that scope. Assign each `taskUuid` exactly one outcome and report evidence so `synced + skipped + permission_pending + failed = discovered`. Success requires complete discovery, verified outcomes, cursor write, and zero pending/failed; process or request success is insufficient.
