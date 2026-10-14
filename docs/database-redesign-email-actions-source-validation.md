# Email action source oracle, 2026-10-13

The frozen source inventory covers nine existing email action tables (103 columns)
and four dependent tables (41 columns). `tests/test_email_action_source.py` and
`tests/test_email_action_children_source.py` execute the captured literal SQLite
DDL and objects in memory. The combined oracle builds all thirteen tables for
both captured parent variants and compares every child table's SQL, column
metadata, foreign keys, indexes, index columns, and own triggers. It also
matches the four child object sets against each variant's frozen related-object
capture, the twelve inbound foreign keys among the thirteen tables, and all
thirteen de-duplicated external trigger SQL declarations (eight from the nine-table
capture and five from the child extension).

The focused native cases establish source behavior that a later storage design
must account for: nine nullable `TEXT` public primary keys admit multiple
`NULL` rows; `email_actions` exposes an independent ordinary signed `rowid`
that can be updated, reused after deleting the highest row, set to zero or a
negative value, and allocated randomly at the signed maximum; its `TEXT`
identity can contain BLOB bytes. The direct retry child has an immutable
composite identity and a restrictive action parent. Feedback permits a null
`expected_current_action_plan_id`. The reply claim and receipt retain
restrictive plan parents, while SQL permits any valid receipt JSON update;
the application narrows enrichment to a previously empty SMTP acceptance ID. The
external dispatch triggers block account and message changes while a claim is
dispatching in the narrow synthetic setup.

The fixture substitutes only the classification, account, and message tables
needed for foreign-key and trigger closure; their full production definitions
and all other EmailStore objects are outside this test. These checks establish
captured source SQLite semantics, not target design, migration correctness,
runtime behavior, provider effects, native-file compatibility, or production
row correctness.

Focused verification: `python -m pytest -q tests/test_email_action_source.py
tests/test_email_action_children_source.py --basetemp=/tmp/email13-source-oracle-pytest`
reported **36 passed**. `ruff check tests/test_email_action_children_source.py`
passed. The task-owned temporary pytest directory was cleaned after validation.
