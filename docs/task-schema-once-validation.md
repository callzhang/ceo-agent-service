# Task schema 单次提交验证

范围：移除 `build_task_agent_prompt` 末尾重复的完整结果 schema。TaskAgentCodexRunner 原有 schema 文件、原生开关、本地 Pydantic 业务校验、Skill 正文、来源事实、路由与会话机制保持不变。Codex 通过原生通道提交；Claude/Friday 使用现有桥接函数在单条消息中附加一次。

## 验证

新增三项跨路线回归在修改前失败：Claude/Friday 各含两份相同 schema，Codex Task 正文重复附加 schema。修改后通过，实际 command factory 指向的文件与完整 `task_agent_output_schema()` 相等。原有字段规则断言改为检查提交的 schema 文件；字段描述与规则仍保留。

`pytest -q tests/test_task_prompt_schema_delivery.py tests/test_task_agent.py`：242 passed。相关三文件 Ruff 与 `git diff --check` 通过。未运行全量测试。

固定匿名输入（ai_minutes、synthetic-source、空候选对象、2026-10-12T10:00:00Z；本地安装的 Skill）在同一进程分别调用基线与候选函数：74,039 → 45,278 字符，移除 28,761 字符（38.85%）。基线只移除重复 schema 标题、完整 JSON 与结尾换行后，剩余输入与候选逐字符相等；SHA256 为 `1463ec01362d3d2a5c6ef2d2b80fe54edc23046054a7641696cdfc2e11cb9cfe`。早期 74,057 字符样本的来源元数据略有不同，不能混作同一固定输入。

独立只读审查未发现阻断项，核对了 factory、Codex adapter 与 Claude/Friday 桥接的实际源码路径；审查者未运行模型。

这是装配与完整契约保留的确定性验证，未新增真实模型业务验收，未合并或部署。剩余 Task 输入仍包含完整 Skill 和业务指令；按需读取 Skill 的改动属于后续独立工作。

## 下一阶段：Skill 按需读取的已确认边界

当前 TaskAgentCodexRunner 使用 `CodexCommandFactory.standard`，不会构建 Consumer/Audit 的角色绑定 `agent_cli`。仓库 `config/service-mcp.json` 仅声明 exa、memory_connector、xiaoqing_interview，因此不能直接把 Consumer 的 `agent_cli.read_skill` 提示搬入 Task。

`runtime_route_surface_capabilities` 声明 Codex/Claude 在本机具有 `local_workspace_access`；Friday 无该能力。Task 当前 required capabilities 只有 structured_output/local_schema_validation，不能假设所有可选路线都能读取本机 Skill。FridayExecutionInput 的既有公共合同也没有 Skill/文件资源字段。

后续应先核实当前配置与各路线的实际 Skill 读取入口，再设计按运行环境提交的最小指令；不得直接删除完整正文并指向不可达本机路径，不新增或变更路由来掩盖读取能力缺失。这里没有修改 Skill 提交方式、权限或路由。
