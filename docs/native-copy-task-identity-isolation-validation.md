# Native copy Task identity isolation validation

This change affects only `scripts/validate_native_copy_lifecycle.py` fixture
allocation. Before the public `ensure_reply_task` call, the candidate database
reserves its existing `tasks` AUTOINCREMENT sequence at `2**52` plus 51 random
UUID bits. The next Task ID is above ordinary source IDs and below `2**53`,
so native JSON/JavaScript consumers represent it exactly. Existing Task rows
and every other allocator remain untouched. Sources already using that high
range fail an explicit fixture precondition; there is no fallback allocation.
The aggregate native report records the reservation and actual Task ID.

The harness continues to create and claim its Task through the public Store,
and execute through the existing worker. It also explicitly calls the existing
terminal-artifact cleanup API for its own Task before acceptance assertions,
including terminal failure. This uses the native artifact root and path rules.
No runtime, prompt, Audit decision, assertion threshold or model output changes.

## Evidence on 2026-10-15

The focused regression creates a small real AutoReplyStore source, backs it up
into two synthetic databases, and monkeypatches only the artifact root to its
pytest temporary directory. Both ordinary copies allocate the same Task ID.
Using the other database's terminal status with the real
`cleanup_terminal_task_artifacts` API removes that candidate path: the control
reproduces the cross-database filesystem boundary. With the allocator-reservation
helper initially doing nothing, the isolated-path assertion also failed:
`another database's terminal cleanup removed candidate artifacts`.

RED command (exit 1, one expected failure, 1.34 seconds):

```sh
/Users/derek/miniforge3/bin/python -m pytest -q tests/test_native_copy_lifecycle_harness.py::test_reserved_candidate_task_survives_other_database_terminal_cleanup
```

After candidate sequence reservation, the same regression verifies native
artifact readback survives the other database's cleanup, IDs and paths differ,
the real Task can be claimed, all pre-existing candidate Task rows and other
sequences are unchanged, original source rows survive, and the source file hash
is unchanged. Its own terminal-failed Task directory is explicitly cleaned.
A first GREEN attempt caught a test snapshot-bound error: the physical Task
input row has an ID after the reply Task. The snapshot check now covers every
original physical row through the original maximum ID, rather than stopping at
the reply Task ID.

Final focused command (exit 0, 7 passed, 8 deselected, 1.69 seconds):

```sh
/Users/derek/miniforge3/bin/python -m pytest -q tests/test_native_copy_lifecycle_harness.py -k 'reserved_candidate_task or fixture_task_reservation or empty_service_manifest or only_real_inherited or resume_scoped or scoped_tool_list'
```

The three new tests include the real Store/artifact regression, empty sequence
reservation on two separate tiny databases with distinct safe IDs and untouched
unrelated sequence, and explicit refusal of a high source allocator without
file mutation. Four adjacent command-exposure tests remain unchanged and pass.
Scoped Ruff and `git diff --check` pass. No whole historical fixture file run,
large database copy, real CLI invocation, production/API/provider access, push,
or deploy was performed. Task-owned pytest directories were removed and their
absence verified; the parent's historical native-copy directory was untouched.

This is fixture identity isolation, not a general multi-database artifact-root
framework. It does not identify the process that deleted the historical path
or establish causal proof for that deletion. The prior failed native case and
independent model signature-loop behavior remain unchanged evidence. Fresh
native acceptance and independent SPEC/QUALITY review belong to the root task.
