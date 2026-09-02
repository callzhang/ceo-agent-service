from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_readme_describes_user_owned_codex_environment() -> None:
    readme = _read("README.md")

    assert "关闭桌面插件、浏览器能力、会话记忆和无关 MCP" not in readme
    assert "直接来自用户的 Codex 安装" in readme
    assert "CEO_REPOSITORY_UPGRADE_DISABLED" in readme


def test_current_docs_describe_meeting_fallback_boundary() -> None:
    architecture = _read("docs/architecture.md")
    reliability = _read("docs/reply-worker-reliability.md")

    assert "仅在明确证明该会话不可发送" in architecture
    assert "会话元数据缺失、" in architecture
    assert "只在所选群已被权威会话信息证明不可发送时" in reliability
    assert "元数据缺失或不一致时不猜测收件人" in reliability


def test_current_docs_describe_upgrade_backup_and_mcp_boundary() -> None:
    architecture = _read("docs/architecture.md")
    inventory = _read("docs/dws-command-inventory.md")

    assert "MCP 配置不由该流程探测、禁用或覆盖" in architecture
    assert "只保留一个最新快照" in architecture
    assert "not a runtime command allowlist" in inventory
    assert "## CEO Service Command Reference" in inventory
    assert "Current practical allowlist" not in inventory


def test_current_email_docs_describe_unsubscribe_consumer_direct_boundary() -> None:
    architecture = _read("docs/architecture.md")
    runtime = _read("docs/runtime-mechanism.md")
    classifier = _read("docs/superpowers/specs/2026-08-29-email-classifier-design.md")

    for document in (architecture, runtime, classifier):
        assert "`auto_reply`" in document
        assert "`unsubscribe` 使用 Consumer-direct" in document

    assert "全局禁用" in architecture
    assert "全局禁用" in runtime
    assert "`auto_reply` 当前禁用" in classifier

    assert "外部写仍只能由 Audit" not in architecture
    assert "之后仍由标准执行 Agent 产生候选、Audit Agent 审核和执行" not in runtime
    assert "`auto_reply`、`unsubscribe` 创建 Agent/Audit 工作" not in classifier
    assert "只有 `auto_reply`、`unsubscribe` 进入 Agent/Audit 生命周期" not in classifier


def test_unsubscribe_spec_reports_reviewed_branch_implementation_state() -> None:
    unsubscribe_spec = _read(
        "docs/superpowers/specs/2026-08-30-email-unsubscribe-branch-integration-design.md"
    )

    assert "written review complete" in unsubscribe_spec
    assert "`codex/email-integration-main`" in unsubscribe_spec
    assert "The Email worker selects the Consumer-direct lifecycle" in unsubscribe_spec
    assert "still expects Audit-accepted operations" not in unsubscribe_spec
