# Agent Runtime 页面现代化设计

## 目标

改善 Settings → Agent Runtime 的信息层级和凭据输入体验，不改变现有运行时配置字段、校验逻辑、`.env` 写入方式或 `/config/agent-runtime` POST 兼容接口。

## 页面结构

页面由 Runtime Overview 和三个配置卡片组成：

- Runtime Overview：显示当前路由、模型、thinking strength，以及 API fallback / Friday Runtime 的启用状态。
- Codex OAuth：模型和 thinking strength。
- Codex API fallback：启用开关、Base URL、Fallback model、API Token。
- Friday Runtime：启用开关、Runtime Base URL、Project ID、Provider 参数、Runtime ticket、Session token。

普通字段使用统一 label、helper text 和两列布局；卡片之间通过状态 badge 区分 active / disabled / configured。

## 凭据组件

所有敏感字段使用统一 password-field 结构：默认 `type=password`，右侧按钮切换 `password/text`，按钮提供 `aria-label`、`aria-pressed` 和键盘可操作语义。已保存凭据只显示 configured 状态和遮罩提示，不向 HTML 回填真实值；用户本次输入仍可显示或隐藏。

覆盖 Codex API Token、Friday Provider API Token、Friday Runtime ticket、Friday session token。

## 数据与兼容性

继续复用现有字段名和 `handle_agent_runtime_config_post()`。空凭据保留已有 secret；新凭据仍只通过 `write_env_values()` 写入 `.env`。不新增任意代码执行、文件读取或外部发送逻辑。

## 验证

- Agent Runtime 页面包含 overview、三个卡片和四个统一凭据控件。
- 已保存 secret 不出现在 HTML value 或明文文本中。
- 四个控件可以显示/隐藏本次输入。
- 既有 Agent Runtime 配置提交和校验测试继续通过。
- launchd 重启后真实 `/settings?tab=agent-runtime` 返回 200。
