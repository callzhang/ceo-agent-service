import pytest

from app.feishu.payloads import (
    FeishuReplyPayload,
    choose_reply_payload,
    delivery_chunk_idempotency_key,
    delivery_chunk_plan_sha256,
    split_reply_payload,
)


def test_plain_short_reply_stays_text_and_hash_is_stable():
    first = choose_reply_payload(" 收到，我下午给结论。 ")
    second = choose_reply_payload("收到，我下午给结论。")
    assert first.kind == "text"
    assert first.text == "收到，我下午给结论。"
    assert first.canonical_json() == second.canonical_json()
    assert first.sha256() == second.sha256()


@pytest.mark.parametrize(
    "text",
    [
        "# 标题\n正文",
        "- 第一项\n- 第二项",
        "[官方文档](https://open.larksuite.com)",
        "```text\nresult\n```",
        "x" * 2801,
    ],
)
def test_markdown_and_long_reply_use_post(text):
    assert choose_reply_payload(text).kind == "post"


def test_mentions_are_structured_deduplicated_and_not_parsed_from_text():
    payload = choose_reply_payload(
        "@Alex 请看一下", trusted_mention_open_ids=("ou_a", "ou_a", "ou_b")
    )
    assert payload.mention_open_ids == ("ou_a", "ou_b")
    assert "Alex" in payload.text

    untrusted = choose_reply_payload("@ou_attacker 请看一下")
    assert untrusted.mention_open_ids == ()


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("text", '<at user_id="ou_attacker">Attacker</at> ping'),
        ("post", '# 标题\n<AT\topen_id="ou_attacker">Attacker</AT>'),
        (
            "text",
            '&lt;at user_id="ou_attacker"&gt;Attacker&lt;/at&gt;',
        ),
        (
            "post",
            '# 标题\n&#60;at\nuser_id="ou_attacker"&#62;Attacker&#60;/at&#62;',
        ),
        ("text", '\\<at user_id="ou_attacker">Attacker</at>'),
    ],
)
def test_untrusted_at_markup_is_rejected_before_approval_hash(kind, text):
    with pytest.raises(ValueError, match="untrusted at markup"):
        FeishuReplyPayload(kind=kind, text=text)
    with pytest.raises(ValueError, match="untrusted at markup"):
        choose_reply_payload(text)


def test_at_like_normal_text_is_preserved_without_creating_mentions():
    text = "讨论 <atlas> 标签、@ou_example 和 &lt;atom&gt; 文本。"

    payload = choose_reply_payload(text)

    assert payload.text == text
    assert payload.mention_open_ids == ()


@pytest.mark.parametrize("open_id", ["", "u_123", " ou_a", "ou_a\n"])
def test_invalid_mention_identity_is_rejected(open_id):
    with pytest.raises(ValueError, match="validated open_id"):
        choose_reply_payload("reply", trusted_mention_open_ids=(open_id,))


def test_sdk_json_and_over_limit_content_fail_closed():
    with pytest.raises(ValueError, match="extra"):
        FeishuReplyPayload.model_validate(
            {
                "kind": "text",
                "text": "hello",
                "receive_id": "oc_attacker",
            }
        )

    with pytest.raises(ValueError, match="post payload exceeds"):
        FeishuReplyPayload(kind="post", text="中" * 11_000)


def test_above_post_limit_remains_supported_as_deterministic_text_chunks():
    text = ("段落内容\n" * 7000).strip()
    payload = choose_reply_payload(text)
    chunks = split_reply_payload(payload)

    assert payload.kind == "text"
    assert "".join(chunks) == text
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 3500 for chunk in chunks)
    plan_hash = delivery_chunk_plan_sha256(chunks)
    keys = [
        delivery_chunk_idempotency_key(
            delivery_key="delivery-stable-key",
            ordinal=ordinal,
            expected_chunks=len(chunks),
            chunk_plan_sha256=plan_hash,
            payload_sha256=payload.sha256(),
        )
        for ordinal in range(len(chunks))
    ]
    assert len(keys) == len(set(keys))
    assert all(len(key) <= 50 for key in keys)

    boundary = FeishuReplyPayload(kind="text", text="x" * 3500 + "\nrest")
    boundary_chunks = split_reply_payload(boundary)
    assert "".join(boundary_chunks) == boundary.text
    assert all(len(chunk) <= 3500 for chunk in boundary_chunks)


@pytest.mark.parametrize(
    "text",
    [
        "```python\n" + ("print('safe')\n" * 400) + "```",
        "# Links\n" + ("[official](https://open.larksuite.com) " * 120),
    ],
)
def test_markdown_that_requires_multiple_wire_chunks_downgrades_to_text(text):
    payload = choose_reply_payload(text)
    chunks = split_reply_payload(payload)

    assert len(text.strip()) > 3500
    assert payload.kind == "text"
    assert len(chunks) > 1
    assert "".join(chunks) == payload.text == text.strip()


def test_chunk_plan_and_provider_uuid_bind_boundaries_and_full_payload():
    first_plan = ("ab", "c")
    second_plan = ("a", "bc")
    first_plan_hash = delivery_chunk_plan_sha256(first_plan)
    second_plan_hash = delivery_chunk_plan_sha256(second_plan)
    assert len(first_plan) == len(second_plan)
    assert "".join(first_plan) == "".join(second_plan)
    assert first_plan_hash != second_plan_hash

    text_payload = FeishuReplyPayload(kind="text", text="hello")
    post_payload = FeishuReplyPayload(kind="post", text="hello")
    plan_hash = delivery_chunk_plan_sha256(("hello",))
    text_key = delivery_chunk_idempotency_key(
        delivery_key="stable-delivery",
        ordinal=0,
        expected_chunks=1,
        chunk_plan_sha256=plan_hash,
        payload_sha256=text_payload.sha256(),
    )
    post_key = delivery_chunk_idempotency_key(
        delivery_key="stable-delivery",
        ordinal=0,
        expected_chunks=1,
        chunk_plan_sha256=plan_hash,
        payload_sha256=post_payload.sha256(),
    )
    assert text_key != post_key


def test_explicit_post_cannot_create_a_multi_chunk_wire_plan():
    payload = FeishuReplyPayload(kind="post", text="x" * 3501)
    with pytest.raises(ValueError, match="must use text format"):
        split_reply_payload(payload)
