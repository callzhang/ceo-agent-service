# 生产trajectory：Consumer是否实际读取完整schema

只读截止2026-10-25 22:26 PDT（2026-10-26 05:26:34 UTC），窗口从当日02:00 PDT开始。仅生产Consumer、codex_oauth、gpt-6-luna；检查准确runtime attempt原生范围及工具返回，不包含匿名评测，不运行新测试或模型。

## 结论

它是可选的按需查询入口，不是“只有原生输出失败才启用”的受控fallback。当前prompt主动提示其存在；生产中使用频繁，不能描述成几乎不用的备份。原生schema始终作为输出通道的一部分提交，与该文本查询是两条独立路径。

| 已完成运行 | 样本数 | 实际尝试查询 | 明确返回完整schema文本 |
| --- | ---: | ---: | ---: |
| fresh冷启动 | 24 | 23（95.8%） | 至少22（91.7%） |
| resume续跑 | 44 | 11（25.0%） | 至少11（25.0%） |
| 合计 | 68 | 34 | 至少33 |

六项failed attempt单列未混入分母；运行中的attempt不计。68项均有可读原生范围。查询尝试包括具名直接调用及exec里实际调用表达式，不把prompt或工具发现列表中的名字算调用。完整读取只计实际返回一个完整JSON schema的文本块；exec表达式失败不计成功，未能确认完整返回的wrapper只计尝试。

## 具体读取及重复例子

- 冷启动Run399471 / attempt399183，session `01a17664-6640-7d10-be0a-509fe484b303`：第64行实际调用`read_consumer_output_schema({"purpose":"general"})`，第67行返回完整11,656字符schema。同一轮第42行的exec包装调用曾失败，不能算一次成功读取。
- session `01a1744e-b8b2-7453-bc22-311fd4083f9a`：Run399408冷启动第35行读取；Run399418续跑第154行再次读取；Run399420续跑第200行又读取。三份返回均11,656字符，摘要相同。
- session `01a175f2-b11c-7161-a415-136a8e70605c`：Run399431冷启动第72行读取；Run399499、399501续跑分别第139、173行再次读取。三份完整返回相同。
- session `01a17618-a48d-78f1-b42c-6285b398b9bb`：Run399438、399446、399452分别第51、103、151行读取，同样一冷两续重复。

33份直接完整返回均为general schema，单份SHA `d465045225ac0eb43cc50c9cc59a56cc3d9818ca4c369d6f4510276af17d01a4`。这些读取进入模型上下文；不能据字符数直接换算净token/费用或一次调用的额外推理耗时。

## 证据边界

至少6个已完成session发生多次确认读取，说明session复用不保证schema文本不重读。该频率不证明读取必要，也不证明没有读取就会失败；没有做因果对照，不发起新评测。这里只核对调用事实，不修改prompt、工具或模型配置。

原生范围、逐次调用/返回行号、run/attempt/session、返回摘要及失败wrapper分类见[版本化证据](../evals/trajectory_optimization/consumer_schema_read_production.results.v1.json)。

## 用户后续决定：删除重复查询工具

Derek要求确认原生schema注入后删除该工具。已核对Consumer runner为Codex选择实际schema，CodexRunner冷启动与resume均传入`--output-schema`，与session是否保有旧说明无关。本次删除agent_cli查询函数和注册、Consumer工具目录名称及prompt中的查询提示，保留原生schema、短格式说明、指纹和完整业务校验；不增加替代工具、不修改schema或续跑策略。历史调用统计保留，不能改写为工具删除后的行为。

回归先确认原生general/OA冷启动与resume四种命令均含准确schema；删除前另四项工具/提示缺席断言如预期失败。删除后的测试、审查和正式生产加载结果待追加。无新增模型评测或任务重放。

本次本地验证：原生general/OA×cold/resume四项交付检查先通过；另四项工具/提示缺席检查先失败后通过。三份受影响测试文件49项通过，适配器参数和指纹兼容定向9项通过；Ruff及diff检查通过。上述均为本地确定性测试，无模型调用、无新增生产业务操作。实际代码删除12行查询函数/注册相关内容和目录条目；prompt保留短格式与schema SHA，未替换为完整schema文本。正式发布及进程加载结果待追加。
