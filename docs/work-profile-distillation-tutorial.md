# Work Profile Distillation Tutorial

This guide explains how to regenerate Alex's repo-local work profile from
local evidence and read-only DingTalk evidence.

Distillation means turning many concrete examples into a smaller operating
profile that the auto-reply worker can use. The profile should capture judgment
order, follow-up behavior, boundaries, and expression style. It should not
copy raw private evidence into committed files.

## Inputs

The profile builder uses these evidence sources:

- `style_corpus.csv`: extracted Alex-style examples from local AI meeting
  notes and recent DingTalk sent messages.
- `/Users/principal/Documents/memory`: local authored or curated work documents.
- DingTalk knowledge base documents read through `dws` in read-only mode.

Runtime evidence and online document cache are written under
`data/profile-evidence/`, which is ignored by Git.

## Step 1: Refresh The Style Corpus

Build the local AI meeting-note corpus:

```bash
cd /path/to/ceo-agent-service
.venv/bin/ceo-agent build-corpus \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/corpus
```

Append recent DingTalk sent-message examples:

```bash
.venv/bin/ceo-agent collect-corpus \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/corpus
```

## Step 2: Build The Work Profile

Run the profile builder:

```bash
.venv/bin/ceo-agent build-work-profile \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/corpus
```

By default this command:

- rebuilds the local AI meeting-note corpus
- appends DingTalk sent-message samples
- scans local work documents
- reads DingTalk knowledge base documents through `dws`
- writes profile evidence under `data/profile-evidence/`
- writes committed profile assets under `profiles/`

Use these flags when you need a narrower run:

```bash
.venv/bin/ceo-agent build-work-profile --skip-minutes-corpus
.venv/bin/ceo-agent build-work-profile --skip-dingtalk-messages
.venv/bin/ceo-agent build-work-profile --skip-dingtalk-kb
```

## Outputs

The committed outputs are:

```text
profiles/work_profile.md
profiles/work_profile.json
profiles/work-skill/SKILL.md
```

The runtime consumes `profiles/work_profile.md` directly. The derived
skill is for manual agent use only; the auto-reply worker should not depend on a
global installed skill.

## Review Checklist

Before using a regenerated profile, check:

- The profile explains decision order, incomplete-material handling, expression
  style, scenario rules, and boundaries.
- The profile does not expose raw sensitive excerpts, local private paths, tokens,
  or DingTalk cache contents.
- Each JSON rule has evidence ids and the evidence ids exist in
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

