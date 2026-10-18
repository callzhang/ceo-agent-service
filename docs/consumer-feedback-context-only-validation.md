# Consumer feedback revision context: bounded acceptance

Production Task 405336's saved complete-trajectory analysis found one Consumer
Codex session across three revisions. Each of the two revisions repeated 41,385
of the initial 41,394 source-input characters. A read-only metadata query on
2026-10-18 found 539 task/generation/session groups with at least two completed
Consumer revisions; this count describes the path's frequency, not the number
of safely compactable inputs. Draft PR #125 also changes Task Agent context;
this release handles only Consumer feedback revisions.

Acceptance requires a shorter submitted task input only after an Audit
return/reject when its completed Consumer parent, task generation, exact Codex
route/session and stable source, Developer, output-contract, image and Skill
inputs match. The initial complete User input must still be readable in that
session, the transcript tail must equal the persisted last Consumer attempt,
and no compaction may have intervened. Any missing or changed evidence keeps
the complete input. The new input must carry the current time, complete Audit
feedback, runtime correction if present, and a complete replacement-result
request. Saved prompt snapshots must represent the actual submitted input.
Audit's independent input, result validation, feedback budget, session binding,
other routes, current source facts and external action rules must not change.

The fixed cases are: cold start, a same-session Audit-feedback revision,
changed source/Skill or contract, wrong session/route, grown or missing native
transcript, compaction, and a production-shaped anonymous source with facts in
the beginning, middle and end. Local affected tests and the fixed native
comparison establish behavior; GitHub Quality is supplementary. After merge,
formal deployment must read back the checkout commit, new service PID,
health, queues, Attention and History, and inspect the installed prompt choice
with a read-only synthetic fixture. A future natural business revision remains
separate evidence.

The 2026-10-18 anonymous Codex OAuth diagnostic used a 51,578-character
fictional source and a type-only output schema. Both arms cold-started and
resumed in their own session without tool calls. Full and compact continuations
were 52,195 and 318 characters; both returned the same material states:
withdrawn old slot, proposed unaccepted new slot, unresolved repeated LA
hour, draft invitation not sent, Mei as current owner, exact middle and late
reference IDs, and no private-topic hint for the public group. This tests
native context retention in one synthetic pair; service route/guard behavior
and broad business quality are not established by it. The recorded result is
`evals/trajectory_optimization/consumer_revision_context.production_shape.native.v1.json`.

After applying only the Consumer portion of PR #125 to current main and binding
all trajectory reads to the same resolved Codex home, the Consumer,
turn-runner, route and native-input tests passed: 287 passed, 4 skipped.
Ruff and whitespace checks passed. The PR #125 Task Agent reuse change was not
included in this branch.
