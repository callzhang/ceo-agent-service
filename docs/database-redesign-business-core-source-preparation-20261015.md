# Business core source preparation, 2026-10-15

Bounded read-only research at codeb670aea428ec63e993e6837a18e585985c951ee6
and the verified264source snapshot. This is not constructor selection, a migration,
a published predecessor binding, body retirement or fresh-source parity. No future
Store/Email version or new DB field is assigned here. Exact metadata/counts are
`docs/superpowers/specs/2026-10-15-database-redesign-business-core-source.json`.
There are no raw records, names, titles, body texts or source URLs in that artifact.

| Existing family | Rows | Source fields | Approved direction |
| --- | ---: | ---: | --- |
| business_projects |32|12| business |
| business_tasks |594|19| business |
| business_task_signals |833|13| actions.signal_fact |
| business_source_documents |188|4| real source identity inline/shared provenance; verify recoverable-body retirement |
| business_task_evidence |897|4| actions.task_evidence |
| business_project_evidence |33|3| actions.project_evidence |
| business_task_date_evidence |0|10| actions.date_fact |

The seven families total65fields/2577rows. Every current column matches the
approved CSV name/type/nullability/PK position; missing/stale/mismatched counts
are zero. This does not by itself establish a closed migration unit.

## Existing transaction and endpoint closure

Task mutation currently writes documents, signals, Tasks, task evidence, date
evidence and Task events in one transaction (six families/58fields). The replay
path joins the deduped signal to the latest original Task event to return the
original command result. Evidence can be copied elsewhere later; events therefore
are necessary replay facts, not optional display logs. See
app/task_semantic_service.py:240-261,445-485.

The Project path adds projects, anchors, project evidence and context revisions
(four families/26fields), making10actual behavior families/84fields together.
The canonical Project anchor is uniquely typed; adopted Project context includes
goal/scope/owner/responsibilities/facts and citations. The7core families have15
direct external families/137fields in their declared dependency closure, including
relations, anchor/cluster/Attention membership, legacy links and the separately
prepared business Followup/Todo-sync families. Explicit endpoint remapping is
needed when these parents move; this is not permission to migrate all15now.

The disabled Followup preparation retains existing business parent endpoints.
It must not be duplicated or assumed already published. Source-only metadata
here observes the current19/51 producer; the future actual Email/Followup
predecessor may change the link closure and must be bound from real declarations.

## Body identity and adopted evidence

source_document_key hashes a tuple of source type/ref/time, conversation, author
identity/kind and evidence text (app/task_source_documents.py:42-65). It is not
just a generic body hash. Source documents keep one shared body which current
readers join as evidence_text. Current Task/Project signals useWorkItem.summary;
Memory/session provenance uses cited excerpts while explicitly recording that
the original was not reread (app/task_agent.py:1014-1077). Thus body can contain
an extracted business summary rather than the provider's entire original.

Signal context_json also holds actual locator/owner/assignment/reply-to/external
Task facts; legacy import can copy an entire legacy row. Do not clear that JSON
as though every member were redundant text. Necessary adopted results live in
Task state/description/owner/date, immutable event snapshots, Project CRM state
and context revisions. Preserve these independently of original-body retirement.
ProjectContextService verifies existing citations in the caller's transaction
(app/project_context_service.py:35-96). No new review/authorization policy is
proposed by carrying that current behavior through the storage mapping.

## Next bounded preparation

Freeze the finite10behavior-family source oracle and incoming endpoint map,
compare actual/genuine published-code fresh declarations and native key/FK/
transaction/replay behavior, and refine the approved CSV into exact projections.
Use the actually published predecessor for release binding; do not reserve a
version or call the current candidate a published source.

For each recoverable source, verify the real locator/version and exact original
identity/bytes before retiring its body. The present source inspection does not
prove any of188bodies retrievable. An Agent summary and a currently fetched provider
body are not automatically identical historical inputs. Retain necessary adopted
business facts and their direct references; create no generic source-body archive.
No unused helper was proved by this bounded inspection, and zero date-evidence
rows do not make its active deadline/writer path dead.

## Complete native source freeze, 2026-10-15

The next bounded preparation above is now recorded in
`docs/superpowers/specs/2026-10-15-database-redesign-business-core-native-source.json`
and `tests/test_business_core_source.py`. The earlier seven-family artifact remains
an earlier observation; the new artifact freezes all ten existing behavior
families, 84 fields and 3,526 actual rows. Events add 8 fields/893 rows, anchors
6 fields/50 rows, and Project context revisions 5 fields/6 rows. All 84 actual
columns match the approved global CSV's type, nullability and PK position.

The actual source is the root-owned immutable Store19/Email51 backup at published
code `7dadc492c1c62d5b0624f2f97dd0c89f4f358646`, SHA-256
`c3fcfb543032ab632c948cd9540739a698dda83a33047b0c9388d636c8bbf67e`,
484,175,872 bytes. Inspection used SQLite `mode=ro`, `query_only=on` and an explicit
read transaction, exported metadata/counts/sequence waters only, then closed its
handle before tests. No source copy was created by this preparation. The fresh
variant was genuinely constructed by that exact code's published AutoReplyStore
and EmailStore in a separate subprocess/archive and an explicit temporary DB.
This is an observed current source binding, not a reserved future predecessor.

The complete ten-family outgoing closure is internal: 12 FK groups/13 FK rows.
Its incoming external closure is 12 physical families, 118 fields and 23 FK groups,
replacing the earlier seven-family external summary for this complete boundary.
The exact literal endpoints and external table DDL/xinfo/FKs are frozen in each
variant. They require endpoint remapping when their business parents are actually
replaced; this does not add those children to the current migration scope. The
nine disabled Followup/Todo-sync families keep their separately prepared ownership.

Native validation and its limits are recorded in
`docs/database-redesign-business-core-native-source-validation.md`. No body has
been proved recoverable or retired, and zero date-fact rows do not retire the
active date path. No target field, version, constructor, writer or runtime policy
was added. Original replay events and adopted Project context remain necessary
business facts independent of any later source-body retirement decision.
