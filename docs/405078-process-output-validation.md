# 405078 process output repair

The original native page input SHA-256 exactly matches the page-digest suffix of
runtime attempt 363868. The native session is
`01a1421c-da44-7c13-aa9f-f28be8cce6fa`; its final `expired` answer passes the
existing page parser and routed text codec. No private page text or URL is copied
here. The runtime attempt instead has empty session/reference and a zero range,
with `runtime_result_invalid`.

The runner stopped reading immediately when its direct child exited. A selector
iteration can read stderr first while the complete stdout event stream is still
queued. That loses both the answer and its thread identity. The deterministic
real-child regression forces precisely that ordering: original runner returned
empty stdout (two failing cases); the repaired runner drains already available
bytes after existing termination without waiting on inherited pipes.

The tests cover stdout larger than one read, both streams, final event callback,
valid page judgement and strict rejection of invalid output. Routed integration
asserts the session/reference/range are durable for both completed and failed
attempts. Existing timeout, UTF-8, callback failure and inherited pipe tests are
retained. Initial focused verification: 33 passed. Final combined process, page judge,
routed execution and result privacy suite: 101 passed in 88.44s. Independent
read-only review reported no actionable findings and independently ran 14 process
cases successfully. Ruff and diff checks passed.

Real Codex CLI synthetic acceptance returned `expired`, captured five stdout
lines and session `01a14238-69c1-7350-a7e3-bc8a397803aa`. Its 13-line original native
record contains zero tool calls. The first synthetic setup failed before CLI
launch because the isolated tree had no service MCP manifest; using the existing
production manifest completed the check. No original business page was used.
Production publication and readback are not yet claimed.

Historical task405078 and runtime363868 are not rewritten. No original unsubscribe
entry is opened, no provider operation is repeated, and no execution marker is
cleared. A fixed transport does not prove the historical subscription outcome.
