# Qwen 最终输出定位（2026-10-24）

结论：原最小探针卡在“已获得来源后，选择再次读工具还是输出原生结果”，不是来源变化、截断或服务重试。它只有最小 Audit 提示，没有模型可见的完整字段说明；原生 schema 主要约束可生成的结果，不保证模型知道如何填它。当前完整 Audit Developer 的冷启动和同 session 续跑均已得到严格有效结果。因此不能从原探针推导“生产 Audit 指令需要修补”，本次没有改 prompt、provider、路线、权限或重试。

这不是全部 Qwen 验收：完整指令续跑仍读了19次，效率未收口；生产 service API Audit 当前未选原生 schema，Consumer 当前完整矩阵也没有由这次 Audit 测试代替。

## 完整失败轨迹

原生 session `01a16cf2-854b-7e22-82f3-83f6eac0fcaa`，CLI JSONL 位于 `~/.codex/sessions/2026/10/24/rollout-2026-10-24T01-06-35-01a16cf2-854b-7e22-82f3-83f6eac0fcaa.jsonl`。已遍历全部660行，按去掉末尾换行的行拼接 SHA-256 为 `df6add3e9b9c52b574e1dae32dfedecad638e0f798c9d740b5f04b40e370443f`。

- 93次 `read_dingtalk_messages` 的参数均为同一匿名会话 `synthetic:S18`；93个 call ID 都有匹配返回，没有缺返回或嵌套重试。
- 去除运行耗时包装后，93次业务返回完全相同：`source_ref=synthetic:S18:cold`、`state=already_handled`、`reminder_already_sent=true`、`phase=cold`。原始返回的计时不同不算业务事实变化。
- 第18行推理已判断无需动作、可批准，但表示准确结果字段未知；后续多次要求自己停止读取并返回，实际仍发出相同读取。100秒预算结束前没有 assistant 最终消息或 task_complete。
- 该探针只有最小角色提示及完整 output schema，**不是完整生产 Audit Developer**。工具返回已经充分；失败发生在结果生成/工具选择层。

## Provider 只读检查与最小对照

GPU4 vLLM 0.29 的 `qwen3_engine_tool_parser.py::get_structural_tag`，在 Responses + tools + auto + JSON schema 时，把工具 grammar 与最终 JSON grammar 组合为 Or；没有要求必须一直调用工具。源码 SHA `fe609579548295ca9f443b966cc00a9772bb8fcce4e19ba2a7edfa4121ca58d4`。`abstract_parser.py::_apply_structural_tag` 将 schema 转成约束后清除请求的 text 格式设置，SHA `cc48579b52a4eaf6cd7d4161ff7e90e20ec4d77492fefa4eb469e47336919a4e`。这支持“解码约束存在，但不是可见字段说明”的判断；不能据此声称模型内部的唯一原因已经证明。

沿现有 Qwen 配置，仅用匿名已读来源、相同最小提示和当前完整 schema，进行三次无状态 Responses 对照。无真实业务、无写工具，工具调用不执行；没有改共享 GPU 服务。

| 条件 | 实测结果 | 响应摘要 SHA-256 |
| --- | --- | --- |
| auto + schema | 1.75秒，reasoning后再次调用读取，无最终文本 | `485e658b2a0f93a8a381bb81ec25dfe6d7a7081699db81d20372d2912124b3b3` |
| none + schema | 2.34秒，392字符最终结果，完整 Audit 解析 approve | `a43bbf623cc6057b4b4c6ff6813a807cc8d636dd1508c3bc85ce53a7368f40a1` |
| auto + schema，同时在 Developer 中展示 schema | 1.76秒，422字符最终结果，完整 Audit 解析 approve | `639558b6b6bd2b3b3009bd229d00bd1e46490b4d071e1c5d2b75484820854632` |

最终 JSON 分支可用。可见契约解决了这次最小探针的收尾，但三次诊断不证明普遍质量，也不构成生产 force-none、重复注入 schema 或降级的理由。响应未保存为完整 HTTP 证据包，表中摘要及本次工具记录仅用于诊断佐证；最终原生有效证据见下节。

## 当前完整 Audit 装配的原生验证

使用真实角色命令构造、当前完整 Audit Developer（27,761字符，SHA `b503cba1c81adb643e2cadded00afc61d550b816079b53d7d6796c80d201779e`）、空 Work Profile、当前完整 Audit schema（`ca522ed193ce692fae5a06177240203e4a3c0cf803b93b7e977382eebacd6937`）。只连接匿名假消息 MCP；额外关闭 shell、unified exec 和业务网络操作，不接生产业务 MCP。复用既有路线配置，没有生产代码分支或配置变更。

session `01a16f62-9cfd-7951-9809-d66c12895639`：

| 阶段 | 完整相关轨迹 | 判断 |
| --- | --- | --- |
| 冷启动 | 58行，SHA `846b2cd18a9593cdf422302d926f7fdc7f6a8df0f177313b2cfa6b7c7ca61309`；6次来源读取 | 产生最终消息并 task_complete；完整原生/Pydantic解析 approve，引用当前 cold 来源 |
| 同 session 续跑 | 累计184行，SHA `4c4cd21a66390301a4c34fb9c4a1f7943051120adb19efa17f715c4ac2a6e9d9`；续轮19次读取 | 最终 approve，引用当前 resume 来源，完整解析通过 |

续轮候选保留旧 cold 引用，而当前工具返回 resume 引用；推理在核对二者。它最终用当前事实确认相同已处理状态，但19次读取仍冗余，不能全部归因为 schema 不可见。保留该效率问题；本轮不新增读取上限、自动停止或场景规则。准确结论是：**原最小探针的无限读取不能作为完整生产指令的失败证据；当前完整 Audit 原生路径能结束，效率及生产启用矩阵仍有边界。**
