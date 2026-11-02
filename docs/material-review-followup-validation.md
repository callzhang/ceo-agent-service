# Material review follow-up validation

Two deterministic tool defects were reproduced before editing: the role-bound Drive reader raised `KeyError: path` for real XLSX and PPTX parser output (both Consumer and Audit), and spreadsheet preview schema omitted its existing row/column limits. The download receipt now owns the absolute material path; the tool schema declares the unchanged 1–200 row and 1–64 column limits. No fallback, external effect, audit policy, formula evaluator, or business-data mutation was added.

Interactive mail and document Skills now describe preserving original period/mail/material identity, reporting precise inspected locations and value provenance, comparing verified successor versions, and distinguishing failed source retrieval from a proven defect. Runtime copies are immutable managed revisions; committing the bundled copies alone does not activate them.

## Regression evidence

- New role-bound package test: 4 failures before the path fix, all `KeyError: path`; XLSX fixture preserves an actual error cell and PPTX uses the real parser.
- Related tool, role-boundary and business-Skill tests: 151 passed in 47.48s after the path fix.
- New schema test failed before the annotation fix (`minimum` missing).
- Expanded check: 169 passed and one release-manifest digest failure; updated only the two changed Skill source digests. Final Drive/schema and publication tests: 34 passed in 49.60s. No behavior change was made to the publication mechanism.
- Ruff and `git diff --check` passed.
- A scoped read of the original remote XLSX through the repaired role tool succeeded, with an existing downloaded path, exact node identity, byte SHA and bounded cell contents. Business evidence is kept only in the private task report.

## Fixed native comparison

`evals/material_review_followup.v1.json` freezes four synthetic cases and the same `gpt-6-luna`, medium reasoning, serial, one-repeat, 180-second settings in both arms. Only the two applicable Skill bodies differ. All shell/MCP/external tools are disabled using the existing business-evaluation native command builder. The result is a simple reply/source/limitations JSON, not a full production Consumer/Audit wire run.

The primary agent reviewed exact outputs against the frozen semantic rubrics: baseline 4/4, candidate 4/4, eight native calls successful with zero tool use. This is a small boundary check and **does not demonstrate an improvement or validate production retrieval/session continuity**. There is no independent semantic grader. Compact receipts are in `evals/material_review_followup.summary.v1.json`.

Reproduce using immutable refs:

```sh
python scripts/eval_material_review_followup.py --baseline-ref a8051eaf8 --candidate-ref <candidate-commit> --output /tmp/material-review-comparison.json
```

The native command uses installed Codex authentication and sends synthetic facts only. Do not replay original business tasks or send correction messages as a test. Release status and remaining gates are reported separately; local tests and a read-only live-source probe are not deployment evidence.
