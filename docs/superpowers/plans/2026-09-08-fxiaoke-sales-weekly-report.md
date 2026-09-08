# Fxiaoke Sales Weekly Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give CEO Agent direct read-only use of the installed `sharecrm` CLI through a new managed `ceo-sales-weekly-report` Skill that creates an on-demand, scored Markdown report in `CEO_WORKSPACE`.

**Architecture:** Keep the implementation Skill-only except for registering the new repository-owned Skill in the existing managed business Skill catalog. The Skill composes `ceo-weekly-report` and `fxiaoke-crm-cli`, relies on the native Codex command reviewer instead of a new CRM wrapper, reads targets from current OKR or an approved plan, reads actuals from Fxiaoke, and writes one uniquely named final Markdown artifact under the configured workspace.

**Tech Stack:** Python 3.12, pytest, Markdown `SKILL.md`, existing managed-Skill SQLite lifecycle, native Codex runtime, official `sharecrm` CLI, launchd.

---

## Scope and File Map

Implement in an isolated `codex/` worktree. Preserve unrelated files and
commits in the main checkout. The design authority is
`docs/superpowers/specs/2026-09-08-fxiaoke-sales-weekly-report-design.md`.

Files created or modified:

- `skills/ceo-sales-weekly-report/SKILL.md`: complete business semantics,
  read-only CRM rules, scoring contract, report structure, and workspace output
  contract.
- `tests/test_sales_weekly_report_skill.py`: focused static contract tests for
  the new Skill.
- `app/business_skills.py`: add the Skill to the existing business Skill
  catalog; do not add CRM command handling.
- `tests/test_business_skills.py`: update the exact catalog expectation and
  prove installation/catalog rendering includes the new Skill.
- `README.md`: document eight business Skills and the new sales-report role.
- `docs/architecture.md`: document the eighth business Skill and its operation
  Skill dependencies.
- `docs/runtime-mechanism.md`: update the baseline business-Skill count without
  changing lifecycle semantics.

Do not modify `app/agent_cli.py`, `app/native_cli_metadata.py`,
`data/config/skill-features.json`, any task schema, database schema, scheduler,
or launchd configuration.

### Task 1: Add the sales weekly report Skill contract

**Files:**
- Create: `tests/test_sales_weekly_report_skill.py`
- Create: `skills/ceo-sales-weekly-report/SKILL.md`

- [ ] **Step 1: Write the failing Skill contract tests**

Create `tests/test_sales_weekly_report_skill.py` with this content:

```python
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-sales-weekly-report" / "SKILL.md"
FEATURES_PATH = ROOT / "data" / "config" / "skill-features.json"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _normalized_skill_text() -> str:
    return " ".join(_skill_text().split())


def test_sales_weekly_report_skill_composes_existing_skills_and_sources() -> None:
    text = _normalized_skill_text()

    for required in (
        "`ceo-weekly-report`",
        "`fxiaoke-crm-cli`",
        "current company OKR or an explicitly approved sales plan",
        "authoritative target",
        "CRM actual",
        "target-definition conflict",
        "Asia/Shanghai",
    ):
        assert required in text


def test_sales_weekly_report_skill_is_explicitly_crm_read_only() -> None:
    text = _normalized_skill_text()

    for required in (
        "CRM reads only",
        "Never pass `--confirm` or `--yes`",
        "Do not create, update, delete, invalidate",
        "Do not assign, transfer, claim, return, or reclaim",
        "Do not create follow-ups or sales activities",
        "Do not send CRM email, IM, feed, or notice",
        "Do not act on CRM approvals, BPM, workflows, stages, automations, or schedules",
        "Do not log in, log out, import token data, or change CLI configuration",
        "cannot establish that a command is read-only",
    ):
        assert required in text

    for forbidden in (
        "Fxiaoke MCP",
        "crm_connector MCP",
        "execute_reviewed_read",
        "execute_reviewed_write",
    ):
        assert forbidden not in text


def test_sales_weekly_report_skill_defines_scoring_and_coverage() -> None:
    text = _normalized_skill_text()

    for required in (
        "target_completion_rate = actual / period_target",
        "time_progress_rate = elapsed_time / total_target_period",
        "progress_index = target_completion_rate / time_progress_rate",
        "piecewise-linear",
        "score coverage",
        "Missing values are not zero",
        "company score",
        "MorningStar",
        "Friday",
        "international business",
        "world-model marketing",
        "Do not rank individual salespeople",
        "待归属",
    ):
        assert required in text


def test_sales_weekly_report_skill_writes_only_one_final_workspace_report() -> None:
    raw_text = _skill_text()
    text = " ".join(raw_text.split())

    for required in (
        "`CEO_WORKSPACE`",
        "`01_业务与客户/销售周报/YYYY/`",
        "`YYYY-MM-DD-HHmm-销售周报.md`",
        "Never overwrite an existing report",
        "Save only the final Markdown report",
        "Reopen the saved file",
    ):
        assert required in text

    assert raw_text.endswith("\n")
    assert not raw_text.endswith("\n\n")


def test_sales_weekly_report_has_no_independent_producer_feature() -> None:
    payload = json.loads(FEATURES_PATH.read_text(encoding="utf-8"))
    feature_skills = {
        skill
        for feature in payload["features"]
        for skill in feature.get("skills", [])
    }

    assert "ceo-sales-weekly-report" not in feature_skills
```

- [ ] **Step 2: Run the tests to verify the missing Skill fails**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_sales_weekly_report_skill.py -q
```

Expected: four tests fail with `FileNotFoundError` for
`skills/ceo-sales-weekly-report/SKILL.md`; the feature-mapping test passes.

- [ ] **Step 3: Create the minimal complete Skill**

Create `skills/ceo-sales-weekly-report/SKILL.md` with this content:

```markdown
---
name: ceo-sales-weekly-report
description: Use when Derek explicitly asks CEO Agent to generate, refresh, reconcile, or score an on-demand sales weekly report from current targets and Fxiaoke CRM actuals.
metadata:
  managed_by: ceo-agent-service
---

# CEO Sales Weekly Report

## Purpose

Create an evidence-backed sales management report, not an activity summary.
Produce a company score and business-line scores from authoritative targets and
current CRM actuals, explain material deviation, and save one final Markdown
artifact in the configured workspace.

This Skill runs only on an explicit report request. It does not schedule itself,
scan CRM proactively, send the report, or change CRM.

## Required Skills And Sources

Before gathering evidence, read and follow both `ceo-weekly-report` and
`fxiaoke-crm-cli` completely, including the references they require.

Use the current company OKR or an explicitly approved sales plan as the
authoritative target. Use Fxiaoke as the authority for CRM actual results. If CRM
also contains a target, compare it with the authoritative target. Record a
target-definition conflict when value, period, owner, business line, or metric
definition differs; never silently choose the CRM target.

If the authoritative target source cannot be resolved, or all material CRM
actuals are unavailable, return a failed outcome and do not create a report.

## Reporting Window

Use `Asia/Shanghai`. Unless the request supplies an explicit interval, cover the
preceding Monday 00:00 inclusive through the current Monday 00:00 exclusive. If
the requested target period is still open, use the actual query time as cutoff
and display it.

Score a quarterly or annual target against elapsed time in that target's own
period. A weekly actual does not become a full-period actual.

## CRM Reads Only

Use the installed native `sharecrm` CLI and its current authenticated user. Do
not copy, request, display, or persist token material.

Allowed work is limited to authentication status, CLI help, object
identification, object and field description, record-name resolution, record
reads, structured detail queries, structured aggregate queries, and other
commands whose current help unambiguously identifies them as reads.

Describe the live object before choosing fields or status values. Prefer
structured semantic detail and aggregate queries over generated SQL. Finish all
pages before claiming a complete result. Distinguish an empty result from a
failed or incomplete query.

Never pass `--confirm` or `--yes` to `sharecrm`.

Do not create, update, delete, invalidate, or otherwise mutate CRM records. Do
not assign, transfer, claim, return, or reclaim customers or leads. Do not create
follow-ups or sales activities. Do not change opportunity stages, owners,
forecasts, amounts, or dates. Do not send CRM email, IM, feed, or notice. Do not
act on CRM approvals, BPM, workflows, stages, automations, or schedules. Do not
log in, log out, import token data, or change CLI configuration.

When current help and `fxiaoke-crm-cli` cannot establish that a command is
read-only, do not run it. Mark the affected metric `无数据`, state the exact
missing capability, and reduce score coverage.

Codex automatic command review is a second review layer, not proof that an
unknown command is read-only.

## Metric Evidence

For every consequential value disclose the target source and period, CRM
object, date field, metric field, status field and included values, record count
or aggregation coverage, formula, cutoff, timezone, pagination completion, and
any masking or permission limit.

Do not substitute one business event for another. A plan is not an actual. A
meeting statement or forecast is not a CRM actual. Pipeline is not a signed
contract. A contract is not recognized revenue, delivery, or collected cash.
Activity count does not prove a business result.

## Business-Line Attribution

Produce one company score and separate scores for MorningStar, Friday,
international business, and world-model marketing.

Attribute a CRM record to a business line only through an explicit live CRM
field or an authoritative target mapping. Do not map records through customer,
project, or salesperson name keywords. Put unresolved records in `待归属`.
Include them in a valid company total but exclude them from business-line
scores.

Do not rank individual salespeople. Name an owner only for a business result,
checkpoint, or acceptance standard.

## Deterministic Progress Score

For a metric where higher values represent progress, calculate:

```text
target_completion_rate = actual / period_target
time_progress_rate = elapsed_time / total_target_period
progress_index = target_completion_rate / time_progress_rate
```

Use this piecewise-linear mapping and round the displayed score to the nearest
integer:

| Progress index | Score | Status |
| --- | ---: | --- |
| `>= 1.10` | `100` | `✅ 超前` |
| `0.95` to `< 1.10` | interpolate `90` to `100` | `✅ 正常` |
| `0.80` to `< 0.95` | interpolate `75` to `90` | `⌛ 轻度偏离` |
| `0.60` to `< 0.80` | interpolate `50` to `75` | `⚠️ 明显偏离` |
| `< 0.60` | interpolate `0` to `50` over `0.00` to `0.60` | `❌ 严重偏离` |

For a bounded metric where lower values are better, including overdue
receivables or sales-cycle duration, use an explicitly labeled inverse formula
that matches the target definition. Never divide by zero.

If an actual is negative, disclose the value and its source. Unless the
authoritative target definition explicitly permits negative actuals and defines
their score treatment, leave that metric unscored and reduce score coverage.

Use approved weights when the target source defines them. Otherwise use:

| Metric group | Weight |
| --- | ---: |
| New signed contracts or orders | 25% |
| Recognized revenue | 15% |
| Payment collected | 20% |
| Gross profit or gross margin | 10% |
| Weighted Pipeline | 15% |
| Opportunities advancing to the next Gate | 10% |
| Overdue receivables and material sales risk | 5% |

Score only metrics with a valid target, compatible CRM actual, defined period,
and consistent definition. Missing values are not zero. Remove an ineligible
metric from the score denominator, normalize remaining eligible weights for the
displayed score, and report score coverage as the original eligible weight.
Always show both the score and score coverage.

## Report Structure

Write these sections in order:

1. `CEO销售判断`
2. `公司业务目标进度评分`
3. `公司销售经营指标`
4. `MorningStar`
5. `Friday`
6. `国际业务`
7. `世界模型营销`
8. `重点Pipeline与异常`
9. `回款、应收与交付风险`
10. `跨周问题与下周检查点`
11. `需CEO讨论与决策`
12. `数据口径、覆盖率与缺失项`

Show abnormalities that need management attention. Preserve prior open issue
IDs when a prior sales report exists. A missed deadline is red immediately; two
weeks without Gate movement is at least yellow; after three weeks without
evidence recommend exactly one of continue, change owner, downgrade, or stop.

## Workspace Output

Resolve the root from `CEO_WORKSPACE`; do not hard-code a machine-specific
path. If it is absent or not a directory, fail with
`ceo_workspace_unavailable`.

Write under `01_业务与客户/销售周报/YYYY/` using the Beijing timestamp filename
`YYYY-MM-DD-HHmm-销售周报.md`. Never overwrite an existing report. If the target
already exists, fail with `sales_weekly_report_path_exists` and leave it
unchanged.

Save only the final Markdown report. Do not save raw CRM responses, debug logs,
tokens, complete customer/contact records, or intermediate calculations.

After writing, reopen the saved file and verify the reporting period, score,
score coverage, twelve required sections, source definitions, and non-empty
content. Return the resolved file path as the report artifact. A generated
answer without a matching saved file is not complete.
```

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_sales_weekly_report_skill.py -q
```

Expected: `5 passed`.

- [ ] **Step 5: Validate the new Skill as a service-managed source**

Run:

```bash
/Users/derek/miniforge3/bin/python -c 'from app.managed_skills import validate_managed_skill_content; from pathlib import Path; p=Path("skills/ceo-sales-weekly-report/SKILL.md"); print(validate_managed_skill_content("ceo-sales-weekly-report", p.read_text(encoding="utf-8")))'
```

Expected: one SHA-256 digest and exit code `0`.

- [ ] **Step 6: Commit the standalone Skill and its tests**

Run:

```bash
git add skills/ceo-sales-weekly-report/SKILL.md tests/test_sales_weekly_report_skill.py
git commit -m "feat(skills): add sales weekly report policy"
```

Expected: one commit containing only the new Skill and focused test file. The
Skill is not live yet because it is not registered in the business catalog.

### Task 2: Register the Skill in the managed business catalog

**Files:**
- Modify: `app/business_skills.py:11`
- Modify: `tests/test_business_skills.py:19`

- [ ] **Step 1: Extend the failing exact-inventory expectation**

Change `EXPECTED_NAMES` in `tests/test_business_skills.py` to:

```python
EXPECTED_NAMES = (
    "ceo-message-triage",
    "ceo-calendar-invite",
    "ceo-document-review",
    "ceo-meeting-work",
    "ceo-mail-review",
    "ceo-personnel-communication",
    "ceo-work-tracking",
    "ceo-sales-weekly-report",
)
```

- [ ] **Step 2: Run the exact inventory test and verify it fails**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_business_skills.py::test_bundled_business_skill_inventory_is_exact_and_valid -q
```

Expected: failure showing that `BUNDLED_BUSINESS_SKILL_NAMES` lacks
`ceo-sales-weekly-report`.

- [ ] **Step 3: Register the new business Skill**

Change `BUNDLED_BUSINESS_SKILL_NAMES` in `app/business_skills.py` to:

```python
BUNDLED_BUSINESS_SKILL_NAMES = (
    "ceo-message-triage",
    "ceo-calendar-invite",
    "ceo-document-review",
    "ceo-meeting-work",
    "ceo-mail-review",
    "ceo-personnel-communication",
    "ceo-work-tracking",
    "ceo-sales-weekly-report",
)
```

Do not change `REPOSITORY_MANAGED_SKILL_NAMES`; it already expands
`BUNDLED_BUSINESS_SKILL_NAMES`. Do not add the Skill to
`data/config/skill-features.json`, because there is no separate producer.

- [ ] **Step 4: Run business catalog and managed import tests**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_business_skills.py tests/test_managed_skills.py tests/test_managed_skill_runtime.py tests/test_sales_weekly_report_skill.py -q
```

Expected: pytest exits `0` with no failures.

- [ ] **Step 5: Commit catalog registration**

Run:

```bash
git add app/business_skills.py tests/test_business_skills.py
git commit -m "feat(skills): register sales weekly report"
```

Expected: one commit with exactly the catalog and inventory-test changes.

- [ ] **Step 6: Restart immediately after the runtime catalog commit**

Record the current PID:

```bash
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Restart the service:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
```

Verify a different running PID:

```bash
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: `state = running`, `last exit code = 0`, and a PID different from the
pre-restart PID.

- [ ] **Step 7: Verify the managed baseline and load receipt through the API**

Resolve the managed Skill and its repository revision:

```bash
skill_id=$(curl --silent --show-error --fail http://127.0.0.1:8765/api/console/settings/managed-skills | jq -r '.items[] | select(.name == "ceo-sales-weekly-report") | .id')
```

```bash
revision_json=$(curl --silent --show-error --fail "http://127.0.0.1:8765/api/console/settings/managed-skills/${skill_id}/revisions" | jq -c '.items[] | select(.source == "repository:skills")' | tail -n 1)
```

```bash
revision_id=$(jq -r '.id' <<<"${revision_json}")
```

```bash
revision_sha=$(jq -r '.sha256' <<<"${revision_json}")
```

Resolve the active configuration and prove it binds that revision:

```bash
config_json=$(curl --silent --show-error --fail http://127.0.0.1:8765/api/console/settings/runtime-skill-configs/current)
```

```bash
config_id=$(jq -r '.active.id' <<<"${config_json}")
```

```bash
jq -e --argjson skill_id "${skill_id}" --argjson revision_id "${revision_id}" '.active.status == "active" and any(.active.bindings[]; .skill_id == $skill_id and .revision_id == $revision_id and .enabled == true)' <<<"${config_json}"
```

Read the append-only load receipt and match the Skill SHA:

```bash
receipt_json=$(curl --silent --show-error --fail "http://127.0.0.1:8765/api/console/settings/runtime-skill-configs/${config_id}/load-receipts" | jq -c '.items[] | select(.error == "")' | tail -n 1)
```

```bash
jq -e --arg skill_id "${skill_id}" --arg revision_sha "${revision_sha}" '.loaded_json | fromjson | .[$skill_id] == $revision_sha' <<<"${receipt_json}"
```

Expected: each command exits `0`; the Skill has a `repository:skills`
revision, the active configuration binds that exact revision, and a successful
load receipt maps its Skill ID to the same SHA.

- [ ] **Step 8: Check service health and backlog after restart**

Run:

```bash
curl --silent --show-error --fail http://127.0.0.1:8765/healthz | jq .
```

Run:

```bash
CEO_WORKER_DB='/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3' PYTHONPATH=. /Users/derek/miniforge3/bin/python -m app.cli quality-check --db '/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3'
```

Expected: health is successful; the quality check reports no new unresolved
`failed` or `processing` violation caused by this change. Record any pre-existing
attention separately rather than claiming it was introduced or repaired here.

### Task 3: Update current architecture documentation

**Files:**
- Modify: `README.md:75`
- Modify: `README.md:225`
- Modify: `README.md:240`
- Modify: `docs/architecture.md:406`
- Modify: `docs/architecture.md:421`
- Modify: `docs/architecture.md:423`
- Modify: `docs/architecture.md:680`
- Modify: `docs/runtime-mechanism.md:48`

- [ ] **Step 1: Update the README inventory and role description**

Change every current statement that the service installs seven business Skills
to eight. Add `ceo-sales-weekly-report` to the explicit inventory and add this
sentence after the inventory paragraph:

```markdown
`ceo-sales-weekly-report` 仅在明确请求时读取当前目标与纷享销客实际数据，生成公司及业务线销售进度评分并把最终 Markdown 保存到 `CEO_WORKSPACE`；它不创建定时任务，也不执行 CRM 写入。
```

- [ ] **Step 2: Update the architecture inventory without changing routing**

Change the three relevant `docs/architecture.md` count references from seven to
eight and add this row to the dynamic Skill table:

```markdown
| `ceo-sales-weekly-report` | 按需核对销售目标、CRM 实际、公司及业务线进度评分并生成 workspace 周报 | `ceo-weekly-report`、`fxiaoke-crm-cli` |
```

After the table, add:

```markdown
`ceo-sales-weekly-report` 没有独立 producer 或功能开关。它由 Consumer 根据明确的销售周报请求动态选择，直接使用安装用户已有的 `sharecrm` 登录态；CRM 只读限制由 Skill 和 Codex automatic review 约束，不表示 service 建立了 `sharecrm` 命令白名单。
```

- [ ] **Step 3: Update the runtime-mechanism baseline count**

Change the statement at `docs/runtime-mechanism.md:48` from seven
service-owned business Skills to eight. Do not change the separate statement
that `feedback_iteration` is not an eighth business task-production switch;
that sentence describes feature-switch semantics, not the business catalog
count.

- [ ] **Step 4: Check the documentation diff and stale counts**

Run:

```bash
git diff --check -- README.md docs/architecture.md docs/runtime-mechanism.md
```

Run:

```bash
rg -n '七个.*业务 Skill|七个 CEO 业务 Skill|七个随服务安装|seven.*business Skill' README.md docs/architecture.md docs/runtime-mechanism.md
```

Expected: `git diff --check` is clean. The count search returns no current
description of the runtime catalog as seven; historical documents under
`docs/superpowers/` are not edited.

- [ ] **Step 5: Commit the documentation update**

Run:

```bash
git add README.md docs/architecture.md docs/runtime-mechanism.md
git commit -m "docs: document sales weekly report skill"
```

Expected: one documentation-only commit.

### Task 4: Run focused and full automated verification

**Files:**
- Test: `tests/test_sales_weekly_report_skill.py`
- Test: `tests/test_business_skills.py`
- Test: `tests/test_managed_skills.py`
- Test: `tests/test_managed_skill_runtime.py`

- [ ] **Step 1: Run the focused feature suite**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_sales_weekly_report_skill.py tests/test_business_skills.py tests/test_managed_skills.py tests/test_managed_skill_runtime.py -q
```

Expected: all focused tests pass.

- [ ] **Step 2: Run the complete repository test suite**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest -q
```

Expected: all non-live tests pass. If a pre-existing unrelated failure appears,
capture its exact test name and reproduce it on the implementation base commit
before classifying it as unrelated; do not modify unrelated behavior.

- [ ] **Step 3: Verify no forbidden implementation surfaces changed**

Run:

```bash
git diff HEAD~3..HEAD -- app/agent_cli.py app/native_cli_metadata.py data/config/skill-features.json launchd
```

Expected: no output.

Run:

```bash
git status --short
```

Expected: no task-owned uncommitted files. Pre-existing user-owned files remain
untouched and are listed separately if present.

### Task 5: Perform live on-demand acceptance in CEO Agent Workbench

**Files:**
- Create externally: `${CEO_WORKSPACE}/01_业务与客户/销售周报/YYYY/YYYY-MM-DD-HHmm-销售周报.md`
- No repository files modified

- [ ] **Step 1: Reconfirm live CLI identity and read access**

Run:

```bash
sharecrm auth status
```

Expected: `tokenStatus` is `normal` and the current authenticated identity is
shown without exposing token material.

Run:

```bash
sharecrm data describe get -d '{"apiName":"AccountObj"}' | jq '{api_name, display_name, field_count: (.fields | length)}'
```

Expected: `api_name` is `AccountObj`, the display name identifies the customer
object, and `field_count` is greater than zero. This is a read-only probe.

- [ ] **Step 2: Create one real Workbench task through the local API**

Run:

```bash
task_id=$(curl --silent --show-error --fail -X POST http://127.0.0.1:8765/api/workbench/tasks -H 'Content-Type: application/json' -d '{"title":"验证纷享销客销售周报 Skill","runtime_kind":"codex"}' | jq -r '.id')
```

Expected: `task_id` is a non-empty UUID.

- [ ] **Step 3: Submit the bounded acceptance request**

Run:

```bash
curl --silent --show-error --fail -X POST "http://127.0.0.1:8765/api/workbench/tasks/${task_id}/turns" -H 'Content-Type: application/json' -d '{"text":"读取并严格遵守 ceo-sales-weekly-report、ceo-weekly-report 和 fxiaoke-crm-cli。按北京时间生成最近一个完整周的销售经营周报。目标使用当前公司 OKR 或可验证的已批准销售计划，实际使用纷享销客。CRM 只能执行只读命令，禁止 --confirm、--yes 以及任何创建、更新、发送、审批或流程操作。生成公司和四条业务线评分及评分覆盖率，只保存最终 Markdown 到配置的 CEO_WORKSPACE，并返回文件路径。","client_request_id":"fxiaoke-sales-weekly-report-live-v1"}' | jq '{id, status}'
```

Expected: one queued or running turn is returned. No confirmation for a CRM
write should appear. A local report-file write may be auto-reviewed as part of
the explicitly requested task.

- [ ] **Step 4: Wait for the persisted turn result**

Poll at intervals shorter than 60 seconds:

```bash
curl --silent --show-error --fail "http://127.0.0.1:8765/api/workbench/tasks/${task_id}/timeline?turn_limit=20&event_limit=100" | jq '{task: .task, turns: [.turns[] | {id, status, final_text, error_code}], artifacts: .artifacts, tool_events: [.events[] | select(.event_type == "tool_completed") | .payload]}'
```

Expected: the turn reaches `completed`; the result contains one sales-report
artifact path. If it fails, preserve the exact error and repair only an
in-scope Skill, catalog, environment, or report-path defect before rerunning
with a new `client_request_id`.

- [ ] **Step 5: Audit the completed native commands**

Read the complete timeline response and inspect each completed command.
Expected:

- at least one `sharecrm auth status` or equivalent status read;
- live object/field discovery before facts are queried;
- only read-only `sharecrm` operations;
- no argument equal to `--confirm` or `--yes`;
- no create, update, delete, assign, transfer, claim, return, reclaim,
  follow-up, send, approval decision, workflow effect, login, logout, token
  import, or configuration change;
- no raw OAuth material in final text or artifacts.

Do not execute a CRM write as a negative test.

- [ ] **Step 6: Verify the saved report directly**

Resolve `report_path` from the persisted task artifact:

```bash
report_path=$(curl --silent --show-error --fail "http://127.0.0.1:8765/api/workbench/tasks/${task_id}/timeline?turn_limit=20&event_limit=100" | jq -r '.artifacts | map(select(.media_type == "text/markdown" or (.path | endswith(".md")))) | last | .path')
```

Verify the path is non-empty:

```bash
test -n "${report_path}"
```

Verify the file exists:

```bash
test -f "${report_path}"
```

Run:

```bash
case "${report_path}" in /Users/derek/Documents/memory/01_业务与客户/销售周报/*/*-销售周报.md) exit 0 ;; *) exit 1 ;; esac
```

Run:

```bash
rg -n '^#|CEO销售判断|公司业务目标进度评分|评分覆盖率|MorningStar|Friday|国际业务|世界模型营销|数据口径、覆盖率与缺失项' "${report_path}"
```

Expected: the file exists under the configured workspace sales-report year
directory and contains all required report sections, score, coverage, reporting
window, cutoff, and source definitions. Inspect the complete file to confirm it
contains no raw CLI response, token, debug log, phone number dump, or invented
actual.

- [ ] **Step 7: Perform final runtime and backlog checks**

Run:

```bash
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Run:

```bash
curl --silent --show-error --fail http://127.0.0.1:8765/healthz | jq .
```

Run:

```bash
CEO_WORKER_DB='/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3' PYTHONPATH=. /Users/derek/miniforge3/bin/python -m app.cli quality-check --db '/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3'
```

Expected: the service remains running and healthy; no new unresolved failed or
processing backlog was introduced; the Workbench turn, Skill loading, CLI
reads, final artifact, and file readback are all persisted or directly
inspectable.

## Completion Evidence

Do not call the implementation complete from the Skill file or focused tests
alone. Final handoff must include:

- the three task-owned commit SHAs;
- focused and full-suite test results;
- old and new service PIDs;
- active managed Skill config and matching load receipt for
  `ceo-sales-weekly-report`;
- Workbench task and turn IDs;
- evidence that completed `sharecrm` commands were read-only;
- the absolute saved Markdown path;
- direct report readback findings;
- `/healthz` output summary;
- final backlog/quality-check result;
- disclosure of any untouched pre-existing worktree changes or unrelated
  failures.
