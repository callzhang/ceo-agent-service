# Email retired identifier fixture retirement validation

This test-only cleanup removes two positive tests that rewrote an obsolete 17-table Email schema and their helper. The accepted current12 contract has a single `emails` parent, and the current schema preflight accepts the exact produced declaration. The source disposition is recorded in `docs/superpowers/plans/2026-10-18-email-retired-identifier-fixture-disposition.md`; the actual-copy receipt is `docs/superpowers/specs/2026-10-17-email-canonical-current-copy.json` (SHA-256 `1d9d2edb43e9d8470bbbdbd37177678c2f72e01a403cfd228223092825493b09`, terminal `COLD_REFORWARD_12_PASS`).

Only these definitions were removed from `tests/test_email_store.py`:

- `_rewrite_required_identifier_case` (pre-edit AST SHA-256 `bfc62f575acc0972525919d1bf71d7a66ce558361f583714c1b63db59d7bb099`)
- `test_current_schema_accepts_case_insensitive_required_bare_identifiers` (pre-edit AST SHA-256 `867d1408f5590d71c64cada93fde353dc1161b26ed17dc89169eba1706306e6b`)
- `test_current_schema_accepts_quoted_required_identifiers` (pre-edit AST SHA-256 `86f538f7b61516fe539f895503245a7aade7ec884ce92b594e8ff5a9458c066e`)

The helper had only those two callers. Tokenizer coverage for quoted identifiers versus string literals, schema identifier/domain validation, exact cold read-only and journal checks, refusal cases, historical native fixtures, inverse oracles, and business assertions remain in place. No import cleanup or runtime behavior change was made.

`tests/test_email_store.py` SHA-256 changed from `7723fbbc6745d9074af13ab35227e67ff938a29e5547f58bf5f27cb94af02a22` before the edit to `5ea7cc9dc7c743f32bcc0a7757259387434b4016c08b6dad8abe89017d5ee5da` after it. The three removed definitions are byte-for-byte source available from `git show HEAD:tests/test_email_store.py`; their AST hashes above match that tracked source. A reviewer can reconstruct this pre-edit working file by restoring only those three definitions to their former anchors and verify the captured pre-edit file SHA above. That reconstructed immediate pre-edit AST matched all 277 remaining function/class AST hashes after removal; no remaining definition changed in this edit. Static search found no references to the removed names.

Validation:

- `/Users/derek/miniforge3/bin/python -m pytest -q tests/test_email_store.py::test_schema_tokenizer_canonicalizes_quoted_identifiers_not_string_literals` — **1 passed**.
- `/Users/derek/miniforge3/bin/python -m ruff check tests/test_email_store.py` — **All checks passed**.
- `/Users/derek/miniforge3/bin/python -m compileall -q tests/test_email_store.py` — **passed**.
- `git diff --check` — **passed**.

This is focused validation of the requested test-only deletion. It does not claim the whole file or suite is green, and it makes no runtime, capacity, deployment, or production behavior claim. Independent SPEC and QUALITY reviews remain required.
