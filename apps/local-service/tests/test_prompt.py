import json

from ceo_agent_service.dingtalk_models import DingTalkConversation, DingTalkMessage
from ceo_agent_service.config import repo_root
from ceo_agent_service.developer_prompt import (
    developer_prompt_template_path,
    read_developer_prompt_template,
    read_user_prompt_template,
    render_developer_prompt_template,
    user_prompt_template_path,
)
from ceo_agent_service.prompt import (
    LinkedDocumentContext,
    build_turn_prompt,
    ceo_agent_thread_prompt,
    message_lines,
    sanitize_dingtalk_prompt_text,
)


CARD_CONTENT = """@Derek Zen(磊哥) 磊哥，董事会报告根据昨天的会议进行了修改，您是否已完成审核？是否可以定稿了？
  引用: 26年董事会报告
![image](https://gw.alicdn.com/imgextra/i4/O1CN019r2O9o1mRbjrcNMe5_!!6000000004951-2-tps-96-54.png)
![image](https://gw.alicdn.com/imgextra/i4/O1CN01DXenu91IyBR0wQXk9_!!6000000000961-2-tps-148-72.png)
![image](https://gw.alicdn.com/imgextra/i4/O1CN01DXenu91IyBR0wQXk9_!!6000000000961-2-tps-148-72.png)
[https://alidocs.dingtalk.com/i/nodes/vy20BglGWOKXmP5zs0OGQn6DWA7depqY?corpId=ding8ffc70a4ef94915f35c2f4657eb6378f&utm_medium=im_card&utm_source=im](https://alidocs.dingtalk.com/i/nodes/vy20BglGWOKXmP5zs0OGQn6DWA7depqY?corpId=ding8ffc70a4ef94915f35c2f4657eb6378f&utm_medium=im_card&utm_source=im)"""


def test_developer_prompt_template_path_can_be_overridden(tmp_path, monkeypatch):
    template_path = tmp_path / "developer.md"
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", str(template_path))

    assert developer_prompt_template_path() == template_path


def test_developer_prompt_template_renders_vars_files_and_code(tmp_path, monkeypatch):
    profile = repo_root() / "profiles" / "derek_work_profile.md"
    script = repo_root() / ".developer_prompt_test_script.py"
    script.write_text(
        "def dynamic_rule():\n"
        "    return 'runtime rule from code'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_PRINCIPAL_DISPLAY_NAME", "Derek")
    monkeypatch.setenv("CEO_PRINCIPAL_HANDOFF_NAME", "Derek")
    try:
        rendered = render_developer_prompt_template(
            "\n".join(
                [
                    "<vars>",
                    "principal = <code: ceo_agent_service.config:principal_display_name()>",
                    "handoff = <code: ceo_agent_service.config:principal_handoff_name()>",
                    "</vars>",
                    "",
                    "principal=<var: principal>",
                    "profile=<file: profiles/derek_work_profile.md>",
                    "code=<code: .developer_prompt_test_script.py:dynamic_rule()>",
                    "handoff=<var: handoff>",
                ]
            )
        )
    finally:
        script.unlink(missing_ok=True)

    assert "principal=Derek" in rendered
    assert profile.read_text(encoding="utf-8").splitlines()[0] in rendered
    assert "code=runtime rule from code" in rendered
    assert "handoff=Derek" in rendered


def test_default_developer_prompt_template_is_a_separate_file():
    template = read_developer_prompt_template()

    assert template.startswith("<vars>")
    assert "principal = Derek" in template
    assert "handoff_name = 磊哥" in template
    variable_block = template.split("</vars>", 1)[0]
    assert "<code:" not in variable_block
    assert "<var: principal>" in template
    assert "<code: ceo_agent_service.prompt:work_profile_instruction()>" not in template
    assert "Derek 工作人格 Profile:" in template


def test_user_prompt_template_path_can_be_overridden(tmp_path, monkeypatch):
    template_path = tmp_path / "user.md"
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(template_path))

    assert user_prompt_template_path() == template_path


def test_default_user_prompt_template_is_a_separate_file():
    template = read_user_prompt_template()
    code_tags = [
        "<code: ceo_agent_service.user_prompt_blocks:style_lines()>",
        "<code: ceo_agent_service.user_prompt_blocks:current_message_block()>",
        "<code: ceo_agent_service.user_prompt_blocks:sender_org_block()>",
        "<code: ceo_agent_service.user_prompt_blocks:known_people_block()>",
        "<code: ceo_agent_service.user_prompt_blocks:linked_documents_block()>",
        "<code: ceo_agent_service.user_prompt_blocks:image_download_block()>",
        "<code: ceo_agent_service.user_prompt_blocks:context_messages_block()>",
    ]

    assert template.strip() == "\n---\n".join(code_tags)
    assert "<code: ceo_agent_service.user_prompt_blocks:current_message_block()>" in template
    assert "<code: ceo_agent_service.user_prompt_blocks:context_messages_block()>" in template
    assert "<var: current_message_block>" not in template
    assert "CEO Agent Prompt" not in template


def test_build_turn_prompt_uses_user_prompt_template_override(tmp_path, monkeypatch):
    template_path = tmp_path / "user.md"
    template_path.write_text(
        "\n".join(
            [
                "CUSTOM USER PROMPT",
                "<code: ceo_agent_service.user_prompt_blocks:current_message_block()>",
                "<code: ceo_agent_service.user_prompt_blocks:image_download_block()>",
                "<code: ceo_agent_service.user_prompt_blocks:context_messages_block()>",
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
                content="@Derek Zen(磊哥) 看下图片",
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
        mentioned_user_ids=["derek-user-1"],
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
                content="@Derek Zen(磊哥) 看下",
            )
        ],
        [context_message],
        style_lines=[],
        include_thread_prompt=False,
    )

    json_text = prompt.split("上下文消息（自上次回复后的新信息，最多 20 条）:", 1)[1]
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
            "mentioned_user_ids": ["derek-user-1"],
            "quoted": {
                "open_message_id": "quoted-1",
                "content": "引用背景",
            },
        }
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


def test_sanitize_dingtalk_prompt_text_keeps_malformed_url_text():
    rendered = sanitize_dingtalk_prompt_text(
        "@Derek Zen(磊哥) 看下这个链接 https://[not-a-valid-ipv6/link?x=1"
    )

    assert "@Derek Zen(磊哥) 看下这个链接" in rendered
    assert "https://[not-a-valid-ipv6/link?x=1" in rendered


def test_sanitize_dingtalk_prompt_text_keeps_url_with_nfkc_unsafe_host_text():
    rendered = sanitize_dingtalk_prompt_text(
        "@Derek Zen(磊哥) 看下这个服务 http://stardust-gpu4:8787？"
    )

    assert "@Derek Zen(磊哥) 看下这个服务" in rendered
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


def test_thread_prompt_explains_first_person_single_chat_subject():
    prompt = ceo_agent_thread_prompt()

    assert "发信人讨论自己的请假、调休" in prompt
    assert "personnel_subject_user_id 必须填写该消息的 sender_user_id" in prompt
    assert "单聊和群聊都适用" in prompt


def test_thread_prompt_treats_mentioned_arrangements_requiring_principal_as_replies():
    prompt = ceo_agent_thread_prompt()

    assert "需要 Derek（磊哥） 参与或确认的安排" in prompt
    assert "即使没有问号，也应视为需要回复" in prompt


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
                content="磊哥，我今天想请一天调休。",
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
        content="磊哥，晓民的转正时间快到了。",
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
        content="磊哥，晓民的转正时间快到了。",
    )

    prompt = build_turn_prompt(
        conversation,
        [message],
        [message],
        style_lines=[],
        include_thread_prompt=True,
        sender_org_lines=[
            '{\n  "name": "Mina 邹",\n  "user_id": "sender-user-1",\n  "title": "首席人力资源专家兼HRVP",\n  "manager": {"name": "Derek Zen", "user_id": "derek-user-1"}\n}'
        ],
    )

    assert "发信人组织信息(JSON):" in prompt
    assert '"name": "Mina 邹"' in prompt
    assert '"user_id": "sender-user-1"' in prompt
    assert '"title": "首席人力资源专家兼HRVP"' in prompt


def test_thread_prompt_requires_dws_doc_read_for_alidocs_links():
    prompt = ceo_agent_thread_prompt()

    assert 'dws doc info --node "<链接>" --format json' in prompt
    assert 'dws doc read --node "<链接>" --format json' in prompt
    assert "extension=able" in prompt
    assert "dws aitable" in prompt
    assert "禁止用 curl、HTTP API 或浏览器直接读钉钉材料" in prompt
    assert "材料读不到，不能凭感觉回复" in prompt


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


def test_thread_prompt_points_to_template_work_profile_path_without_injecting_content():
    prompt = ceo_agent_thread_prompt()

    assert "Derek 工作人格 Profile" in prompt
    assert (
        "/Users/derek/Documents/Projects/ceo-agent-service/profiles/derek_work_profile.md"
        in prompt
    )
    assert "先读取并核对该文件" in prompt
    assert "Profile 内容:" not in prompt
    assert "先判断材料是否完整" not in prompt


def test_thread_prompt_requires_oa_review_principles_for_approval_messages():
    prompt = ceo_agent_thread_prompt()

    assert "management/OA/钉钉审批审阅原则.md" in prompt
    assert "材料完整且符合审批原则" in prompt
    assert "直接执行通过" in prompt
    assert "以评论的形式回复审批人" in prompt
    assert "明确不匹配规则或 SOP" in prompt
    assert "退回" in prompt
    assert "缺任何实质材料时不能给批准、退回或拒绝结论" not in prompt


def test_thread_prompt_does_not_default_oa_calendar_to_no_reply():
    prompt = ceo_agent_thread_prompt()

    assert "审批/OA/日程/文件状态/自动同步等通知性消息，只记录 no_reply" not in prompt
    assert "不能因为通知格式默认 no_reply" in prompt


def test_thread_prompt_requires_witty_reply_for_direct_jokes():
    prompt = ceo_agent_thread_prompt()

    assert "真人直接 @ Derek（磊哥） 或分身开玩笑" in prompt
    assert "简短、机智、克制的玩笑" in prompt
    assert "体现判断力和幽默感" in prompt
    assert "不要写成流程说明或机制解释" in prompt
    assert "如果玩笑要求分身做无法真实执行的动作" not in prompt


def test_thread_prompt_requires_polite_reply_for_direct_thanks():
    prompt = ceo_agent_thread_prompt()

    assert "单聊里如果对方只是" in prompt
    assert "表示感谢、确认收到、认可或客气收口" in prompt
    assert "不要因为“只是感谢/客气”直接 no_reply" in prompt


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
                content="https://alidocs.dingtalk.com/i/nodes/doc123 @Derek Zen(磊哥) 看下",
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
