# Email history paired initializer deadline repair

Existing authorized database-redesign release path only; same original author,
same SPEC then same QUALITY after repair. No next implementation or production
publication before normal-path actual-data validation.

## Evidence and root cause

- Original frozen unit39d4353e passed local SPEC/QUALITY but actual full-copy
  session58148 failed at the existing whole-child timeout300s. Failed evidence
  SHA256 b2c4cae9740e3e5ed90deb3d17dddcac3a694391e2231ddf95b1816a7a73bb9b.
- Unchanged-runtime diagnostic47355 completed one forward on a fresh read-only
  production snapshot with launcher-only1200s cap. Profile SHA256
  d24ca24be72ab4232b92ea1fa0ee76934748ac7beed5885f100716c575999cd6 records99
  timing events and462.326s total. Child alone412.570s; before history339.545s;
  history conversion41.937s including98,242 retained Actions restore35.936s,
  snapshot0.874s,183 new history inserts0.073s. No blocked/unbounded stage found.
  All40 retained families/types/IDs/ROWIDs/waters compare; root temp absent and
  all15/6/371 original pins stable. This is diagnostic, not normal-path acceptance.
- The cap covers every existing conversion, validation, commit and two cold
  constructors. It is used by the real deploy/repository publication path.
  Existing deployment health waiting is already15minutes for large-data startup
  (`app/repository_updater.py:HEALTH_WAIT_SECONDS`). A300s whole-child budget is
  below measured necessary work even before the new history stage.

## Minimal same-author change

Work only in the assigned isolated worktree. Claim scoped test path before edit.
Change only `_migrate_pair`'s paired initializer cap from300 to15*60 seconds and
add a concise factual comment explaining full local migration versus cold open.
Keep a bounded child timeout, error propagation, metadata transport and all
declarations/transaction/FK/public-field behavior unchanged. No retry, fallback,
new setting, new guard, logging framework, pipeline rewrite or change to older
unrelated Store-only timeout. Diagnostic launcher overrides stay outside runtime.

Add focused regression in `tests/test_email_action_schema_release.py`, using the
existing paired subprocess transport test pattern. A deterministic child double
models the measured roughly413s completion: the original300s call must raise
TimeoutExpired; the corrected bounded15min call must consume all seven existing
inverse-metadata messages and update the release object correctly. Also retain
propagation of actual child failure/timeout beyond its budget; no long wall-clock
sleep or production/source copy in the unit test. Record RED then GREEN and run
only directly affected transport/publication cases. Do not rerun all prior
worker/field tests absent a new semantic change.

After checks pass, update architecture/runtime and unit validation documents in
scope. Return exact changed paths/hashes, commands and results; no commit/push/
deploy/provider/model/CLI/source DB writes. Root creates r2 freeze preserving
original39/failed/profile files, then same SPEC and same QUALITY re-review.

Only after both reapprove, root repeats the normal uninstrumented full
116→13→116→13 proof with40 distinct retained families, live current+genuine83
synthetic writes, whole-source schema inverse, cold processes, allocator/FK
checks and capacity. No diagnostic cap override may enter that acceptance run.
