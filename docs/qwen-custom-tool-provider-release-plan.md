# Qwen custom-tool provider release proposal

Status: offline candidate verified; production publication and both-slot reload await explicit approval. This is distinct from the previously approved native-schema provider release.

## Change and evidence

The installed Responses converter omits native custom tools. The candidate restores them as an internal JSON object (`input.value`) and returns original raw input in native custom events; Qwen framing grammar excludes literal markup openers inside encoded string values but permits JSON escapes. Ordinary functions and complete final schemas are retained. Namespace grammar uses the same flattened names as the serving converter. Optional auto text and disabled tools remain valid.

Evidence: `evals/trajectory_optimization/qwen_custom_tool_transport.framed.prototype.patch` and `.framed.prototype.v2.json`, 10 integrated plus12 transport tests, validated forced/namespace requests, incremental real-token streams, complete current Consumer schema and registered42 tools. Additional seeded corpus:129 texts exactly restored. Source hashes below define the exact patch, not an upgrade to another vLLM version. No model inference has been performed with this candidate. Original custom format is validated after decoding rather than fully enforced in generation; custom input delta waits for the complete envelope.

## Operational boundary

Host: `stardust-gpu4`; native package `/home/derek/miniforge3/envs/llm-gateway-vllm-029/lib/python3.12/site-packages`, vLLM0.29.0. Both current Qwen replicas use this environment: slot0/GPU0/port9000 and slot1/GPU1/port9010, model `qwen3.8-27b`; manager port8002. Read-only snapshot at2026-10-13T16:54:33Z showed both ready, with13 active requests each; backend PIDs1340918/port9000 and1142177/port9010. Recheck immediately before any action; this is not an idle receipt.

The existing manager has slot-specific `POST /admin/kill` and `POST /admin/start`, but no atomic drain or slot reservation across the reload. Stop sends signals to the backend process group and can escalate after its wait. New arrivals may race the idle observation. Changing the common package affects both replicas when they next import/restart. A slot1-only reload cannot prove the configured route is uniformly repaired, because the gateway can route to slot0. No new routing, drain, retry or authorization policy is introduced by this release.

## Proposed approved scope

1. Recheck six installed hashes and both slot/PID states. If sources differ, stop publication and review the new diff. Create and verify a backup of exactly these six source files; retain its hashes and location.
2. Wait for slot1 known active requests to finish. Stop only slot1 through `POST /admin/kill` with `{"slot_id":1}`; confirm terminal backend state. Do not interrupt known active requests.
3. Apply the reviewed six-file patch to the shared native package, verify every candidate hash and imports using the native interpreter. No pip upgrade, endpoint/config/model/prompt change. Slot0 may continue serving its already-loaded old modules during this transition; this is a mixed-version interval, not acceptance evidence.
4. Start slot1 with `POST /admin/start` and `{"slot_id":1,"model":"qwen3.8-27b"}`. Read back new PID, ready/running, port9010 health. If this fails, restore the verified six-file backup and recover slot1 through the same manager.
5. Wait for slot0 known active requests to finish. Reload slot0 through the same slot-specific kill/start endpoints; read back new PID, ready/running, port9000 health. Both slots must load the candidate before claiming the configured gateway route repaired. If slot0 cannot become idle, leave publication incomplete and report the mixed-version state; do not force-stop active requests.
6. Run actual native Codex Consumer/Audit complete-schema cold-start and same-session continuation through configured `qwen_gpu4`, with current registered role catalogs and anonymous read-only MCP fixture callbacks. Every cold run must produce a valid complete business result and required source read before its continuation can count. Preserve complete native trajectories, tool receipts, session IDs and terminal output. Verify each backend version/PID and both health endpoints plus gateway status after probes.
7. If a model/format regression appears, save the complete trajectory and diagnose its mechanism. Roll back the six-file backup and reload each affected slot only after its known active requests end. No fallback, prompt workaround or schema disablement is part of this proposal.

This approval would cover the common six-file provider repair, both Qwen replica reloads and anonymous native role probes, including a possible brief Qwen interruption/arrival race and mixed-version interval. It would not merge/deploy PR31 into CEO Agent production or publish unrelated prompt drafts. The full original configured-route and business acceptance gates remain required before enabling PR31.

## Exact source fingerprints

| Native relative path | Installed original SHA256 | Candidate SHA256 |
| --- | --- | --- |
| `vllm/entrypoints/openai/responses/protocol.py` | `d0d4f4b4776c40ad9744e08ffb9135e1b26af696b7ffb6a5ee3e3a60a1be2c90` | `7d5472ee808137cc4de89b9ec14217588d205eb7e728f6e6673ecd980ecb8ecf` |
| `vllm/entrypoints/openai/responses/streaming_events.py` | `f2681d82bdb1d041b3d78535db90b556d29c6474f790b783079c7ea156319017` | `17733c91dad2e60325c75fa75a6e9784018033af2b4cce574dcebb2241eba98a` |
| `vllm/entrypoints/openai/responses/utils.py` | `4a07bb382fb22ca79516db24a7269491ac949e1171d059479179ed1407024632` | `c322aeeccf013dd03b41dcc2c2defe6489b6ee504351548305ed08a74d16bfc5` |
| `vllm/tool_parsers/qwen3_engine_tool_parser.py` | `fe609579548295ca9f443b966cc00a9772bb8fcce4e19ba2a7edfa4121ca58d4` | `2b4ec8b7be0ec49f2003d0a9bd41fba3ffeda9466ade1a9c46e81c2148b2c5dd` |
| `vllm/tool_parsers/structural_tag_registry.py` | `be9475e0bbb08de435b13e208278188f65fbb1b72f83e910f18825525fefbf3e` | `593f71a8c04e3197576878b70bb3ba5797228f6cd3aa2a6270df026682d920fe` |
| `vllm/tool_parsers/utils.py` | `bd5caa24867098d5883570668766f9abb1d06e6d6ac48ce68d37dcd7a0d727dd` | `e115feaefe03b38e6898bb2f789663109b608e950a46a11199036c035336e8c5` |
