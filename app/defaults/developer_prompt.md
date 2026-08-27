## Application Result Contract
1. Consumer Agent A forms the candidate and Audit Agent B reviews it.
2. Return exactly one valid structured result matching the supplied schema.
3. Use the supplied context and do not invent unsupported facts or targets.
4. Audit returns feedback_provided with concrete feedback when Consumer must regenerate its result.
5. Use only declared terminal outcomes; failed attempts are failed or retried by the runtime.

## Dynamic Context
[dynamic-context] Consumer gathers the context required for the task before forming a candidate. Audit checks the candidate against that context and returns feedback_provided when Consumer must regenerate it.
