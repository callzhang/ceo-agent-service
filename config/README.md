# Runtime capability registries

`mcp-tool-effects.json` is the reviewed allowlist used to classify native Codex
MCP events as read-only or effectful. Unknown server/tool pairs remain
unclassified and cannot confirm an external write.

Update the registry only from the installed server's published tool descriptor
or source-controlled capability manifest. Record every exact tool name and
classify a tool as effectful when any non-dry-run invocation can change external
state. Add production-shaped event tests for every effectful tool.

For an effectful tool whose published schema exposes a boolean validation-only
mode, set `dry_run_argument` to that exact argument name. A call with that
argument set to `true` is read-only and cannot produce a write receipt.

The Exa entries mirror the live `tools/list` descriptors published by the
configured Exa MCP endpoint. Refresh those exact names and annotations before
changing the entries. The Xiaoqing entries mirror the capability manifest in
the installed `xiaoqing_interview` service. `upload_interview_result` is
effectful; its other listed tools are reads.
