# OA form attachment path repair, 2026-10-20

## Selected trajectory and finite acceptance

Complete native Consumer run 387670 and Audit run 387692 for task 416238 each called `read_dingtalk_oa_process_attachment` on the same `.xlsx` form attachment and received `Error executing tool ...: 'path'`. The process-scoped download had returned bytes; workbook extraction produced `format=xlsx` and sheet data but no `path`, while the role wrapper indexed `result["path"]` to retain the source for later reads. Both roles then tried a generic Drive lookup and received a provider permission denial. A separate PDF attachment failure in task 416235 was a provider `success=false` download response and is outside this repair.

Acceptance is limited to the successful-download boundary: an exact OA instance/file ID and valid `.xlsx` must return its extracted cells, original-byte SHA-256 and the actual saved file path through both role tools. Existing text/image/PDF extraction, PDF page cache, size checks, provider errors and OA decisions must remain unchanged. No generic Drive fallback or new authorization is added. The production gate is focused tests, independent review, repository deployment, and version/PID/health/queue/Attention/History readback; a natural OA business outcome requires a later matching run.

## Local evidence

The new Consumer/Audit role-level `.xlsx` regression failed on the old code with the same `KeyError: 'path'`. The materialization wrapper now sets `path` to the temporary file it just wrote, independently of extractor-specific result fields. The fixed case confirms exact cell content, source IDs, original-byte digest and file bytes for both roles. `tests/test_oa_process_attachment_reader.py` and `tests/test_agent_cli.py`: **52 passed**. Ruff and `git diff --check` passed. The historical provider-denied PDF call was not replayed.

Independent review and production readback are pending.
