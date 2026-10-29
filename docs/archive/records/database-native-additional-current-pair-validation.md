# Additional native-copy harness: current paired candidate

The scheduled, work-summary and Workbench offline acceptance cases now start from the same published Store/Email candidate shape as the lifecycle harness. The historical `source.sqlite3` remains unchanged. `prepare` copies that source to `candidate.sqlite3`, publishes the paired Store2026-10-14.1/Email52 release, takes a SQLite backup at `pre-native-target.sqlite3`, and reserves candidate-only high Task IDs before any case allocates a Task. Current store constructors open only after publication.

After a case, `main` compares all original target rows against the pre-native target backup, checks that backup's file hash, checks the historical source hash from `source-meta.json`, and runs SQLite `quick_check`. The JSON result records `published_pair`, `target_baseline_sha256`, `task_id_reservation`, and the original target table count alongside the existing case result. The baseline precedes fixture writes, so the row comparison measures the native case against the actual published candidate rather than against retired historical Email tables.

The focused regression uses a genuine Store19/Email51 database created by the archived `d06c148b` code in a task-owned `/private/tmp/ceo-native-copy.*` directory. Before the fix, the new preparation test failed at the current constructor with `Store19/Email51 requires the paired schema release before target constructors`. After the fix, both additional and lifecycle harness suites passed: 21 tests. Mock case functions exercised all three `main` selections without launching a native CLI, verified high Task allocation and source/backup hashes, and rejected changed or deleted original target rows. No live database copy, native model turn, provider delivery, production action, or Business projection conversion was performed in this unit.


## Root acceptance and actual-source native readback

Sequential fresh SPEC and QUALITY accepted commit
`e66e5dec8e04f401c0808c01550f01a4849047e3`. QUALITY independently reran both
focused harness suites: **21 passed in 48.67 seconds**, with its own pytest
temporary root removed and verified absent. This is preparation evidence.

Root then captured a new production database snapshot at
**2026-10-15T13:05:57 UTC**, with producer HEAD `7dadc492` unchanged across capture.
The immutable input was Store2026-10-13.5/Email51, 121 non-internal physical
tables, 484,958,208 file bytes, SHA256
`2f3bf1bd7b462a24d8cecb8a8afd7d699e1a7ddfc35cfb4f015343de45a4dff4`.
Quick check passed; the one existing `runs`→`tasks` finding remained. Three
independent candidate copies used the committed paired migration before real
Codex CLI 0.154.0 execution:

| Case | Real native execution | Persisted result | Outbound substitute |
| --- | --- | --- | --- |
| Work summary | One TaskAgent CLI call | Input done/compacted, one candidate/open Business Task, one applied semantic decision | No delivery |
| Scheduled | Consumer then Audit, one call each | Audit approve, execution done, action attempt verified, one external-action receipt | One in-memory delivery |
| Workbench | One CLI call | Completed turn, expected sum 100, completed event and persisted native session on reopen | No delivery |

All four native turns exited successfully and their real native files and
complete recorded bounds were independently read back. Each case preserved
all original rows in **103 ordinary target tables** and the exact original
FK finding. This comparator excludes virtual FTS tables and their shadows;
it is not a claim of comparing every physical target table. Baseline and
source file hashes were checked after each case. Temporary databases,
workspaces and the three owned parent directories were removed after handles
closed; their absence was verified. The scheduled fixture's high-ID artifact
root was already absent. Native Codex originals remain in its own storage.

Metadata-only evidence and root database/native readbacks are in
`superpowers/specs/2026-10-15-database-redesign-additional-native-current-pair.json`.
The copied Email29 target still has 108 physical tables; the work-summary
baseline was 507,281,408 bytes with no free pages versus the 484,958,208-byte
source with 19 free pages. This is a measured capacity increase, not savings.

These cases do not select or validate four-kind Business migration: its old
parent tables remain. They also do not fix the separate earlier Audit
signature misjudgment or establish the latest full CI result. Production
checkout remained `7dadc492` after the cases; no production data write, main
merge, push or deployment occurred.
