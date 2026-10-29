# Task/Run final fix-wave report

## Scope and findings

Read the repository's `fix-loop.md` guidance before the fix wave. The only open whole-branch Important finding was I1 from the review of `635ecdb569ad0355316ceab3d35cbca46ca1dc0e`: [the Task5 acceptance report](task-run-entity-copy-acceptance.md) still described the scoped re-review and main integration as pending. Corrected its opening status and remaining-gates section to say the Task5 scoped SPEC/QUALITY review passed, main through `278431eabf2bd1903582d9a480c5f423bdd9ab15` is integrated, the whole-branch review passed apart from I1, and deployment/readback remain pending. Updated the later round-one paragraph so it no longer says the scoped review is still required. The historical 316/59/3 test result, missing exact node list, production-evidence boundary, and no-shrink claim remain unchanged.

## Pinned main drift

Merged pinned `f9a7d73c07988f4dd4c65f9a28768bf9ca749ea6` normally into `635ecdb569ad0355316ceab3d35cbca46ca1dc0e`. The merge commit is `b7fbfdc5c23c25852eaf9ec108f1389fa3b1d81a`, with parents `635ecdb569ad0355316ceab3d35cbca46ca1dc0e` and `f9a7d73c07988f4dd4c65f9a28768bf9ca749ea6`.

The pinned delta is two commits, two files, one insertion and three deletions relative to `278431eabf2bd1903582d9a480c5f423bdd9ab15`: the managed-Skill release manifest drops `old_managed_sha256` for the unmanaged `ceo-wechat` Skill; the other change closes its completed claim. No runtime or schema code changed. Existing dirty Task5 review claim rows were preserved outside this fix-wave commit.

## Verification and boundary

Ran the targeted manifest digest regression:

```sh
pytest -q --assert=plain --tb=short -o faulthandler_timeout=20 \
  tests/test_consumer_system_release.py::test_repository_consumer_release_manifest_tracks_every_source_digest
```

Result: **1 passed in 0.30s**. No source-copy, model, full-suite, build, provider, production, push, or deploy operation was performed. Final whole-branch review requested one scoped re-review of this fix wave; release/deployment and live readback remain pending.
