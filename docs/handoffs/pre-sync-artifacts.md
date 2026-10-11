# Historical pre-sync artifacts

Recorded on 2026-10-11 under Derek's instruction to commit old pending work and
leave new changes alone. All four files were present before main synchronization;
their original Git blobs match the untracked-file tree of stash
`8d5f03d1c583a97f0e943fe164b600e254a5d49e` (third parent).

| Artifact | Status |
| --- | --- |
| `docs/email-pending-feedback-preview.html` | Historical standalone visual preview; current Email behavior is defined by the implementation and architecture/runtime documents. |
| `docs/handoffs/unapplied-project-retrieval-patches/unreleased-candidate-dev.patch` | Unapplied candidate for Project context refresh and retrieval expansion. |
| `docs/handoffs/unapplied-project-retrieval-patches/ranking-candidate-v2.patch` | Unapplied candidate for literal Project-title retrieval ranking. |
| `docs/superpowers/specs/2026-09-29-task-evidence-classification-and-project-authority-design.md` | Superseded Task-first design draft, now explicitly labeled historical. |

The preview and both patch files retain their original contents. Only the
design draft's status/provenance annotation was updated for archival clarity.
Committing the patches preserves the candidate diffs; it does not apply their
code or establish an approved algorithm change. Adoption requires a separate
implementation decision and review against the current code.
