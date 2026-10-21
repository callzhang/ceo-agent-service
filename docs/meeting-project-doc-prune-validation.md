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

Independent `codex review --uncommitted` reported no actionable issue in this
scoped change. The earlier broad two-file test run is not used as acceptance:
it captured the pre-fix fake-adapter signature failure and continued through
unrelated database-heavy tests. The corrected focused Meeting Agent file and
two planner/prompt integration tests above are the completed local checks.

## Release and production readback

PR #194 merged at `366dd8241d295c6d5c1188e94910abcf3db0d206` on
2026-10-21. A standard `python -m app.deploy --target-commit` deployment of
that exact commit completed without `--skip-quiet-wait`. Immediately after the
deployment, the production checkout was clean at the merge commit, launchd
supervisor PID `79458` and service worker PID `79470` were new, `/healthz`
returned HTTP 200 with `ok: true`, the queue endpoint reported zero active
items, and Attention and History both returned HTTP 200 with readable items.

A post-deploy anonymous Meeting run used the deployed commit's
`RoutedCodexExecution` and Meeting command factory with a temporary SQLite
database, an empty MCP manifest, a fictional business-alignment source and
synthetic profile. Its native session was
`01a15ded-c3f6-71b0-816e-24965a563424`. The structured decision passed
the fixed recipient, rollout and owner checks and made zero tool calls. The
native input contained the service Meeting prompt and the global CLI bootstrap,
but no User message for `/Users/derek/Documents/memory/AGENTS.md` and no
`Memory 资料目录：用途与存放规则` heading. The first readback attempt lacked the
local `MEMORY_CONNECTOR_URL` environment variable and stopped before command
build; the successful run used an empty temporary MCP manifest and did not
connect to business MCP or perform an external action.

No natural production Meeting job had run after the deployment at the time of
this readback. This result verifies the deployed command's native input and
one fictional structured decision; it does not claim a live recipient outcome
or a measured general token saving.
