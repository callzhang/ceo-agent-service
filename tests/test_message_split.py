from app.message_split import split_dingtalk_text


def test_split_dingtalk_text_keeps_short_message_unchanged():
    assert split_dingtalk_text("收到\n\n继续推进", limit=100) == ["收到\n\n继续推进"]


def test_split_dingtalk_text_splits_on_paragraphs_and_adds_chunk_numbers():
    text = "第一段" * 10 + "\n\n" + "第二段" * 10 + "\n\n" + "第三段" * 10

    chunks = split_dingtalk_text(text, limit=35)

    assert len(chunks) > 1
    assert chunks[0].startswith("【1/")
    assert chunks[-1].startswith(f"【{len(chunks)}/{len(chunks)}】")
    assert all(len(chunk) <= 35 for chunk in chunks)
    assert "第一段" in chunks[0]
    assert _join_chunk_bodies(chunks) == text.replace("\n\n", "")


def test_split_dingtalk_text_splits_oversized_line():
    chunks = split_dingtalk_text("A" * 90, limit=40)

    assert len(chunks) == 3
    assert all(len(chunk) <= 40 for chunk in chunks)
    assert _join_chunk_bodies(chunks) == "A" * 90


def _join_chunk_bodies(chunks: list[str]) -> str:
    return "".join(chunk.split("\n", 1)[1] for chunk in chunks)
