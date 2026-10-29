# OA Consumer OCR native handoff, 2026-10-20

## Selected failure and acceptance

Task 405641 repeatedly failed while reviewing scanned OA attachments. Its
complete native Consumer trajectory for run 386649 contains a page-one
`read_dingtalk_oa_process_attachment` result with `ocr_input_base64`, MIME,
one-based page number and source SHA-256. The model-visible tool output is
truncated *inside* the base64 value (`25497 tokens truncated`), making it
invalid input for `ai_services.ocr_document`. The Agent did not call OCR and
returned `retry`; the service rejected that result because its historical OA
comment candidate predates the typed `request_access` purpose output. The
separately published receipt-prefix matcher does not establish that the old
comment is a typed access-request receipt. These are two distinct failures.

This change addresses only OCR byte handoff for Codex OA Consumer. Acceptance:
an anonymous source tool returns an image, the same native CodeMode JS cell
passes its exact bytes to `ai_services.ocr_document` without printing base64,
and the result retains OCR text, one-based page and source digest. Ordinary
Consumer and Audit commands, MCP allowlists, external actions, the retry gate,
and oversized-image policy must remain unchanged. A real OA business decision
requires separate post-deploy trajectory and receipt readback.

## Fixed-case evidence

- The role-command regression failed before the new `purpose_name` option
  existed and passed after the OA-only nested CodeMode setting. A second
  regression failed before the task-specific handoff instruction existed and
  passed after it; the general Consumer and Audit instructions remain direct.
- An anonymous native Codex OAuth run used a temporary read-only MCP source
  tool and the configured AI Services OCR tool within one CodeMode cell. It
  returned `OCR TEST 4821`, page `1` and source SHA `synthetic-source` without
  emitting the image base64. The mock source, generated PNG and CLI workspace
  were deleted with their temporary directory. This proves cross-MCP byte
  transfer in the native client, not the full production OA route.
- Direct AI Services OCR on a separate anonymous PNG returned the same text.
  A prior test that pasted image base64 into the model prompt timed out; that
  result is inconclusive and is not used as positive evidence.
- Focused command, instruction and session-contract tests passed. The broader
  Consumer test run was interrupted after 132 passed and 2 skipped; three
  failures were unrelated current-tree fixtures: two compact Actions queries
  select a nonexistent `lease_owner`, and the general capability-length test
  expects 3556 characters although the unchanged baseline is 3958. The OA
  capability text is shorter (about 3.6K characters) than that baseline.
- PR #174 review found that the first OA session-key prefix did not pass the
  final route selector's existing `selected-skills:` compatibility check.
  The exact final-selector regression failed before the correction and passed
  after the OA key retained that prefix; a matching OA session still resumes.
  The post-correction OA role/session/attachment/PDF group passed 46 tests.

## Release and production readback

The released command keeps ordinary Consumer and Audit direct-only. OA Codex
Consumer gets `mcp__agent_cli` and `mcp__ai_services` as nested tools in
CodeMode, with the same allowed tool names and provider approval settings.
Its selected-Skill session key incorporates this OA-only instruction so an
existing OA session with contradictory direct-call guidance is refreshed once;
unaffected task sessions keep their prior key. Audit still excludes functions.

PR [#174](https://github.com/callzhang/ceo-agent-service/pull/174)
merged at 2026-10-20 02:48 UTC as `296b6d82f0b9b374f1caa64e9ec223d8e6c7207b`.
The normal `python -m app.deploy` command waited for in-flight work and
reported `deployed cee038b1 -> 296b6d82`; no quiet-wait bypass or manual
restart was used. The production checkout is clean at the merge commit, and
the installed source contains the OA CodeMode instruction and
`selected-skills:oa-ocr-handoff:` session key. The replacement supervisor PID
is `33501` (started 2026-10-20 02:55 UTC).

Postdeploy `/healthz`, Console Status, Attention and History each returned
HTTP 200. Status reported the launchd job running, system health `healthy`,
zero quality-gate violations and 13 queues. The readback snapshot showed
10 pending and 3 processing items; Attention had 2 groups and History returned
5 requested items. These queue counts are live observations and may change.
No real OA task was replayed for this release, so production installation and
health are verified while a natural OA OCR/business outcome remains unproved.
The anonymous native cross-MCP case above is the behavior evidence for the
byte-handoff mechanism. Long DingTalk comment images rejected as
`image_material_ocr_input_too_large` remain a separate source-size issue and
are not addressed here. GitHub's automatic Quality check was queued at merge;
it was not used as the behavior gate.
