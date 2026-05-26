# Derek Work Profile Design

## Purpose

Build a repo-owned Derek work profile for `ceo-agent-service`.

The profile should help the DingTalk CEO agent decide and reply more like Derek
in work contexts. It is not a biography, a generic personality summary, or a
replacement for Derek's final judgment. It is a structured, evidence-backed
operating profile for:

- when to reply
- how to judge incomplete information
- how to ask follow-up questions
- how to phrase concise work replies
- when to hand off to Derek
- what the agent must not claim or decide

The first implementation should use the profile as a stable local project asset.
The Nuwa-style skill is a derived artifact for manual agent use, not the runtime
decision path for automated DingTalk replies.

## Confirmed Scope

Use approach 1: generate the service-ready profile first, then derive a Derek
skill from the profile.

The first version should produce both:

- a `ceo-agent-service` profile used by the local auto-reply runtime
- a Nuwa-style Derek skill that can later be installed under
  `~/.agents/skills/derek-perspective/`

Runtime auto-reply should not directly invoke Nuwa or the global skill. It should
read the repo-local profile.

## Inputs

### Existing Evidence Corpus

Reuse the existing evidence pipeline instead of rebuilding it.

The current service already has paths for:

- DingTalk messages sent by Derek, collected through `dws list_messages_by_sender`
- Derek's AI meeting transcript utterances from `workspace/AI听记/**/*.md`
- `derek_style_corpus.csv`, which stores extracted behavior examples

This corpus remains the highest-value evidence for real behavior and response
style.

### Local Knowledge Base

Use `/Users/derek/Documents/memory/` as the local knowledge base input.

Most material in this folder can be treated as Derek-authored or Derek-curated
for first-version extraction. Higher-confidence authored sources include:

- `Thinking/`
- `management/strategy/`
- product strategy documents
- business writing
- management writing
- other clearly authored local docs

The local knowledge base is an input source only. Do not write generated profiles
back into `/Users/derek/Documents/memory/`.

### Live DingTalk Knowledge Base

The first version should also pull online DingTalk knowledge base documents in
read-only mode.

Use DWS document tools:

- `dws doc list` to browse workspaces or folders
- `dws doc info` to inspect node metadata such as title, type, creator, and time
- `dws doc read` to read online document Markdown content

The live DingTalk knowledge base pass must be read-only. It must not create,
move, update, delete, or change permissions for any DingTalk knowledge base
nodes.

Cache pulled documents locally under ignored runtime data so repeated profile
generation does not need to rescan every online document.

## Evidence Classification

Normalize all inputs into evidence records with at least:

- `id`
- `source_type`
- `title`
- `timestamp`
- `location` or source handle
- `scenario`
- `evidence_strength`
- `sensitivity`
- `excerpt`
- `usable_for_profile`

Use these evidence strength classes:

- `behavior_high`: Derek's sent DingTalk messages and direct transcript speech
- `authored_high`: clear Derek-authored local or knowledge base writing
- `authored_assumed`: likely Derek-authored local material
- `kb_live_doc`: online DingTalk knowledge base document content
- `kb_live_doc_assumed`: online knowledge base content whose author cannot be
  confirmed but is still useful
- `background_only`: context material that must not directly become a Derek rule

Sensitive evidence should stay in ignored data files. Stable profile files should
not include raw sensitive excerpts.

## Extraction Flow

Do not ask a model to summarize all materials in one pass. Use a staged flow.

1. Collect evidence
   - load `derek_style_corpus.csv`
   - scan selected local knowledge base directories
   - read live DingTalk knowledge base documents
   - cache online document reads

2. Label evidence
   - source
   - scenario
   - evidence strength
   - sensitivity
   - whether it can be used for profile extraction

3. Extract atomic rules
   - one small rule per observed behavior or judgment pattern
   - each rule has a trigger, do action, do-not action, scenarios, confidence,
     and evidence ids

4. Merge rules into the work profile
   - decision framework
   - expression framework
   - follow-up question framework
   - boundary framework
   - scenario playbooks
   - honest boundaries

5. Derive a Derek skill
   - generate the skill from the stable profile
   - keep role-play language bounded by work context and honesty constraints
   - do not make the automated DingTalk runtime depend on the skill

## Output Files

Use repo-local paths.

Committed project assets:

```text
profiles/derek_work_profile.md
profiles/derek_work_profile.json
profiles/derek-skill/SKILL.md
```

Ignored runtime evidence data:

```text
data/profile-evidence/evidence_index.jsonl
data/profile-evidence/dingtalk_kb_cache/
```

Do not use `style_profile.md` for this new work profile. That name belongs to
the existing style corpus concept and may already exist in historical or runtime
state. If an existing style profile is found later, treat it as input evidence
and do not overwrite it.

## Profile Shape

`profiles/derek_work_profile.md` should be concise and readable. It should
include:

- purpose and scope
- core judgment order
- decision framework
- expression framework
- follow-up question framework
- scenario playbooks
- boundary framework
- honest boundaries

`profiles/derek_work_profile.json` should be structured for tests and future
evals. Each rule should include:

- `id`
- `title`
- `category`
- `scenarios`
- `trigger`
- `do`
- `dont`
- `confidence`
- `evidence_ids`

`profiles/derek-skill/SKILL.md` should be a Nuwa-style derived skill for manual
use. It should state that it is Derek's work perspective, not Derek himself. It
must not authorize the agent to make real-world commitments, execute approvals,
or make final personnel judgments.

## Runtime Integration

`ceo-agent-service` should use the repo-local profile, not Nuwa directly.

Add a prompt rule equivalent to:

```text
If profiles/derek_work_profile.md exists in this repository, read it before
making work-context judgments about reply style, follow-up questions, refusal,
handoff, or decision framing.
```

The profile must not override existing hard guardrails:

- real-world actions only Derek can perform must hand off to Derek
- OA and approval decisions require full material review
- internal personnel matters remain sensitive
- candidate judgments require role and resume evidence
- local paths, tool names, session ids, citations, and runtime details must not
  appear in outward replies
- system and notification cards should continue to be filtered before Codex

If the profile file is missing, current behavior should continue unchanged.

## Evaluation

Evaluate first-version quality at three levels.

### Structure Checks

- profile markdown includes all required sections
- JSON rules include required fields
- evidence ids referenced by JSON rules exist in `evidence_index.jsonl`
- committed profile files do not contain raw sensitive evidence excerpts
- runtime evidence and cache files remain ignored

### Behavior Replay

Use historical DingTalk messages or tests to check whether profile-guided replies:

- are shorter and more direct
- ask for missing material before judging
- avoid generic advice
- avoid claiming Derek's real-world status or actions
- avoid final approval, personnel, finance, or customer-critical decisions
  without evidence
- still comply with the existing decision JSON schema

### Human Review

Derek should review:

- whether the profile contains rules that do not sound like him
- whether key work scenarios are missing
- whether replayed replies are better, worse, or unchanged

## Completion Criteria

The first implementation is complete when:

- `profiles/derek_work_profile.md` exists
- `profiles/derek_work_profile.json` exists
- `profiles/derek-skill/SKILL.md` exists
- `data/profile-evidence/evidence_index.jsonl` and
  `data/profile-evidence/dingtalk_kb_cache/` are produced or expected as ignored
  runtime data
- prompt integration reads the profile when present
- profile absence keeps existing behavior unchanged
- tests cover prompt integration and ignored evidence data boundaries
- Derek has reviewed the generated profile and no obvious false-personality
  rules remain
