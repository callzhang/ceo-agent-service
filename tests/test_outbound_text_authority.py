from __future__ import annotations

from app.outbound_text_authority import (
    provider_send_texts,
    unprepared_send_texts,
)


def _shell(command: str) -> dict:
    return {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "exit_code": 0,
            "status": "completed",
            # The runtime logs the command exactly as the shell received it.
            "command": "/bin/zsh -lc '" + command + "'",
            "aggregated_output": '{"success":true}',
        },
    }


# The live send from Audit run 20020: the body was composed by the turn and the
# feedback links were transcribed out of its prompt, breaking the encoding.
WAYNE_DM = (
    'dws chat +dm --to "Wayne" --content "审批已补充要求，请补齐报价与利润测算。'
    "反馈：%E5%BE%85%E5%A4%84%E7%90%86%E，尚\" --yes"
)


def test_a_composed_body_is_not_authorized() -> None:
    sent = provider_send_texts([_shell(WAYNE_DM)])

    assert sent and "%E5%BE%85" in sent[0]
    assert unprepared_send_texts(sent, ["完全不同的服务文案\n\n反馈：https://x/y"]) == sent


def test_the_prepared_body_passes_verbatim() -> None:
    prepared = "客户合同审批已退回补充。请提供合同正文的可访问链接。\n\n反馈：https://example/f/abc"
    sent = provider_send_texts(
        [_shell(f'dws chat +dm --to "Wayne" --content "{prepared}" --yes')]
    )

    assert sent
    assert unprepared_send_texts(sent, [prepared]) == []


def test_provider_trimming_still_counts_as_the_prepared_body() -> None:
    prepared = "宋述，你的续签申请需要补充合同正文。\n\n反馈：https://example/f/abc"
    sent = ["宋述，你的续签申请需要补充合同正文。\n\n反馈：https://example/f/abc  "]

    assert unprepared_send_texts(sent, [prepared]) == []


def test_reading_and_help_are_not_sends() -> None:
    events = [
        _shell("dws chat +messages-send --help"),
        _shell('dws chat +dm --to "X" --content "hi" --dry-run'),
        _shell("dws oa approval detail --instance-id abc --format json"),
    ]

    assert provider_send_texts(events) == []


def test_every_send_spelling_is_read() -> None:
    events = [
        _shell('dws chat +dm --to "A" --content "one" --yes'),
        _shell('dws chat +send-to-group --group "cid-1" --content "two"'),
        _shell('dws chat +messages-send --group "cid-1" --text "three"'),
        _shell('dws chat message send --open-dingtalk-id "id" --content "four"'),
    ]

    assert provider_send_texts(events) == ["one", "two", "three", "four"]


def test_a_piped_send_is_still_a_send() -> None:
    events = [_shell('dws chat +dm --to "A" --content "five" --yes 2>&1 | head -20')]

    assert provider_send_texts(events) == ["five"]
