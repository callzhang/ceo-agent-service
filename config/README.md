# Runtime capability registries

`service-mcp.json` is the committed service MCP seed. Runtime resolves only the
manifest selected by `CEO_SERVICE_MCP_CONFIG_PATH`; it never copies transports
from `~/.codex/config.toml`. Setup creates an editable local copy at
`data/config/service-mcp.json`. Delete optional server entries from that copy
when they are not used. A present entry must resolve to one complete URL or
command transport, otherwise runtime and MCP doctor fail closed.

The manifest may contain non-secret static values and environment variable
names. The Xiaoqing OAuth MCP endpoint is static so service runs reuse the
existing Codex OAuth session without a second transport configuration. Bearer
tokens and dynamic header values belong only in the service environment, never
in the JSON file.

`ceo-agent doctor-mcp --verify-live` performs the read-only MCP `initialize`
handshake for each URL transport. A configured URL is not sufficient evidence
that the service can use it: authorization failures are reported as
`needs_login`, while network and server failures remain service errors.

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

For generic controlled-CLI wrappers, set a readback relation's
`operation_match` to `registered` and list the exact normalized inner read/write
operation pairs in `operation_relations`. Target identity matching still applies;
an unregistered inner operation pair cannot confirm a write.

The Exa entries mirror the live `tools/list` descriptors published by the
configured Exa MCP endpoint. Refresh those exact names and annotations before
changing the entries. The Xiaoqing entries mirror the capability manifest in
the installed `xiaoqing_interview` service. `upload_interview_result` is
effectful; its other listed tools are reads.
