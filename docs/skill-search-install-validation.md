# Skill search and installation validation

Status: implementation and focused offline verification complete; independent
branch review, standard deployment gate and production UI/readback are pending.
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

- 179 selected Python tests across 13 distinct files passed, one process per file,
  including 34 new first-use/repository/discovery/installation/API tests. A
  separate existing launchd test file is recorded when complete.
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

## Release boundaries

Repository creation, company policy/test migration, current public-content
sanitization and historical disclosure treatment remain separate work. The
unpublished OA optimization branch/results have not been included. Pending
installation is not process activation; a real business outcome is not needed
for this feature's acceptance. Standard deployment and live readback must be
recorded separately after review. No completed approval or message is replayed.
