# Result Decision Quality and Human Escalation Design

**日期：** 2026-09-10  
**状态：** 已确认设计，待实现计划与代码实施

## 目标

把所有 Consumer Agent A / Audit Agent B 的结果质量拆成可解释、可校验的通用字段，减少把“事实还不完整”或“技术失败”错误升级为 `needs_human`。

系统需要区分三种情况：

1. 事实不足，但可以向事实提供者提出一个具体问题；
2. 事实已经足够，但高风险判断缺乏足够确定性；
3. 事实已经足够，但现有 Skill/规则没有覆盖这类可复用决策。

## 已确认的判定规则

结果统一携带四个顶层字段：

```json
{
  "risk": "low | medium | high",
  "confidence": 0.0,
  "rule_coverage": 0.0,
  "information_completeness": 0.0
}
```

数值字段的范围都是 `0.0 <= value <= 1.0`：

- `risk`：判断错误的潜在后果等级；
- `confidence`：Agent 对当前判断正确性的把握度；
- `rule_coverage`：当前适用 Skill/规则对这一类任务的覆盖程度；
- `information_completeness`：完成当前判断所需事实的完整程度。

判定优先级固定为：

```text
if information_completeness < 0.5:
    ask_back
else if (risk == high and confidence < 0.5) or rule_coverage < 0.5:
    needs_human
else:
    follow the applicable Skill and complete autonomously
```

### `ask_back`

`information_completeness < 0.5` 时优先生成普通澄清 proposal，不进入 `needs_human`。该 proposal：

- 只提出一个具体、可回答的问题；
- 发送给拥有缺失事实的参与者或来源；
- 说明当前可以做什么、缺少什么、不会做什么；
- 经过 Audit 审核后才发送；
- 回复到达后复用同一业务对象、同一 `reply_attempt` 和兼容的 Codex session，创建新 revision 继续执行。

如果澄清后信息仍不完整，继续生成下一条最小澄清 proposal；不能把普通材料缺失直接升级成 Derek 的管理决策。

### `needs_human`

信息已经足够时，以下任一条件成立即可进入 `needs_human`：

```text
(risk == high and confidence < 0.5) or rule_coverage < 0.5
```

结果还必须提供 2 至 4 个互斥且可执行的决策选项。选项必须描述可复用的规则选择，例如：

- 仅本次按某规则处理；
- 将该规则沉淀到适用 Skill 后继续；
- 暂停并要求补充一个明确的授权边界。

选项不能只是让 Derek 代做当前具体业务动作。用户反馈可以同时标记为一次性反馈和 Skill 更新；反馈保存后产生新 revision，在同一 session 中继续执行。

### `failed`

技术、依赖、provider、读取、路由、schema、Audit 执行和重试失败始终是 `failed`，不受 `confidence`、`rule_coverage` 或 `information_completeness` 的低值影响。

领域错误即使错误对象包含 `authorization_required=true` 也不能自动进入 `needs_human`；只有通用错误码 `authorization_required` 才表示不可替代的授权边界。

## 处理流程

```text
Consumer result
  -> validate risk/confidence/rule_coverage/information_completeness
  -> technical/provider/schema error? ---- yes -> failed
  -> information_completeness < 0.5? ----- yes -> ask_back proposal
  -> (high risk + low confidence)
       OR rule_coverage < 0.5? ------------ yes -> needs_human + options
  -> otherwise -----------------------------> Audit review and autonomous execution
```

`ask_back` 不是新的持久化终态。它沿用普通 proposal / Audit / provider 发送链路，发送成功后任务继续等待新输入；新输入通过同一业务对象映射为新 revision。`needs_human` 仍是可见的人工决策投影，直到用户选择并提交反馈。

## 契约与兼容迁移

- Consumer 和 Audit 的 wire schema、JSON Schema、提示词和结果持久化校验必须同步增加两个数值字段。
- 新结果四个字段都必填；缺字段、超范围或类型错误是 `runtime_result_validation_failed`，不是 `needs_human`。
- 现有历史结果中的 `confidence` 保持可读，不回写历史 run，也不把旧结果重新解释为当前人工待办。
- 当前投影只有在最新结果满足新判定规则时才显示为 `needs_human`；不满足门槛的旧投影按对应技术/领域错误修正为 `failed`，或重新进入 `ask_back` 流程。
- 所有领域任务共享这套字段和阈值，不为邮件、OKR、OA 或会议增加专属例外。

## 测试和验收

必须先添加失败测试，再实现最小代码。至少覆盖：

1. 高风险、低置信度、信息完整：进入 `needs_human`；
2. 高风险、高置信度、规则覆盖低、信息完整：进入 `needs_human`；
3. 低风险、低置信度、规则覆盖充分、信息完整：不进入 `needs_human`；
4. 信息不完整且规则覆盖低：优先生成 `ask_back`，不能直接 `needs_human`；
5. 澄清回复沿用同一业务对象、同一 attempt 和同一 session，产生新 revision；
6. 技术/provider/schema/Audit 失败即使字段低值也保持 `failed`；
7. 结果缺少任一必填字段或数值越界时拒绝为结构化结果；
8. `needs_human` 缺少 2–4 个互斥选项时拒绝；
9. 邮件领域授权拒绝、登录失败、目标不匹配和授权证据不足保持失败；
10. 当前 Attention/质量扫描只报告满足新门槛的当前投影，不报告历史误报。

验收还需要包含服务重启、健康检查、当前数据库投影读回，以及一条真实 `ask_back` 和一条真实 `needs_human` 的端到端证据。

## 非目标

- 不新增 `ask_back` 持久化状态；
- 不改变统一 Consumer → Audit → feedback → revision 生命周期；
- 不把所有低信息结果交给 Derek；
- 不允许技术失败通过填写低置信度字段绕过失败分类；
- 不改写原始 run、session、provider 结果或历史审计事实。
