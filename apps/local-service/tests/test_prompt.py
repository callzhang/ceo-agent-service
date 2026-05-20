from ceo_agent_service.dingtalk_models import DingTalkConversation, DingTalkMessage
from ceo_agent_service.prompt import (
    LinkedDocumentContext,
    build_turn_prompt,
    message_lines,
    sanitize_dingtalk_prompt_text,
)


CARD_CONTENT = """@Derek Zen(磊哥) 磊哥，董事会报告根据昨天的会议进行了修改，您是否已完成审核？是否可以定稿了？
  引用: 26年董事会报告
![image](https://gw.alicdn.com/imgextra/i4/O1CN019r2O9o1mRbjrcNMe5_!!6000000004951-2-tps-96-54.png)
![image](https://gw.alicdn.com/imgextra/i4/O1CN01DXenu91IyBR0wQXk9_!!6000000000961-2-tps-148-72.png)
![image](https://gw.alicdn.com/imgextra/i4/O1CN01DXenu91IyBR0wQXk9_!!6000000000961-2-tps-148-72.png)
[https://alidocs.dingtalk.com/i/nodes/vy20BglGWOKXmP5zs0OGQn6DWA7depqY?corpId=ding8ffc70a4ef94915f35c2f4657eb6378f&utm_medium=im_card&utm_source=im](https://alidocs.dingtalk.com/i/nodes/vy20BglGWOKXmP5zs0OGQn6DWA7depqY?corpId=ding8ffc70a4ef94915f35c2f4657eb6378f&utm_medium=im_card&utm_source=im)"""


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
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="周俊杰",
        single_chat=True,
        unread_point=1,
    )
    message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="周俊杰",
        single_chat=True,
        sender_name="周俊杰",
        sender_user_id="junjie-user-1",
        create_time="2026-05-15 13:00:00",
        content="磊哥，我今天想请一天调休。",
    )

    prompt = build_turn_prompt(
        conversation,
        [message],
        [message],
        style_lines=[],
        include_thread_prompt=True,
    )

    assert "发信人讨论自己的请假、调休" in prompt
    assert "personnel_subject_user_id 必须填写该消息的 sender_user_id" in prompt
    assert "单聊和群聊都适用" in prompt
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


def test_thread_prompt_requires_dws_doc_read_for_alidocs_links():
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
        include_thread_prompt=True,
    )

    assert 'dws doc read --node "<链接>" --format json' in prompt
    assert "禁止用 curl、HTTP API 或浏览器直接读钉钉在线文档" in prompt
    assert "文档读不到，不能凭感觉回复" in prompt


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
