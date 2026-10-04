# Quality CI Recovery

## Portable Repository Fixtures

Repository updater tests explicitly initialize both their bare remote and local
checkout on `main`. They must pass with `init.defaultBranch=master` as well as
`main`, without relying on developer or runner Git configuration. The fixture
regression checks the remote HEAD and both checkout branches.

## Shared Skill Dependencies

Quality CI uses `CEO_SKILLS_ROOT=ci/shared-skills`. These are read-only test
snapshots of the installed shared Skills, not the production authoring location.
The OKR headless wrapper resolves this same configured root for its browser and
direct-source modules. Its regression uses an empty home directory so installed
developer Skills cannot hide missing CI inputs.

The test environment includes fasttext for real classifier training and zsh for
the supervisor launcher contract. Neither dependency is replaced with a mock or
an unconditional skip.

## Successful Runtime Terminals

A successful Codex terminal must not authorize failure failover, route pausing,
or authorization recovery. Unclassified failures still retain the existing
failover policy. The adapter regression distinguishes these outcomes explicitly.

## Bounded Fresh-Session Retries

The route selector can authorize one fresh retry after a transient failure in
a fresh CLI process. This is not evidence of an incompatible persisted resume
session, so the turn runner must not invoke resume-session clearing for it.
Keep that clearing operation fenced by its persisted session failure evidence.
Do not pause a route before its authorized same-route fresh retry is claimed;
pause and switch routes if the bounded retry fails again. Integration tests
cover recovery on the second process and exhaustion into the eligible API route.
