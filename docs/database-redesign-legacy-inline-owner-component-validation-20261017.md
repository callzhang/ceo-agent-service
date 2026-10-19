# Unselected WorkTodo import owner component

`app/business_legacy_owner_storage.py` is a transaction-owned component. No
constructor, current schema factory, importer, selector, or release path calls
it. It has not been run against the actual database, selected for runtime, or
deployed.

The component recognizes the current Business declaration and the four exact
named LegacyLinks objects in the source capsule. It also requires the exact
Actions WorkUpdate guard. Capture records declarations and the old allocator
water, without copying source rows. Forward compares immutable captured
declarations and guard while reading valid links live, so a supported insert
after capture does not fail merely because its retired allocator advanced.
At forward execution it reads live links and
accepts only valid WorkTodo-to-formal-Task pairs. It refuses other source or
target alternatives, missing owners/targets, and unsupported declarations
before dropping the old link. It also rejects MAIN or TEMP tables that declare
an inbound FK to the independent link; dropping that parent with FK checks off
would otherwise leave the child broken. The old link-local `id`, `created_at`, and
allocator are intentionally retired container metadata even for a supported
populated source link. The translated business fact is the owner's typed public
identity plus the target Task public identity.

Forward uses SQLite's native `ALTER TABLE business ADD COLUMN` for one nullable
`import_task_id` FK to `business(task_public_id)` with a WorkTodo-kind CHECK.
It drops the old independent link and its one Action guard. The old link's two
own triggers disappear with its table. The source WorkTodo `project_id` payload
is unchanged. The two helper calls read/assign the target under the caller's
transaction, returning `created` or `skipped` and rejecting a conflicting
target. Assignment checks that the Task ID is an actual integer and resolves
to a formal Task before comparing it with the current slot, so `None`, float
and boolean values cannot masquerade as an idempotent retry. Inverse validates
every supplied old link declaration and the Action
guard against the known source before DDL. It checks live MAIN and TEMP schema
dependencies in a metadata-only in-memory copy using SQLite's native qualified
`ALTER TABLE main.business DROP COLUMN`. The same probe covers TEMP views,
triggers and a TEMP table shadowing the Business name. It then uses native `DROP COLUMN`,
verifies the exact original Business declaration, restores old link declarations
and guard, and creates
valid old WorkTodo/Task rows from live current owner facts. Old SQLite defaults
assign local IDs and timestamps to inverse-created links.

Focused synthetic verification with `/Users/derek/miniforge3/bin/python`:

- RED: the new test module initially failed collection because the component
  did not exist.
- RED repair: separate tampered inverse Action guard and LegacyLinks DDL cases
  showed that the first version accepted a no-op guard or failed only after
  `DROP COLUMN`; a supported insert after capture failed on changed allocator
  water. A current owner-column view exposed a native `DROP COLUMN` failure
  during inverse. TEMP view and trigger dependencies later exposed the same
  failure because the first probe cloned only MAIN. Each has a focused regression.
- GREEN: `python -m pytest -q tests/test_business_legacy_owner_storage.py`
  passed 17 tests in 43.33 seconds. A managed temporary database is built by the current12
  `AutoReplyStore` constructor once, then copied to independent in-memory
  databases. The fixture seeds real Actions and Business rows. It checks native owner and
  target restrictions, explicit owner-delete retirement, nullable/kind/FK
  behavior, two owners sharing one Task, idempotence and conflict, exact empty
  source/no-allocator inverse, supported populated source, old lookup after
  inverse and reforward, malformed declaration and unsupported alternative
  refusal, and unrelated schema/object preservation. Full stored values,
  SQLite storage classes, physical IDs and unrelated sequence rows are
  compared across forward and inverse for both Actions and Business. Dangling
  old owners/targets and an unexpected external link view refuse before DDL;
  MAIN and TEMP owner-column views and a TEMP trigger also refuse before inverse
  DDL, preserving both schemas. SQLite likewise refuses a TEMP Business name
  shadow in the metadata probe before the live operation.
- QUALITY repair RED: a populated child table with a declared FK to the old
  link passed capture, and dropping the parent would leave a foreign-key
  violation. `None` on an empty owner slot and a float/boolean equal to an
  assigned integer returned `skipped`. GREEN: targeted tests for MAIN/TEMP
  inbound FK refusal and strict assignment ID validation passed 3 cases; the
  full focused module result is recorded below.
- `python -m ruff check` and `python -m py_compile` on the two new modules,
  plus scoped `git diff --check`, passed after removing one unused test local.

The component is not the selected 11-table migration. The caller unit still
needs to remove the unreachable official-project branch, replace the current
importer link reader/writer, keep source digest input stable, and align current
factories and strict validators with the omitted link and Action guard. A
separate high-stakes proof must compare a consistent actual source copy and
the complete forward/inverse path before any publication claim.
