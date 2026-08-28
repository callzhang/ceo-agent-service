import json
import hashlib
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
from app.consumer_agent import (
    CORE_DYNAMIC_SKILL_BODY,
    REVIEWED_DWS_READ_INSTRUCTIONS,
)
from app.user_prompt_blocks import USER_PROMPT_BLOCKS
from tests.prompt_structure import validate_prompt_structure


def test_consumer_oa_work_is_completed_by_agent_instead_of_generic_handoff():
    assert "Do not stop at a generic" in REVIEWED_DWS_READ_INSTRUCTIONS
    assert "carry the workflow through the documented" in REVIEWED_DWS_READ_INSTRUCTIONS
    assert "normal retry contract" in REVIEWED_DWS_READ_INSTRUCTIONS
    assert "only when the information required" in REVIEWED_DWS_READ_INSTRUCTIONS


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
    assert "independently selects and reads every applicable" in developer_template
    assert "{{current_message}}" in user_template
    assert "CEO Agent Prompt" not in user_template


def test_unmodified_legacy_developer_prompt_is_upgraded(tmp_path, monkeypatch):
    developer_path = tmp_path / "data" / "prompts" / "developer_prompt.md"
    developer_path.parent.mkdir(parents=True)
    legacy = "legacy default"
    developer_path.write_text(legacy, encoding="utf-8")
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", str(developer_path))
    monkeypatch.setattr(
        "app.developer_prompt.LEGACY_UNCUSTOMIZED_DEVELOPER_PROMPT_SHA256S",
        {_sha256_for_test(legacy)},
    )

    assert read_developer_prompt_template() == SEED_DEVELOPER_PROMPT_TEMPLATE.read_text(
        encoding="utf-8"
    )


def test_customized_developer_prompt_is_preserved(tmp_path, monkeypatch):
    developer_path = tmp_path / "data" / "prompts" / "developer_prompt.md"
    developer_path.parent.mkdir(parents=True)
    developer_path.write_text("custom instructions", encoding="utf-8")
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", str(developer_path))

    assert read_developer_prompt_template() == "custom instructions"


def _sha256_for_test(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_default_developer_prompt_assigns_execution_to_audit_role():
    prompt = SEED_DEVELOPER_PROMPT_TEMPLATE.read_text(encoding="utf-8")

    assert (
        "1. [role_boundary] Role Boundary: Consumer Agent A gathers facts and proposes "
        "a typed candidate; Audit Agent B applies the operation Skill and executes an "
        "accepted candidate."
    ) in prompt
    assert "read-only representative" not in prompt


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
    assert "<code: app.prompt:work_profile_instruction()>" not in template
    assert "work_profile_path" not in template
    assert "Alex 工作人格 Profile:" not in template


def test_canonical_default_prompt_keeps_only_runtime_invariants_and_skill_loading():
    template = render_developer_prompt_template(read_developer_prompt_template())

    validate_prompt_structure(
        template,
        contract_models=(),
        dynamic_skill_body=CORE_DYNAMIC_SKILL_BODY,
        audit_rules=None,
        context_facts=None,
        size_limit=3_000,
    )


def test_developer_prompt_delegates_memory_operations_to_installed_skills():
    template = read_developer_prompt_template()

    assert "memory_connector" not in template
    assert "memory_recall" not in template
    assert "memory_write" not in template
    assert "unavailable Memory dependency never triggers login" in template


def test_personnel_skill_keeps_business_facts_out_of_personnel_sensitivity():
    template = read_developer_prompt_template()
    skill = _personnel_skill_prose()

    assert "A person's name alone does not make a business fact personnel information" in skill
    assert "Ownership, delivery, revenue, customer progress, project risk" in skill
    assert "A person's name alone does not make a business fact personnel information" not in template
    assert "没有列出的字段不要编造职位或上下级关系" not in template
    assert "login, reset, or logout" in template


def test_developer_prompt_delegates_latest_material_review_to_business_skill():
    template = read_developer_prompt_template()

    assert "前一次依据的材料已经被修改、补充、评论确认或按要求更新" not in template
    assert "处理文档时，如果是钉钉文档可以用评论功能" not in template
    assert CORE_DYNAMIC_SKILL_BODY in template
    assert "independently selects and reads every applicable" in template


def test_dynamic_skill_contract_does_not_create_runtime_reconciliation_policy():
    template = read_developer_prompt_template()

    assert "already-unknown effect" not in template
    assert "strictly read-only evidence reconciliation" not in template
    assert "service retries an ordinary failed turn" in template


def test_developer_prompt_defines_role_execution_boundary():
    template = read_developer_prompt_template()

    assert (
        "1. [role_boundary] Role Boundary: Consumer Agent A gathers facts and proposes "
        "a typed candidate; Audit Agent B applies the operation Skill and executes an "
        "accepted candidate."
    ) in template
    assert "read-only representative" not in template


def test_developer_prompt_leaves_solution_workflow_to_business_skills():
    template = read_developer_prompt_template()

    assert "不要只讲方向、原则或抽象道理" not in template
    assert "回复必须给可执行建议" not in template
    assert CORE_DYNAMIC_SKILL_BODY in template


def test_developer_prompt_delegates_output_shape_to_pydantic_contract():
    template = read_developer_prompt_template()

    assert "Pydantic output contract" in template
    assert "field combinations are authoritative" in template
    assert "queue_okr_review" not in template
    assert "domain_payload" not in template


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
    named_variables = [
        "{{style_lines}}",
        "{{current_message}}",
        "{{sender_org}}",
        "{{known_people}}",
        "{{context_messages}}",
        "{{material_references}}",
        "{{linked_documents}}",
        "{{image_download_status}}",
    ]

    assert template.strip() == "\n---\n".join(named_variables)
    assert "{{current_message}}" in template
    assert "{{context_messages}}" in template
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
    assert "没有列出的字段不要编造职位或上下级关系" not in prompt


def test_thread_prompt_delegates_direct_message_triage_to_business_skill():
    prompt = ceo_agent_thread_prompt()

    assert "明确要求 明哥 处理、确认、决策或对某个结论表态" not in prompt
    assert CORE_DYNAMIC_SKILL_BODY in prompt
    assert "independently selects and reads every applicable" in prompt


def test_thread_prompt_leaves_structured_analysis_policy_to_business_skills():
    prompt = ceo_agent_thread_prompt()

    assert "写出列表" not in prompt
    assert "直接给出可用的结构化初版" not in prompt
    assert "independently selects and reads every applicable" in prompt


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
    assert "independently selects and reads every applicable" in prompt
    assert "DWS 登录/工具问题" not in prompt
    assert "不要说成对方没有提供材料" not in prompt


def test_thread_prompt_does_not_embed_followup_document_policy():
    prompt = ceo_agent_thread_prompt()

    assert "文档、复盘或补充材料" not in prompt
    assert "先用当前消息、引用、合并前序消息和上下文判断它的角色" not in prompt
    assert "independently selects and reads every applicable" in prompt


def test_thread_prompt_delegates_business_context_retrieval():
    prompt = ceo_agent_thread_prompt()

    assert "默认不了解当前业务背景" not in prompt
    assert "dws aisearch" not in prompt
    assert "memory_recall" not in prompt
    assert "independently selects and reads every applicable" in prompt


def test_thread_prompt_does_not_embed_sender_org_policy():
    prompt = ceo_agent_thread_prompt()

    assert "发信人组织信息" not in prompt
    assert "不要编造职位" not in prompt
    assert "independently selects and reads every applicable" in prompt


def test_thread_prompt_does_not_always_load_work_profile(
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

    assert "明哥 工作人格 Profile" not in prompt
    assert "Profile 内容:" not in prompt
    assert "# Work Profile" not in prompt
    assert "Core Operating Loop" not in prompt


def test_thread_prompt_does_not_embed_approval_workflow():
    prompt = ceo_agent_thread_prompt()

    assert "management/OA/钉钉审批审阅原则.md" not in prompt
    assert "材料完整且符合审批原则" not in prompt
    assert "independently selects and reads every applicable" in prompt


def test_thread_prompt_does_not_embed_notification_workflow():
    prompt = ceo_agent_thread_prompt()

    assert "审批/OA/日程/文件状态/自动同步等通知性消息，只记录 no_reply" not in prompt
    assert "不能因为通知格式默认 no_reply" not in prompt


def test_seed_prompt_delegates_calendar_rules_to_business_skills():
    prompt = SEED_DEVELOPER_PROMPT_TEMPLATE.read_text(encoding="utf-8")

    assert "<var: calendar_rules_path>" not in prompt
    assert "independently selects and reads every applicable" in prompt
    assert CORE_DYNAMIC_SKILL_BODY in prompt


def test_thread_prompt_delegates_minutes_handling_to_business_skill():
    prompt = ceo_agent_thread_prompt()

    assert "如果新消息或引用涉及“静默会”、AI 听记、会议纪要链接或会议材料" not in prompt
    assert "independently selects and reads every applicable" in prompt
    assert CORE_DYNAMIC_SKILL_BODY in prompt


def test_personnel_skill_delegates_candidate_evidence_to_specialist():
    prompt = ceo_agent_thread_prompt()
    skill = _personnel_skill_prose()

    assert "Load `stardust-interview` for candidate evaluation" in skill
    assert "follow its evidence, role-fit, and interview workflow" in skill
    assert "Do not reproduce or replace those specialist workflows here" in skill
    assert "resume, role requirements, and interview records" not in skill
    assert "Ask for specifically missing candidate or role material" not in skill
    assert "候选人上下文不能只看当前一句话" not in prompt
    assert "先查会话名、消息、引用、AI 听记、面试记录、简历和岗位材料" not in prompt


def test_thread_prompt_delegates_lightweight_interaction_judgment():
    prompt = ceo_agent_thread_prompt()

    assert "真人直接 @明哥 或分身开玩笑" not in prompt
    assert "不要为了显得参与而发送低信息增益文字" not in prompt
    assert "independently selects and reads every applicable" in prompt


def test_thread_prompt_prevents_interjecting_on_group_broadcasts():
    prompt = ceo_agent_thread_prompt()

    assert "@所有人不是自动跳过的理由" not in prompt
    assert "群聊广播如果是在推进高价值客户线索" not in prompt
    assert "independently selects and reads every applicable" in prompt


def test_thread_prompt_delegates_reaction_policy_to_business_skill():
    prompt = ceo_agent_thread_prompt()

    assert "不要为了显得参与而发送低信息增益文字" not in prompt
    assert "不要为了“礼貌收口”发送“收到”“好的”这类低信息增益文字" not in prompt
    assert "no_reply 通常用空数组" not in prompt
    assert "dws_message_reaction" not in prompt
    assert "independently selects and reads every applicable" in prompt


def test_thread_prompt_delegates_document_reply_shape_to_skills_and_schema():
    prompt = ceo_agent_thread_prompt()

    assert "dws_markdown_document_reply" not in prompt
    assert "正文仍完整写在 user_response.text" not in prompt
    assert "Pydantic output contract" in prompt


def test_thread_prompt_keeps_generic_reaction_output_contract():
    prompt = ceo_agent_thread_prompt()

    assert "我让明哥本人看一下" not in prompt
    assert "dws_message_reaction" not in prompt
    assert "independently selects and reads every applicable" in prompt


def test_thread_prompt_treats_existing_principal_reaction_as_handled():
    prompt = ceo_agent_thread_prompt()

    assert "已有 reaction" not in prompt
    assert "通常说明真人已经用轻量方式处理过" not in prompt
    assert "dws_message_reaction" not in prompt
    assert "independently selects and reads every applicable" in prompt


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
