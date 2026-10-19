# A1 production-copy acceptance

This is acceptance evidence for the unselected reference-only selection schema
component. It does not activate a Store version, verify runtime selection
callers, publish a release, or complete the four-unit model simplification.

The parent agent performed this run on 2026-10-19 using Python 3.12.11 and SQLite
3.53.4. Production was accessed only to create the earlier read-only online
SQLite backup; no production migration or outbound provider operation occurred.
No service worker was started against the candidate database.

| Check | Result |
|---|---|
| Frozen source | Deployed code lineage `d6a1b024ca38aa25ec444a06fea7ef31512b9a56`; database 472,539,136 bytes |
| Source schema SHA-256 | `052ac4fcff4540471376b5700da4db6aac6099548da7ab5c8f85b8dcb99d9e51` |
| Source fact inventory | 100,561 Actions; 36,582 Tasks; 254,889 Runs; 9,487 Emails; 5,171 Business; all 12 stored tables hashed |
| Original selection rows | 0; nonempty old-source selections are covered by component fixtures |
| Forward conversion | Passed in 44.62 seconds; original stored facts and allocator rows unchanged |
| New current fact | Added one local selection bound to a real approved review and its exact candidate's unique option key; new Actions count 100,562 |
| Inverse | Exact full source schema recreated, added selection retained, branch text reconstructed from its bound immutable option |
| Other business data | Every other table's stored-value digest unchanged, including SQLite storage classes and payload bytes |
| Actual published old code | Cold-opened inverse database with archived deployed `app.store.AutoReplyStore`; schema manifest current and new selection readable |
| Old-code identity proof | Asserted loaded `app.store.__file__` was beneath the archived published-code directory |
| Reforward | All current stored table facts exactly equal to the target facts captured after the new selection |
| Structural checks | `quick_check=ok` throughout; final `integrity_check=ok` in 20.39 seconds |
| Foreign keys | Preserved existing finding `runs / row 190042 / tasks / FK 1`; no new finding. This is not a claim that the original database has zero FK findings. |

Both inverse and reforward used the candidate's current data. The source backup
was not restored over the candidate. The inverse carried only the source reply
variant metadata, not business-row snapshots. The old-code opener started in a
fresh process; it loaded the archived production code, checked the complete
schema manifest, and read the post-forward selection.

The temporary acceptance inputs, code archive, candidate database and detailed
hash report live under `/private/tmp/ceo-model-simplification-0zjph5co` while this
implementation remains active. They are working artifacts to clean when the
task ends, not retained historical backups or application storage.

This initial full-copy roundtrip used component code hash
`3dd3f0a7bea9428e6d71e842a5b8d1b0162cf11c680820fb55e7112f51cf9de0`.
Independent QUALITY review subsequently identified order-sensitive comparison
of preexisting FK findings. The correction compares sorted complete tuples,
preserving duplicate findings. Its regression produced four genuine failures
before the change and seven focused passes afterward. QUALITY rereview approves
the frozen component; declarations and all source factory hashes are unchanged.

The parent then replayed **final code**
`0c815aea4ff006f6bac9d1876b8865031096fedd0905ba29b2f42d1d3c8e5825`
on the same current candidate: inverse reproduced the exact full source schema
and retained the new fact; actual archived old Store cold-open passed its full
manifest and decoded the added selection's branch with the correct option key;
reforward reproduced every current stored table digest; final
`integrity_check=ok` and the original one-FK finding were unchanged. The detailed
body-free result is committed in
`superpowers/specs/2026-10-19-business-model-a1-copy.json`.

This completes the unselected component's copy/schema-inverse acceptance.
Runtime caller conversion, versioned publication, UI/native CLI acceptance and
production readback remain separate work.
