# Task-specific action contract lookup

The previous lookup selected table rows but retained all capability prose. A calendar response query included OA identity and message-body/reaction details; OA queries included unrelated chat/document details. The lookup now retains common prose and only the explicit `## Capability:` sections applicable to its selected capability. A section may name multiple capabilities. All common text must precede the first capability section. The canonical document and all its action rows/rule words remain complete, and its full content still supplies the conversation fingerprint. No handlers, audit policy, Pydantic models, routing or sessions change.

Regression: unrelated capability prose was present before the fix; the new synthetic registry test fails before and passes after. All68 affected contract/context/composition tests pass. Independent review verifies unchanged rule-word contents/table rows and correct scope assignments.

Measured lookup length: calendar3947→2723 characters (31.0% less); document create3938→2882 (26.8% less); OA revert3974→3469 (12.7% less). Reductions remove unrelated sections, not necessary facts.

Fixed anonymous OA comparison uses real AuditAgent/temporary stores with the complete schema from the independent native-output draft. Baseline and scoped variants both approve a legal candidate and return only the genuine wrong-node correction. Same-case Task inputs are identical, while Developer contains different runtime timestamps/workspaces. All complete and bind the exact candidate. This is limited non-regression evidence, not proof of repairing earlier false ID judgments. Empty Profile/synthetic Skill and fixture-persisted Consumer candidate remain explicit limitations. No production release or four-domain business acceptance is claimed.

Receipt: evals/trajectory_optimization/action_contract_scope.results.v1.json. This change is independent from native-output PR31.
