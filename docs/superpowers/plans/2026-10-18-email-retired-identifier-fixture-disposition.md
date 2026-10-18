# Retired Email identifier-normalization fixture: source disposition

Proposal only, do not apply while actual-copy43535 runs. Current accepted R3
pins app/test/docs; no runtime or test is changed by this note. Full final design
and all business/copy/capacity/CLI/UI/release requirements remain unchanged.

## Observed mismatch

Runtime-unit controls97046 reached the quoted-positive test's setup and failed:
_rewrite_required_identifier_case expected17native physical email_* parents,
found0. The helper is a synthetic sqlite_schema rewriter, using261identifier
names,186replacements,17tables/25indexes/32triggers/58SQLdefinitions. Its only
callers are the bare-case and quoted-identifier current-schema positive tests
in tests/test_email_store.py. The accepted current12 graph has one emails parent;
the corresponding17native parents were folded into typed containers. The helper
cannot represent selected current schema without a new normalization framework.

## Current contract versus former test allowance

The accepted current_canonical_email_parent_is_current compares the full actual
owned declaration with the selected target hash, then refuses retired parents.
It explicitly supports that produced declaration, not arbitrary hand-rewritten
schema text with the same SQLite meaning. Runtimeunit adds only existing domain
validation of object names before the unchanged hash. A quote/case text rewrite
is not created by normal service writes, CLI commands or migration publication.
Source migrations also recognize exact known declarations. The old parser's
lexical equivalence behavior does not prove current pair preflight supports
unrecognized literal schema variants.

Preferred next test-only disposition: retire the2current-positive tests and their
now-unowned native17table mutator instead of adding a runtime compatibility layer
or weakening declaration checks to satisfy them. This cancels obsolete test-only
schema mutation machinery, not task/email business functionality. Independently
review this interpretation against current source and approved design before
selection. If a real current caller/contract requires normalizing quoted/case
rewrites, supply that source evidence and retain meaningful coverage; do not
label a real behavior obsolete merely because a test fails.

Keep tokenizer quoted-identifier vs string-literal unit test, nontext metadata
and schema-identifier domain checks, exact current cold read-only/journal/no-write
checks, malformed column/unique/extra-column refusal checks, all historical native
factory and inverse oracles, and every public business/locator/classification/
plan/receipt/claim assertion. No new mirror-of-hash test is necessary solely to
replace a retired unsupported-format positive. No production behavior or new
policy is proposed. Remove no other helpers/tests based on this note.

After actualcopy ends, a bounded sole author can apply only these2tests+their
unreferenced helper under a fresh scope and source review; static reference/AST/
diff checks and independent SPEC then QUALITY must confirm no surviving reader
or assertion was lost. This does not establish whole-file test green: any other
unadapted native test fixtures need separate evidence/disposition.
