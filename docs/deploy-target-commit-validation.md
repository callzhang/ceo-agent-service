# Optional exact-SHA deployment, 2026-10-20

## Problem and acceptance

An operator approved forced deployment of `d46e47400`, but a later `main` commit arrived between the operator's preflight lookup and `app.deploy` selecting its target. The persisted deployment operation targeted and installed `763caecd3`. The deployer had no way to receive the approved SHA, and its normal merge uses a movable `origin/main` ref.

`python -m app.deploy` without a target retains the existing current-main behavior. With `--target-commit <full 40-character SHA>`, the selected commit must be in the fetched `origin/main` ancestry and fast-forward from the production checkout. A later main fast-forward must not change the selected commit. If main is rewritten so the selected commit is no longer in its ancestry while the deploy waits, stop before the normal service stop and backup. Even if the ref moves after that check, merge the literal selected SHA. The existing quiet wait, backup, verification, rollback, publication, and restart behavior remain in place. A settings-only `--restart` cannot take a target SHA.

## Local verification

The first three new cases failed before implementation because neither the deploy function nor the updater accepted a pinned target. Focused cases now cover a selected commit already behind main, a main fast-forward during the quiet wait, a main rewrite during the wait, a fast-forward after the service stop, an invalid SHA, and rollback of the selected commit after verification failure. The ordinary deploy and publication tests remained in the affected-file run: **61 passed**. Ruff and `git diff --check` passed. Production publication and readback are pending.
