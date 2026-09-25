from app.minutes_todo_context import PARAGRAPHS_AFTER, PARAGRAPHS_BEFORE, todo_transcript_excerpts


def _paragraphs(count):
    return [
        {"nickName": f"人{index}", "paragraph": f"第{index}句", "startTime": index * 1000, "endTime": index * 1000 + 900}
        for index in range(count)
    ]


def _todos(*entries):
    return {"result": {"dingtalkTodoList": [dict(entry) for entry in entries]}}


def test_the_excerpt_is_centred_on_the_paragraph_being_spoken_when_the_item_was_extracted():
    paragraphs = _paragraphs(30)
    [excerpt] = todo_transcript_excerpts(_todos({"title": "整理清单", "createdTime": 15_050}), paragraphs)

    assert excerpt["todo"] == "整理清单"
    assert excerpt["lines"][PARAGRAPHS_BEFORE] == "人15：第15句"
    assert len(excerpt["lines"]) == PARAGRAPHS_BEFORE + 1 + PARAGRAPHS_AFTER


def test_the_excerpt_is_cut_short_at_either_end_of_the_transcript():
    paragraphs = _paragraphs(5)
    [first] = todo_transcript_excerpts(_todos({"title": "开头", "createdTime": 100}), paragraphs)
    [last] = todo_transcript_excerpts(_todos({"title": "结尾", "createdTime": 4_500}), paragraphs)

    assert first["lines"][0] == "人0：第0句"
    assert last["lines"][-1] == "人4：第4句"


def test_an_item_with_no_known_position_gets_no_excerpt():
    paragraphs = _paragraphs(5)
    todos = _todos(
        {"title": "没有时间", "createdTime": 0},
        {"title": "缺字段"},
        {"title": "超出录音", "createdTime": 999_999},
        {"createdTime": 1_000, "title": ""},
    )
    assert todo_transcript_excerpts(todos, paragraphs) == []


def test_a_transcript_without_timings_or_a_payload_without_items_yields_nothing():
    assert todo_transcript_excerpts(_todos({"title": "x", "createdTime": 500}), [{"nickName": "a", "paragraph": "b"}]) == []
    assert todo_transcript_excerpts({"result": {"actions": []}}, _paragraphs(3)) == []
    assert todo_transcript_excerpts("not a payload", _paragraphs(3)) == []


def test_speaker_labels_are_kept_exactly_as_dingtalk_gave_them():
    paragraphs = [{"nickName": "发言人 3", "paragraph": "我来做", "startTime": "1000", "endTime": "2000"}]
    [excerpt] = todo_transcript_excerpts(_todos({"title": "t", "createdTime": 1500}), paragraphs)
    assert excerpt["lines"] == ["发言人 3：我来做"]


def test_each_sentence_is_its_own_line_so_it_can_be_quoted_with_its_speaker_label():
    paragraph = {
        "nickName": "Claire", "paragraph": "我们很希望合作。然后请威尔帮忙准备材料。", "startTime": 1000, "endTime": 5000,
        "sentenceList": [{"sentence": "我们很希望合作。"}, {"sentence": "然后请威尔帮忙准备材料。"}, {"sentence": " "}],
    }
    [excerpt] = todo_transcript_excerpts(_todos({"title": "t", "createdTime": 2000}), [paragraph])
    assert excerpt["lines"] == ["Claire：我们很希望合作。", "Claire：然后请威尔帮忙准备材料。"]
