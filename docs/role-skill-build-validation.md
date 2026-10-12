# One native Skill configuration build per role turn

Consumer/Audit previously passed Task Skill names from AgentTurnProcess to CodexRuntimeAdapter, which walked the installed Skill roots and serialized an override. The required role command builder then removed that exact skills.config and walked the roots again to produce its existing role-default configuration. This was duplicate command-construction work, not additional model input.

Remove the unused execute parameter and its Consumer/Audit forwarding. Task Skill discovery and invocation_facts retain the selection. The role builder still generates the same final configuration once. Direct standalone CodexRuntimeAdapter callers retain task-selected Skill configuration support. No Task/Developer text, Skill body, final native visibility, MCP/tool policy, route, session, result validation, output schema or business judgment changes.

Regression evidence from the full Consumer and Audit caller paths: original code2 failed, calls [('ceo-document-review',), ()]; corrected code2 passed, calls [()]. Each test also checks the exact final role-default Skill configuration and the selected Task read entry. Five affected files:265 passed,4 skipped in62.41 seconds. Ruff and git diff checks passed. Independent read-only review found no other AgentTurnProcess.execute(skill_names=...) consumers and confirmed direct adapter support remains unchanged. Architecture/runtime documentation is synchronized.

This deterministic optimization is independent of draft native schema PR31. Main keeps native output schema disabled. The synthetic Audit type-feedback failures remain unresolved and are not represented as repaired by this deletion. Deployment and process/queue/Settings readback follow separately.


## Production readback

Formal deploy completed production7baa1f0e ->54e6c209, supervisor PID16960 ->52040. Its quiet wait allowed the active Meeting job to finish before backup/fast-forward/restart. Main native output schema remains disabled. An initial HTTP readback timed out; bounded checks confirmed the same new supervisor alive and health HTTP200, then Status recovered to HTTP200 in0.74 seconds without another restart. The delay's cause is not established.

Post-readback: every processing queue0, Reply/Attempt failed2 and Email provider action failed1 match the pre-deploy failures;3 Email provider actions remain pending. Attention total3 is unchanged. History HTTP200 and current Audit Settings preview HTTP200. No business failure was replayed or relabeled. This verifies the deployed deterministic command-construction cleanup, not the still-failing Audit semantic judgment or draft native schema business gates.
