# Task same-input source projection sharing

Acceptance for this change is limited to one Task semantic context. A repeated
projection must appear once with later documents pointing to it only when the
complete serialized bundle becomes shorter. Expanding every pointer must
reproduce the original bundle exactly; document identity, per-document
metadata, signal and citation bindings, retrieval selection, and the fixed
Project/Task/Attention decisions must not change. Cold and same-session
continuation are both required. After release, verify the deployed commit and
new service process, health, queues, Attention, and History, plus a read-only
projection check against the installed code. A synthetic result does not prove
future production Task decisions.

The Task semantic context can include separate source documents whose visible
ranges and decoded excerpts are identical. Serializing those same character
ranges on every document repeats input bytes without adding evidence.

The implementation shares only exact projections within one semantic context
through `duplicate_of_document_id`. It keeps every document ID, full length,
truncation and citation-budget flag, every source-signal mapping and citation.
The Task prompt tells the Agent how to resolve the projection while keeping
source identities separate. The transform is used only when it reduces the
complete serialized source bundle. There is no cross-turn reuse, no retrieval
change, and no change to source text or citation validation.

For attempt `371553`, the source bundle had 68 documents and 106 signal
references, with 11 exact duplicate groups across 24 documents. The source
document JSON fell from 171,912 to 141,444 characters (30,468 fewer), while
expanding every pointer reconstructed the original 68-document array exactly.
The equivalent full User message projection fell by 7.92% before the short
pointer-resolution instruction.

The recorded anonymous native Codex OAuth comparison is in
`evals/trajectory_optimization/task_source_projection_sharing.native.v1.json`.
Baseline/candidate User inputs were 43,395/40,955 characters on cold start and
16,936/14,964 on same-session risk continuation. Both arms matched the fixed
Project/Task/Attention outcomes without validation corrections or tool calls.
Summing all native usage records gave 147,425 baseline versus 139,684 candidate
input tokens across both turns. This is one synthetic comparison; cache
proportions differed, and it does not establish general token savings or
quality improvement.

Focused verification in the isolated worktree:

- `CEO_USER_PROMPT_TEMPLATE_PATH=app/defaults/user_prompt.md pytest -q tests/test_task_source_projection_sharing.py tests/test_task_retrieval.py tests/test_task_source_documents.py tests/test_task_agent.py` — 297 passed.
- Ruff and `git diff --check` passed.

The same 297 focused tests and Ruff passed after merging `origin/main` into the
PR branch on 2026-10-18. The older PR Quality run failed in unrelated
repository-wide tests; it does not replace the fixed-case behavior check.
