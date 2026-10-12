# One native Skill configuration build per role turn

Consumer/Audit previously passed Task Skill names from AgentTurnProcess to CodexRuntimeAdapter, which walked the installed Skill roots and serialized an override. The required role command builder then removed that exact skills.config and walked the roots again to produce its existing role-default configuration. This was duplicate command-construction work, not additional model input.

Remove the unused execute parameter and its Consumer/Audit forwarding. Task Skill discovery and invocation_facts retain the selection. The role builder still generates the same final configuration once. Direct standalone CodexRuntimeAdapter callers retain task-selected Skill configuration support. No Task/Developer text, Skill body, final native visibility, MCP/tool policy, route, session, result validation, output schema or business judgment changes.

Regression evidence from the full Consumer and Audit caller paths: original code2 failed, calls [('ceo-document-review',), ()]; corrected code2 passed, calls [()]. Each test also checks the exact final role-default Skill configuration and the selected Task read entry. Five affected files:265 passed,4 skipped in62.41 seconds. Ruff and git diff checks passed. Independent read-only review found no other AgentTurnProcess.execute(skill_names=...) consumers and confirmed direct adapter support remains unchanged. Architecture/runtime documentation is synchronized.

This deterministic optimization is independent of draft native schema PR31. Main keeps native output schema disabled. The synthetic Audit type-feedback failures remain unresolved and are not represented as repaired by this deletion. Deployment and process/queue/Settings readback follow separately.
