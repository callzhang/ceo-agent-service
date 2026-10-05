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

History-chart cache tests bind their warm-up event and call counter to their
own SQLite path. A read for another app/database is explicitly interleaved
between repeated requests; it must not be mistaken for rebuilding this app's
cached chart. This prevents background warmers from contaminating the assertion.

## Linux Readback Corrections

The first remote run reduced 83 failures to five, while the local complete suite
passed. The reaction fixture still depended on an installed `dws` executable;
it now supplies a fake executable as well as the fake process runner. Queue
startup recovery tests isolate scheduled-task seeding, which has its own
coverage, rather than requiring the developer's installed operation Skills.

OKR refresh locking uses `tempfile.gettempdir()` instead of macOS's
`/private/tmp`; its regression redirects the temporary directory and verifies
the actual lock file exists there.

fastText 0.9.3 initializes its input matrix in ten native blocks, one per
initialization thread. The adapter's single thread initialized only the first
tenth, leaving hashed ngram rows untouched. The new regression reproduced this
locally (5,006 initialized rows out of 50,005), independently of whether the
allocator's remaining bytes happened to be zero or NaN. Training uses ten
threads so every block is initialized, and the regression checks the complete
matrix and finite predictions. The model remains a frozen candidate; this fix
does not activate or promote a production classifier.

Native implementation evidence:
[DenseMatrix initialization](https://github.com/facebookresearch/fastText/blob/main/src/densematrix.cc)
and [training matrix construction](https://github.com/facebookresearch/fastText/blob/main/src/fasttext.cc).

After initialization was repaired, Linux exercised prediction and exposed the
library's single-string `np.array(probabilities, copy=False)` incompatibility
with NumPy 2. The adapter uses the public batch prediction API for its one
input and unwraps the one result. This preserves labels, probabilities, and
margin without modifying the library, pinning an obsolete NumPy, or catching
and hiding prediction failures. A contract regression checks the batch input
and the unchanged prediction output, in addition to real training coverage.

The next Linux run passed all 9,867 backend tests and exposed frontend fixture
portability issues: decision timestamps were asserted in the developer's
timezone rather than the viewer's local timezone, and SSE tests inspected a
connection before the passive effect created it. Timestamp assertions use the
fixture's exact instant in the current viewer timezone. SSE assertions wait for
the actual connection, and components are unmounted before global mocks are
removed. The full frontend suite is also verified under `TZ=UTC`.
