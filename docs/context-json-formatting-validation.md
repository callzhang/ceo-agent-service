# Context JSON formatting validation

## Scope and evidence

This independent deterministic change starts from current main bdc98bb3 and changes only app/agent_context.py `_json`: ensure_ascii=False remains; indent=2 becomes separators=(",", ":"). No held Task Skill, native schema, provider, policy or routing draft is included. No source fact is removed. Historical inputs and native trajectories are not rewritten.

Six actual saved Audit candidate blocks were decoded, compacted and decoded again with exact value equality. Removed characters: attempt360292 2234;360058 2668;360021 1653;359943 2690;359852 3276;359764 2755. These are only the Candidate revision block savings, not all JSON blocks or a total-token/quality claim. Original private bodies are not copied; metadata-only source hashes/ranges are in evals/trajectory_optimization/context_json_formatting.live_metadata.v1.json. Production was read through SQLite URI mode=ro with bounded queries and without Store initialization, provider calls or data writes.

The recent long Audit inputs are largely original source_bindings (captured messages), not proof of prompt-rule duplication. Separately, failed task404975 has repeated native outputs with invalid or mismatched candidate_digest. JSON formatting does not repair that issue and candidate-binding checks are unchanged. This change is not evidence that all trajectories are optimized.

## Verification

New lossless compact-format regression fails before the change and passes afterward, including original string spaces/newlines/tabs, escapes, Unicode, nested values, null/false/numbers and special keys. Formatting-dependent existing tests now check parsed JSON field values instead of requiring spaces. The first broad affected run has9 whitespace-only assertion failures,227 passed and4 skipped; the45 context/lossless tests pass after assertion correction. One Audit candidate-option assertion is changed to parse the candidate block and compare both option keys. Full affected rerun and independent readonly review are in progress. No publication or deployment yet.

The final affected five-file rerun passes236 tests with4 skipped in92.98s. After independent review, parsed-field checks also require exact Python types, preserving JSON bool/integer distinction;45 focused checks pass again. Independent readonly reviewer ran69 context/candidate-digest/Audit checks successfully, verified digest independence and found no runtime blocker. Targeted Ruff/diff pass. These are code/integration checks; formal deploy and production input readback remain pending.


## Formal publication and bounded production readback

Commit91cfb8e470680a61aa37991257128f48a6f20384 pushed to main. Formal `python -m app.deploy` reports deployed bdc98bb3 ->91cfb8e4. Production source matches the reviewed source byte-for-byte; supervisor33961/service33966/web33967/email33968 are new processes. Health200/ok in0.02s. Correct console Status200 in10.71s, summary pending3/processing0/failed23/retryable0/Attention17. The3 pending items are email provider actions. These are current queue totals, not proof the existing failures were caused or resolved by this release. Attention200 in6.89s (15 groups); public Consumer/Codex Settings preview200/available/current in0.88s. This is public input-display acceptance, not a new task's native business execution.

History page_size2/include_chartfalse times out first at15s then at45.03s. Full operational readback remains incomplete; no new deploy/restart/replay or broad Store change follows the timeout. Earlier pre-read used nonexistent /api/status,/api/attention,/api/history paths; correct /api/console paths are used for all acceptance results above. One transient connection refusal occurred during the formal stop/start window before deploy returned. Source/process/API metadata are saved in context_json_formatting.production.v1.json, without private inputs or settings secrets. No total-token/business-quality or digest-error repair claim is made.
