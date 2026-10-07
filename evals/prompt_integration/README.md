# Prompt integration fixed comparison v2

This corpus measures the approved prompt assembly/compaction change against
`0d6bf42eb2922ed57a6bc6de8b836f5a443106c3`. It is synthetic, tool-free and
policy-neutral. It does not replay business actions or prove production outcomes.

The 20 checked-in cases cover messages (2), OA materials (3), email (2), meeting
summaries (2), calendar/timezone facts (6), scheduled tasks (2), feedback revision
(1), and prior-stage/ordinary document receipts (2). The canonical case SHA is
`5041b0dfb2656fceea617ffedbae2913592878c7158917112937f0b5a89edea2`.
The case data and runtime settings were frozen before candidate model runs.

## Actual inputs and evidence boundaries

`assemble.py` executes imports inside each immutable archived Git ref. Consumer
uses the real `AgentTaskContext.render`, scheduled prefix, revision feedback,
continuation and production User-template composition. Audit uses the real
`AuditTurnContext.render`, complete fixed candidate and canonical candidate
digest. Candidate Audit rendering passes the same developer_audit_rules argument
as the actual runner, removing only its redundant Task copy; baseline rendering
remains unchanged. Each candidate role loads its own frozen PromptConfiguration,
including the Audit role that does not consume the User template. Both roles use their ref's real developer-instruction renderer, default
configuration templates, fixture work-profile wrapper, task Skill override and
final-route Runtime Context. The baseline task prefix copies the exact baseline
Consumer runner assembly because that ref predates the shared pure function.

The fixture short profile is identical for both refs. Full private profile size
is reported separately as counts only. Full `source_bindings` are present in
both task evidence and the fixed Audit candidate; no field/leaf projection or
truncation is allowed. Native tool availability is intentionally empty, matching
the rendered Runtime Context. Supplied inline facts are synthetic complete
readbacks, and timezone conversion tables are provided as verified synthetic
evidence. This measures preservation/reasoning over those facts, not live tool
retrieval or a model's unaided timezone database.

Developer shared/default template text may differ by candidate ref as the
approved integration change. Audit Rules and profile fixture sources are kept
constant. Shared local rules are whatever the actual developer renderer reads,
and their exact resolved body is retained in the frozen assembled input artifact.
No production business task is replayed; no business-source tool, external
write, email or message is called. The allowed live reads are route/model/thinking
metadata from the prompt-preview APIs and the private profile file character/byte
counts. Private profile body is never saved. Source extraction is read-only apart
from temporary Git archives.

The route metadata was independently verified through both live read-only prompt
preview APIs: `codex_oauth`, `codex_cli`, `gpt-5.6-luna`, `medium`.
Every role starts an isolated ephemeral native session, serial concurrency 1,
300-second per-role timeout, one repetition. There are 80 role invocations for a
full two-arm comparison. Native CLI config/auth is reused; all inherited MCP
servers, native tools, hooks, skills/plugins, web search and subagents are disabled
with the established business-evaluation command. Any tool event invalidates that
role. A native authentication/capacity/transport failure preserves the partial
artifact and stops; missing model output is never promoted to a pass.

Fresh-session history is zero prior turns. This evaluation cannot prove session
reuse/resume behavior; focused runtime regression tests cover that separately.
The supplied timezone tables were separately checked with local Python zoneinfo,
including roundtrip/fold checks. Provider usage is taken only from actual `turn.completed.usage` events. Character
counts are not tokens; no tokenizer estimate is fabricated.

## Invocations

Raw inputs, CLI events and native reports live in the private working-data
directory, with exact-byte/SHA relocation receipts. Git retains only synthetic
corpora, code, documentation and compact summary metrics.

Run from the isolated implementation worktree using the existing Python runtime:

```sh
EVAL_ARTIFACT_ROOT=/Users/derek/Documents/memory/ceo-agent-service/prompt-integration-20261006/evaluation
python scripts/eval_prompt_integration.py --prepare-only --arms baseline --output "$EVAL_ARTIFACT_ROOT/baseline-size.v2.json"
python scripts/eval_prompt_integration.py --probe --output "$EVAL_ARTIFACT_ROOT/probe.v1.json"
python scripts/eval_prompt_integration.py --run --arms baseline --cases message-progress,calendar-split-dst --output "$EVAL_ARTIFACT_ROOT/baseline-smoke.v2.json"
python scripts/eval_prompt_integration.py --prepare-only --candidate-ref CANDIDATE_SHA --output "$EVAL_ARTIFACT_ROOT/comparison-size.v2.json"
python scripts/eval_prompt_integration.py --run --candidate-ref CANDIDATE_SHA --cases message-progress,calendar-split-dst --output "$EVAL_ARTIFACT_ROOT/comparison-smoke.v2.json"
python scripts/eval_prompt_integration.py --run --candidate-ref CANDIDATE_SHA --output "$EVAL_ARTIFACT_ROOT/comparison.v2.json"
pytest -q evals/prompt_integration/test_harness.py
```

`--candidate-ref` must resolve to an immutable commit. Dirty working-tree candidates
are not evaluated. `--arms baseline` allows baseline work before candidate commit.
Reports save full actual input text, SHA, candidate digest, native events, strict
normalized result, real usage, elapsed time and failures incrementally after each
invocation.

## Independent review

Automatic screening measures strict role schema, supported outcome, Consumer
stage/predecessor binding and Audit exact digest/revision binding. It never grants
semantic quality. Outcome screening alone can flag a plausible equivalent answer
for review; it is not authority for a new business policy.

An independent reviewer should inspect each exact output against the full fixed
case/source binding and the case's `semantic_rubric`. Score each dimension from
0 (incorrect), 1 (partial), 2 (complete):

1. Facts: original-trigger authority, source IDs, dates, receipt/completion
   boundaries, full supporting facts and no invented targets or facts.
2. Candidate usefulness: complete requested deliverable, established audience,
   concrete meaningful content, appropriate handling of missing source-owned
   facts, no new permission/authorization requirement.
3. Continuation: preserved stage, revision, feedback, action identity, prior
   receipts, historical human answer and current business-state distinction.
4. Timezone (applicable cases): known versus unknown zone, date-specific offsets,
   split DST, next-day conversion, repeated/nonexistent local time and explicit
   availability boundary.
5. Audit: independent judgment on the exact fixed candidate, honest and concrete
   feedback, no rewriting, external execution or new business policy.

Review every failure and any baseline/candidate disagreement. Compare quality per
category and dimension, not just aggregate outcomes. Audit approval of a good
candidate and return/reject of deliberately defective candidates are both tested.
Shorter Developer text is an efficiency measurement only; quality acceptance
requires no unexplained deterioration across the independent dimensions above.
The default design target is at least 30% static Developer character reduction,
with 50% aspirational; there is no arbitrary cap on dynamic evidence.

Compact retained evidence can be generated without more model calls:

```sh
python evals/prompt_integration/summarize.py "$EVAL_ARTIFACT_ROOT/baseline-native.v2.json" --output evals/prompt_integration/baseline-summary.v2.json
```

Some fixed subject judgments require reviewer care: the policy-gap fixture has
rule_coverage=1.0 despite an uncovered class, and email drafting may expose
existing disagreement about a no_action result containing complete prepared
text. These are immutable comparison inputs and retained disagreements, not
permission-policy changes or automatic evidence that one arm is better.

After both immutable native reports complete, produce the independent paired packet
and keep the arm key away from the reviewer until scoring is finished:

```sh
python evals/prompt_integration/blind_review.py "$EVAL_ARTIFACT_ROOT/baseline-native.v2.json" "$EVAL_ARTIFACT_ROOT/candidate-native.v2.json" --output "$EVAL_ARTIFACT_ROOT/blind-review.v2.json" --key "$EVAL_ARTIFACT_ROOT/blind-key.v2.json"
```

The packet retains complete case context and exact wire output. It omits arm
labels, latency, length and expected-outcome labels to keep those from substituting
for source/semantic assessment. The key is a separate decoding artifact.

The20 frozen cases use a task Skill override rather than the default catalog.
Their source bindings preserve complete synthetic facts but do not equal the
production context_source projection required to activate its exact-copy source
reference optimization. Therefore this corpus can assess other integration
changes while catalog compaction and matching-source dedup remain separate
mechanical/size evidence. candidate-working-tree-size.v2.json is a read-only
current-tree measurement, not an immutable candidate/model result.

The main harness also accepts --artifact-root (defaulting to the private path
above). Relative --output filenames resolve beneath that root. Compact metrics
from summarize.py may be saved under evals/prompt_integration; full reports must
stay in the private working-data directory.

## Matching-source and private-profile supplements

`supplement.v1.json` copies message-progress, oa-current-instance,
feedback-revision and prior-stage from v2. Only the fixed subject's reviewed source
bindings are regenerated with the exact production `context_source` projection;
non-task-context OA evidence, proposals, contexts and labels are preserved.
Canonical cases SHA is
`2433049a74ad6206af93adba5e488e10271eef95bc1cb3f8354fc174672f9658`.
Baseline/current `context_source` ASTs were verified identical before freezing.

`supplement.py` runs eight native roles per arm using the same runtime settings.
It checks complete binding equality, exact candidate digest equality between
arms, and that the actual candidate Audit Task exercises
`Candidate revision.candidate.source_bindings[0].value`. Its reports remain
separate from the 20-case corpus.

```sh
python evals/prompt_integration/supplement.py --arm baseline --output "$EVAL_ARTIFACT_ROOT/supplement-baseline-native.v1.json"
python evals/prompt_integration/supplement.py --arm candidate --candidate-ref CANDIDATE_SHA --output "$EVAL_ARTIFACT_ROOT/supplement-candidate-native.v1.json"
```

The separate private-profile pair uses those same four cases and unchanged native
settings, original profile for baseline and independently reviewed compact English
profile for candidate. Pass --profile-path and its frozen --profile-sha256; a
custom profile body is read only into private actual-input artifacts. The harness
records its path/hash/counts and verifies it did not change during assembly.
Candidate must select the corresponding profile baseline with --baseline-report.
These runs never write either profile or publish a live configuration.

```sh
python evals/prompt_integration/supplement.py --arm baseline --profile-path /Users/derek/Documents/memory/ceo-agent-service/prompt-integration-20261006/profile.original.md --profile-sha256 d2c07c3eff42e1d7fdc1553d6c86ca97c33ccc82b4c96aef4a650564a5a93c72 --output "$EVAL_ARTIFACT_ROOT/supplement-profile-baseline-native.v1.json"
python evals/prompt_integration/supplement.py --arm candidate --candidate-ref CANDIDATE_SHA --profile-path /Users/derek/Documents/memory/ceo-agent-service/prompt-integration-20261006/profile.compact.en.md --profile-sha256 60616e0a4466818758daaeeb4fc5a2fee0f4c64aca1d862da8af4880b4823d5f --baseline-report "$EVAL_ARTIFACT_ROOT/supplement-profile-baseline-native.v1.json" --output "$EVAL_ARTIFACT_ROOT/supplement-profile-candidate-native.v1.json"
```

Private profile hashes and numeric measurements do not substitute for independent
semantic review. Reference tokenizer counts are separately labelled; provider
usage comes only from the native event records. Catalog selection/formatting is
still a unit/property-review boundary, not covered by these task overrides.
