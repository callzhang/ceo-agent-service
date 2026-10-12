# Task-specific action contract lookup

The previous lookup selected table rows but retained all capability prose. A calendar response query included OA identity and message-body/reaction details; OA queries included unrelated chat/document details. The lookup now retains common prose and only the explicit `## Capability:` sections applicable to its selected capability. A section may name multiple capabilities. All common text must precede the first capability section. The canonical document and all its action rows/rule words remain complete, and its full content still supplies the conversation fingerprint. No handlers, audit policy, Pydantic models, routing or sessions change.

Regression: unrelated capability prose was present before the fix; the new synthetic registry test fails before and passes after. All68 affected contract/context/composition tests pass. Independent review verifies unchanged rule-word contents/table rows and correct scope assignments.

Measured lookup length: calendar3947→2723 characters (31.0% less); document create3938→2882 (26.8% less); OA revert3974→3469 (12.7% less). Reductions remove unrelated sections, not necessary facts.

Fixed anonymous OA comparison uses real AuditAgent/temporary stores with the complete schema from the independent native-output draft. Baseline and scoped variants both approve a legal candidate and return only the genuine wrong-node correction. Same-case Task inputs are identical, while Developer contains different runtime timestamps/workspaces. All complete and bind the exact candidate. This is limited non-regression evidence, not proof of repairing earlier false ID judgments. Empty Profile/synthetic Skill and fixture-persisted Consumer candidate remain explicit limitations. No production release or four-domain business acceptance is claimed.

Receipt: evals/trajectory_optimization/action_contract_scope.results.v1.json. This change is independent from native-output PR31.


## Four-domain actual runner comparison

Two variants each reach10 terminal role invocations: four Consumer/Audit cold pairs plus calendar Consumer/Audit same-session continuation. The configured OAuth model is gpt-5.6-luna/medium with complete draft-native schema. Corresponding Consumer Task and Developer inputs are identical; Audit Developer is identical, but generated candidates and hence Audit Task differ. All four Consumers in both groups perform the requested substantive work; calendar accepts the correct LA interval and subsequent accepted state yields no_action in the same sessions, mail produces no duplicate action, document identifies three source conflicts and preserves the source reply, and project reply uses the existing unresolved remediation task.

This is not four-domain acceptance. Calendar's list fixture ignores the query window, and actual provider edge semantics are unverified. Mail's extra thread-replies read is unsupported by the fixture. Work tracking here is a project-risk reply, not the separate extraction-agent lifecycle.

Actual message preparation exposes a distinct failure: candidate chat bodies exactly match persisted service-prepared final_body, including recipient-facing feedback references. Baseline work and scoped document Audits quote those references as credentials and fail the existing sensitive-value check; scoped work requests removing the service-added footer, which is not a meaningful Consumer business correction. Baseline document approves, but its historical preview equals generic credential-redacted input rather than raw input. Earlier failed trajectories remain failures. These are not proven regressions caused by capability scoping, and are not repaired by changing safety behavior here. A separately scoped proposal is awaiting explicit approval, per repo AGENTS.md.

Full trajectory review and native attempt pointers are recorded in evals/trajectory_optimization/action_contract_four_domains.results.v1.json. No production release is claimed.
