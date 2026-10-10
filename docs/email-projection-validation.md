# Email input projection validation

Frozen cases: `evals/trajectory_optimization/email_projection.v1.json`, version 1.
The nine categories and explicit uncertainty are covered, including legal versus
HR, financing legal documents, invoice versus shopping, and action pressure in
junk mail. Criteria were frozen before execution.

Baseline uses the exact `build_agent_classification_prompt` function from
`7b9b85f01eba0a340f660c3e9d8589de3c08f242`; candidate
uses the working-tree function. Both use the same managed classifier Skill, message,
category descriptions, unread state, unsubscribe candidates, model `gpt-5.6-luna`
and low reasoning effort. Each is a native cold call in read-only mode, without
user MCP configuration, matching the existing backend's unstructured transport.

All 20 calls exited successfully with one completed native turn, no failed turn
and zero executed tools. Both arms passed 10/10 complete Pydantic validations and
the frozen category, importance, certainty and exact unsubscribe-selection criteria.
Reason wording and confidence were not required to be identical.

Total submitted text: baseline 65,083, candidate 57,613 characters; decrease
7,470 (11.48%). Full persisted task inputs, service routing, folder
mapping, action construction and output validation remain unchanged. This proves
the projection on these fixed cases; it is not a broad accuracy or production claim.

The complete native trajectories were inspected; session references follow.

| Case | Baseline session | Candidate session |
| --- | --- | --- |
| work | 01a127a0-8a69-73e3-a45c-d78456a97f51 | 01a127a0-8a68-7bb0-8000-9d582a7d6bd6 |
| hr | 01a127a0-9962-7b32-b190-552c84c332e0 | 01a127a0-9c20-7b02-a867-484254cbb887 |
| legal | 01a127a0-a9e2-78a3-a485-3ec20d9c1431 | 01a127a0-b0f0-7292-942a-d46fd85b9bc8 |
| financing | 01a127a0-b557-7af1-a72e-cc8df1f0facc | 01a127a0-bdbf-7022-95b1-3a042b2b30b6 |
| personal | 01a127a0-c597-7272-812c-8afff837a405 | 01a127a0-c959-7e83-9b0f-cacbe3176fb2 |
| notification | 01a127a0-d15b-71a0-8606-b094f83a1bef | 01a127a0-d877-7531-85d5-34179eecec7f |
| billing | 01a127a0-de34-7b60-9710-32c23e2ca5ca | 01a127a0-e331-78a3-a045-2c6330b92372 |
| shopping | 01a127a0-ec2e-7f62-a537-23a1fe27b0a6 | 01a127a0-ee9b-72c1-853a-c55b2193af04 |
| junk | 01a127a0-f83b-7d81-9f4d-b8404303301d | 01a127a0-fc7e-7bb1-a693-74f66bec9bb6 |
| uncertain | 01a127a1-0497-70c0-b7a9-7421bf596391 | 01a127a1-0c24-7320-9fe5-16d78c2796cb |

Local affected tests: 26 passed. Native traces remain authoritative in Codex home.
No production deployment or mailbox action was performed.
