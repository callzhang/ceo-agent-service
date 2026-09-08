---
name: ceo-minutes-sync
description: Use when a scheduled or manual CEO Agent task must synchronize new, changed, or newly accessible DingTalk AI minutes into durable local work data.
metadata:
  managed_by: ceo-agent-service
---

# CEO Minutes Sync

Synchronize verified DingTalk AI 听记 content. Keep per-item state so later runs recover incomplete or inaccessible material.

**REQUIRED SUB-SKILL:** Use `dingtalk-minutes` for listing, reading, pagination, and export because it owns the current data and completeness contracts.

**CONDITIONAL SUB-SKILL:** Use `dingtalk-minutes-access-request` only when access is denied, a request is necessary, and the task explicitly authorizes sending it. Do not send an access request merely because discovery found a restricted item; preserve `permission_pending` for recheck.

## Durable layout

In `<working-directory>/data/ai-minutes-sync/`, keep:

- one directory per stable `taskUuid`;
- an archived manifest with source identity and artifact evidence;
- `content-cursor.json`, updated atomically after reads and archives verify.

The cursor is content state, not a newest-list timestamp. Per `taskUuid`, preserve `status`, `last_verified_source_version`, `source_updated_at`, `fetched_at`, artifact names, and each `artifact_sha256`. Keep blocked IDs as `permission_pending`.

## Discovery and complete reads

1. Load the cursor. Search new items and every saved `permission_pending` item so newly granted access is filled even behind the newest boundary.
2. List the required scope with `--page-all`. Completeness requires `data.complete=true`. Follow `meta.pagination.next_token`; if a cursor repeats, vanishes early, or a page fails, keep the prior boundary and fail discovery.
3. Through `dingtalk-minutes`, read real basic metadata, summary, and complete transcript for each item. The complete transcript must satisfy its own pagination contract. Missing or failed artifacts are not empty content.
4. Archive only after required reads succeed. Record supplied `source_version`; otherwise derive a stable version record from identity, `source_updated_at`, artifact metadata, and hashes. Save `fetched_at` and every `artifact_sha256` in the archived manifest.
5. Compare source evidence with the manifest. Only proven unchanged complete items are `skipped`; changed, newly accessible, incomplete, or stale items require verification.

## Permission recovery

On denial, record stable ID, URL, evidence, and check time as `permission_pending`, never absent or empty.

Retry every permission-pending item later. When access returns, use `dingtalk-minutes`; clear its permission-pending state only after required content, manifest, and hashes verify.

If explicitly authorized, use `dingtalk-minutes-access-request` and record whether it sent a request. Submission remains `permission_pending`, not synchronized content.

## Result ledger and success

Assign every discovered `taskUuid` exactly one outcome for this run:

- `synced`: current content is read, archived, hashed, and in the cursor;
- `skipped`: evidence proves the archive current and complete;
- `permission_pending`: content remains inaccessible;
- `failed`: a required discovery, read, archive, or verification failed.

Report IDs, evidence, and counts satisfying `synced + skipped + permission_pending + failed = discovered`. Categories are mutually exclusive: exactly one outcome per item.

Exit code 0, a list request, HTTP success, or directory creation does not prove synchronization. Run status is successful only when discovery pagination is complete, every item has exactly one outcome, `synced` evidence verifies, the cursor update succeeds, and both `permission_pending` and `failed` are zero. Otherwise return partial or failed evidence without advancing past unresolved content.
