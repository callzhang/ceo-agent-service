from pathlib import Path

from ceo_agent_service.corpus import (
    CorpusRecord,
    append_records,
    build_dingtalk_records_from_sender_payload,
    build_style_profile,
    count_information_units,
    extract_minutes_records,
    is_conversational_dingtalk_text,
    is_informative_reply,
    is_plain_text_dingtalk_message,
    load_corpus_records,
    retrieve_similar_examples,
)


def test_extract_minutes_records_for_exact_derek_speakers_preserves_context(tmp_path: Path):
    minutes = tmp_path / "meeting.md"
    minutes.write_text(
        """# Transcript
周俊杰
00:01
这个怎么处理？
磊哥
00:05
先把问题收敛清楚，再判断客户价值和投入优先级，不要为了一个临时问题开新口子。
Derek Zen
00:07
这句不应该进入语料。
Derek
00:09
这个方向可以继续，但要先形成可验证的闭环，明确负责人、时间点和交付标准。
""",
        encoding="utf-8",
    )

    records = extract_minutes_records(minutes, source_title="会议")

    assert [record.speaker_name for record in records] == ["磊哥", "Derek"]
    assert [record.derek_reply for record in records] == [
        "先把问题收敛清楚，再判断客户价值和投入优先级，不要为了一个临时问题开新口子。",
        "这个方向可以继续，但要先形成可验证的闭环，明确负责人、时间点和交付标准。",
    ]
    assert records[0].context == "这个怎么处理？"
    assert records[1].context == "这句不应该进入语料。"


def test_append_records_filters_split_person_outputs_and_deduplicates_message_ids(tmp_path: Path):
    csv_path = tmp_path / "corpus.csv"
    records = [
        CorpusRecord(
            source_type="dingtalk",
            source_title="Friday",
            timestamp="2026-05-13 18:00:00",
            context="怎么处理",
            derek_reply="先按客户价值判断优先级，再确认负责人和交付时间，不要只说推进。",
            message_id="msg-1",
            conversation_id="cid-1",
            speaker_name="磊哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="Friday",
            timestamp="2026-05-13 18:01:00",
            context="怎么处理",
            derek_reply="先按客户价值判断优先级，再确认负责人和交付时间，不要只说推进。（by磊哥分身）",
            message_id="msg-2",
            conversation_id="cid-1",
            speaker_name="磊哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="Friday",
            timestamp="2026-05-13 18:02:00",
            context="重复消息",
            derek_reply="不要重复写这条消息，语料里同一个消息 ID 只能保留一次，避免训练口气时放大同一观点。",
            message_id="msg-1",
            conversation_id="cid-1",
            speaker_name="磊哥",
            metadata_json="{}",
        ),
    ]

    append_records(csv_path, records)
    append_records(csv_path, records)

    content = csv_path.read_text(encoding="utf-8")
    assert content.count("msg-1") == 1
    assert "msg-2" not in content
    assert "不要重复写" not in content


def test_append_records_filters_short_low_information_replies(tmp_path: Path):
    csv_path = tmp_path / "corpus.csv"
    records = [
        CorpusRecord(
            source_type="dingtalk",
            source_title="Friday",
            timestamp="2026-05-13 18:00:00",
            context="怎么处理",
            derek_reply="嗯，好，拜。",
            message_id="msg-short",
            conversation_id="cid-1",
            speaker_name="磊哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="Friday",
            timestamp="2026-05-13 18:01:00",
            context="怎么处理",
            derek_reply="先确认这个事情的业务价值和客户影响，再决定是不是今天必须投入研发资源。",
            message_id="msg-long",
            conversation_id="cid-1",
            speaker_name="磊哥",
            metadata_json="{}",
        ),
    ]

    append_records(csv_path, records)

    content = csv_path.read_text(encoding="utf-8")
    assert "msg-short" not in content
    assert "msg-long" in content


def test_append_records_writes_header_and_deduplicates_when_csv_exists_empty(tmp_path: Path):
    csv_path = tmp_path / "corpus.csv"
    csv_path.touch()
    record = CorpusRecord(
        source_type="dingtalk",
        source_title="Friday",
        timestamp="2026-05-13 18:00:00",
        context="怎么处理",
        derek_reply="先确认业务价值，再确定负责人、交付时间和验收标准。",
        message_id="msg-1",
        conversation_id="cid-1",
        speaker_name="磊哥",
        metadata_json="{}",
    )

    append_records(csv_path, [record])
    append_records(csv_path, [record])

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("source_type,source_title,timestamp,context,derek_reply,message_id")
    assert lines[1].count("msg-1") == 1
    assert csv_path.read_text(encoding="utf-8").count("msg-1") == 1


def test_load_corpus_records_reads_informative_records(tmp_path: Path):
    csv_path = tmp_path / "corpus.csv"
    append_records(
        csv_path,
        [
            CorpusRecord(
                source_type="dingtalk",
                source_title="Friday",
                timestamp="2026-05-13",
                context="排期怎么处理",
                derek_reply="先定优先级，再确认谁负责、什么时候交付、怎么验收。",
                message_id="msg-1",
                conversation_id="cid-1",
                speaker_name="磊哥",
                metadata_json="{}",
            )
        ],
    )

    records = load_corpus_records(csv_path)

    assert len(records) == 1
    assert records[0].message_id == "msg-1"


def test_build_style_profile_mentions_direct_business_tone():
    records = [
        CorpusRecord(
            source_type="dingtalk",
            source_title="Friday",
            timestamp="2026-05-13",
            context="是否继续",
            derek_reply="先判断客户价值，再决定投入，不要为了局部效率牺牲整体优先级。",
            message_id="msg-1",
            conversation_id="cid-1",
            speaker_name="磊哥",
            metadata_json="{}",
        )
    ]

    profile = build_style_profile(records)

    assert "先结论" in profile
    assert "业务" in profile


def test_retrieve_similar_examples_by_highest_character_overlap():
    records = [
        CorpusRecord(
            source_type="dingtalk",
            source_title="Friday",
            timestamp="2026-05-13",
            context="项目排期怎么处理",
            derek_reply="先定优先级，再确认谁负责、什么时候交付、怎么验收。",
            message_id="msg-1",
            conversation_id="cid-1",
            speaker_name="磊哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="HR",
            timestamp="2026-05-13",
            context="候选人怎么样",
            derek_reply="先看岗位匹配，再看过往负责范围和是否真的承担过结果。",
            message_id="msg-2",
            conversation_id="cid-2",
            speaker_name="磊哥",
            metadata_json="{}",
        ),
    ]

    examples = retrieve_similar_examples("这个项目排期要不要改", records, limit=1)

    assert examples[0].message_id == "msg-1"


def test_information_units_ignore_media_links_and_count_chinese_or_words():
    assert count_information_units("嗯，好，拜。") < 20
    assert is_informative_reply("先确认这个事情的业务价值和客户影响，再决定是不是今天必须投入研发资源。")
    assert not is_informative_reply(
        "![图片](https://example.com/very/long/path) [文件] product-logic.md"
    )


def test_system_memory_capture_message_is_not_informative_reply():
    assert not is_informative_reply(
        "AI自动抓取，用于公司记忆，如和工作内容无关或者涉及个人隐私，请拒绝"
    )
    assert not is_informative_reply(
        "AI自动抓取，用于会议纪要整理，如和工作内容无关或者涉及个人隐私，请拒绝\n其它企业"
    )


def test_dingtalk_message_type_filter_requires_text_when_type_is_available():
    assert is_plain_text_dingtalk_message({"content": "[文件] product.md", "msgType": "text"})
    assert not is_plain_text_dingtalk_message({"content": "这是文件", "msgType": "file"})
    assert not is_plain_text_dingtalk_message({"content": "这是图片", "messageType": "image"})
    assert is_plain_text_dingtalk_message({"content": "这是一条纯文本消息", "contentType": "TEXT"})


def test_dingtalk_message_type_filter_falls_back_for_current_dws_payload_shape():
    assert is_plain_text_dingtalk_message({"content": "这是一条纯文本消息"})
    assert not is_plain_text_dingtalk_message({"content": "[文件] product-logic.md"})
    assert not is_plain_text_dingtalk_message({"content": "[日程]"})
    assert not is_plain_text_dingtalk_message({"content": "![图片](@lQLPJwKtm28)"})
    assert not is_plain_text_dingtalk_message(
        {"content": "AI自动抓取，用于公司记忆，如和工作内容无关或者涉及个人隐私，请拒绝"}
    )


def test_conversational_dingtalk_text_filters_pasted_documents_not_short_replies():
    assert is_conversational_dingtalk_text(
        "@张晓民(Xiaomin张晓民) 核心问题是 batch-size=1，这个在服务器上改成8-16就好了。"
    )
    assert not is_conversational_dingtalk_text("已更新文档：2026Q2-三条曲线与部门问题管理汇报.md")
    assert not is_conversational_dingtalk_text(
        "| 类别 | 代表 | 缺口 |\n|---|---|---|\n| Insights | NielsenIQ | 慢 |"
    )
    assert not is_conversational_dingtalk_text(
        "- 第一条\n- 第二条\n- 第三条\n- 第四条"
    )
    assert not is_conversational_dingtalk_text("这是一条很长的粘贴文本" * 30)


def test_build_dingtalk_records_from_sender_payload_filters_and_preserves_quote():
    payload = {
        "result": {
            "conversationMessagesList": [
                {
                    "title": "技术部",
                    "openConversationId": "cid-1",
                    "singleChat": False,
                    "messages": [
                        {
                            "content": "好的",
                            "createTime": "2026-05-14 12:00:00",
                            "openConversationId": "cid-1",
                            "openMessageId": "msg-short",
                            "sender": "磊哥",
                        },
                        {
                            "content": "一个文件消息不应该进入语料，即使内容字段看起来有不少中文。",
                            "createTime": "2026-05-14 12:00:20",
                            "openConversationId": "cid-1",
                            "openMessageId": "msg-file",
                            "sender": "磊哥",
                            "msgType": "file",
                        },
                        {
                            "content": "AI自动抓取，用于公司记忆，如和工作内容无关或者涉及个人隐私，请拒绝",
                            "createTime": "2026-05-14 12:00:30",
                            "openConversationId": "cid-1",
                            "openMessageId": "msg-system",
                            "sender": "磊哥",
                        },
                        {
                            "content": "已更新文档：2026Q2-三条曲线与部门问题管理汇报.md\n最新发现主要有三点。",
                            "createTime": "2026-05-14 12:00:40",
                            "openConversationId": "cid-1",
                            "openMessageId": "msg-doc",
                            "sender": "磊哥",
                        },
                        {
                            "content": "可以纳入，但主题要围绕业务落地、AI 提效和工程实践闭环，不做单纯算法理论分享。",
                            "createTime": "2026-05-14 12:01:00",
                            "openConversationId": "cid-1",
                            "openMessageId": "msg-long",
                            "quotedMessage": {
                                "content": "aijam是否可以把算法大神们纳入进来？",
                                "openMessageId": "quoted-1",
                            },
                            "sender": "磊哥",
                            "senderOpenDingTalkId": "open-1",
                        },
                    ],
                }
            ]
        }
    }

    records = build_dingtalk_records_from_sender_payload(payload, limit=1000)

    assert len(records) == 1
    assert records[0].source_type == "dingtalk"
    assert records[0].source_title == "技术部"
    assert records[0].context == "aijam是否可以把算法大神们纳入进来？"
    assert records[0].message_id == "msg-long"
