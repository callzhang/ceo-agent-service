# Audit Revision Session Continuity Validation

The full trajectories for reply tasks 15950 and 416444 each used four separate Audit sessions across four proposal revisions. Both repeatedly read the same direct conversation; the first trace reached its final no-message conclusion only on the fourth revision, while the second produced conflicting Audit conclusions and exhausted its review revisions. Task 416444's Consumer already reused one session across its revisions. This supports retaining continuity in the Audit role's own session while keeping it separate from Consumer.

`claim_agent_run` now selects the latest continuable Audit session from the same reply task and execution generation. An explicit `session_continuable=false` on the latest Audit run still starts a fresh session. The current candidate and revision remain bound and are sent to each Audit. No routing, prompt rule, review criteria, retry budget, action permission or external execution behavior changed.

Validation:

- The new Store regression failed before the change because a later Audit proposal revision received no prior Audit session. The companion regression confirms a noncontinuable latest session is not resumed.
- The Store session-boundary selection passed 4 tests; the Audit runner/session-resume selection passed 6 tests; all 29 tests in `tests/test_audit_agent.py` passed.
- The full `tests/test_agent_turn_store.py` file reported 51 passed and 12 failures in unchanged Consumer native-output parsing, fallback and unrelated result-codec paths. Those failures do not touch the new Audit session lookup; no full-suite pass is claimed.
- An anonymous read-only native Codex OAuth replay used a synthetic direct-chat fixture and a read-only fake MCP tool. The cold Audit and same-session revised Audit both rejected a duplicate broad agenda question and cited the explicit agenda. The native thread ID was preserved across `exec resume`; each turn reread the synthetic current messages. The fixture exposes no send or write tool, and no production record or external action was used.

The native replay verifies actual CLI continuation and the expected decision on this fixed synthetic case. It does not prove a natural task outcome or a reduction in current-source reads; rereading current evidence remains permitted and occurred in both turns.
