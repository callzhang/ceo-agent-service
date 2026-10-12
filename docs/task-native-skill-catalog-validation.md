# Task 原生 Skill 目录验证

2026-10-12 同步父分支 `678df76e`（包含当前 main 的 typed storage 变更）后，目录、schema、指令去重和既有 runtime repair 五个受影响测试文件合计 118 passed；未执行本地全量测试。三个文档末尾追加冲突均保留两侧内容，既有并行材料读取说明也随正常三方合并保留。本条仅是合并后回归，不替代原生对照或生产验收。

## 原因与改动

Task command factory 未传 native skill_names，最终适配器使用全量原生目录。另一个既有 helper 从 Skill 正文按名称/命令推断递归依赖，连“交由其他角色处理”的说明也被当成依赖。只选 ceo-work-tracking 会扩到48个业务名称，配置中启用52条。

删除这层正文推断；目录只使用明确选择与既有默认 Memory 插件。Task 通过现有 adapter 参数传当前 ceo-work-tracking。完整 Skill 正文、动态事实、结果契约、业务校验、读路径、工具、权限、路线和session不改；未声明选择的其他调用保持原样。Consumer/Audit 使用空选择时配置逐字符相等，SHA256为 `37307c77bb7b0e81e46f8ec8e5134ef4201905df0158db5d966c01749c33b65e`。

## 回归和实际目录

两项回归修改前分别因自动启用正文引用和 Task 未向最终 adapter 传 skill_names 失败，修改后通过。已删除函数的四项直接测试同步移除，没有删除业务应用测试。151项目录/路由/适配器相关测试、240项Task相关测试通过；Ruff/diff检查通过。只读独立代码审查未发现权限、路由或业务正文隐藏变化。

当前配置条目启用数52→5；Codex官方 `debug prompt-input` 实际目录条目169→6，文本字符33474→16881。多出的一个是CLI自带plugin-creator，保留CLI原有处理，不将配置数冒充模型可见数。该debug测量不含业务Task正文、结果schema或整个会话历史，不能当成服务提交prompt长度。初次sandbox debug受文件权限拒绝，提权只读运行后通过，没有修改生产设置。

## 原生业务对照

模型gpt-5.6-luna/medium、完整native Task schema。两版每阶段业务Task正文及实际服务检索/渲染输入完全相同；差异为目录。冷启动显式新项目、职责和正常进展，两版完整本地应用均保存1 Project/0 Task。续接同session后提交相同回款风险：验收材料确认是付款日期安排的上游门槛，保存职责支持赵华负责回款排期。两版均复用Project、生成1个display-only候选下一步及1份Attention提案，本地完整业务应用通过；未拆成材料确认和付款日期两个重复候选。

基线Task建议“确认客户验收材料并落实回款日期”，候选“确认客户验收材料并落实客户回款日期”，均建议赵华，不声称实际接受/正式分派。首次工具不可用运行两版CEO类别不同：基线push，候选watch，不据单个样本宣称判断全面优于基线。

基线cold记录原生Skill descriptions were shortened警告，候选cold/risk没有；基线risk也没有。首次工具不可用运行的完整事件检查未见Memory工具调用，测试只声明本地假Memory；不把正确JSON、退出0或本地应用成功当成Memory工具使用验收。测试MCP不连接真实服务，未写外部业务或真实记忆。首次脚本缺少真实服务清单环境变量，模型尚未启动；改为测试空清单后执行，未引入生产fallback。

## 证据与边界

摘要：`evals/trajectory_optimization/task_native_skill_catalog.results.v1.json`。原始匿名输入、全部原生事件、决定及本地应用数据库暂存 `/private/tmp/ceo-task-native-catalog-_47_cl8j`。该对照未覆盖其他提供商、所有任务或生产回读；本变更尚未合并部署。Memory测试工具可用性已由下述单独只读探针确认，不扩成新prompt规则。

测试传输诊断已定位：最初导入未安装的 standalone fastmcp；改为项目已安装的 mcp.server.fastmcp 后，还需使用该SDK的 `@server.tool()` 注册形式。本地注册清单确认 user_get/memory_recall/memory_write 后，原生只读探针成功调用 memory_fixture.user_get，完成事件无错误。仅从源码读到 Synthetic Principal 的先前探针被明确判失败，不用输出内容代替工具回执。现正在使用正确工具注册重新运行业务对照，旧记录继续保留为工具不可用边界。

### 正确注册夹具下的重跑

新的两版冷启动和同session风险续接均结束并再次通过完整本地业务应用：正常场景1 Project/0 Task；风险场景1 Project/1 display-only候选，建议赵华，1份Attention提案。当前两版均为watch；先前工具不可用样本的push/watch差异仍保留，不能借重跑抹掉采样差异。两版每阶段prompt SHA一致，角色、模型、事实相同。

实际原生只读user_get探针成功且有MCP完成回执，证明正确注册的测试工具可用。四次匿名业务运行仍未调用Memory write；这些合成材料不能作为真实durable-memory业务验收，也未通过额外prompt强迫写入。旧启动错误、导入/注册错误、从源码推测名字的失败探针均保留为无效工具验收证据。此项改动的明确证据是目录真实减少与固定业务判断/应用未回归，不是全任务最优或Memory行为验证完成。

## Existing CI fixture correction

GitHub run38231038884 failed5 tests after11806 passes. Locally reproduced5 failures/27 passes: four semantic-repair tests hit a FakeAdapter signature that lacked the real adapter's skill_names argument; one source assertion still required the removed inline schema. FakeAdapter now accepts the real parameter and RecordingAdapter explicitly checks the Task selection. No repair, same-receipt replay, exhaustion-count, damaged-trajectory or reset-isolation assertion changed. The obsolete schema assertion is corrected separately above. Four affected test files pass91 locally, Ruff and diff checks pass, and independent read-only review found no relaxed business oracle. No full suite ran alongside the live service or manual Actions rerun was requested.
