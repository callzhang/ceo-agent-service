# Data Runtime Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the repository cleanup so real local runtime data lives under ignored `data/` paths, seed/default files keep first-run behavior working, cache/backup files are not committed, and only directionally aligned WIP remains.

**Architecture:** Source-controlled defaults stay in `app/defaults/` and documentation. Mutable runtime files live under `data/` and are ignored, with `data/README.md` documenting what each local directory means. DWS command knowledge is kept as a small operational inventory, while generated schema snapshots and incomplete expanded references are removed from commit scope.

**Tech Stack:** Python service, SQLite runtime database, Markdown docs, Git ignore rules, `pytest`, `rg`, `dws` CLI documentation.

---

## File Structure

- Modify `README.md`: document the `data/` layout and point profile/corpus examples to `data/work-profile` and `data/corpus`.
- Modify `data/README.md`: explain each local runtime subdirectory and cleanup policy for backups, feedback exports, image caches, and temporary report outputs.
- Modify `docs/nvwa-work-profile-installation.md`: point Nvwa/profile setup commands at `data/corpus` and `data/work-profile`.
- Modify `docs/work-profile-distillation-tutorial.md`: point checkers and review workflow at `data/corpus`, `data/profile-evidence`, and `data/work-profile`.
- Create `docs/dws-command-inventory.md`: keep a small DWS capability and safety inventory generated from non-mutating probes.
- Do not create or commit `docs/dws-command-schema.snapshot.json`: this is a generated local snapshot.
- Do not create or commit `docs/dws-exhaustive-command-reference.md`: without generated per-service files it becomes a misleading index.
- Do not modify runtime source in this cleanup pass unless verification shows a regression in existing `data/` behavior.

## Scope Check

This plan covers one subsystem: local runtime-data hygiene and supporting docs. DWS command documentation is included only as a small safety inventory because it supports the service integration boundary. Broader DWS command generation, per-service docs, or automated schema diffing should be a separate plan.

### Task 1: Establish The Dirty Baseline

**Files:**
- Read: `README.md`
- Read: `data/README.md`
- Read: `docs/nvwa-work-profile-installation.md`
- Read: `docs/work-profile-distillation-tutorial.md`
- Read: `.gitignore`
- Read: `docs/superpowers/plans/2026-06-13-data-runtime-cleanup.md`

- [ ] **Step 1: Inspect current worktree**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
git status --short
```

Expected output before this cleanup is limited to docs and optional DWS inventory WIP:

```text
 M README.md
 M data/README.md
 M docs/nvwa-work-profile-installation.md
 M docs/work-profile-distillation-tutorial.md
?? docs/dws-command-inventory.md
?? docs/superpowers/plans/2026-06-13-data-runtime-cleanup.md
```

- [ ] **Step 2: Check for stale top-level runtime paths**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
rg -n "├── prompts|├── corpus|profiles/work_profile\.md|--corpus-dir /path/to/corpus" \
  README.md docs/nvwa-work-profile-installation.md docs/work-profile-distillation-tutorial.md data/README.md || true
```

Expected before editing: any matches are stale references that need to move to `data/`. Expected after Task 2: no output.

- [ ] **Step 3: Check for generated DWS artifacts**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
find docs -maxdepth 1 \( -name 'dws-command-schema.snapshot.json' -o -name 'dws-exhaustive-command-reference.md' \) -print
```

Expected after Task 3: no output.

### Task 2: Align Public Docs To The `data/` Runtime Layout

**Files:**
- Modify: `README.md`
- Modify: `docs/nvwa-work-profile-installation.md`
- Modify: `docs/work-profile-distillation-tutorial.md`
- Modify: `data/README.md`

- [ ] **Step 1: Update `README.md` profile and corpus paths**

Replace stale profile/corpus examples with these exact path forms:

```markdown
`data/work-profile/work_profile.md`
```

```bash
.venv/bin/ceo-agent build-work-profile \
  --workspace /path/to/workspace \
  --corpus-dir /path/to/data/corpus \
  --dingtalk-kb-workspace '<workspace-id-or-url>'
```

```text
├── app/defaults/                # 首次运行会复制到 data/ 的默认 Prompt 模板
├── data/                        # SQLite、Prompt override、corpus、profile 等本地运行态数据
```

- [ ] **Step 2: Update `docs/nvwa-work-profile-installation.md` command paths**

Use these exact command examples:

```bash
.venv/bin/ceo-agent build-corpus \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/data/corpus

.venv/bin/ceo-agent collect-corpus \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/data/corpus

.venv/bin/ceo-agent build-work-profile \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/data/corpus
```

Expected output section:

```text
data/work-profile/work_profile.md
data/profile-evidence/evidence_index.jsonl
```

- [ ] **Step 3: Update `docs/work-profile-distillation-tutorial.md` checkers**

Use these exact checker commands:

```bash
test -s data/corpus/style_corpus.csv && echo "PASS style corpus exists"
test -s data/corpus/style_profile.md && echo "PASS style profile exists"
test -s data/profile-evidence/evidence_index.jsonl && echo "PASS evidence index exists"
test -s data/work-profile/work_profile.md && echo "PASS work profile exists"
test ! -e data/work-profile/work_profile.json && echo "PASS no work_profile.json generated"
test ! -e data/work-profile/work-skill/SKILL.md && echo "PASS no derived work skill generated"
test ! -d data/profile-evidence/dingtalk_kb_cache && echo "PASS no DingTalk KB cache directory generated"
```

Use this final Git checker:

```bash
git status --short data/work-profile data/profile-evidence data/corpus
```

- [ ] **Step 4: Tighten `data/README.md` cleanup policy**

The cleanup policy must include these bullet points:

```markdown
- Optional export files such as `feedback.jsonl`; do not treat these as durable
  project files.
- Cache directories such as `image-attachments/`; the service recreates them as
  needed and removes them after each run.
```

- [ ] **Step 5: Verify no stale path references remain in active docs**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
rg -n "├── prompts|├── corpus|profiles/work_profile\.md|--corpus-dir /path/to/corpus" \
  README.md docs/nvwa-work-profile-installation.md docs/work-profile-distillation-tutorial.md data/README.md || true
```

Expected:

```text
```

### Task 3: Keep DWS Safety Inventory, Drop Generated DWS Artifacts

**Files:**
- Create: `docs/dws-command-inventory.md`
- Do not add: `docs/dws-command-schema.snapshot.json`
- Do not add: `docs/dws-exhaustive-command-reference.md`

- [ ] **Step 1: Keep `docs/dws-command-inventory.md` as the only DWS WIP document**

The document must start with this content:

```markdown
# dws Command Inventory

This document records the local `dws` CLI surface inspected for the CEO
auto-reply service. It is intentionally operational rather than tutorial-style:
use it to decide which commands are safe for the worker, which commands require
explicit human approval, and where to find exact parameter schemas.
```

- [ ] **Step 2: State that raw schema JSON is regenerated locally**

The regeneration section must use `/tmp`, not a committed docs path:

````markdown
## Regenerating The Snapshot

Run this after upgrading `dws`:

```bash
dws schema -f json > /tmp/dws-command-schema.snapshot.json
```

Then update this file if the version, service discovery output, service table, or
safety-relevant command families changed. Do not commit the generated raw JSON
unless there is a specific review need for the full schema artifact.
````

- [ ] **Step 3: Remove generated or misleading DWS files from the worktree**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
python3 - <<'PY'
from pathlib import Path
for path in [
    Path("docs/dws-command-schema.snapshot.json"),
    Path("docs/dws-exhaustive-command-reference.md"),
]:
    if path.exists():
        path.unlink()
PY
```

Expected:

```text
```

- [ ] **Step 4: Verify only the inventory remains**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
find docs -maxdepth 1 \( -name 'dws-command-schema.snapshot.json' -o -name 'dws-exhaustive-command-reference.md' \) -print
```

Expected:

```text
```

### Task 4: Clean Local Runtime Debris Without Touching Durable Local Data

**Files:**
- Preserve: `data/auto-reply.sqlite3`
- Preserve: `data/corpus/`
- Preserve: `data/profile-evidence/`
- Preserve: `data/prompts/`
- Preserve: `data/work-profile/`
- Preserve: `.venv/`
- Preserve: `node_modules/`

- [ ] **Step 1: Scan for backup, feedback, image-cache, and project cache debris**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
find . -path './.git' -prune -o \
  \( -name '*~' -o -name '*.bak' -o -name '*.bak-*' -o -name '*.backup-*' \
     -o -name '*.before-*' -o -name 'feedback.jsonl' -o -name 'backfill_*.jsonl' \
     -o -name 'image-attachments' -o -name '.pytest_cache' -o -name '.ruff_cache' \
     -o -name '__pycache__' \) -print | sort
```

Expected acceptable matches before cleanup are only project caches outside `.venv`, such as:

```text
./.pytest_cache
./.ruff_cache
./app/__pycache__
./tests/__pycache__
```

Matches under `.venv/` are dependency internals and should not drive repository edits.

- [ ] **Step 2: Delete only project-level caches**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
for p in .pytest_cache .ruff_cache app/__pycache__ tests/__pycache__ .vercel/output/static/.ruff_cache; do
  if [ -e "$p" ]; then
    find "$p" -type f -delete
    find "$p" -type d -empty -delete
  fi
done
```

Expected:

```text
```

- [ ] **Step 3: Verify no project-level debris remains**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
find . -path './.git' -prune -o -path './.venv' -prune -o -path './node_modules' -prune -o \
  \( -name '*~' -o -name '*.bak' -o -name '*.bak-*' -o -name '*.backup-*' \
     -o -name '*.before-*' -o -name 'feedback.jsonl' -o -name 'backfill_*.jsonl' \
     -o -name 'image-attachments' -o -name '.pytest_cache' -o -name '.ruff_cache' \
     -o -name '__pycache__' \) -print | sort
```

Expected:

```text
```

### Task 5: Verify And Commit The Cleanup

**Files:**
- Stage: `README.md`
- Stage: `data/README.md`
- Stage: `docs/nvwa-work-profile-installation.md`
- Stage: `docs/work-profile-distillation-tutorial.md`
- Stage: `docs/dws-command-inventory.md`
- Stage: `docs/superpowers/plans/2026-06-13-data-runtime-cleanup.md`

- [ ] **Step 1: Run whitespace diff check**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
git diff --check
```

Expected:

```text
```

- [ ] **Step 2: Run targeted path and generated-artifact checks**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
rg -n "├── prompts|├── corpus|profiles/work_profile\.md|--corpus-dir /path/to/corpus" \
  README.md docs/nvwa-work-profile-installation.md docs/work-profile-distillation-tutorial.md data/README.md || true
find docs -maxdepth 1 \( -name 'dws-command-schema.snapshot.json' -o -name 'dws-exhaustive-command-reference.md' \) -print
```

Expected:

```text
```

- [ ] **Step 3: Stage only aligned cleanup files**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
git add README.md data/README.md docs/nvwa-work-profile-installation.md docs/work-profile-distillation-tutorial.md docs/dws-command-inventory.md
git add docs/superpowers/plans/2026-06-13-data-runtime-cleanup.md
git status --short
```

Expected staged output:

```text
M  README.md
M  data/README.md
A  docs/dws-command-inventory.md
M  docs/nvwa-work-profile-installation.md
M  docs/work-profile-distillation-tutorial.md
A  docs/superpowers/plans/2026-06-13-data-runtime-cleanup.md
```

- [ ] **Step 4: Commit**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
git commit -m "docs: align local data cleanup guidance"
```

Expected: commit succeeds and prints a new commit hash.

- [ ] **Step 5: Restart service only if runtime code changed**

For this docs-only cleanup, do not restart `com.ceo-agent-service.main`. If implementation later changes Python runtime code, prompt rendering, routing logic, launchd config, or service behavior, run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected after a runtime-code change: launchd reports a fresh running process and no unresolved `failed` or `processing` backlog.

## Self-Review

- Spec coverage: The plan covers moving user-editable runtime material to ignored `data/` paths, keeping first-run defaults in source, ignoring `node_modules`, removing backups/feedback/cache debris, deleting non-useful DWS generated artifacts, and retaining only aligned DWS inventory docs.
- Placeholder scan: The plan contains no deferred implementation markers and no undefined files or functions.
- Type consistency: The same paths are used throughout: `data/corpus`, `data/work-profile/work_profile.md`, `data/profile-evidence`, `data/prompts`, and `docs/dws-command-inventory.md`.
