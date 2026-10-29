# OA form attachment path repair, 2026-10-20

## Selected trajectory and finite acceptance

Complete native Consumer run 387670 and Audit run 387692 for task 416238 each called `read_dingtalk_oa_process_attachment` on the same `.xlsx` form attachment and received `Error executing tool ...: 'path'`. The process-scoped download had returned bytes; workbook extraction produced `format=xlsx` and sheet data but no `path`, while the role wrapper indexed `result["path"]` to retain the source for later reads. Both roles then tried a generic Drive lookup and received a provider permission denial. A separate PDF attachment failure in task 416235 was a provider `success=false` download response and is outside this repair.

Acceptance is limited to the successful-download boundary: an exact OA instance/file ID and valid `.xlsx` must return its extracted cells, original-byte SHA-256 and the actual saved file path through both role tools. Existing text/image/PDF extraction, PDF page cache, size checks, provider errors and OA decisions must remain unchanged. No generic Drive fallback or new authorization is added. The production gate is focused tests, independent review, repository deployment, and version/PID/health/queue/Attention/History readback; a natural OA business outcome requires a later matching run.

## Local evidence

The new Consumer/Audit role-level `.xlsx` regression failed on the old code with the same `KeyError: 'path'`. The materialization wrapper now sets `path` to the temporary file it just wrote, independently of extractor-specific result fields. The fixed case confirms exact cell content, source IDs, original-byte digest and file bytes for both roles. `tests/test_oa_process_attachment_reader.py` and `tests/test_agent_cli.py`: **52 passed**. Ruff and `git diff --check` passed. The historical provider-denied PDF call was not replayed.

Independent review approved the scoped diff with no Critical or Important findings and independently reran the 52 focused tests.

## Production readback

On 2026-10-20, deployment installed `763caecd3ccb550aa3c2608eac6746d8ff3296d4` from `b6f87c14a03b2dadb4b37ee637f15baa3e922294`. The committed OA fix `edcf9322758a1b0f8c724b191b026cd1ccbbe880` is an ancestor of the installed commit, and its materialization branch still returns `"path": str(material_path)`. The persisted deployment operation reports `succeeded`, with original, target and installed commits matching that transition and no error. launchd reports a new running PID, `75708`; `/healthz` returned `ok: true`.

The production workers endpoint responded with 13 queues, 15 pending, 4 processing, 11 failed, 2 retryable and 1 Attention item. `/api/console/attention` returned 1 item; `/api/console/history` returned records with total 36,567. Settings Runtime Context for both Consumer and Audit returned the native `mcp__agent_cli__read_dingtalk_oa_process_attachment` tool name. These are readback values, not claims that the remaining queue items were caused or resolved by this repair. No new matching OA `.xlsx` business run has yet established the end-to-end outcome.

Release scope caveat: the operator had explicitly approved forced deployment of `d46e47400b51569287c8f570f4ad97dd8f2c9341`, but `main` advanced before the deployment process selected its target. The persisted operation target is `763caecd3ccb550aa3c2608eac6746d8ff3296d4`, which includes a subsequent Dashboard metadata correction. The deployed code passed the release readback above, but this version-selection race should be addressed separately before another exact-SHA approval is treated as pinned by a preflight `ls-remote` check.
