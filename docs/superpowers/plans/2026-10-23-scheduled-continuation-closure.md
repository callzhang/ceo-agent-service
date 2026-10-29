# 通用定时任务续跑闭环核查执行计划

> **For agentic workers:** Use `superpowers:executing-plans` inline. 用户已批准 spec 并要求直接核查，无需再次确认。只读调查不适用代码 TDD 或全量测试；本文件的步骤和最终报告即执行记录。

**Goal:** 判断现有通用定时任务续跑机制是否已闭环，并以已有轨迹和回执给出有限结论。
**Architecture:** 沿用现有运行记录、CLI 原生 JSONL、任务产物和服务回执；历史版本按运行记录读取。先复用已有证据，源文件缺失就记录缺口，不重放任务制造证据。
**Tech Stack:** Git、Python、SQLite `mode=ro`/`query_only`、现有原生轨迹读取工具。
**Spec:** [已批准设计](../specs/2026-10-23-scheduled-continuation-closure-design.md)。

## 1. 更新与冻结证据范围

- [x] 在独立工作树执行 `git pull --no-rebase origin main`；返回 `Already up to date`。
- [x] 记录生产 SHA、417176/417170 的调度身份、代次、Skill/配置、路线及每轮原生 JSONL 路径和边界。
- [x] 复用 `docs/archive/records/scheduled-okr-agent-acceptance.md`、`docs/archive/records/scheduled-task-generic-agent-validation.md` 的已有修复与固定评测。仅在相关内容确实改变时补查。

## 2. 逐轮核对实际推进

- [x] 完整读取 417176 的失败、续跑、两次修订及三个 Audit 调用范围；提取各轮收到的反馈、来源/分析/报告产物读取与写入、终态及耗时/可得 token。
- [x] 完整读取 417170 的四次失败范围，与相应修复版本对齐，区分必要复核和没有推进的重读。417039 仅作版本对照。
- [x] 不把不同任务间的成功相互替代；不把 session ID 相同或 `done` 单独计为通过。

## 3. 终态与通用性

- [x] 核对 417176 最终候选、Audit 结论、文档内容读回和服务发送回执；优先用已保存证据，确有缺口时只读查询同一结果。
- [x] 从已有日报/管理周报记录选一条链，核对共用续跑/反馈代码路径。无同类轨迹则限定结论，不触发新运行。
- [x] 按 spec 五项条件分别判定；没有必要的新代码或变化，不运行测试或模型评测。

## 4. 交付并结束本项

- [x] 在 `docs/scheduled-continuation-closure-verification.md` 写一页结论，附精确运行/代码/回执引用；元数据放 `evals/trajectory_optimization/scheduled_continuation_closure.readback.v1.json`，不复制业务正文、凭据或原始日志。
- [x] 自查证据是否直接支持结论、是否混淆历史修复与现有缺陷；执行 `git diff --check`，只提交本项文档/元数据并释放认领。
- [x] 以“已闭环 / 未闭环 / 证据不足”之一收口。若未闭环，只列已确认原因与最小修复范围，本计划不实施修复；不无限轮询或扩展问题。
