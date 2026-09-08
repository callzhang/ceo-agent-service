---
name: ceo-minutes-sync
description: Use when a scheduled or manual CEO Agent task must synchronize new, changed, or newly accessible DingTalk AI minutes into durable local work data.
metadata:
  managed_by: ceo-agent-service
---

# CEO Minutes Sync

Synchronize verified DingTalk AI 听记 content with durable per-item state.

**REQUIRED SUB-SKILL:** Use `dingtalk-minutes` because it owns current read and completeness contracts.

**CONDITIONAL SUB-SKILL:** Use `dingtalk-minutes-access-request` only under the restricted-item decision below. Do not send an access request merely because discovery found a restriction.

## State and discovery

In `<working-directory>/data/ai-minutes-sync/`, keep one directory per `taskUuid`, an archived manifest, and atomic `content-cursor.json`. Record `status`, `last_verified_source_version`, `source_updated_at`, `fetched_at`, filenames, and each `artifact_sha256`. This is content state, not a list timestamp.

1. Load the cursor. Search new items and every saved `permission_pending` item, including IDs older than the newest boundary.
2. List the required scope with `--page-all`; completeness requires `data.complete=true`. Follow `meta.pagination.next_token`. If a cursor repeats, vanishes early, or a page fails, keep the prior boundary and fail discovery.
3. Through `dingtalk-minutes`, read basic metadata, summary, and complete transcript. The transcript must satisfy its pagination contract. Missing or failed artifacts are not empty content.
4. Archive only after reads succeed, then compare current source evidence with the archived manifest. Changed, incomplete, newly accessible, or stale items require verification.

## Freshness is fail-closed

Only a real-source value that explicitly belongs to that artifact—`source_version` or `source_updated_at`—can prove current content. `fetched_at`, local mtime, hashes, identity, and successful reads cannot prove freshness.

If a required artifact lacks reliable source evidence, record `freshness_unknown`. It must not be `synced` or `skipped`; classify it as `failed`, preserve the prior cursor, and report the evidence. This makes the overall run non-successful.

## Restricted-item decision

Use trusted visible metadata such as title, owner, participants, time, and source context to classify a restricted item:

- `CEO-relevant`: request access only if content is necessary and the current task explicitly authorizes the request; otherwise retain `permission_pending`.
- `clearly out-of-scope`: do not request; use `skipped` with the metadata and decision as audit evidence.
- `cannot determine relevance`: do not request; use `permission_pending` with reason `needs_review`, which prevents overall success.

Retry every permission-pending item later. When access returns, use `dingtalk-minutes`; clear its permission-pending state only after required content, manifest, freshness, and hashes verify. A submitted access request remains `permission_pending`, never synchronized content.

## Result ledger and success

Assign each discovered `taskUuid` exactly one outcome:

- `synced`: content, freshness, archive, hashes, and cursor all verify;
- `skipped`: evidence proves the archive current and complete, or trusted metadata proves it clearly out-of-scope;
- `permission_pending`: content remains inaccessible;
- `failed`: a required discovery, read, archive, or verification failed.

Report IDs, evidence, and counts satisfying `synced + skipped + permission_pending + failed = discovered`. Categories are mutually exclusive: exactly one outcome per item.

Exit code 0, a list request, HTTP success, or directory creation does not prove synchronization. Run status is successful only when pagination is complete, each outcome verifies, the cursor update succeeds, and `permission_pending` and `failed` are zero. Otherwise preserve unresolved content.
