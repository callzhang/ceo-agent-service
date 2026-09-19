from __future__ import annotations

from app.outbound_text_authority import (
    provider_send_texts,
    shell_send_commands,
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


def _mcp(argv: list[str]) -> dict:
    return {
        "type": "item.completed",
        "item": {"type": "mcp_tool_call", "arguments": {"argv": argv}},
    }


def test_every_send_spelling_is_caught_by_shape_not_by_a_list() -> None:
    """Turns invent command names, and an invented send still reaches a person.

    Over thirty days production turns ran `chat send`, `im send`,
    `dingtalk send-to-user` and a dozen other names that are not real
    commands. A rule keyed to the spellings we already know sees none of them.
    """
    for command in (
        "dws chat +dm --to A --content x",
        "dws chat +messages-send --group g --text x",
        "dws chat +send-to-group --group g --content x",
        "dws chat +messages-reply --text x",
        "dws chat message send --content x",
        "dws chat send --content x",
        "dws im send --content x",
        "dws dingtalk send-to-user --content x",
        "dws mail message reply --body x",
    ):
        assert shell_send_commands([_shell(command)]), command


def test_reading_what_a_send_did_is_not_a_send() -> None:
    for command in (
        "dws chat +messages-query-send-status --id 1",
        "dws chat message query-send-status --id 1",
        "dws chat +messages-send-status --id 1",
        "dws schema chat +messages-send json",
        "dws shortcut schema chat +messages-reply",
        "dws chat +dm --help",
        "dws chat +dm --to A --content x --dry-run",
        "dws oa approval approve --instance-id 1 --remark ok",
        "dws chat +messages-add-emoji --id 1",
        "dws doc +comment-create --text x",
    ):
        assert shell_send_commands([_shell(command)]) == [], command


def test_the_reviewed_tool_is_the_sanctioned_path_and_is_not_flagged() -> None:
    """Only a shell send is the turn going around the service.

    The reviewed tool carries the same argv and is how a send is supposed to
    happen, so flagging it would leave the turn nowhere to go.
    """
    assert shell_send_commands([_mcp(["dws", "chat", "+dm", "--to", "A", "--content", "x"])]) == []
