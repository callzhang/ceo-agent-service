# Physical provenance transition: source constraints, 2026-10-18

This is a bounded source analysis for the approved physical provenance unit. It is not DDL, a selected migration, or runtime acceptance. B/C/D is concurrently being implemented by the sole runtime author; this note does not edit that code. The current selected intermediate retains `business_source_documents.body`; the successful actual11 rehearsal used an immutable earlier runtime snapshot.

## Immutable facts versus changing their storage representation

`app/action_business_definitions.py:178-193` currently makes an existing `business_task_signals` Action unconditionally immutable on UPDATE/DELETE. Its INSERT guard protects both the legacy public Signal ID and dedupe key. The approved unique-to-shared transition must move the first Signal's inline exact binding to a shared provenance FK when a second owner arrives. An ordinary UPDATE of the first Action therefore fails under the current declaration. The physical unit must explicitly translate the existing immutable-fact semantics into the new representation: source identity and all independent Signal facts stay equal while only their inline/shared representation changes in the same transaction. Do not remove Signal immutability generally, allocate a replacement public Signal ID, or silently bypass the guard in an application writer. Test logical mutations still fail, shared promotion succeeds, and an interrupted promotion leaves neither a partial reference nor changed fact.

`app/store.py` still defines the historical source body table with a unique identity key, NOT NULL/nonblank body, immutable UPDATE/DELETE/REPLACE triggers, and an integer Signal FK. Those are exact source declarations for historical readers/inverse, not rules that can be copied onto body-free provenance. The physical unit removes or translates the body-specific declarations rather than satisfying them with an empty body.

## Shared identity must not create a second copy of its fields

The existing Signal Action fields are source_type/ref/time, conversation_id/title, author ID/name/kind, source_document_id, context_json, dedupe_key, and created_at. `business_action_schema_parts()` emits generated columns and finite payload checks for every field; it currently requires every projected Signal field to be non-NULL. The exact binding additionally has the already approved identity key, complete body SHA-256/UTF-8 bytes, and proved locator metadata. A shared binding is not represented honestly by keeping its full identity both in each Action and in provenance.

The physical projection must return the same fact/binding API for inline and shared rows while the stored representation has one owner of the identity facts. Existing conversation_title/context_json/dedupe_key/Signal created_at remain per-Signal independent facts. The full identity is the unchanged eight-element source key, not just source_ref or body SHA. Same-reference versions and distinct Signal IDs/roles remain distinct.

The existing source lookup index includes source_type/ref/time and public ID (`app/action_business_definitions.py:151`), and the document index uses source_document_id (`:168-169`). Generated columns cannot read a parent table. Therefore retiring the inline shared fields necessarily changes that projection/query/index path; it cannot be achieved by changing the FK target string alone. Measure exact task+source-ref and ID/dedupe paths on a real copied corpus. Do not retain redundant original identities merely to preserve an old index. All inbound evidence/date/relation/Attention links continue to target the preserved Signal public ID.

## Old container identity and inverse are separate from Signal identity

The public source_document_id grouping is being retired by B/D in favor of the exact identity key. No permanent numeric alias is selected by this note. The physical unit must nevertheless state how historical source-document IDs, their created_at and allocator are treated in its inverse evidence. The original declaration uses AUTOINCREMENT; regenerating arbitrary IDs and then comparing only text is not proof of restoring the original source schema/facts. Declaration/inverse metadata or the bounded verified pre-cutover oracle can describe pre-forward container identity for rehearsal without storing another original-body copy in target runtime data. Explain any intentionally retired container bookkeeping explicitly; do not claim all source fields were preserved if they were not.

A frozen pre-forward copy cannot supply exact bodies for Signals created after forward migration. A current-state inverse must resolve those exact versions through recorded locators and check full binding SHA/bytes/key before materializing the historical NOT NULL body row. Missing exact bodies must expose an unresolved inverse, never blank text or restored stale receipts. The approved design stage5 already prohibits stale-DB rollback after the external-effect window opens and permits compatible inverse or forward repair. The physical release must select and rehearse that boundary before activation; this note does not choose a new write restriction or infer original-retirement authorization.

## Coordinator ordering and finite tests

The paired coordinator is `app/action_email_schema_release.py`, not a separate guessed paired-storage module. Its forward apply currently ends with canonical Email migration followed by Legacy owner migration before publishing both version markers. Current Legacy owner capture verifies the exact Business declaration and the existing `trg_sync_legacy_update_public_id` Actions guard, rather than the entire Actions table (`app/business_legacy_owner_storage.py:35-67,143-160`). A new Actions/provenance rebuild must preserve the applicable owner guard contract and compose with the exact manifests and paired inverse ordering; the full Actions shape is checked by its own schema selectors. The physical component must be composed with the existing canonical and inline-owner selectors; do not weaken declaration validation or rewrite historical fixtures as current.

Required proof remains: current source cardinality decides unique/shared representation; exact shared FK and strict payload/declaration checks; no original body in any target table/metadata archive; all Signal IDs and inbound links/roles preserved; unique-to-shared promotion atomic; new exact source writes and inverse/current-fact/old-code-write/reforward; unavailable new original produces the documented recovery boundary; independent current constructors; no provider calls from migration SQL; no real outbound sends; and fair before/after page, index, WAL and lookup measurements. The known historical20 retirement decision was subsequently resolved by Derek’s latest explicit authorization, recorded in `superpowers/specs/2026-10-18-provenance-original-retirement-decision.json`. Current availability refresh for other originals and the physical recovery proof remain prerequisites.

## Existing weekly-history search boundary rechecked without new provider calls

The installed `dingtalk-doc` Skill and its `references/doc.md` identify `doc +version-list` followed by `doc +fetch --version`; version0 is the initial version, not an invalid coordinate. The actual CLI exists at `/Users/derek/.local/bin/dws`. This local capability lookup did not fetch a provider body, list new versions, or change a profile/document.

The existing 2026-10-15 historical receipt has complete metadata pagination for103 listed versions across5 exact nodes:51/13/22/3/14 versions. It reports97 unique exact-bound versions,3 then-missing version bindings and3 then-unattempted versions. Its101 historical body calls include its explicitly recorded one-call accounting excess; do not erase or relabel that historical failure. No matching expected Markdown/body was found for the15-version cohort.

The 2026-10-17 unattempted receipt subsequently checked the3 named versions16/9/0 (plus its separately disclosed shape-discovery read); each returned exact node/requested version but no matching original. The missing-binding receipt checked117/114/1065 and likewise returned exact node/requested version with negative whole-body comparisons. Thus the six known omissions/binding gaps already have finite follow-up evidence. No newly known, unattempted coordinate emerged from this source audit, so no historical provider request was repeated. This does not prove permanent unavailability or prove current availability for older positive receipts. This source audit itself did not resolve retirement. Derek subsequently accepted permanent removal of the exact20-version cohort in the scoped decision above; their original availability remains unproved.

Audited metadata receipt hashes:

- `superpowers/specs/2026-10-15-business-weekly-historical-source-probe.json`: `7530ab9b4d240e14c01fa8ebe131ad96bfeeb847506959fa85bc4de026d472f6`.
- `superpowers/specs/2026-10-17-business-weekly-unattempted-source-probe.json`: `db2dd2dcedc406d56607921a233bc1f125fc721f401e47e7ad2278b54b7444aa`.
- `superpowers/specs/2026-10-17-business-weekly-missing-binding-source-probe.json`: `249939e615ba7f12638946abcc42a5723c3364d0af64a37c5bee5e5d29de0c95`.

## New backfill producer composition to verify before physical retirement

B/C/D changes `minutes_owner_backfill` to obtain the current meeting record from `get_minutes_info(...).result` before producing its new canonical WorkItem. The accepted A2 exact provider reader reconstructs the `meeting` component from an exhausted `list_minutes_page` exact-UUID match. Both paths are explicitly allowed by the source plan, but equal UUID alone does not prove their complete dictionaries serialize identically. The physical availability tests must compose the actual new backfill producer with its chosen exact source reader, rather than using identical minimal fake records at both endpoints as proof. If the full endpoint shapes differ, retain the real producing endpoint in operational coordinates or select the genuine list producer; do not add a per-row fallback or claim a matching body without the full hash/bytes/key checks. This is a physical-source availability requirement, not a demonstrated failure of the currently selected old-body backend. No real provider read or new locator/schema field was selected by this note.

## New-write locator capture is part of physical closure

Current `SourceSignal` (`app/task_semantic_service.py:54-66`) has source facts,
body and context, but no operational native/provider locator. Current
`_task_source_signal`/`_project_source_signal` construct that transient shape;
`apply_task_agent_decision` accepts `codex_session_id` and the caller obtains it
from the runner, but the source constructors do not bind an exact raw JSONL
line/leaf/range. `SourceVersionBinding.from_signal` permits an explicitly supplied
locator; that does not prove the actual writer supplies one. B/C/D legitimately
uses the still-selected old body backend and is not claimed to solve this.

The physical unit must cover newly written versions as well as historical
metadata conversion. Existing native read supports recorded raw-line SHA and
decoded leaf/range checks (`app/native_trajectory.py:241-314`); session UUID alone
is not such a binding. Capture an exact operational coordinate from the actual
producing runtime/provider path while the transient input is present, validate
its complete body against the incoming binding, and persist only that coordinate
and existing identity/check facts. Do not make runtime load research receipts,
guess the latest matching session, or introduce another original archive. A
synthetic positive should compose the actual source producer, metadata writer
and selected exact reader, followed by a new Signal and exact-source inverse.
Missing-coordinate behavior must remain honest and participate in the scoped
retirement/recovery contract; the historical20 authorization does not silently
authorize future unproved-body loss. This source read did not select a locator
capture algorithm, add a field to `SourceSignal`, or perform native/provider I/O.

Corrected after inspecting the complete prompt composition: removing the
WorkItem `summary` field from its metadata does NOT remove the current original
from the Agent input. `task_agent.py` passes `work_item=work_item` into
`render_task_semantic_context`; `source_bundle` marks that exact current version
`full=True`; `_document_projection` then emits `(0,len(body))` as the visible
range, and the resulting candidate context enters the native CLI prompt.
The2048-character limit applies to ordinary historical context, not that current
WorkItem. Existing `test_shared_source_prompt_does_not_duplicate_current_work_item_body`
asserts that original occurs once in the composed prompt. Therefore new Agent
source capture should locate this existing full native leaf/session/record and
validate exact bytes/key. No prompt expansion, archive, or synthetic write-back
is needed. For historical versions or producers outside this call path, inspect
the actual native files and all roles before declaring missing or considering
upstream. A source-code inference is not a substitute for native-file proof.
Current incoming observed text remains authoritative for its own quote checks;
a genuine source that becomes unavailable later is an honest unavailable read,
not a reason to introduce an unrelated per-write provider availability policy.
