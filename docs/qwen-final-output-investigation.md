# Qwen 最终输出定位（2026-10-24）

阶段性结论（最终修复见后续节）：原最小探针卡在“已获得来源后，选择再次读工具还是输出原生结果”，不是来源变化、截断或服务重试。它只有最小 Audit 提示，没有模型可见的完整字段说明；原生 schema 主要约束可生成的结果，不保证模型知道如何填它。当前完整 Audit Developer 的冷启动和同 session 续跑均已得到严格有效结果。因此不能从原探针推导“生产 Audit 指令需要修补”，本次没有改 prompt、provider、路线、权限或重试。

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


## 最终根因与组合修复

仅让格式可见曾取得一次冷/续成功，但另一次正式装配结束于reasoning-only、没有最终消息；仅修grammar的私有canary仍持续读取。两者都不是可发布的单独修复，原失败记录保留。

确定性根因之一在provider：旧表达式为 `Or(Sequence(reasoning,required_tool),final_json)`。实际xgrammar matcher拒绝 `reasoning</think>\n\n{"ok":true}`，却接受同一前缀后的工具调用。修复使用原生reasoning前缀，组合为 `Sequence(reasoning,Or(required_tool,final_json))`，错误字段/类型及未声明工具仍拒绝。代码属于[llm-gateway修复fce6e9f](https://github.com/callzhang/llm-gateway/commit/fce6e9f)，不在CEO中按Qwen分支。

另一端是服务输入只保留schema哈希，却继续描述应用层扁平wire字段，模型不知道实际decoder的decision包装。修复从同一native schema文件提供紧凑JSON，哈希及严格schema不变，不恢复重复的Pydantic业务schema。general增加11,656字符的schema正文及小标题说明，必需格式不作推测性截断；不报告性能节省。保留可解析的指纹标题和后续Markdown分段，不改变session选择。

两项组合在独立GPU0/环回9020 canary验证，生产共享解析器当时仍为旧SHA；客户端只用标准SSH端口转发读取私有后端，同一模型/原邮件事实/假只读MCP，不永久修改路由。session `01a171e0-a846-7762-bbf9-6c6c0ba5730a` 冷启动57行SHA `81482acf3fdf946a33fb4257b9127f41eb3a628d77951c954a641dce5b73cb03`；续跑累计79行SHA `b8d38173618759f04e325e0f61a749af58cbb5b520947b6e2d83b6b13840e761`。两轮均读取当前假消息、完整解析no_action，没有外发动作，分别5次与1次成功只读调用。Developer SHA为 `88726efa0dda2d8e6d2031e09b9a2a968702eb80d6b80166cb27d453c2d603ff`。完整结果见[验收文件](../evals/trajectory_optimization/qwen_consumer_native_output_fix.results.v1.json)。

测试：两个purpose的新增契约可见性回归先失败；指纹段落解析回归发现并纠正候选格式问题。native Consumer/保存会话/装配47项通过，实际Consumer续会话路径1项通过；独立审查另核对其中19项（重叠，不相加）。provider三项CPU测试先红后绿，涵盖正向工具/JSON和严格负例。旧Settings native-preview测试12例仍在fixture建库时因不存在record_type列失败，未到达本改动、文件未改；未顺手修数据库测试。没有重跑其他业务模型案例。

发布：provider已备份并安装候选源码SHA `9bcfe24914a52cb4f4cd2fc45f47cdc0f62f1c4695172b0815f953a4b2d2be5f`，旧文件SHA `fe609579...` 保存在 `/home/derek/.local/state/llm-gateway/qwen-native-output-20261025/`；当前已载入的旧进程沿自然空闲卸载后才更新，不能由文件安装宣称已加载。CEO提示提交/正式部署及最终生产回读另行追加。API429仍阻塞，API Audit原生启用和G1自然失败样本不属于本次变更。
