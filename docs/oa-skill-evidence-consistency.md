# OA 审批读取一致性

钉钉 OA 审批必须按 `dingtalk-misc` 的 `references/oa.md` 执行。服务自有的
`app.cli read-oa-approval-detail` 负责提供审批实例状态、表单、任务和操作记录的
规范化读取；DWS 的 detail、tasks、records 读取只作为补充证据，不能在两者冲突时
直接驱动同意、拒绝或退回。

如果同一审批的读取结果不一致，系统不得把这个技术/数据一致性问题转成让 Derek
选择“同意”或“拒绝”。本轮不执行写操作，保存 `oa_live_evidence_conflict`，沿用
回复任务的 exponential backoff 自动重新读取；达到重试上限后才标记为系统失败。

History 对该状态显示为“OA 读取结果不一致，系统自动重新读取”，而不是暴露原始
模型审计术语或生成业务决策选项。
