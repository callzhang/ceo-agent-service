# Skill search and installation validation

Status: implementation, independent branch review and its bounded fix pass
complete; standard deployment gate and production UI/readback are pending.
This document does not assert that company Skill repository migration is done.

## Implemented behavior

The existing Settings Skills page accepts Git repository URLs and public Skill
names. Results show sourced descriptions, authors/maintainers and modification
times. Per-row Install publishes the complete exact-commit package and its
managed revision/pending config; unrelated bindings and feedback capability are
preserved. An installed list is visible before selecting a feature. External
Skills retain their exact frontmatter and can be edited through the existing UI.

First deployment installs missing public baseline packages before startup while
preserving already-installed content. Bootstrap verifies the full baseline.
Private URLs are read using native service-account Git authentication and never
sent to the public name index. Installation does not call repository export,
start an external action or change task/Audit authorization policy.

## Evidence

- 204 selected Python tests across 16 distinct files passed, one process per file,
  including 48 new first-use/repository/discovery/installation/API tests and
  the existing startup, catalog, task-option and launchd regressions.
- Frontend search component: 4 passed; Settings page: 66 passed and 2 preexisting
  skips; API client: 2 passed. TypeScript and production build pass.
- Regression RED evidence includes absent baseline installer/repository reader/
  discovery/installation/routes/search component, and external editing returning
  422 before marker-preserving integration. Package rollback uses a real SQLite
  abort; repeat/concurrent installs and local-edit preservation use real files.
- Integrated first-use test uses a synthetic Git repo and real Store, route,
  reader and installer. It installs one of two Skills after moving the source
  branch, preserves the searched commit, retains earlier active bindings, and
  verifies the new revision's load receipt only after normal test startup.
- Live read-only public search returned 10 index matches with 4 hydrated usable
  results and 2 repository-level metadata errors. The official provider result
  included description, author and time. Unavailable matches remain explicit;
  this is not a claim that every indexed repository fits supported layouts or is
  reachable. No public Skill was installed into the real user environment.
- Ruff and diff checks pass. No real employee/OA data or private repository body
  is included in these tests or this document.

## Independent review and fix pass

A fresh read-only reviewer identified five reproducible functional issues. The
source-shaped input classifier, ordinary multiline external frontmatter, root
package boundaries, CRLF byte identity/capture, and post-load-failure config
parent/inheritance behavior each have regression RED→GREEN evidence. Duplicate
name rows are also unavailable through direct installation. The config change
race still refuses publication and preserves the competing configuration.

Two review notes were treated as functional issues: baseline preparation now
appears before the first runbook verifier/startup, and the Skills page has an
explicit return to all installed Skills. Owned metadata validation, prior frozen
revisions, existing runtime load evidence and task/action policies are retained.
No second review verdict is claimed; the author's fix pass is verified by the
focused suite and the mandatory release gate remains independent.

Native discovery against an existing private source also succeeded using the
service account's existing Git authentication. That probe installed nothing;
its repository identity, Skill names and bodies are not part of this artifact.

## Support limits and implementation rulings

Only the existing supported repository layouts are listed. Selecting a package
excludes nested independent Skill directories; dependencies are installed
separately. Tree/blob/archive/local installation, advanced ref selection,
auto-update, hooks and automatic tool provisioning are outside this release.
Symbolic links and bytecode caches follow existing omission semantics. Git LFS,
submodule materialization and non-UTF-8 filenames are not validated installation
capabilities. Distinct URL spellings/transports remain distinct source identities.
An unavailable remote or unreachable commit produces an honest upstream error;
no offline repository cache/remote-retention guarantee is claimed.

Ordinary filesystem/SQLite exception rollback is verified. Durable recovery from
process death between filesystem publication and DB commit, or from a second
filesystem failure during rollback, is not added; interruption may require manual
resolution rather than automatic overwrite. Console auth/trust/SSRF/malware and
outbound policy changes remain separately scoped. Active status retains existing
validated load-writer semantics, with real PID correspondence verified at release.
Large histories/packages and fleet concurrency extremes are not benchmarked beyond
existing command/request time budgets. The baseline's literal python3 convention
was retained: the reviewer ran its exact 15-package operation under system3.9.6
successfully; future non-stdlib imports would need renewed interpreter validation.

## Release boundaries

Repository creation, company policy/test migration, current public-content
sanitization and historical disclosure treatment remain separate work. The
unpublished OA optimization branch/results have not been included. Pending
installation is not process activation; a real business outcome is not needed
for this feature's acceptance. Standard deployment and live readback must be
recorded separately after review. No completed approval or message is replayed.
