# Native structured output on Claude CLI

The common routed runtime now passes an existing command-factory output-schema declaration through the Claude CLI `--json-schema` JSON argument. An omitted explicit schema path uses the same Codex decision schema that the Codex CLI path uses. Developer instructions stay in their existing input contract; the output schema is not duplicated in task text.

The Claude event normalizer consumes the native terminal `structured_output`, serializes it as canonical JSON and hands it to the existing strict workload parser. A declared schema requires that native terminal field. This does not add Markdown/fenced-JSON parsing or change Audit, business tools, effect classification, delivery gates or receipt handling.

Verification: the native CLI parameter regression fails when the argument is removed. Normalizer regressions cover native structured output taking precedence over human explanation, a structured result with no text field, and a missing required structured field. A real no-tools CLI probe using the existing OAuth login and fixed repository schema exited0, emitted its internal StructuredOutput tool plus a matching tool result, normalized turn.started→item.started→item.completed→turn.completed, and left0 open tool items. Its synthetic value was {"outcome":"no_code_change","summary":"native full-stream probe"}. This is protocol verification, not business recovery or PR CI acceptance.

Workbench optional per-turn contracts and history reconstruction are a separate follow-up and are not required by this transport commit.

Claude CLI schema arguments omit only the top-level `$schema` dialect metadata: its native validator rejects the fixed files' 2020-12 URI. All field constraints pass through unchanged; fixed files and strict local model validation retain their original contract. Both fixed Meeting schema regressions failed before this change and pass afterward.

Formal gate at f9be20fb6 finished in454.08seconds:8898 passed/3 failed/2 skipped/1 deselected. No code was installed. Two failures were obsolete text-schema delivery assertions; they now verify native inline CLI delivery without duplicating the schema in the task message, while preserving exact Unicode/whitespace/field constraints and the source file. The third identified two new unregistered schemas; both are now PINNED to their strict models in the existing complete schema registry. No gate files or assertions were removed.

After correction,121 focused schema/Workbench/routed regressions pass in9.09seconds and Ruff is clean. Independent review confirmed that lossless delivery, business input and strict model pinning remain covered. A fresh standard gate/deployment is required.
