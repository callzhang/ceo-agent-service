# Material request regression coverage

A request for external material completes its reviewed request stage with
`continue_after_execution=false`. Its verified comment receipt proves the
request was delivered; it does not establish that the applicant supplied the
material. Re-entering that completed execution runs no further Consumer or
Audit turn.

The normal OA inbound path reuses the same business task ID. A new input after
completion creates a new execution generation, preserves earlier canonical
receipts and input history, and obtains a fresh complete candidate and review
from the current source facts. It does not resume a speculative next stage.

`tests/test_material_request_stage.py` covers both paths through the actual
store, orchestrator and OA comment executor. `tests/test_material_source_identity.py`
checks that missing native OA details or a canonical process ID different from
the requested ID cannot create a source binding or dispatch a reviewed action.
Both boundaries already existed in runtime; these tests do not add waiting
states, source policies or triggers. Subprocess-only mutations demonstrate that
the tests reject immediate continuation and removal of canonical OA identity
validation. The model's choice of the continuation flag is covered separately
by the Consumer/Audit instructions and native business evaluation.
