# Email 分类 Agent 专属 Luna 模型设计

## 目标

冷启动阶段，Email Worker 自动领取服务观察到的未读邮件，并由 Email 分类 Agent 生成类别、
`important` 和可选的 `unsubscribe_url`。该分类 Agent 使用 `gpt-5.6-luna`，不改变 OA、会议、
任务、Consumer、Audit 或其他共享 Agent 的模型。

分类确认仍然不是 Email task，也不进入 Consumer/Audit 生命周期。只有既有规则允许的退订等
Agent 写动作才创建 `channel=email` task；SMTP 和邮件回复继续禁用。

## 配置与运行边界

新增可选环境变量：

```text
CEO_EMAIL_CLASSIFIER_MODEL=gpt-5.6-luna
```

- 未配置时，Email 分类继续使用共享 Codex OAuth route 的模型，保持现有行为。
- 配置时，只覆盖 Email 分类所构建的 `codex_oauth` route；不修改 `codex_api`、
  `friday_runtime` 或其他 failover route。
- 配置值必须属于服务已支持的 Codex runtime 模型集合；非法值使 Email Worker 启动失败并记录
  明确配置错误，不静默回退。
- 模型覆盖发生在 Email 分类专用 routed execution 的构建边界，不修改共享 runtime router 的
  全局配置。
- Email 描述优化 Agent 不在本次范围内，继续使用现有共享模型。

## 数据流与追溯

```text
未读邮件
  -> Email 分类任务
  -> Email 专属 routed execution
  -> codex_oauth / gpt-5.6-luna
  -> 分类结果持久化
  -> 既有确定性移动、标星或 Trash 动作
```

每个新的 `email_classification` runtime attempt 必须把实际 route model 保存为
`gpt-5.6-luna`。已经完成的分类及其不可变 attempt 不重跑；重启前已处于 `pending` 且尚未创建
runtime attempt 的分类，从新进程开始使用 Luna。正常 runtime failover 规则保持不变，若首选
Codex OAuth route 不可用，可使用已配置且健康的后续 route；attempt 分别记录其实际模型。

## 验证

1. 测试 Email 模型覆盖只替换 `codex_oauth` route，其他 route 保持原模型。
2. 测试未配置时保持共享模型，非法模型被拒绝。
3. 测试 Email Worker 将专属模型配置传给分类 backend，不传给描述优化或其他 Agent。
4. 运行完整 Email 测试。
5. 配置 `CEO_EMAIL_CLASSIFIER_MODEL=gpt-5.6-luna` 后重启 launchd 服务。
6. 等待一个新的 Email 分类 attempt，回读数据库确认其 `workload_kind=email_classification`、
   `route_name=codex_oauth`、`model=gpt-5.6-luna`，并确认没有新增失败或卡住的 Email 动作。

## 非目标

- 不改变共享 `CEO_CODEX_MODEL`。
- 不改变分类类别、类别描述、训练数据、Shadow Model 或晋升门槛。
- 不增加新的 Audit、安全或授权规则。
- 不重跑已经完成的邮件分类。
- 不启用 SMTP、自动回复或其他发送能力。
