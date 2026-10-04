# Consumer / Audit contract release

The reviewed Consumer/Audit role contract has files outside the Git checkout. The
service reads installed Skills from `~/.agents/skills`; the Audit rules text is
the ignored `data/prompts/audit_rules.md` in the production checkout unless the
production service environment selects another path. A normal Git fast-forward
does not replace those files. It also does not move existing scheduled tasks to
new immutable managed Skill revisions.

For this contract release, `python -m app.deploy --publish-consumer-system-contracts`
uses the normal production deploy's quiet wait, launchd stop, database backup,
fast-forward, import check, restart, and health check. In the stopped interval
after the backup and import check, it publishes only the nine source and target
files in `ci/consumer-system-contract-release.json`: six service-managed
`SKILL.md` files, the generic DingTalk OA Skill, the weekly report's DingTalk
runbook, and the Audit rules. The manifest pins the previously observed SHA-256
of each installed file and the reviewed SHA-256 of each committed replacement.
It separately pins the old managed revision SHA-256 because a process may have
loaded an immutable revision different from the installed file. A mismatch
stops publication before replacement.

The publisher records exact previous file bytes and the original scheduled
Skill references in a release receipt under the database's `release-receipts`
directory. It creates or reuses the six target managed revisions, updates only
scheduled managed references to those six Skills using task versions, and
creates an immutable next-start runtime configuration from all existing
bindings. The generic OA Skill remains an operation Skill reference, which
the scheduler reads from its installed file for each execution. Other files,
task fields, operation Skill references, and unrelated runtime bindings remain
as they were.

After restart, the deploy checks that the new active configuration and a live
worker's load receipt contain the six target Skill digests. It also reads back
the nine files and affected scheduled references. Only then is the release
receipt marked verified. The exact previous texts remain in that release's
receipt for rollback review. A live receipt PID alone does not prove that it
belongs to the replacement worker; the final production readback must compare
the receipt PID with the launchd supervisor's actual child process. If restart,
health, or readback fails, the updater stops the replacement service, restores
the nine prior files and scheduled references, stages an immutable rollback
configuration, then follows its existing Git rollback and old-service restart
path. Immutable revision history and additive database schema remain recorded.
If receipt finalization fails after a healthy, verified restart, deployment
remains succeeded and the deploy command includes the recorded finalization
error in its output.

The deploy's progress store writes only the already-present `service_state`
table before the quiet wait and backup. Constructing the full application
store, which may migrate schema, happens only in the stopped publication
window after the database backup. A normal `python -m app.deploy` does not
publish these external contracts.

The offline rollback-startup check covers an installed old Skill whose
content was a `runtime:agents-skills` revision while the prior active
configuration pointed at another revision. On restart, repository import may
record the same content as a distinct `repository:skills` revision: revision
identity includes its source. The restored runtime configuration and scheduled
references still select their original revision IDs, and the old service can
load its snapshot normally.
