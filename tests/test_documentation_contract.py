from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _markdown_section(document: str, heading: str) -> str:
    lines = document.splitlines()
    assert heading in lines, f"missing Markdown heading: {heading}"

    heading_prefix = heading.split(" ", 1)[0]
    assert heading_prefix and set(heading_prefix) == {"#"}
    heading_level = len(heading_prefix)
    start = lines.index(heading)
    end = len(lines)
    active_fence: str | None = None

    for index in range(start + 1, len(lines)):
        candidate = lines[index].lstrip()

        if active_fence is not None:
            fence_character = active_fence[0]
            fence_length = len(candidate) - len(candidate.lstrip(fence_character))
            if fence_length >= len(active_fence) and not candidate[fence_length:].strip():
                active_fence = None
            continue

        fence_character = candidate[:1]
        if fence_character in {"`", "~"}:
            fence_length = len(candidate) - len(candidate.lstrip(fence_character))
            if fence_length >= 3:
                active_fence = fence_character * fence_length
                continue

        if " " not in candidate:
            continue
        prefix = candidate.split(" ", 1)[0]
        if prefix and set(prefix) == {"#"} and len(prefix) <= heading_level:
            end = index
            break

    return "\n".join(lines[start:end])


def _assert_attachment_content_is_inaccessible(section: str) -> None:
    semantic_section = "".join(section.split())
    assert "任何组件都不得下载、打开、OCR、解析、总结或推断附件正文" in semantic_section


def test_markdown_section_keeps_nested_headings_and_stops_at_same_level() -> None:
    document = """### Email
before
#### Nested
nested content
### Next
outside
"""

    section = _markdown_section(document, "### Email")

    assert "#### Nested" in section
    assert "nested content" in section
    assert "### Next" not in section
    assert "outside" not in section


def test_markdown_section_stops_at_higher_level_heading() -> None:
    document = """### Email
inside
## Higher
outside
"""

    section = _markdown_section(document, "### Email")

    assert "inside" in section
    assert "## Higher" not in section
    assert "outside" not in section


@pytest.mark.parametrize("fence", ("```", "~~~"))
def test_markdown_section_ignores_heading_lines_inside_fences(fence: str) -> None:
    document = f"""### Email
before
{fence}text
### Fenced example heading
{fence}
after
### Next
outside
"""

    section = _markdown_section(document, "### Email")

    assert "### Fenced example heading" in section
    assert "after" in section
    assert "### Next" not in section
    assert "outside" not in section


def test_attachment_policy_rejects_reversed_permission_polarity() -> None:
    reversed_policy = (
        "附件是 metadata-only，没有 image/content material；"
        "任何组件都可以下载、打开、OCR、解析、总结或推断附件正文。"
    )

    with pytest.raises(AssertionError):
        _assert_attachment_content_is_inaccessible(reversed_policy)


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


def test_current_email_docs_describe_audited_v2_boundary() -> None:
    architecture = _read("docs/architecture.md")
    runtime = _read("docs/runtime-mechanism.md")
    classifier = _read("docs/superpowers/specs/2026-08-29-email-classifier-design.md")

    email_sections = (
        _markdown_section(architecture, "### Email Agent task 映射"),
        _markdown_section(runtime, "### Email task 的运行边界"),
    )

    for section in email_sections:
        semantic_section = "".join(section.split())

        assert "以下 Email audited-v2 内容是已批准的生命周期政策" in section
        assert "后续实现提交必须满足" in section
        assert "当前分支尚未将运行时路由" in section
        assert "`email_unsubscribe_consumer_direct_v1`" in section
        assert "切换到 `email_unsubscribe_audited_v2`" in section
        assert "Consumer-direct v1" in section
        assert "不是目标政策" in section
        assert "尚未部署" in section
        assert "未在生产启用" in section

        assert "`label`、`mark_read`、`archive`、`move`、`trash`" in section
        assert "不创建 CEO Agent task，也不创建 Consumer/Audit run" in section
        assert "`auto_reply`、SMTP 和 `mailto` 发送全部禁用" in section

        assert "`email_unsubscribe_audited_v2`" in section
        assert "Consumer A 是只读" in section
        assert "每个revision只提出一个" in semantic_section
        assert "task" in section
        assert "ActionPlan" in section
        assert "operation" in section
        assert "Audit Agent B 是唯一" in section
        assert "task-bound" in section
        assert "unsubscribe 写能力" in section
        assert "不重放已接受的operationprefix" in semantic_section
        assert "`awaiting_audit`" in section
        assert "领域状态" in section
        assert "不是顶层 task 状态" in section

        assert "附件" in section
        assert "metadata-only" in section
        assert "没有image/contentmaterial" in semantic_section
        _assert_attachment_content_is_inaccessible(section)
        assert "附件正文" in section or "附件内容" in section

        assert "`email_unsubscribe_consumer_direct_v1` 是目标生命周期" not in section

    assert "当前分支的配置和 runtime 禁用 `auto_reply`" in classifier
    assert "Email 尚未部署，也未在生产启用" in classifier
    assert "当前 Email 部署禁用" not in classifier


def test_email_design_and_historical_docs_point_to_approved_audited_v2() -> None:
    classifier = _read("docs/superpowers/specs/2026-08-29-email-classifier-design.md")
    unsubscribe_spec = _read(
        "docs/superpowers/specs/2026-08-30-email-unsubscribe-branch-integration-design.md"
    )
    progress = _read(
        "docs/superpowers/specs/2026-08-31-email-integration-main-progress.md"
    )
    approved_design = _read(
        "docs/superpowers/specs/2026-09-02-email-ceo-agent-audited-fusion-design.md"
    )

    status_lines = [line for line in approved_design.splitlines() if line.startswith("状态：")]
    assert status_lines == ["状态：书面规范已批准，已进入实施计划"]
    assert "`email_unsubscribe_audited_v2`" in approved_design
    assert "move-to-Trash" in approved_design

    approved_design_path = "2026-09-02-email-ceo-agent-audited-fusion-design.md"
    assert approved_design_path in classifier
    assert approved_design_path in unsubscribe_spec
    assert approved_design_path in progress
    assert "历史实现记录" in unsubscribe_spec
    assert "不是新任务的可执行目标" in unsubscribe_spec


def test_unsubscribe_spec_reports_reviewed_branch_implementation_state() -> None:
    unsubscribe_spec = _read(
        "docs/superpowers/specs/2026-08-30-email-unsubscribe-branch-integration-design.md"
    )

    assert "written review complete" in unsubscribe_spec
    assert "`codex/email-integration-main`" in unsubscribe_spec
    assert "The Email worker selects the Consumer-direct lifecycle" in unsubscribe_spec
    assert "## 2. Implementation Truth as of 2026-08-30" in unsubscribe_spec
    assert "the then-defined production" in unsubscribe_spec
    assert "target was Consumer-direct" in unsubscribe_spec
    assert "## 2. Current Truth" not in unsubscribe_spec
    assert "the production path defined" not in unsubscribe_spec
    assert "here is Consumer-direct" not in unsubscribe_spec
    assert "still expects Audit-accepted operations" not in unsubscribe_spec
