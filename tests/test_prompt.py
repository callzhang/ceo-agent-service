import json
from pathlib import Path

import pytest

from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.config import env_file_path
from app.config import profile_evidence_dir
from app.config import repo_root
from app.config import work_profile_path
from app.developer_prompt import (
    CONFIGURABLE_PROMPT_VARIABLE_DEFAULTS,
    DeveloperPromptTemplateError,
    SEED_DEVELOPER_PROMPT_TEMPLATE,
    configurable_prompt_variable_pairs,
    developer_prompt_template_path,
    prompt_template_variables,
    read_developer_prompt_template,
    read_user_prompt_template,
    render_developer_prompt_template,
    user_prompt_template_path,
    write_configurable_prompt_variables,
)
from app.prompt import (
    LinkedDocumentContext,
    MaterialReferenceContext,
    build_turn_prompt,
    ceo_agent_thread_prompt,
    message_lines,
    sanitize_dingtalk_prompt_text,
    work_profile_instruction,
)
from app.user_prompt_blocks import USER_PROMPT_BLOCKS


CARD_CONTENT = """@Alex Chen(明哥) 明哥，董事会报告根据昨天的会议进行了修改，您是否已完成审核？是否可以定稿了？
  引用: 26年董事会报告
![image](https://gw.alicdn.com/imgextra/i4/O1CN019r2O9o1mRbjrcNMe5_!!6000000004951-2-tps-96-54.png)
![image](https://gw.alicdn.com/imgextra/i4/O1CN01DXenu91IyBR0wQXk9_!!6000000000961-2-tps-148-72.png)
![image](https://gw.alicdn.com/imgextra/i4/O1CN01DXenu91IyBR0wQXk9_!!6000000000961-2-tps-148-72.png)
[https://alidocs.dingtalk.com/i/nodes/vy20BglGWOKXmP5zs0OGQn6DWA7depqY?corpId=ding8ffc70a4ef94915f35c2f4657eb6378f&utm_medium=im_card&utm_source=im](https://alidocs.dingtalk.com/i/nodes/vy20BglGWOKXmP5zs0OGQn6DWA7depqY?corpId=ding8ffc70a4ef94915f35c2f4657eb6378f&utm_medium=im_card&utm_source=im)"""
PERSONNEL_SKILL_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "ceo-personnel-communication"
    / "SKILL.md"
)


def _personnel_skill_prose() -> str:
    return " ".join(PERSONNEL_SKILL_PATH.read_text(encoding="utf-8").split())


@pytest.fixture(autouse=True)
def _render_prompt_tests_from_canonical_seed(monkeypatch):
    # Installation-local prompt state is synchronized and read back at deployment.
    monkeypatch.setenv(
        "CEO_DEVELOPER_PROMPT_TEMPLATE_PATH",
        str(SEED_DEVELOPER_PROMPT_TEMPLATE),
    )


def test_developer_prompt_template_path_can_be_overridden(tmp_path, monkeypatch):
    template_path = tmp_path / "developer.md"
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", str(template_path))

    assert developer_prompt_template_path() == template_path


def test_prompt_template_paths_default_to_ignored_data_files(monkeypatch):
    monkeypatch.delenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", raising=False)
    monkeypatch.delenv("CEO_USER_PROMPT_TEMPLATE_PATH", raising=False)

    assert developer_prompt_template_path() == repo_root() / "data" / "prompts" / "developer_prompt.md"
    assert user_prompt_template_path() == repo_root() / "data" / "prompts" / "user_prompt.md"


def test_prompt_template_paths_expand_home(monkeypatch):
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", "~/developer.md")
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", "~/user.md")

    assert developer_prompt_template_path() == Path.home() / "developer.md"
    assert user_prompt_template_path() == Path.home() / "user.md"


def test_read_prompt_templates_seed_missing_configured_files(tmp_path, monkeypatch):
    developer_path = tmp_path / "data" / "prompts" / "developer_prompt.md"
    user_path = tmp_path / "data" / "prompts" / "user_prompt.md"
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", str(developer_path))
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(user_path))

    developer_template = read_developer_prompt_template()
    user_template = read_user_prompt_template()

    assert developer_path.exists()
    assert user_path.exists()
    assert "<var: principal>" in developer_template
    assert "<code: app.prompt:work_profile_instruction()>" in developer_template
    assert "<code: app.user_prompt_blocks:current_message_block()>" in user_template
    assert "CEO Agent Prompt" not in user_template


def test_default_developer_prompt_assigns_tool_execution_to_current_agent_role():
    prompt = SEED_DEVELOPER_PROMPT_TEMPLATE.read_text(encoding="utf-8")

    assert "你必须自行读取材料并通过当前角色获准的 CLI/MCP 工具完成任务" in prompt
    assert "AI 只负责生成结构化计划" not in prompt


def test_calendar_rules_path_is_not_an_effective_prompt_variable(monkeypatch):
    monkeypatch.setenv(
        "CEO_PROMPT_VAR_CALENDAR_RULES_PATH",
        "custom/calendar-rules.md",
    )

    assert "calendar_rules_path" not in CONFIGURABLE_PROMPT_VARIABLE_DEFAULTS
    assert "calendar_rules_path" not in prompt_template_variables()
    assert "calendar_rules_path" not in dict(configurable_prompt_variable_pairs())
    assert "CEO_PROMPT_VAR_CALENDAR_RULES_PATH" not in (
        Path(".env.example").read_text(encoding="utf-8")
    )
    with pytest.raises(DeveloperPromptTemplateError, match="unsupported"):
        write_configurable_prompt_variables(
            [("CEO_PROMPT_VAR_CALENDAR_RULES_PATH", "custom/calendar-rules.md")]
        )


def test_developer_prompt_template_renders_vars_files_and_code(tmp_path, monkeypatch):
    del tmp_path
    profile = repo_root() / ".developer_prompt_test_profile.md"
    profile.write_text("- profile line\n", encoding="utf-8")
    script = repo_root() / ".developer_prompt_test_script.py"
    script.write_text(
        "def dynamic_rule():\n"
        "    return 'runtime rule from code'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("USER_ALIAS", "Alex")
    try:
        rendered = render_developer_prompt_template(
            "\n".join(
                [
                    "<vars>",
                    "principal = <code: app.config:user_alias()>",
                    "handoff = <code: app.config:user_alias()>",
                    "</vars>",
                    "",
                    "principal=<var: principal>",
                    f"profile=<file: {profile}>",
                    "code=<code: .developer_prompt_test_script.py:dynamic_rule()>",
                    "handoff=<var: handoff>",
                ]
            )
        )
    finally:
        script.unlink(missing_ok=True)
        profile.unlink(missing_ok=True)

    assert "principal=Alex" in rendered
    assert "- profile line" in rendered
    assert "code=runtime rule from code" in rendered
    assert "handoff=Alex" in rendered


def test_default_developer_prompt_template_is_a_separate_file():
    template = read_developer_prompt_template()

    assert not template.startswith("<vars>")
    assert "principal = 明哥" not in template
    assert "handoff_name = Alex" not in template
    assert "<vars>" not in template
    assert "<var: principal>" in template
    assert "<code: app.prompt:work_profile_instruction()>" in template
    assert "work_profile_path" not in template
    assert "Alex 工作人格 Profile:" not in template


def test_developer_prompt_delegates_memory_to_agent_mcp_tools():
    template = read_developer_prompt_template()

    assert "memory_connector MCP 可用" in template
    assert "检索优先级是：memory_recall、本地文件、dws aisearch、dws 知识库" in template
    assert "优先调用 memory_recall 获取可复用上下文" in template
    assert "业务判断、人员判断、项目背景、客户口径、审批/日历处理" in template
    assert "调用 memory_write 记录一条业务 episode" in template
    assert "不要传 user_id" in template
    assert "memory_write 失败不应改变最终 JSON" in template


def test_personnel_skill_keeps_business_facts_out_of_personnel_sensitivity():
    template = read_developer_prompt_template()
    skill = _personnel_skill_prose()

    assert "A person's name alone does not make a business fact personnel information" in skill
    assert "Ownership, delivery, revenue, customer progress, project risk" in skill
    assert "A person's name alone does not make a business fact personnel information" not in template
    assert "没有列出的字段不要编造职位或上下级关系" in template
    assert "刷新凭证或弹出授权页" in template


def test_developer_prompt_delegates_latest_material_review_to_business_skill():
    template = read_developer_prompt_template()

    assert "前一次依据的材料已经被修改、补充、评论确认或按要求更新" not in template
    assert "处理文档时，如果是钉钉文档可以用评论功能" not in template
    assert "涉及专业业务流程时" in template
    assert "agent_cli.read_skill" in template


def test_developer_prompt_defines_non_executable_action_boundary():
    template = read_developer_prompt_template()

    assert "只有特定真人、群主、管理员、审批人、系统 owner 或外部系统权限才能完成的现实动作" in template
    assert "不能只回复“可以、方向对、应该做”" in template
    assert "必须明确说明当前不能代为执行该动作" in template
    assert "handoff_to_human" in template


def test_developer_prompt_requires_executable_advice_for_solution_requests():
    template = read_developer_prompt_template()

    assert "不要只讲方向、原则或抽象道理" in template
    assert "回复必须给可执行建议" in template
    assert "下一步动作、执行 owner 或需要谁配合" in template
    assert "关键约束/平台边界、验收口径" in template
    assert "给出可落地的替代路径或需要补齐的材料" in template


def test_developer_prompt_documents_agent_envelope_output_protocol():
    template = read_developer_prompt_template()

    assert "kind 必须是 reply、okr_review、no_action 或 error" in template
    assert '{"type":"queue_okr_review"}' in template
    assert "发信人本人的 OKR/KR 进度" in template
    assert "直接下属、岗位管理者、团队成员或其他第三方" in template
    assert "OKR 审核流程只会读取发信人本人的 OKR" in template
    assert "目标确认" in template
    assert "不要输出 queue_okr_review" in template
    assert "服务会把退回意见单独发消息给审批申请人" in template
    assert "user_response.mode 必须是 send_reply、ask_clarifying_question、handoff_to_human 或 no_reply" in template
    assert "domain_payload 默认使用空对象" in template
    assert "domain_payload.calendar_response_status" in template
    assert "domain_payload.candidate_context_known" in template
    assert "action 必须是 send_reply" not in template
    assert "reply_text 必须非空" not in template


def test_work_profile_path_default_is_not_user_specific(monkeypatch):
    monkeypatch.delenv("CEO_WORK_PROFILE_PATH", raising=False)

    assert work_profile_path() == repo_root() / "data" / "work-profile" / "work_profile.md"


def test_config_paths_expand_home(monkeypatch):
    monkeypatch.setenv("CEO_ENV_FILE", "~/.ceo-agent-test.env")
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", "~/profile.md")
    monkeypatch.setenv("CEO_PROFILE_EVIDENCE_DIR", "$HOME/profile-evidence")

    assert env_file_path() == Path.home() / ".ceo-agent-test.env"
    assert work_profile_path() == Path.home() / "profile.md"
    assert profile_evidence_dir() == Path.home() / "profile-evidence"


def test_work_profile_instruction_uses_configured_principal_name(
    tmp_path, monkeypatch
):
    profile = tmp_path / "profile.md"
    profile.write_text("# Generic Profile\n\n- Keep replies concise.", encoding="utf-8")
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile))
    monkeypatch.setenv("USER_ALIAS", "Alex")

    instruction = work_profile_instruction()

    assert "Alex 工作人格 Profile" in instruction
    assert "the principal 工作人格 Profile" not in instruction
    assert "更接近 Alex 的判断顺序" in instruction
    assert "更接近 the principal 的判断顺序" not in instruction


def test_work_profile_instruction_seeds_missing_configured_profile(
    tmp_path,
    monkeypatch,
):
    profile = tmp_path / "data" / "work-profile" / "work_profile.md"
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile))
    monkeypatch.setenv("USER_ALIAS", "Alex")

    instruction = work_profile_instruction()

    assert profile.exists()
    assert "Alex 工作人格 Profile" in instruction
    assert "No distilled work profile has been generated yet." in profile.read_text(
        encoding="utf-8"
    )


def test_user_prompt_template_path_can_be_overridden(tmp_path, monkeypatch):
    template_path = tmp_path / "user.md"
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(template_path))

    assert user_prompt_template_path() == template_path


def test_default_user_prompt_template_is_a_separate_file():
    template = read_user_prompt_template()
    code_tags = [
        "<code: app.user_prompt_blocks:style_lines()>",
        "<code: app.user_prompt_blocks:current_message_block()>",
        "<code: app.user_prompt_blocks:sender_org_block()>",
        "<code: app.user_prompt_blocks:known_people_block()>",
        "<code: app.user_prompt_blocks:context_messages_block()>",
        "<code: app.user_prompt_blocks:material_references_block()>",
        "<code: app.user_prompt_blocks:linked_documents_block()>",
        "<code: app.user_prompt_blocks:image_download_block()>",
    ]

    assert template.strip() == "\n---\n".join(code_tags)
    assert "<code: app.user_prompt_blocks:current_message_block()>" in template
    assert "<code: app.user_prompt_blocks:context_messages_block()>" in template
    assert "<var: current_message_block>" not in template
    assert "CEO Agent Prompt" not in template


def test_user_prompt_block_registry_orders_material_references_before_assets():
    expressions = [block.expression for block in USER_PROMPT_BLOCKS]

    assert expressions[
        expressions.index("app.user_prompt_blocks:context_messages_block()") :
        expressions.index("app.user_prompt_blocks:image_download_block()") + 1
    ] == [
        "app.user_prompt_blocks:context_messages_block()",
        "app.user_prompt_blocks:material_references_block()",
        "app.user_prompt_blocks:linked_documents_block()",
        "app.user_prompt_blocks:image_download_block()",
    ]


def test_build_turn_prompt_uses_user_prompt_template_override(tmp_path, monkeypatch):
    template_path = tmp_path / "user.md"
    template_path.write_text(
        "\n".join(
            [
                "CUSTOM USER PROMPT",
                "<code: app.user_prompt_blocks:current_message_block()>",
                "<code: app.user_prompt_blocks:material_references_block()>",
                "<code: app.user_prompt_blocks:image_download_block()>",
                "<code: app.user_prompt_blocks:context_messages_block()>",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(template_path))

    prompt = build_turn_prompt(
        DingTalkConversation(
            open_conversation_id="cid-1",
            title="产品群",
            single_chat=False,
            unread_point=1,
        ),
        [
            DingTalkMessage(
                open_conversation_id="cid-1",
                open_message_id="msg-1",
                conversation_title="产品群",
                single_chat=False,
                sender_name="Mina",
                create_time="2026-05-15 13:00:00",
                content="@Alex Chen(明哥) 看下图片",
            )
        ],
        [],
        style_lines=[],
        include_thread_prompt=False,
        image_download_errors=["msg-1: resource @img error unsupported resourceType: image"],
    )

    assert prompt.startswith("CUSTOM USER PROMPT")
    assert "当前待处理消息:" in prompt
    assert "图片读取状态:" in prompt
    assert "unsupported resourceType: image" in prompt
    assert "上下文消息（自上次回复后的新信息，最多 20 条）:" in prompt


def test_context_messages_block_renders_json_array():
    context_message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="ctx-1",
        conversation_title="产品群",
        single_chat=False,
        sender_name="Mina",
        sender_user_id="sender-user-1",
        sender_open_dingtalk_id="open-sender-1",
        message_type="text",
        create_time="2026-05-15 12:59:00",
        content="上文背景",
        mentioned_user_ids=["principal-user-1"],
        quoted_message_id="quoted-1",
        quoted_content="引用背景",
    )

    prompt = build_turn_prompt(
        DingTalkConversation(
            open_conversation_id="cid-1",
            title="产品群",
            single_chat=False,
            unread_point=1,
        ),
        [
            DingTalkMessage(
                open_conversation_id="cid-1",
                open_message_id="msg-1",
                conversation_title="产品群",
                single_chat=False,
                sender_name="Mina",
                create_time="2026-05-15 13:00:00",
                content="@Alex Chen(明哥) 看下",
            )
        ],
        [context_message],
        style_lines=[],
        include_thread_prompt=False,
    )

    json_text = prompt.split("上下文消息（自上次回复后的新信息，最多 20 条）:", 1)[
        1
    ].split("\n---", 1)[0]
    records = json.loads(json_text)

    assert records == [
        {
            "open_message_id": "ctx-1",
            "create_time": "2026-05-15 12:59:00",
            "sender": {
                "name": "Mina",
                "user_id": "sender-user-1",
                "open_dingtalk_id": "open-sender-1",
            },
            "message_type": "text",
            "content": "上文背景",
            "mentioned_user_ids": ["principal-user-1"],
            "quoted": {
                "open_message_id": "quoted-1",
                "content": "引用背景",
            },
        }
    ]


def test_context_messages_block_includes_existing_reactions():
    context_message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="ctx-1",
        conversation_title="产品群",
        single_chat=False,
        sender_name="Mina",
        create_time="2026-05-15 12:59:00",
        content="上文背景",
        raw_payload={
            "emotionReplyList": [
                {"emoji": "OK", "replyUsers": ["明哥"]},
                {"text": "我去摇人", "replyUsers": ["Alex"]},
            ]
        },
    )

    prompt = build_turn_prompt(
        DingTalkConversation(
            open_conversation_id="cid-1",
            title="产品群",
            single_chat=False,
            unread_point=1,
        ),
        [
            DingTalkMessage(
                open_conversation_id="cid-1",
                open_message_id="msg-1",
                conversation_title="产品群",
                single_chat=False,
                sender_name="Mina",
                create_time="2026-05-15 13:00:00",
                content="@Alex Chen(明哥) 看下",
            )
        ],
        [context_message],
        style_lines=[],
        include_thread_prompt=False,
    )

    json_text = prompt.split("上下文消息（自上次回复后的新信息，最多 20 条）:", 1)[
        1
    ].split("\n---", 1)[0]
    records = json.loads(json_text)

    assert records[0]["reactions"] == [
        {"reaction": "OK", "users": ["明哥"]},
        {"reaction": "我去摇人", "users": ["Alex"]},
    ]


def test_message_lines_remove_repeated_card_images_and_shorten_links():
    lines = message_lines(
        DingTalkMessage(
            open_conversation_id="cid-1",
            open_message_id="msg-1",
            conversation_title="26年董事会筹备组",
            single_chat=False,
            sender_name="Lily",
            sender_user_id="lily-user-1",
            create_time="2026-05-14 15:04:04",
            content=CARD_CONTENT,
        )
    )
    rendered = "\n".join(lines)

    assert "董事会报告根据昨天的会议进行了修改" in rendered
    assert "Lily sender_user_id=lily-user-1 2026-05-14" in rendered
    assert "26年董事会报告" in rendered
    assert "![image]" not in rendered
    assert "utm_medium" not in rendered
    assert "corpId" not in rendered
    assert (
        "https://alidocs.dingtalk.com/i/nodes/vy20BglGWOKXmP5zs0OGQn6DWA7depqY"
        in rendered
    )


def test_message_lines_include_existing_reactions():
    lines = message_lines(
        DingTalkMessage(
            open_conversation_id="cid-1",
            open_message_id="msg-1",
            conversation_title="产品群",
            single_chat=False,
            sender_name="Mina",
            create_time="2026-05-15 13:00:00",
            content="@Alex Chen(明哥) 看下",
            raw_payload={
                "emotionReplyList": [
                    {"emoji": "OK", "replyUsers": ["明哥"]},
                    {"emoji": "👍", "replyUsers": ["Mina", "Alex"]},
                ]
            },
        )
    )

    rendered = "\n".join(lines)

    assert "已有 reaction: OK（明哥）；👍（Mina, Alex）" in rendered


def test_sanitize_dingtalk_prompt_text_keeps_malformed_url_text():
    rendered = sanitize_dingtalk_prompt_text(
        "@Alex Chen(明哥) 看下这个链接 https://[not-a-valid-ipv6/link?x=1"
    )

    assert "@Alex Chen(明哥) 看下这个链接" in rendered
    assert "https://[not-a-valid-ipv6/link?x=1" in rendered


def test_sanitize_dingtalk_prompt_text_keeps_url_with_nfkc_unsafe_host_text():
    rendered = sanitize_dingtalk_prompt_text(
        "@Alex Chen(明哥) 看下这个服务 http://stardust-gpu4:8787？"
    )

    assert "@Alex Chen(明哥) 看下这个服务" in rendered
    assert "http://stardust-gpu4:8787？" in rendered


def test_build_turn_prompt_sanitizes_quoted_card_without_repeating_assets():
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="26年董事会筹备组",
        single_chat=False,
        unread_point=1,
    )
    message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="26年董事会筹备组",
        single_chat=False,
        sender_name="Lily",
        create_time="2026-05-14 15:04:04",
        content=CARD_CONTENT,
        quoted_message_id="quoted-1",
        quoted_content=CARD_CONTENT,
    )

    prompt = build_turn_prompt(
        conversation,
        [message],
        [message],
        style_lines=[],
        include_thread_prompt=False,
    )

    assert prompt.count("![image]") == 0
    assert prompt.count("O1CN01DXenu91IyBR0wQXk9") == 0
    assert prompt.count("utm_source") == 0
    assert prompt.count("https://alidocs.dingtalk.com/i/nodes/") <= 3


def test_personnel_skill_explains_first_person_single_chat_subject():
    prompt = ceo_agent_thread_prompt()
    skill = _personnel_skill_prose()

    assert "When the recipient asks about their own personnel information" in skill
    assert "the subject and recipient are the same person" in skill
    assert "单聊里可以回答发信人关于他自己的请假、调休" not in prompt
    assert "没有列出的字段不要编造职位或上下级关系" in prompt


def test_thread_prompt_delegates_direct_message_triage_to_business_skill():
    prompt = ceo_agent_thread_prompt()

    assert "明确要求 明哥 处理、确认、决策或对某个结论表态" not in prompt
    assert "涉及专业业务流程时" in prompt
    assert "agent_cli.read_skill" in prompt


def test_thread_prompt_requires_direct_structured_output_for_analysis_requests():
    prompt = ceo_agent_thread_prompt()

    assert "写出列表" in prompt
    assert "直接给出可用的结构化初版" in prompt
    assert "不要只回复“可以、我会整理、先出一版”" in prompt


def test_build_turn_prompt_keeps_user_message_separate_from_thread_prompt():
    prompt = build_turn_prompt(
        DingTalkConversation(
            open_conversation_id="cid-1",
            title="周俊杰",
            single_chat=True,
            unread_point=1,
        ),
        [
            DingTalkMessage(
                open_conversation_id="cid-1",
                open_message_id="msg-1",
                conversation_title="周俊杰",
                single_chat=True,
                sender_name="周俊杰",
                sender_user_id="junjie-user-1",
                create_time="2026-05-15 13:00:00",
                content="明哥，我今天想请一天调休。",
            )
        ],
        [],
        style_lines=[],
        include_thread_prompt=True,
    )

    assert "当前待处理消息:" in prompt
    assert "会话: 周俊杰" in prompt
    assert "CEO Agent Prompt" not in prompt
    assert "周俊杰 sender_user_id=junjie-user-1" in prompt


def test_build_turn_prompt_includes_known_people_lines():
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="Mina 邹",
        single_chat=True,
        unread_point=1,
    )
    message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Mina 邹",
        single_chat=True,
        sender_name="Mina 邹",
        create_time="2026-05-15 13:00:00",
        content="明哥，晓民的转正时间快到了。",
    )

    prompt = build_turn_prompt(
        conversation,
        [message],
        [message],
        style_lines=[],
        include_thread_prompt=True,
        known_people_lines=["- 张晓民: user_id=subject-user-1"],
    )

    assert "可用组织人员标识" in prompt
    assert "- 张晓民: user_id=subject-user-1" in prompt


def test_build_turn_prompt_includes_sender_org_lines():
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="Mina 邹",
        single_chat=True,
        unread_point=1,
    )
    message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Mina 邹",
        single_chat=True,
        sender_name="Mina 邹",
        create_time="2026-05-15 13:00:00",
        content="明哥，晓民的转正时间快到了。",
    )

    prompt = build_turn_prompt(
        conversation,
        [message],
        [message],
        style_lines=[],
        include_thread_prompt=True,
        sender_org_lines=[
            '{\n  "name": "Mina 邹",\n  "user_id": "sender-user-1",\n  "title": "首席人力资源专家兼HRVP",\n  "manager": {"name": "Alex Chen", "user_id": "principal-user-1"}\n}'
        ],
    )

    assert "发信人组织信息(JSON):" in prompt
    assert '"name": "Mina 邹"' in prompt
    assert '"user_id": "sender-user-1"' in prompt
    assert '"title": "首席人力资源专家兼HRVP"' in prompt


def test_thread_prompt_delegates_document_commands_to_operation_skills():
    prompt = ceo_agent_thread_prompt()

    assert 'dws doc info --node "<链接>" --format json' not in prompt
    assert 'dws doc read --node "<链接>" --format json' not in prompt
    assert "extension=able" not in prompt
    assert "普通钉钉文件不同于钉钉在线文档" not in prompt
    assert "agent_cli.read_skill" in prompt
    assert "DWS 登录/工具问题" in prompt
    assert "不要说成对方没有提供材料" in prompt


def test_thread_prompt_preserves_context_anchor_for_followup_documents():
    prompt = ceo_agent_thread_prompt()

    assert "文档、复盘或补充材料" in prompt
    assert "先用当前消息、引用、合并前序消息和上下文判断它的角色" in prompt
    assert "不要仅因为文档正文包含 OKR、分数或证据链" in prompt
    assert "把它当作 OKR 打分依据" in prompt
    assert "叮当 OKR 或系统数据" in prompt
    assert "不能替代 OKR 审核流程" in prompt


def test_thread_prompt_defaults_to_business_context_retrieval():
    prompt = ceo_agent_thread_prompt()

    assert "默认不了解当前业务背景" in prompt
    assert "本地文件" in prompt
    assert "dws aisearch" in prompt
    assert "dws 知识库" in prompt
    assert "审批、日程、文档、链接、图片" in prompt
    assert "若这些材料已经足以判断是否回复和回复内容，不要再做本地 workspace 或 graphify 检索" not in prompt


def test_thread_prompt_requires_sender_org_context_when_available():
    prompt = ceo_agent_thread_prompt()

    assert "发信人组织信息" in prompt
    assert "JSON" in prompt
    assert "title" in prompt
    assert "manager" in prompt
    assert "不要编造职位" in prompt
    assert "本 thread 必须主动使用 graphify" not in prompt


def test_thread_prompt_injects_work_profile_without_exposing_path(
    monkeypatch,
    tmp_path,
):
    profile = tmp_path / "work_profile.md"
    profile.write_text(
        "# Work Profile\n\n"
        "This profile is a runtime work-judgment profile.\n\n"
        "## Core Operating Loop\n\n"
        "- Decide whether to reply.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "CEO_WORK_PROFILE_PATH",
        str(profile),
    )

    prompt = ceo_agent_thread_prompt()

    assert "明哥 工作人格 Profile" in prompt
    assert (
        "/Users/principal/Documents/Projects/ceo-agent-service/data/work-profile/work_profile.md"
        not in prompt
    )
    assert "不要再尝试读取 profile 文件路径" in prompt
    assert "Profile 内容:" in prompt
    assert "# Work Profile" in prompt
    assert "This profile is a runtime work-judgment profile" in prompt
    assert "Core Operating Loop" in prompt


def test_thread_prompt_requires_oa_review_principles_for_approval_messages():
    prompt = ceo_agent_thread_prompt()

    assert "management/OA/钉钉审批审阅原则.md" in prompt
    assert "材料完整且符合审批原则" in prompt
    assert "直接执行通过" in prompt
    assert "以评论的形式回复审批人" in prompt
    assert "明确不匹配规则或 SOP" in prompt
    assert "退回" in prompt
    assert "不能用拒绝冒充退回" in prompt
    assert "缺任何实质材料时不能给批准、退回或拒绝结论" not in prompt


def test_thread_prompt_does_not_default_oa_calendar_to_no_reply():
    prompt = ceo_agent_thread_prompt()

    assert "审批/OA/日程/文件状态/自动同步等通知性消息，只记录 no_reply" not in prompt
    assert "不能因为通知格式默认 no_reply" in prompt


def test_seed_prompt_delegates_calendar_rules_to_business_skills():
    prompt = SEED_DEVELOPER_PROMPT_TEMPLATE.read_text(encoding="utf-8")

    assert "<var: calendar_rules_path>" not in prompt
    assert "agent_cli.read_skill" in prompt
    assert "最具体适用的业务 Skill" in prompt


def test_thread_prompt_delegates_minutes_handling_to_business_skill():
    prompt = ceo_agent_thread_prompt()

    assert "如果新消息或引用涉及“静默会”、AI 听记、会议纪要链接或会议材料" not in prompt
    assert "涉及专业业务流程时" in prompt
    assert "agent_cli.read_skill" in prompt
    assert "最具体适用的业务 Skill" in prompt


def test_personnel_skill_requires_candidate_context_lookup_before_clarifying():
    prompt = ceo_agent_thread_prompt()
    skill = _personnel_skill_prose()

    assert "Use `stardust-interview` to read available conversation context" in skill
    assert "resume, role requirements, and interview records" in skill
    assert "Ask for specifically missing candidate or role material only after" in skill
    assert "候选人上下文不能只看当前一句话" not in prompt
    assert "先查会话名、消息、引用、AI 听记、面试记录、简历和岗位材料" not in prompt


def test_thread_prompt_delegates_lightweight_interaction_judgment():
    prompt = ceo_agent_thread_prompt()

    assert "真人直接 @明哥 或分身开玩笑" not in prompt
    assert "不要为了显得参与而发送低信息增益文字" not in prompt
    assert "agent_cli.read_skill" in prompt


def test_thread_prompt_prevents_interjecting_on_group_broadcasts():
    prompt = ceo_agent_thread_prompt()

    assert "@所有人不是自动跳过的理由" not in prompt
    assert "群聊广播如果是在推进高价值客户线索" not in prompt
    assert "agent_cli.read_skill" in prompt


def test_thread_prompt_prefers_reaction_for_low_information_group_mentions():
    prompt = ceo_agent_thread_prompt()

    assert "不要为了显得参与而发送低信息增益文字" not in prompt
    assert "不要为了“礼貌收口”发送“收到”“好的”这类低信息增益文字" not in prompt
    assert "no_reply 通常用空数组" in prompt
    assert "dws_message_reaction" in prompt


def test_thread_prompt_allows_markdown_document_reply():
    prompt = ceo_agent_thread_prompt()

    assert "dws_markdown_document_reply" in prompt
    assert "正文仍完整写在 user_response.text" in prompt
    assert "服务会创建 Markdown 文档并在聊天里回复文档链接" in prompt


def test_thread_prompt_keeps_generic_reaction_output_contract():
    prompt = ceo_agent_thread_prompt()

    assert "我让明哥本人看一下" not in prompt
    assert "dws_message_reaction" in prompt


def test_thread_prompt_treats_existing_principal_reaction_as_handled():
    prompt = ceo_agent_thread_prompt()

    assert "已有 reaction" not in prompt
    assert "通常说明真人已经用轻量方式处理过" not in prompt
    assert "dws_message_reaction" in prompt


def test_build_turn_prompt_includes_prefetched_dingtalk_document():
    prompt = build_turn_prompt(
        DingTalkConversation(
            open_conversation_id="cid-1",
            title="CEO-2 管理群",
            single_chat=False,
            unread_point=1,
        ),
        [
            DingTalkMessage(
                open_conversation_id="cid-1",
                open_message_id="msg-1",
                conversation_title="CEO-2 管理群",
                single_chat=False,
                sender_name="张毅倜(ET)",
                create_time="2026-05-18 00:33:40",
                content="https://alidocs.dingtalk.com/i/nodes/doc123 @Alex Chen(明哥) 看下",
            )
        ],
        [],
        style_lines=[],
        include_thread_prompt=False,
        linked_documents=[
            LinkedDocumentContext(
                url="https://alidocs.dingtalk.com/i/nodes/doc123?utm_source=im",
                title="数据导入导出业务低效根因和最终解法",
                markdown=(
                    '<span style="color: red;">核心结论</span>\n'
                    "根因是协作方式不对。"
                ),
            )
        ],
    )

    assert "已获取的钉钉材料:" in prompt
    assert "数据导入导出业务低效根因和最终解法" in prompt
    assert "https://alidocs.dingtalk.com/i/nodes/doc123" in prompt
    assert "utm_source" not in prompt
    assert "<span" not in prompt
    assert "根因是协作方式不对。" in prompt


def test_build_turn_prompt_includes_material_references_for_agent_reading():
    prompt = build_turn_prompt(
        DingTalkConversation(
            open_conversation_id="cid-1",
            title="CEO-2 管理群",
            single_chat=False,
            unread_point=1,
        ),
        [
            DingTalkMessage(
                open_conversation_id="cid-1",
                open_message_id="msg-1",
                conversation_title="CEO-2 管理群",
                single_chat=False,
                sender_name="韩露",
                create_time="2026-06-08 18:46:32",
                content="@Alex Chen(明哥) 看第二份材料",
            )
        ],
        [],
        style_lines=[],
        include_thread_prompt=False,
        material_references=[
            MaterialReferenceContext(
                kind="dingtalk_doc",
                reference="https://alidocs.dingtalk.com/i/nodes/doc123?utm_scene=team_space",
                source_message_id="msg-1",
                source_sender="韩露",
                source_time="2026-06-08 18:46:32",
            ),
            MaterialReferenceContext(
                kind="dingtalk_minutes",
                reference="7632756964333134343836383736303334325f3435313431363430365f35",
                source_message_id="msg-1",
                source_sender="韩露",
                source_time="2026-06-08 18:46:32",
            ),
        ],
    )

    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert "类型: dingtalk_doc" in prompt
    assert "dws doc info --node" in prompt
    assert "dws doc read --node" in prompt
    assert "类型: dingtalk_minutes" in prompt
    assert "dws minutes get info --id" in prompt
    assert "如果判断依赖材料正文，必须先读取材料" in prompt
