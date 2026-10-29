# Business and provenance source preparation

Source preparation only for the approved ten-core-table redesign. No target
schema, release version, migration, source retirement or production acceptance
is selected here. The eventual immediate published predecessor is unbound.

A root readonly mode=ro/query_only single DB read transaction at 2026-10-14T08:02:00.584797+00:00
verified the25 selected tables below. Production checkout HEAD was separately
observed as `d9d340939cc5665cb2b9e26ee3f474216981ace6`; code HEAD and DB snapshot are not atomic.
The current schema totals247 fields,46 indexes (including automatic indexes),
42 outgoing FK constraints and9 owned triggers. Source research counted63
lexical CHECK clauses; this is not a substitute for freezing exact native
CREATE SQL, expressions and both actual/fresh variants in the next capture.

| Current physical table | Fields | Indexes | Outgoing FK | Owned triggers | Rows |
|---|---:|---:|---:|---:|---:|
| business_anchors | 6 | 2 | 0 | 0 | 50 |
| business_attention_events | 8 | 1 | 2 | 0 | 13 |
| business_attention_items | 16 | 2 | 3 | 0 | 8 |
| business_attention_proposal_tasks | 3 | 2 | 2 | 0 | 17 |
| business_attention_tasks | 3 | 2 | 2 | 0 | 17 |
| business_legacy_links | 12 | 4 | 10 | 0 | 0 |
| business_project_candidates | 8 | 1 | 3 | 0 | 10 |
| business_project_context_revisions | 5 | 1 | 1 | 0 | 6 |
| business_project_evidence | 3 | 1 | 2 | 0 | 30 |
| business_projects | 12 | 1 | 1 | 0 | 32 |
| business_source_documents | 4 | 1 | 0 | 3 | 181 |
| business_task_anchor_links | 8 | 2 | 3 | 0 | 96 |
| business_task_date_evidence | 10 | 1 | 2 | 3 | 0 |
| business_task_events | 8 | 1 | 2 | 0 | 881 |
| business_task_evidence | 4 | 2 | 2 | 0 | 885 |
| business_task_relations | 7 | 3 | 3 | 0 | 0 |
| business_task_signals | 13 | 3 | 1 | 3 | 818 |
| business_tasks | 19 | 3 | 1 | 0 | 584 |
| business_work_cluster_tasks | 3 | 2 | 2 | 0 | 16 |
| business_work_clusters | 3 | 0 | 0 | 0 | 16 |
| service_bugfix_candidates | 12 | 2 | 0 | 0 | 3 |
| todo_evidence_candidates | 15 | 5 | 0 | 0 | 3 |
| wechat_memory_candidates | 22 | 1 | 0 | 0 | 0 |
| work_projects | 25 | 1 | 0 | 0 | 975 |
| work_todos | 18 | 2 | 0 | 0 | 3327 |

## Concrete dependencies and body-read gap

This source set follows the approved global CSV:12 business parents/candidates,
one source-document family,11 existing evidence/association action families
and business_legacy_links. Four follow-up/sync families remain in their own
unit. They currently declare four inbound edges to business_tasks/signals;
their future action representation must be rechecked before this publication.
No source age or zero row count justifies cancelling those contracts. Scalar
legacy project/Todo/source IDs must not silently become guessed FKs.

Source research observed181 document identities used by818 signals, with114
identities shared by multiple signals. A shared source_ref does not establish
a shared version. source_document_key in app/task_source_documents.py hashes
source type/ref/time, conversation, author identity/kind and exact evidence
text. Existing identities and the multiple evidence-role/project relationships
must survive; no merge by title, reference alone or body hash is authorized.

The original-body table contains about5.8MB of text. Existing Store signal
reads join document.body; ProjectContextService.apply and historical project,
attention and suggestion validators read it in their caller's writer
transaction. Task retrieval also validates stored owner evidence against that
body. New command evidence_text validation is a separate path. Root verified
the Store writer, shared identity function, ProjectContextService and retrieval
code against the research findings. Deleting body alone is not functional
conversion. No provider I/O should be introduced while holding the writer
transaction as an incidental workaround.

Minutes and report scanners can fetch current source material, but no checked
generic resolver recreates an arbitrary historical document identity. Exact
version reconstruction/canonicalization and availability remain unverified.
The single console signal is an authored Console action linked to a task/event;
its original text is constructed from then-current actor/action/title and has
no demonstrated separate immutable body receipt. The single service_data_repair
signal has no demonstrated current writer or original repair artifact. Neither
ref exactly matched retained task input or native-session references in the
bounded check; no task-input-to-native chain was established for them. No
provider fetch, native transcript copy or guessed origin was used.

The approved spec/CSV already requires accurate original-source verification
before body retirement. These are unresolved source questions, not permission
to retain a document archive in provenance or to claim migration success.
The next bounded unit must capture literal actual/fresh declarations, full
external SQL/FK closure and these body-dependent callers, then demonstrate
exact version/source read behavior before selecting a business/provenance
migration boundary. Adopted results, legacy/formal distinction and all actual
evidence rows remain separate preservation requirements.
