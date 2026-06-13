# Work Profile Distillation Tutorial

This guide explains how to regenerate Alex's repo-local work profile from
local evidence and read-only DingTalk evidence.

Distillation means turning many concrete examples into a smaller operating
profile that the auto-reply worker can use. The profile should capture judgment
order, follow-up behavior, boundaries, and expression style. It should not
copy raw private evidence into committed files.

For full profile distillation, this workflow depends on the local Nvwa persona skill. The repository command prepares evidence and writes the runtime profile file; the Nvwa step reviews the evidence and rewrites the profile so it reflects the user's real work judgment rather than a static template.

## Inputs

The profile builder uses these evidence sources:

- `style_corpus.csv`: extracted Alex-style examples from local AI meeting
  notes and recent DingTalk sent messages.
- `/Users/principal/Documents/memory`: local authored or curated work documents.
- DingTalk knowledge base documents read through `dws` in read-only mode.

Runtime evidence is written under `data/profile-evidence/`, which is ignored by Git.

## Prerequisite: Nvwa Skill

Install the Nvwa persona skill before rebuilding a reviewed work profile:

```bash
test -f ~/.agents/skills/nvwa/SKILL.md
```

Expected path:

```text
~/.agents/skills/nvwa/SKILL.md
```

Checker:

```bash
test -f ~/.agents/skills/nvwa/SKILL.md && echo "PASS nvwa skill installed"
```

Satisfies when:

- The command prints `PASS nvwa skill installed`.
- The skill is local under `~/.agents/skills/nvwa/SKILL.md`.
- No generated profile has been rebuilt yet; this check only verifies the review
  skill dependency.

## Step 1: Refresh The Style Corpus

Build the local AI meeting-note corpus:

```bash
cd /path/to/ceo-agent-service
.venv/bin/ceo-agent build-corpus \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/data/corpus
```

Append recent DingTalk sent-message examples:

```bash
.venv/bin/ceo-agent collect-corpus \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/data/corpus
```

Checker:

```bash
test -s data/corpus/style_corpus.csv && echo "PASS style corpus exists"
test -s data/corpus/style_profile.md && echo "PASS style profile exists"
```

Satisfies when:

- `data/corpus/style_corpus.csv` exists and is non-empty.
- `data/corpus/style_profile.md` exists and is non-empty.
- `build-corpus` output reported scanned local AI meeting-note files.
- `collect-corpus` output reported the current `dws` sender user and collected
  records, or explicitly reported `records=0` for a no-new-data run.

## Step 2: Build The Work Profile

Run the profile builder:

```bash
.venv/bin/ceo-agent build-work-profile \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/data/corpus
```

By default this command:

- rebuilds the local AI meeting-note corpus
- appends DingTalk sent-message samples
- scans local work documents
- reads DingTalk knowledge base documents through `dws`
- writes `data/profile-evidence/evidence_index.jsonl`
- writes `data/work-profile/work_profile.md`

Use these flags when you need a narrower run:

```bash
.venv/bin/ceo-agent build-work-profile --skip-minutes-corpus
.venv/bin/ceo-agent build-work-profile --skip-dingtalk-messages
.venv/bin/ceo-agent build-work-profile --skip-dingtalk-kb
```

Checker:

```bash
test -s data/profile-evidence/evidence_index.jsonl && echo "PASS evidence index exists"
test -s data/work-profile/work_profile.md && echo "PASS work profile exists"
test ! -e data/work-profile/work_profile.json && echo "PASS no work_profile.json generated"
test ! -e data/work-profile/work-skill/SKILL.md && echo "PASS no derived work skill generated"
test ! -d data/profile-evidence/dingtalk_kb_cache && echo "PASS no DingTalk KB cache directory generated"
```

Satisfies when:

- `data/profile-evidence/evidence_index.jsonl` exists and is non-empty.
- `data/work-profile/work_profile.md` exists and is non-empty.
- The command did not generate `data/work-profile/work_profile.json`.
- The command did not generate `data/work-profile/work-skill/SKILL.md`.
- The command did not generate `data/profile-evidence/dingtalk_kb_cache/`.
- Any skipped source is intentional and visible from the command flags.

## Outputs

The local runtime outputs are ignored by Git:

```text
data/work-profile/work_profile.md
data/profile-evidence/evidence_index.jsonl
```

The runtime consumes `data/work-profile/work_profile.md` directly. The builder should not
generate `data/work-profile/work_profile.json`, `data/work-profile/work-skill/SKILL.md`, or a
`data/profile-evidence/dingtalk_kb_cache/` directory.

## Step 3: Review With Nvwa

After `build-work-profile` prepares the evidence and runtime profile file, run a
Codex session with the Nvwa skill loaded and ask it to rewrite only
`data/work-profile/work_profile.md` from `data/profile-evidence/evidence_index.jsonl`,
`data/corpus/style_corpus.csv`, and `data/work-profile/work_profile.md`.

Checker:

```bash
test -s data/work-profile/work_profile.md && echo "PASS reviewed work profile exists"
```

Satisfies when:

- The Nvwa review step explicitly read `data/profile-evidence/evidence_index.jsonl`.
- The Nvwa review step explicitly read `data/corpus/style_corpus.csv`.
- The Nvwa review step explicitly read the initial `data/work-profile/work_profile.md`.
- `data/work-profile/work_profile.md` is rewritten as a profile, not as a transcript or
  evidence dump.
- Claims in the profile can be traced back to evidence records without exposing
  raw sensitive excerpts.

## Review Checklist

Before using a regenerated profile, check:

- The profile explains decision order, incomplete-material handling, expression
  style, scenario rules, and boundaries.
- The profile does not expose raw sensitive excerpts, local private paths, tokens,
  or DingTalk cache contents.
- Important claims can be traced to evidence ids in
  `data/profile-evidence/evidence_index.jsonl`.
- The profile does not authorize the agent to make final approvals, personnel
  decisions, financial commitments, or customer-critical decisions without
  Alex's explicit action.

Run the focused tests:

```bash
cd /path/to/ceo-agent-service
.venv/bin/pytest tests/test_work_profile.py tests/test_prompt.py tests/test_worker.py::test_consumer_codex_command_embeds_work_profile_content -q
```

Run the full local-service suite before committing behavior changes:

```bash
.venv/bin/pytest -q
```

Final checker:

```bash
git status --short data/work-profile data/profile-evidence data/corpus
```

Satisfies when:

- `data/work-profile/work_profile.md` appears if the reviewed profile changed.
- `data/profile-evidence/` runtime evidence is not staged or committed.
- `data/corpus/` runtime corpus files are not staged or committed unless the project
  deliberately changes the corpus policy.
- Focused tests pass before relying on the regenerated profile.
- Full local-service tests pass before committing behavior changes.
