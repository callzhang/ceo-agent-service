# Native schema integration on current main

Draft PR31 (3a4942a8) is integrated with current main b451cfe7 in the isolated codex/native-schema-current-main branch. No main merge, production deployment, prompt reduction or provider change is performed here. Native schema files and canonical business models remain unchanged.

## Confirmed integration repairs

Terminal Settings history now reads native input without RAM format-length metadata. The initial merge duplicated the Codex appendix on Codex previews and retained it on Claude previews: two real-file regressions RED, then GREEN. Native appendix separation uses the exact service-owned terminal boundary only for recorded Codex input (or old RAM native-format receipts without runtime kind). Claude business text quoting that appendix is preserved; its regression failed before this source check. Recorded but unrecognized format boundaries show unavailable in current previews and preserve the original in history. Unredacted original character lengths are never used to slice redacted input. There is only one native format version in this unshipped implementation; a future appendix change requires a separately verified historical-version design.

The runtime and preview share one native format PromptSection. The running input receipt records actual submitted Task and the format source/length. The old PR test read a RAM input after terminal cache release; it now captures the real live receipt during executor invocation rather than restoring duplicate persisted bodies.

Validation: affected Consumer/Audit/codec/Settings suite 215 passed, 4 skipped before final source-boundary additions. Final codec and Settings tests 44 passed after those changes. Ruff and diff checks passed. Independent read-only review found the Claude boundary issue; its correction and current-version scope passed re-review.

## Provider gate remains incomplete

Anonymous source data only; all role callbacks stubbed, no private Skill or business records, no external action dispatch. Complete unchanged schemas and the actual current role catalogs are used.

- codex_api Audit cold: provider HTTP429, zero source calls, no valid final result. Corrected Consumer cold: same HTTP429. Initial Consumer harness duplicated --skip-git-repo-check and exited before model invocation; that result is explicitly excluded from provider conclusions. No resume without valid cold.
- qwen_gpu4 Consumer cold: native and canonical output valid but no_action instead of the required nonempty candidate; the one source call also alters the start timestamp. Not passed and not resumed.
- qwen_gpu4 Audit cold: 48 successful repeats of the same exact source call in 180 seconds, no final result. Deadline stopped only the task-owned fixture process. Not passed and not resumed.
- Single-variable Audit source-content control is running: same model/schema/Task/tool declarations/effort/deadline, only source return reduced from nested maps to marker. Live trace already contains repeated successful reads; terminal result pending. This is not yet a confirmed provider or schema defect. Prior no_action/marker compatibility gates do not prove this nonempty/current-input gate.

Evidence: evals/trajectory_optimization/native_schema_current_main.results.v1.json; complete anonymous native fixture files remain at the artifact directories recorded there for ongoing trajectory diagnosis. Next: finish the control, compare the previous passing native input/command with this failing input, isolate the confirmed difference before changing any production mechanism or prompt.
