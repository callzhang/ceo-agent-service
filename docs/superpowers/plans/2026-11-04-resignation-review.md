# Resignation Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 离职节点围绕是否同意离职和离职时间作判断，避免后续交割凭证把当前审批无限退回。

**Architecture:** 修改现有 People Skill 的 Resignation 标准及通用 OA Skill 对该阶段的说明。Consumer 和 Audit 读取同一冻结版本，通过现有版本化 Skill 与原 OA 队列执行。业务规则留在 Skill，采用匿名对照评估检验实际判断。

**Tech Stack:** Markdown Skill、现有 managed Skill revision、pytest、原生 Consumer/Audit 评估脚本、DingTalk DWS。

**Spec:** `../specs/2026-11-04-oa-review-corrections-design.md` 的 A 与实施约束。A 是待批准的业务标准，实施前须经 Derek 确认。

## Global Constraints

- 业务规则位于 Skill，不在 Python 中写人名、实例 ID 或审批类别特例。
- 实现不按 Codex、Claude、Friday 路由分出不同业务规则。
- 每个测试进程保持 2 GB、600 秒限制。代码通过既有 `python -m app.deploy` 门禁发布。
- 沿用现有版本化 Skill、原任务 Consumer→Audit→System 执行及回执机制。
- 实施时使用 using-git-worktrees Skill，从最新 main 与最新 `.agents` 来源建立隔离工作区并认领文件。
- `.agents` 工作区存在其他 Agent 的修改，提交与发布仅包含本计划认可的两个 Skill hunk。

## Review Focus

- 审批后一个工作日办理交割，不能被当成审批前已逾期，归 Task 1 的 `resignation_after_approval_cutover`。
- 已核实负责人的具体确认与旧文档待确认不一致时采用最新事实，归 Task 1 的 `resignation_owner_confirmed`。
- 存在会改变离职时间的真实矛盾时不能直接批准，归 Task 1 的 `resignation_date_conflict`。
- 单据已回到他人节点时不重开审批，归 Task 1 的 `resignation_not_our_task` 和 Task 2 的现场复核。
- 历史任务使用旧冻结 Skill 时，发布文件不等于新规则已生效，归 Task 2 的 revision/SHA 核验及既有快照测试。

---

## 文件与接口

- Modify `/Users/derek/.agents/skills/stardust-oa-people-review/SKILL.md` 的 Resignation 段。它在 `/Users/derek/.agents` Git 仓库中受跟踪，是 runtime-only versioned Skill。
- Modify `/Users/derek/.agents/skills/dingtalk-oa-approval/SKILL.md` 的阶段说明，仅明确离职适用业务 Skill 的审批阶段。其他审批的必需材料要求继续按各自规则判断。
- Modify `evals/oa_skill_proportionality/cases.json`，加入六个匿名离职例子。
- Use `scripts/eval_oa_skill_replay.py` 的 `--skills-root/--skill-path/--baseline-ref/--with-audit`。它支持工具关闭的 Skill 对照。
- Test `tests/test_oa_review_skill_binding.py` 与 `tests/test_oa_manual_rerun_skill_refresh.py`，确认新轮次引用最新 Skill 且旧快照不变。
- Update `docs/runtime-mechanism.md`，说明离职判断和审批后交割的阶段。

### Task 1. 确定规则文案并通过旧版/新版对照评估

**Interfaces**

- Consumes Spec A 的已批准标准，以及当前两份 Skill 的原始字节和 SHA。
- Produces 两份候选 Skill 文本、匿名 case manifest 和 Consumer/Audit 对照结果。
- 现有 `skills_at(ref: str | None, *, root: Path, paths: tuple[str, ...]) -> str` 用于读取版本，`operations(result: dict | None) -> list[str]` 用于核对真实提案动作。

- [ ] **Step 1. 写六个匿名用例。** 采用现有 manifest 的 `id/trigger/facts/expect_not/expect_any` 格式。

  | 用例 ID | 给定事实 | 必须得到的结果 |
  | --- | --- | --- |
  | resignation_after_approval_cutover | 离职和日期明确，负责人同意，按批准后一个工作日交割，未查实关键工作无人承接 | approve；Audit approve；不要求提前完成或截图 |
  | resignation_owner_confirmed | 负责人明确确认企微已删除，未见矛盾，旧文档仍写待处理 | approve；不再索取重复删除凭证 |
  | resignation_routine_receipts_missing | 负责人同意，项目已有接手安排，普通逐项签收尚未齐全，未查实决定性风险 | approve；不因统一清单格式退回 |
  | resignation_date_conflict | 申请 last day 与负责人明确确认的 last day 不同，差异影响当前决定 | return/revert_task；意见仅要求澄清日期 |
  | resignation_critical_work_conflict | 负责人明确反对当前交接安排，关键客户承诺仍无人承接，属于当前决定的实质矛盾 | return/revert_task；说明具体事项及需澄清的责任安排 |
  | resignation_not_our_task | 历史退回已生效，当前 task 属于发起人，Derek list-pending 中没有该实例 | no_action；无外部动作 |

- [ ] **Step 2. 建临时 Skill-only Git 仓库。** 在 `mktemp -d /tmp/oa-resignation-eval.XXXXXX` 中只复制两份当前 Skill，保存为 `skills/<name>/SKILL.md` 并提交 baseline。生产 Skill 此时保持原版本，临时仓库路径记为 `skill_eval_root`。
- [ ] **Step 3. 运行旧版，确认有实际失败。** 从 CEO 实现工作树执行 `python scripts/eval_oa_skill_replay.py --skills-root "$skill_eval_root" --skill-path skills/dingtalk-oa-approval/SKILL.md --skill-path skills/stardust-oa-people-review/SKILL.md --cases evals/oa_skill_proportionality/cases.json --baseline-ref HEAD --case resignation_after_approval_cutover --case resignation_owner_confirmed --case resignation_routine_receipts_missing --case resignation_date_conflict --case resignation_critical_work_conflict --case resignation_not_our_task --with-audit --model gpt-6-luna --repeats 2`。该模型来自调查时当前 OA Consumer 实际运行记录；比较两侧使用完全相同设置。
- [ ] **Step 4. 在临时副本修改 Resignation 段。** 用 Spec A 的七条规则替换当前“交接未完成即退回”及未定义完成标准的表述。明确负责人具体陈述可以作为事实依据，后续交割按已有流程办理。通用 OA 段只增加该阶段分工的短引用，不把其他业务规则复制进去。
- [ ] **Step 5. 用 Step 3 相同命令运行候选对照。** 验收逐条检查候选 `ok=true`、`violates=[]`、实际 operations 命中该例 `expect_any`，以及 approve/return 候选的独立 Audit approve。脚本退出 0 不能单独算通过，它当前不会强制检查 expect_any；必须核对输出。正例两轮都通过，反例不被误批。
- [ ] **Step 6. 运行快照回归。** `python -m pytest -q tests/test_oa_review_skill_binding.py tests/test_oa_manual_rerun_skill_refresh.py tests/test_managed_skill_runtime.py`。这些测试覆盖既有发布和重跑接线，不用新 Python 决策器替代行为评估。
- [ ] **Step 7. 更新文档并分别提交。** CEO 仓库只提交匿名 case manifest 和行为文档。`.agents` 的隔离分支只提交两份 Skill 的批准 hunk，提交名 `fix: review resignation at the decision stage`。保留基线、候选 SHA 和逐例结果的简洁结论；清理原始临时模型轨迹。

### Task 2. 发布 Skill 并让真实下一轮使用它

**Interfaces**

- Consumes Task 1 通过的精确 Skill 字节与 `.agents` 提交。
- Uses `capture_runtime_skill_edits(store: AutoReplyStore, *, skills_root: Path | None = None)` 的既有启动捕获流程，以及 `refresh_oa_rerun_skill_snapshot` 的正式新轮次刷新。
- Produces 新 Skill revision/SHA、服务加载证据和按实时归属得到的业务结果。

- [ ] **Step 1. 核对并发布批准的两份 Skill hunk。** 先比较运行文件当前 SHA 和评估 baseline，保存当前有效版本；若有并发变化，合并后重跑受影响例子。通过 `.agents` 正常提交路径安装批准文本，随后用既有 `python -m app.deploy --restart` 完成 quiet 重启与启动版本捕获。若 Task B 代码同时发布，先按 B 的正式 gate 部署，再做此 Skill 发布。
- [ ] **Step 2. 核对版本生效。** 读取 managed Skill revisions、运行文件 SHA 和新 OA 输入里的 frozen Skill SHA，三者与候选一致。生产记录不到新 revision 就停止业务恢复并修正发布问题，不用旧规则重跑。
- [ ] **Step 3. 重新读取思睿与 ET 的详情、tasks 和 Derek list-pending。** 思睿调查时已回发起人，ET 也已有真实退回。当前不归 Derek 时记录等待下一次正常提交，不把旧节点强行改回待办。
- [ ] **Step 4. 在新提交或仍有效的 Derek 当前节点上走原流程。** Consumer 按新标准判断，Audit 读同一冻结 Skill，System 执行最终候选。只处理当前任务身份，复用原回执；不得重发历史退回。
- [ ] **Step 5. 核对真实效果。** 同意后验证当前 CEO 节点完成及流程下一节点。若当前仍无有效 CEO 待办，交付为“规则已发布，历史流程待正常回到 CEO”，不能声称已批准离职。
- [ ] **Step 6. 读回 healthz、PID、队列、Attention、History，移除 claim 并清理本计划创建的临时材料。** 只保留版本、评估结论与审批结果链接。

## 自审结果

Spec A 的阶段、责任人确认、具体矛盾、人工例外和历史节点都已落到规则、评估或现场验收。计划不新增审批层、项目特例或后台交割调度器。Skill 内容发布与实际 OA 批准分开验收。
