# Nvwa Work Profile Installation

This guide describes the local dependency needed to generate a reviewed
`profiles/work_profile.md`.

For a full machine setup, including `dws`, Codex CLI, Memory Connector,
interactive parameters, corpus preparation, audit web management, launchd, and
permission checks, use
[docs/agent-installation-runbook.md](agent-installation-runbook.md). This file
only covers the Nvwa/profile part of that flow.

## What Nvwa Does

Nvwa is the persona-distillation skill used after evidence collection. The CEO
service does not load Nvwa at runtime. Runtime reads only
`profiles/work_profile.md` through `work_profile_instruction()`.

Use Nvwa to read prepared evidence, extract stable work judgment patterns,
rewrite the runtime Markdown profile, and keep sensitive evidence out of
committed files.

## Install The Skill

Install the internal Nvwa skill package so this file exists:

```bash
test -f ~/.agents/skills/nvwa/SKILL.md
```

Expected path:

```text
~/.agents/skills/nvwa/SKILL.md
```

If the check fails, install or sync the Nvwa skill from the internal skill
source before regenerating a reviewed profile. Do not copy generated profile
content into `~/.agents/skills`; generated profile content belongs in this
repository.

## Prepare Data

```bash
.venv/bin/ceo-agent build-corpus \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/corpus

.venv/bin/ceo-agent collect-corpus \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/corpus

.venv/bin/ceo-agent build-work-profile \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/corpus
```

Expected outputs:

```text
profiles/work_profile.md
data/profile-evidence/evidence_index.jsonl
```

The builder should not produce `profiles/work_profile.json`,
`profiles/work-skill/SKILL.md`, or `data/profile-evidence/dingtalk_kb_cache/`.

## Review With Nvwa

Start a Codex session with the Nvwa skill available and ask it to rewrite only
`profiles/work_profile.md` using:

```text
profiles/work_profile.md
data/profile-evidence/evidence_index.jsonl
corpus/style_corpus.csv
```

The Nvwa review prompt should require Markdown-only output, no extra files, no
raw private excerpts, no absolute local paths, no tokens/session ids, and
preservation of hard safety boundaries around approvals, HR, finance, legal,
customer-critical commitments, and real-world actions.

## Runtime Check

The service reads the final profile through:

```text
app.prompt:work_profile_instruction()
```
