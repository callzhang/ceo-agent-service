# 分层质量体系

本工程的质量闭环由本地 `ceo-quality`、GitHub Actions、可信 Mac live 评测、SQLite 迁移演练、运行时健康检查和不可变本地 release 共同组成。CI 不上传聊天正文、附件、Codex session、SQLite、人员标识、本地路径或认证材料。

## 环境与退出码

工程固定使用 Python 3.12，依赖以 `uv.lock` 为准：

```bash
uv sync --frozen --extra dev --extra reader-build
```

所有子命令支持 `--format text|json|junit`。退出码为：

| 退出码 | 含义 |
| --- | --- |
| `0` | 通过 |
| `1` | 质量检查未通过 |
| `2` | 配置或输入错误 |
| `3` | 基础设施不可用 |

## 本地门禁

```bash
uv run ceo-quality check --tier pr
uv run ceo-quality check --tier full --format json
uv run ceo-quality eval --suite protocol
uv run ceo-quality eval --suite safety
uv run ceo-quality eval --suite semantic
uv run ceo-quality db-check --mode check --db "$CEO_WORKER_DB"
uv run ceo-quality db-check --mode rehearse --db "$CEO_WORKER_DB" --backup-dir "/path/to/backups"
uv run ceo-quality snapshot --db "$CEO_WORKER_DB" --format json
```

`pr` 包含 Ruff、Pyright basic、非 live 全量测试、84% 全局覆盖率、关键模块逐文件 95% 覆盖率以及 protocol/safety golden 评测。`full` 额外运行 semantic golden、依赖漏洞和凭证扫描。CI 对 PR 再要求变更行覆盖率 90%。

关键模块包括泄漏检查、人员权限、目标/幂等执行计划、Codex 工具隔离、质量案例模型、评测器和迁移注册表。安全、协议和关键案例必须 100% 通过；semantic 总通过率至少 95%，关键案例仍必须 100%。

## Golden 与 live 评测

脱敏案例位于 `quality/cases/golden.jsonl`。`QualityCase` 会拒绝人员标识、消息正文、路径和凭证形态字段。人工反馈只有在人工检查、脱敏并以代码评审方式提交后才能进入该文件；运行服务不会自动训练、自动修改 prompt 或自动生成回归案例。

可信 Mac 的 `trusted-live-eval` workflow 仅接受精确 commit，并验证该 commit 属于本仓库远端分支。它先在 `origin/main` 运行基线，再运行目标 commit；新增失败会阻断。外部 fork、普通 PR 事件和未设置 `CEO_QUALITY_TRUSTED=1` 的环境不能调用本机认证模型。

可信 runner 还需在受保护 environment 中配置 `CEO_QUALITY_LIVE_EVAL_COMMAND`。该命令从 stdin 接收脱敏 JSONL，并向 stdout 返回每行 `{ "id": "...", "recorded_output": {...} }`，不得回传原始聊天或认证数据。

## 数据库安全

`schema_migrations` 记录有序 migration 的版本、名称和 checksum。迁移演练执行以下流程：

1. 原库 `quick_check` 和外键检查；
2. SQLite online backup；
3. 只在副本上执行 migration registry；
4. 再次执行完整性和外键检查；
5. 仅保留最近 7 个 `ceo-agent-*.sqlite3` 备份。

当前 registry 只包含新增质量表，属于 expand 迁移；旧内联迁移会逐步移入 registry。发布回滚只切换代码 release，不自动还原数据库。只有服务停止并明确确认后才允许人工恢复备份。

## 运行时健康与事件

- `GET /health/live`：进程存活，始终独立于 readiness。
- `GET /health/ready`：组件、backlog 或 SLO 不满足时返回 `503`。
- `GET /api/quality`：只返回 commit、PID、schema version、组件心跳、聚合 backlog、队列年龄和 SLO，不返回正文、人员标识、路径或凭证。
- `GET /quality`：Audit UI 的质量页；即使 readiness 失败仍可访问。

核心 required components 默认为 network、DWS、producer、consumer、meeting producer/consumer、task maintenance 和 quality monitor。可通过 `CEO_QUALITY_REQUIRED_COMPONENTS` 覆盖，并为启用的 WeChat 或其他连接器增加对应组件。组件连续失败 3 次或超过 3 个 60 秒周期没有成功心跳即 not-ready。

默认任务终态 SLO 是 30 分钟，可用 `CEO_QUALITY_TASK_SLO_SECONDS` 调整。processing/unknown 上限按 `2 × CEO_QUALITY_PROCESSING_TIMEOUT_SECONDS + 5 分钟` 计算。failed 或 unknown 外部动作立即 fail。30 天反馈样本达到 20 条后，负反馈率大于 5% 或人工纠正率大于 10% 会建立去重质量事件；事件可通过质量 API 确认负责人/期限并解决。

## 本机发布与回滚

launchd 使用稳定链接 `~/.local/share/ceo-agent-service/current`，release 位于其 `releases/<commit>` 下；数据库、`.env`、DWS keychain 和 corpus 位于 `~/Library/Application Support/CEO Agent Service`，不随 release 切换。

发布 sandbox 必须至少启用一个 connector，显式设置 `live_enabled: true`，并同时提供测试对象、allowlist 和 canary 命令。例如：

```json
{
  "name": "trusted-local",
  "live_enabled": true,
  "connectors": {
    "dingtalk": {
      "enabled": true,
      "test_target_ref": "synthetic-test-target",
      "allowlist": ["synthetic-test-target"],
      "canary_command": ["/approved/path/canary", "--target", "{target_ref}"]
    }
  }
}
```

将文件保存到本机受控路径，且不要提交真实测试对象：

```bash
uv run ceo-quality release-check \
  --db "$CEO_WORKER_DB" \
  --restart \
  --sandbox-profile "/private/path/trusted-local.json"
```

命令依次执行 full gate、数据库备份/副本迁移、精确 commit 构建、原子切换、`launchctl kickstart -k`、新 PID/commit/schema/readiness/backlog 验证和 connector canary。任何一步失败都会切回上一 release 并再次 kickstart；仅保留最近 3 个 release。

## 仓库设置

代码已经提供只读权限且固定 Action SHA 的 Linux/macOS workflow。仓库管理员仍需在 GitHub branch protection 中把 `linux-quality` 和 `macos-quality` 设为 `main` 必需状态；这是仓库服务端设置，不能仅靠 workflow 文件强制完成。
