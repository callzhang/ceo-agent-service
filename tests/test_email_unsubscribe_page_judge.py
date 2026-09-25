"""The Agent that reads an unsubscribe page: its contract, prompt and mapping."""

from types import SimpleNamespace

import pytest

from app.email_unsubscribe import UnsubscribePageState
from app.email_unsubscribe_page_judge import (
    MAX_EVIDENCE_CHARS,
    MAX_PAGE_TEXT_CHARS,
    build_page_judge,
    build_prompt,
    parse_page_judgement,
)

ACTION = "email-action:" + "a" * 64


def test_the_reply_may_be_bare_fenced_or_wrapped_in_prose() -> None:
    bare = '{"state": "done", "evidence": "You\'ve been unsubscribed."}'
    fenced = "Here it is:\n```json\n" + bare + "\n```"
    prose = "My judgement: " + bare + " Hope that helps."

    for reply in (bare, fenced, prose):
        parsed = parse_page_judgement(reply)
        assert parsed.state == "done"
        assert parsed.evidence == "You've been unsubscribed."


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "not json at all",
        '{"state": "unsubscribed", "evidence": "x"}',
        '{"state": "done"}',
        '{"state": "done", "evidence": "   "}',
        '{"state": "done", "evidence": "x", "extra": 1}',
    ],
)
def test_anything_but_the_exact_object_is_rejected(reply: str) -> None:
    with pytest.raises(ValueError):
        parse_page_judgement(reply)


def test_long_evidence_is_cut_and_whitespace_collapsed() -> None:
    parsed = parse_page_judgement(
        '{"state": "done", "evidence": "' + ("word  \\n" * 300) + '"}'
    )

    assert len(parsed.evidence) == MAX_EVIDENCE_CHARS
    assert "  " not in parsed.evidence


def test_the_prompt_carries_the_host_and_a_bounded_page_never_the_link() -> None:
    prompt = build_prompt(
        "substack.com", "Sign in " + "x" * (MAX_PAGE_TEXT_CHARS * 2), ["button:unsubscribe"]
    )

    assert "Host: substack.com" in prompt
    assert "button:unsubscribe" in prompt
    assert prompt.count("x") <= MAX_PAGE_TEXT_CHARS
    assert "token" not in prompt.casefold()


class _Routed:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(value=kwargs["parser"](self.reply))


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("done", UnsubscribePageState.DONE),
        ("already_unsubscribed", UnsubscribePageState.ALREADY_UNSUBSCRIBED),
        ("login_required", UnsubscribePageState.LOGIN_REQUIRED),
        ("captcha", UnsubscribePageState.CAPTCHA),
        ("payment", UnsubscribePageState.PAYMENT),
        ("action_required", UnsubscribePageState.ACTION_REQUIRED),
        ("expired", None),
        ("unknown", None),
    ],
)
def test_each_state_maps_to_what_the_browser_understands(state, expected) -> None:
    routed = _Routed('{"state": "%s", "evidence": "what the page says"}' % state)
    judge = build_page_judge(routed)

    judgement = judge(ACTION, "example.com", "Some page", ["button:unsubscribe"])

    assert judgement.state is expected
    assert judgement.evidence == "what the page says"


def test_the_turn_is_keyed_by_the_action_and_the_page_it_reads() -> None:
    routed = _Routed('{"state": "done", "evidence": "ok"}')
    judge = build_page_judge(routed)

    judge(ACTION, "example.com", "first page", [])
    judge(ACTION, "example.com", "second page", [])

    keys = [call["workload_key"] for call in routed.calls]
    assert all(key.startswith(f"email-unsubscribe-page:{ACTION}:") for key in keys)
    assert keys[0] != keys[1]
    assert routed.calls[0]["workload_kind"] == "email_unsubscribe_page"


def test_a_reply_that_is_not_a_judgement_fails_the_turn_instead_of_guessing() -> None:
    routed = _Routed("I think it worked!")
    judge = build_page_judge(routed)

    with pytest.raises(ValueError):
        judge(ACTION, "example.com", "Some page", [])
