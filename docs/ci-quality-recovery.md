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

## Current Test Contracts

Fixtures use task-class decision options and full decision evidence for
`needs_human`; successful results do not carry those fields. Terminal
authorization denials remain failed across later scheduling, rather than being
converted to parse retries. WeChat failures retain their underlying classified
diagnostic, bounded to 500 characters, without completing the business task.

Startup doubles include the Bootstrap stores required for legacy inventory;
service command tests use the authoritative catalog. Console tests use the
current fresh status read and settings field allowlist, and wait for background
history warming before asserting cache reuse. Bootstrap tests isolate
`CEO_SKILLS_ROOT` with their temporary HOME.

Email browser fixtures do not override the canonical Chrome channel a second
time. Classifier integration fakes attach to the shared connection-registry
source factory, not the removed direct-source entry point, and use valid
environment secret references. Meeting integration supplies group search and
typed conversation metadata. External I/O fences, real classifier training,
headless browser effects, delivery receipts, and authorization gates remain
covered; no tests are disabled to obtain a passing result.

Frontend fixtures assert the current localized scheduled-run state and password
visibility accessibility contract, rather than removed explanatory text. Image
upload tests wait for runtime capability loading to enable the input before
choosing a file, so they exercise a real user action instead of firing a change
on a disabled control. The complete frontend suite passes 632 tests; the two
existing skips are unchanged, and both real workbench browser checks pass.
