# Meeting native workspace document input

## Evidence and acceptance boundary

The latest five readable production Meeting attempts on 2026-10-21 each received
the same 7,060-character `/Users/derek/Documents/memory/AGENTS.md` as a native
User message. Their service-submitted Meeting input already contained the frozen
managed Meeting Skill, Work Profile, and complete meeting source. The five
attempts made no file or other tool calls. The Meeting Codex command does not
currently set `project_doc_max_bytes`, so the CLI supplies that workspace
document independently of the service prompt. This is extra, unrelated
directory guidance for the observed structured-decision turns; it
does not prove that any decision was wrong.

This change is accepted only if the Meeting Codex command excludes the native
workspace document while retaining the same service-submitted Skill, Work
Profile, source, schema, route, and decision validation. The anonymous fixed
comparison must preserve structured validity, the recipient for a business
alignment case, the agreed rollout, and the audience/sensitive-content boundary
for a mixed-content case. Other workloads and non-Codex Meeting routes must
keep their existing behavior. Local focused tests, independent review, and a
post-deploy native input plus PID/health/queue/Attention/History readback are
required before a production acceptance claim.

No live Meeting source text or private profile is stored in this report.

## Local validation

The command-submission regression failed before the one-line runtime change
(`project_doc_max_bytes` was `None`) and passes after it. The focused Meeting
agent file passes 94 tests. The first broader run exposed a test adapter that
did not accept the newly forwarded native option; the local adapter now checks
that exact value and retains its existing command assertions. Ruff, compile,
and whitespace checks pass.

An ephemeral native Codex OAuth probe in a temporary directory verified the
CLI boundary without tools: with its synthetic `AGENTS.md` present, the default
command returned the document's canary; with `project_doc_max_bytes=0`, it
returned `NONE`. The anonymous fixed Meeting comparison kept identical source,
Skill body, synthetic profile, schema, model, and checks within each pair.
Both baseline and candidate passed business alignment and mixed-sensitive
cases (4/4 valid, 0 tool calls), including exact group, 10% rollout, assigned
owner, and no sensitive hint. The synthetic document was 8,335 characters;
the observed production document was 7,060 characters. These checks establish
the native input change and two fixed outcomes, not general Meeting quality or
token savings. Reproduction code and concise receipt are in
`evals/trajectory_optimization/meeting_project_doc_native.py` and
`meeting_project_doc_native.v1.json`.
