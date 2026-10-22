# Task2B report — partial semantic-reference implementation

Status: incomplete. This commit implements and tests the semantic-reference utility only. The requested forward migration, current-fact inverse, all-20-family conversion, allocator preservation, source/target declaration validation, and round-trip fixture have not been implemented. Do not use this as a migration or publication candidate.

Implemented in `app/task_entity_migration.py`:

- Build indexes by source kind + physical ID and by source kind + typed public ID.
- Translate feedback Workbench TEXT refs only on exact source-public-key matches; preserve unresolved strings and record sparse field/value/type markers.
- Resolve typed Action feedback public IDs to the actual target owner ID, including kinds with zero current rows. The composed Task3 converter must call this typed resolution as it rewrites source Action FKs.
- Restore canonical Workbench IDs to their exact source public TEXT key by owner identity.

Focused verification: `pytest -q tests/test_task_entity_migration.py` — 3 passed; Ruff and `git diff --check` passed. This does not satisfy Task2B's required forward/inverse conversion contract.

Commit: see the task handoff for the commit SHA.
