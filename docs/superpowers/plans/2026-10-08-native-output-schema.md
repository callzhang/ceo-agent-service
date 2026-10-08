# Native Consumer/Audit output schema repair

Goal: use native Codex output-schema with direct MCP and reused sessions, while preserving canonical Consumer/Audit business models, validation, results and policies. Derek explicitly requests this repair after native format incompatibility was reproduced.

Design: expand RootModel reference; require all fixed properties; encode open JSON objects as entries(key,value) with recursive JSON values; omit unsupported composition only from native structural projection. Original Pydantic validators remain unchanged and run after lossless decoding. Fixed committed native schema files, no invocation temp files or native-home/config wrappers. Claude/Friday canonical transport unchanged. Reject duplicate entry keys as invalid encoding, not last-write-wins. Per-turn native-format instruction supports existing sessions; Settings records actual submitted task input.

- [x] RED regressions: native schema shape, lossless nested/key collision values, unchanged business rejection, native command flag and cold/resume instructions.
- [x] Implement one schema/codec module; generate checked-in schemas; integrate Codex command/parse boundary and Settings preview.
- [x] GREEN focused tests and schema snapshots; verify route/session/correction/stage/result behavior unchanged.
- [x] Native actual-code Consumer/Audit cold+resume with MCP; frozen calendar comparison baseline/final; no expected business answer, no provider writes.
- [ ] Independent scoped review; docs architecture/runtime same commit. Push/merge, formal quiet deploy, PID/health/queues/Attention/History/Settings readback.

Evidence: existing native tests show simple supported object schema+MCP cold/resume works; current exact schemas fail before tool calls. Research prototype entry representation passed both roles cold/resume and preserved nested objects, arrays, scalar types, null, literal entries key. A result_json string wrapper would leave business JSON unstructured and is excluded.

## Verification ledger

- RED: missing native module; nested object retained transport entries instead of canonical map; runtime still omitted output-schema and returned encoded payload.
- GREEN: recursive JsonValue/schema-directed codec, canonical business rejection, schema snapshots and multiple-assistant selection; runtime valid/malformed nested outputs pass. Final affected suite: 202 passed, 4 skipped. Focused regressions: 26 passed. Ruff and git diff --check passed; actual native appendix replaces the old plain-Task preview expectation.
- Research prototype and actual codec each passed Consumer/Audit native cold+resume with direct read-only MCP and unchanged canonical validation.
- Prefinal native semantic arm passed3/4 cold vsbaseline4/4; false-midnight-overlap result contradicted its own correct judgment, with early summary/action falsely declining. No provider dispatch; do not count corrected resume as first-pass equivalence.
- Final native projection emits existing evidence/judgment before action and summarylast, because constrained generation follows property order. Original business models, source inputs, scoring, session and policies unchanged. Final frozen comparison: candidate 4/4 cold semantic cases and 1/1 matching Consumer resume; Audit cold/resume 2/2 native schema and canonical validation. Fresh baseline decisions 4/4, complete time evidence 3/4. No date/case keyword logic; this sample does not establish causal superiority or cost savings.
- Independent review found only stale docs (updatedhere) and2 fixture defects (fixed); final structural review clean. Exact-schema service-API provider matrix remains the pre-release acceptance gate.

Final evidence: /Users/derek/Documents/memory/ceo-agent-service/native-output-schema-final-20261008/REPORT.md; 64 files checksum verified. Canonical Consumer/Audit business model hashes remain unchanged.

## Release disposition

Merged origin/main a8792d21 without conflict; repeated affected tests: 202 passed, 4 skipped in 45.31s. Ruff and diff checks passed. Implementation f7b34120; integration b23e3841.

Configured service-API acceptance is unresolved. codex_api exact Consumer/Audit produced no valid cold output; controls with schema OFF and minimal schema ON both returned provider HTTP 429, so this failure is not attributable to the native schema. Qwen minimal schema is accepted, but exact Consumer/Audit produced no final agent message; schema-OFF/minimal controls also omitted the requested synthetic MCP read. Model-discovery warning is not established as the root cause; no provider-wide output limit was established. Same-session API resumes were not run without a valid cold result. Native environment and configured credential identities matched production. Evidence: /Users/derek/Documents/memory/ceo-agent-service/native-output-schema-providers-20261008/.

Keep as unpublished-to-production draft; no main merge/deploy or successful API-route claim. Next gate: obtain exact Consumer/Audit cold and matching-resume results on both configured service-API providers, preserving the current fixed schemas and canonical validation; investigate Qwen full-schema generation separately from the harmless metadata warning. Do not hide the gap with a fallback or new routing policy.
