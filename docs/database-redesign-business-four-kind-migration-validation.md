# Four-kind Business finite migration validation, 2026-10-15

This commit adds an unselected source-bound migration and inverse for Task,
Project, Anchor and Project context revision. It adds three VIRTUAL typed public
aliases and full UNIQUE indexes to the existing Business declaration. The
fifteen retained children keep their public ID columns; nineteen FK endpoints
move to those aliases, while twenty-one other FK groups keep their source
parents. No Store constructor, caller, coordinator, version marker, provider,
production database, push or deploy selects this code.

The fresh fixture in `tests/fixtures/business_four_kind_fresh_declarations.json`
was generated from the current `AutoReplyStore` constructor before any Store
bootstrap change. It contains declarations and auxiliary SQL only, with no
private rows. Actual tests reconstruct the accepted actual parent and Attention
DDL from the source oracle, retain the verified identical auxiliary declarations,
then recheck the full actual fingerprints. The accepted oracle is
`docs/superpowers/specs/2026-10-15-database-redesign-business-core-native-source.json`.

The focused migration tests use native SQLite transactions and both actual and
fresh declarations. They populate all four parent kinds and all fifteen child
families with synthetic facts, including colliding signed public IDs, a two-row
Task merge cycle, Project/Anchor/Context references, BLOB and infinity storage
classes, nullable edges, six hidden child ROWIDs, and a deleted high child
sequence value. They compare every source-shaped value and `typeof`, all 40
child FK groups, 29 child indexes, three date triggers, four parent and nine
child sequence waters, `quick_check`, and native FK findings. Each translated
endpoint rejects a wrong-kind parent; referenced parent deletes and public-ID
updates fail under native FK enforcement. Inverse includes target writes,
deletion and Project rebind made after preparation, then reforward reads them
back. Empty sequence rows stay absent. A preexisting unrelated `runs/tasks`
FK finding remains identical. Malformed source/target declarations, broken moved
references, tampered inverse metadata and injected DDL failure are refused or
rolled back without a committed schema change.

This is code and local SQLite validation only. Selecting the coupled
Store/Email release, replacing current callers and bootstrap, copied-database
proof, CI and deployment are later tasks under the reviewed integration plan.
