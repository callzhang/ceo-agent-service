# Business Entity Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 退出旧来源型记录和并行状态机，使 Action、Task、Run、Email 的每种事实只有一个权威归属。

**Architecture:** 分成三个可独立发布的子项目，先统一外部 Action，再统一 Task/Run，最后收束 Email 与残余业务记录。这里是总路线；只把第一单元写成可执行详细计划，其他单元须在取得第一单元接口后分别形成 spec/plan，不伪装成已经有准确 DDL。

**Tech Stack:** Python、SQLite JSONB、pytest、React/TypeScript、原生 Codex CLI、app.deploy。

**Spec:** `docs/superpowers/specs/2026-10-21-action-entity-unification-design.md`（第一单元）；本总路线不授权扩大审查/安全策略。

## Global Constraints

- 保留 provenance、必要状态、已采用业务事实和实际执行回执。
- 不需要运行时兼容旧结构、旧 DTO 或旧类型名称；只保留必要的显式迁移与回退工具。
- 不新增或改变审查、授权、确认、取消、重试和外部结果核对策略。
- 先在隔离 worktree 和数据库副本验证；原生 CLI 验收的对外发送使用替身。
- 邮件预览保留至多 1,000 Unicode 字符。

## Review Focus

1. 不同旧类型相同整数 ID：不能误合并；第一单元 Task 1/2 覆盖。
2. 多个观察者共享一个真实 effect：不能重复发送；第一单元 Task 3/4 覆盖。
3. 生产推进后副本基线过期：发布前重取源并重新验证；第一单元 Task 6/7 已从新生产来源复验并完成部署读回。
4. Schema 回退包含正迁后的新记录：不能回滚成旧数据快照；第一单元 Task 5/6 覆盖。
5. 旧 UI/API 仍调用已删除 DTO：必须随 runtime 原子切换；第一单元 Task 4 覆盖。

---

## Task 1: 统一已有外部动作（先实施）

**Files / Interfaces:** 详细契约、TDD 步骤、文件职责和验收见 `2026-10-21-action-entity-unification.md`；不得另行复制或简化其接口。

- [x] 第一外部 Action 单元 Task 1–7 已完成、通过 parent SPEC/QUALITY 审阅、标准部署并完成生产读回；详见 [Phase 1 生产验收](../../action-entity-production-acceptance.md)。
- [x] 实际七个有数据 source families、旧 writer/reader 范围及 unified Action/Receipt/Observer/identity-map 结构已记录；其余两项零行 discriminator 声明、专有 unsubscribe、方案/审查和其他 Actions 类型明确留在范围外。
- [x] 生产 Action 接口已发布，可供后续独立单元设计引用；后续工作仍须按各自边界实施与验收。

## Task 2: Task/Run 真正统一（后续独立 spec/plan，不在第一单元顺手实施）

**Files:** `app/task20_definitions.py`、`app/task_lifecycle_definitions.py`、`app/run_storage.py`、`app/dispatcher/adapters.py`、实际任务生产/领取/恢复 caller、`app/history.py` 与对应前端页面。
**Consumes:** Task 1 发布的统一动作身份/查询。
**Produces:** 一项工作一种 Task 生命周期；输入版本/附件/别名不再是 Task 类型；Run 各角色和重试共享执行模型。逻辑运行与真实 CLI invocation 先核对基数，不能强并或虚构模型调用。

- [ ] 源码确认实际 Task/输入版本/业务关联基数，写独立 spec，列出现有字段与真实消费者。
- [ ] 写该单元详细 TDD 计划，包含候选/选择/审查结果退出 Actions 的准确归属、既有版本绑定、调度历史保留边界及所有 UI 读取。
- [ ] 计划审阅后实施；同人口容量、副本写入、原生 Consumer/Audit/修订与当前数据回退通过后独立发布。

## Task 3: Email 与残余业务记录（后续独立 spec/plan）

**Files:** `app/email_canonical_definitions.py`、`app/email_store.py`、`app/email_provider_actions.py`、`app/email_unsubscribe.py`、实际 Business/evidence/feedback caller 与对应页面。
**Consumes:** 前两单元的 Action 与 Task/Run 接口。
**Produces:** Email 单一事实归属；真实可恢复的退订操作使用既有 Action 生命周期；工作进展/错误/业务关联退出 Actions，输入正文按独立来源退休。

- [ ] 核对授权/历史配置/邮件原文可重读性与全部退订 continuation 的恢复事实；写独立 spec/plan，不编造字段。
- [ ] 实施时保留唯一授权事实、业务结果、回执和 1,000 字符预览；不得把无法找回的独有事实列作已删除冗余。
- [ ] 副本/回退/原生及 UI 通过后发布，逐条关闭旧类型与旧 writer，而非只关新写入、继续保留另一套历史运行时。

## 总体完成标准

所有发布单元分别完成；每种保留实体给出统一 schema、唯一 writer 和真实 reader；旧 origin/type 兼容分派退出；必要事实/回执可查询且可恢复；容量和增长有同人口实测。后续两单元目前只有边界，没有详细实施计划，不得声称整个统一工程已经准备完成或实现完成。
