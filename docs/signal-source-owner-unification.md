# Signal source owner unification

## Frozen scope and source

Derek authorized removal of unused frontend adapters/queries, then one source owner per Signal. The adapter unit has no schema change. This unit changes only Signal source storage, not source availability, business adoption, Audit, authorization, retry or external effects.

Source: Store `2026-10-25.2`, Email54, code baseline `dc07ddcbe`. Source manifest contains478 objects; canonical JSON SHA256 `7b4c91875faf9cf1bc57f912fc692b7c2a3966a03b9b2e384b2d8470ce0c832b`. The215-object standalone Actions scaffold digest is `679e4bd4a5b9166a10754884a7dcab0b7b33b5c9137e01d270888b5cc2ff46a0`. The full478-object manifest is the actual source contract; the standalone scaffold is not the current Task/Run assembly. These are frozen schema declarations, not a claim about the latest production data.

The old Signal payload has17 ordered slots: the12 existing `SOURCE_FIELDS`, `provenance_id`, then `conversation_title`, `context_json`, `dedupe_key`, `created_at`. A sole owner stores source metadata inline; two owners share `provenance`. The target Store `2026-10-25.3` / Email54 stores only the existing `provenance_id` plus those four own facts (five slots). Every source, including a sole owner, lives in the existing immutable `provenance` table. No body, new business field or generic history container is introduced.

Target declaration:476 objects, canonical JSON SHA256 `234e17843afc171b4fa8cb42f3e1d959ef2ba415f43e295dc7db640ab3d02221`. It removes the two inline source indexes and Signal leaf trigger; locator validation remains on the immutable provenance parent. The current Actions target also retains the declared immutable retry-request insert guard; that generic current-schema guard is separate from Signal migration behavior. Actions table/checks and immutable-update trigger change to the reference representation.

## Exact ownership and retirement

| Existing field(s) | Sole target owner | Handling |
|---|---|---|
| source_type, source_ref, source_time, conversation_id | provenance | Preserve exact values/types and source identity |
| author_user_id, author_name, author_kind, identity_key | provenance | Preserve; identity_key remains unique |
| body_sha256, body_utf8_bytes | provenance | Preserve metadata only; no source body copy |
| native_provenance_json, provider_locator_json | provenance | Preserve exact locator text and existing validation |
| provenance_id | Signal | Required FK for every Signal |
| conversation_title, context_json, dedupe_key, created_at | Signal | Preserve all existing independent facts |
| public Signal identity (legacy_key) and physical Actions id | Existing identity columns | Preserve both namespaces and every reference |

Retire current inline-source generated columns, inline identity/source indexes, inline/shared UNION, COALESCE reads and second-owner promotion. Preserve the source factory with old17-slot semantics only for explicit migration/inverse tooling. The referenced-source index and parent source index support the single join. All immutable source and Signal rules remain; creating a second Signal no longer edits the first Signal.

## Explicit release and current-data inverse

`prepare_signal_source_owner_release(path)` validates the exact source manifest/pair and returns the standard `publish`, `verify_loaded`, `rollback`, `finalize` interface. Only the Actions declaration/payload and provenance parent rows change; unchanged rows remain byte-identical. Source marker/time, allocation watermarks and the existing FK finding set are preserved. Unmapped source fields or malformed source bindings fail before mutation.

Forward creates missing provenance parents from exact inline values, rewrites each Signal to its reference and own facts, and rebuilds Actions with stable physical/public identities. It never infers a source from a title, similar text or numeric collision. Target runtime writes only the reference representation.

Inverse operates on current data, including Signals created after publication: a source with one Signal is represented inline in the old17-slot format; a source with multiple Signals remains shared. Parent rows moved inline are retired in the explicit schema transaction, preserving their source facts in the Signal. Existing shared sources retain parent IDs. No original body/payload backup or runtime fallback is added. Old code must cold-start, read and write the inverse database, followed by successful forward migration.

## Ownership boundary and gates

The other task owns `store_schema_release.py`, `current_publication_sources.py`, selector/preflight integration tests and freezing/retiring the old Action trigger release. This task owns Signal current DDL/runtime, version constants and its independent release/inverse module/tests. Integration accepts only the exact approved25.2/54→25.3/54 pair, without extending historical compatibility.

Required evidence: scoped regression tests; exact frozen source digest; latest production-derived copy preserving logical source bindings, typed non-Signal data, references, identities, sequences and original FK findings; new one-owner/two-owner Signals; inverse; old-code cold read/write; reforward; affected detail/source/history reads. Test outbound providers remain substitutes. Release follows the standard deploy gate (including its clean-clone full-suite test); only after successful readback are task backups and temporary artifacts removed. No production migration has been performed by this design document.

## Validation state

The implementation, independent reviews and current-production copy/old-code inverse proofs passed; see [executed acceptance](signal-source-owner-validation.md). Selector integration, full Quality, standard deployment/readback and cleanup remain pending.
