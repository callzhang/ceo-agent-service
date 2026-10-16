# Existing eight Tasks kinds: native writer obligations, 2026-10-16

Read-only source analysis at df113cb4; no target runtime selection. This is a finite preparation report for converting the existing eight kinds, not an implementation plan or acceptance of their conversion. New twelve-kind source writers are a separate unselected component. Final destination remains ten core tables plus Email migration ledger.

## Exact mutation inventory

The two live DAO source files contain116INSERT/UPDATE sites:105UPDATEs,6directINSERTs,5insert_task calls. Line anchors refer to this commit before conversion; DELETE and direct read conversion is additionally required.

| Kind | UPDATE count | Exact UPDATE call anchors |
| --- | ---: | --- |
| reply_tasks |54| app/store.py:9147,9221,9250,9704,9866,9868,12489,12544,12665,12719,12828,12983,13046,13084,13237,13259,13279,13319,13356,13438,13498,13549,13626,13697,13811,13921,13999,14030,14091,14121,14598,18283,18393,18477,18533,18540,18565,19037,19232,19305,19332,19395,19512,19566,19628,23041,23215,23285,23359,23422,23580,23629,23726,24025 |
| reply_task_inputs |3| app/store.py:8841,9151,18517 |
| work_summary_inputs |8| app/store.py:25903,25939,26048,26076,26108,26121,26138,26157 |
| meeting_alignment_jobs |16| app/store.py:15866,15900,16666,16756,16801,16844,16872,16900,16925,16943,17017,17070,17114,17181,17195,26177 |
| okr_review_requests |4| app/store.py:17534,17613,17645,17660 |
| weekly_okr_analysis_jobs |7| app/store.py:11158,27764,27786,27813,27850,27907,27961 |
| workbench_tasks |4| app/workbench/store.py:298,328,512,1215 |
| workbench_turns |9| app/store.py:8085; app/workbench/store.py:662,703,730,1431,1824,1910,2137,2466 |

| Kind | Existing insert_task | Direct INSERT |
| --- | --- | --- |
| reply_tasks | none | app/store.py:9166 plain;9813UPSERT |
| reply_task_inputs | app/store.py:8586IGNORE |9195,18364IGNORE |
| work_summary_inputs | none |25849UPSERT |
| meeting_alignment_jobs |15893IGNORE | none |
| okr_review_requests | none |17466UPSERT |
| weekly_okr_analysis_jobs |27744IGNORE | none |
| workbench_tasks | app/workbench/store.py:215 | none |
| workbench_turns | app/workbench/store.py:502 | none |

Three UPSERTs must remain native single statements. Reply9813 copies excluded fields, increments old input_version and clears physical pointers. OKR17466 only updates existing failed rows. Work-summary25849 excludes processing/done/skipped rows. Translate conflict targets to generated source columns with the exact record_type partial-index predicate. Existing-field copies use encoded excluded.payload_json leaves; do not decode/re-encode BLOB/nonfinite scalar facts.

## Minimal required interface and native statement shape

The smallest common capability is a finite INSERT/SET SQL-fragment compiler, with ordered bindings, rather than a new execution/transaction layer. Bound values use the existing native scalar codec; trusted insert_json/update_json use encoded JSON SQL as in state_storage._json_assignments. Structural assignments are existing legacy_key,parent_id,current_input_id,claimed_input_id. No observed runtime writer supplies physical id. The caller retains original verb/IGNORE/UPSERT, target alias, leading CTE, FROM, WHERE, RETURNING, binding order and transaction ownership. Use explicit task20_column references; no regex SQL rewriting, new business fields, row-loop merging, blanket materialization, implicit locks or commits.

All RHS expressions in a native UPDATE observe the original row. At store18393, new input_version/current_input_id are assigned while claimed_input_version/claimed_input_id deliberately receive OLD versions. Conditional status at9221 determines attempts/lock/error/availability from OLD status. UPDATE FROM19395 uses receipts.outcome. CTE claimers16666/16801 select and update candidates atomically. Native executemany16872 retains status/lock/token CAS. The finite dynamic SET builder16756 has its allowed fields at16705 and OLD-status lock expression16748. Correlated aliases include reply_tasks/tasks/historical, meeting_alignment_jobs and job/stale. A helper may not collapse these SQL scopes.

Pinned Python3.13.12/SQLite3.50.4 synthetic in-memory source probe found plain UPDATE RETURNING starts an implicit transaction (rowcount1; rollback undoes); leading WITH UPDATE RETURNING does not (rowcount-1; rollback leaves change). Meeting claimer16666 currently has no explicit BEGIN; ready-delivery16801 already uses _immediate_write_transaction. Preserve those original outer shapes. State update_state_rows is not a drop-in because its _lock requires and mutates a caller transaction; borrow only finite JSON codec/expression construction.

Omitted defaults execute as SQL inside the native INSERT. Explicit None differs from omission. Complete target payload includes nullable fields as JSON null. Six numeric kinds retain their existing AFTER INSERT public-key fill; return actual cursor lastrowid and retain post-insert reads. Physical pointer assignments stay in SET so native UPDATE OF triggers fire even when assigned unchanged. Native constraint precedence/IGNORE behavior must be differentially verified; this inventory does not prove equivalence.

## Fourteen RETURNING sites

Nine full meeting rows: store16666,16801,16844,16900,16925,16943,17070,17114,17195. Return physical id as id, legacy_key, then existing meeting source fields except public id, explicitly aliased. Mapper15797 converts legacy_key to the public model id, while16828 inserts row.id into the physical delivery-claim FK. A public projection alone loses this distinction. Unqualified generated target columns are valid in SQLite RETURNING; do not depend on UPDATE alias qualification. One public meeting id17017 stays cast(legacy_key as integer). Four physical reply ids19332,19395,19628,23422 stay id. No observed INSERT RETURNING.

## Direct reads and deletion outside projection constants

Convert source-field references at store8066; meeting15849,15908,16041,16218,16242,16272,16466,16601,16639,16790,17205; OKR17506; reply23183; workbench/store229,275,970. Keep structural identities and pointer references physical. Reply duplicate cleanupDELETE18553 includes source business predicates. The central task_storage._projection607 preserves public input pointers, WB public parent keys and retired final_text sentinel. task_id_expression586 remains structural. Existing workbench child projections92,96,106 join only physical identities/kinds and need no source-field substitution.

## Verification boundary

Bounded researcher inspected both DAOs and task_storage source. Root reread decisive old-RHS18393, UPDATE FROM19395, mapper15797, CTE16666 and delivery-return16801/16828 anchors. No caller or helper change has been applied. Before activation, require complete canonical implementation, genuine old-eight native source differential tests including source error/transaction priority, current public DAO/CAS/recovery proof, copied forward/current-fact inverse/true predecessor writes/reforward and native CLI fake-outbound/UI/business-result/receipt acceptance. Public REAL projections retain the separate documented scan boundary; guarded Tasks12 single-row reads remain indexed. No production effects were performed.
