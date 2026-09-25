"""An Agent reads the page an unsubscribe link lands on and says what it means.

Keyword rules could not tell a site's "Sign in" navigation from a login wall,
nor read a confirmation that a script paints after the page shell, so a
completed unsubscribe was recorded as "login required" (Derek, 2026-09-25:
the Agent should judge, and the page's own words go in the record).

The service still does every mechanical step: it opens the link, waits for the
page text to stop changing, operates a control and captures the text. This
module only turns "here is the page" into one of a few states plus the words
that led there. The link itself carries a token, so the Agent gets the host,
never the address.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent_runtime_router import (
    CodexCommandFactory,
    RoutedCodexExecution,
    RoutedResultCodec,
)
from app.email_unsubscribe import UnsubscribePageJudgement, UnsubscribePageState

#: How much of the page the Agent reads. Confirmations sit near the top of what
#: a page renders; the rest is navigation and footers.
MAX_PAGE_TEXT_CHARS = 6000
MAX_EVIDENCE_CHARS = 400

_RESULT_CODEC = RoutedResultCodec.text(schema_id="email.unsubscribe-page-judgement.v1")

_DEVELOPER_INSTRUCTIONS = (
    "You judge one web page reached from an email's unsubscribe link. Return "
    "exactly one JSON object {\"state\": ..., \"evidence\": ...} and perform no "
    "action."
)

_PROMPT = """The page below is what an email's unsubscribe link opened. Decide what the page says about THIS unsubscribe request.

state, exactly one of:
- done: the page confirms the recipient is now unsubscribed or removed (for example "You've been unsubscribed", "You will no longer receive ...").
- already_unsubscribed: the page says the recipient was already unsubscribed, or the subscription is off.
- login_required: the page cannot be used without signing in and offers no other way to unsubscribe. A "Sign in" or "Log in" link in the site's navigation or header is NOT this: judge what the page does about the unsubscribe.
- captcha: the page asks the recipient to solve a captcha.
- payment: the page asks for payment or card details.
- expired: the link is invalid, expired, or the subscription cannot be found.
- action_required: the page still needs the recipient to do something to unsubscribe, and controls are listed below.
- unknown: you cannot tell.

evidence: the sentence or sentences of the page text that decided it, copied as written, at most {max_evidence} characters. Navigation menus and footers are not evidence.

Host: {host}
Controls the service can operate: {controls}

Page text:
{text}
"""


class UnsubscribePageJudgementResult(BaseModel):
    """The only accepted output of the page-judging Agent."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    state: Literal[
        "done",
        "already_unsubscribed",
        "login_required",
        "captcha",
        "payment",
        "expired",
        "action_required",
        "unknown",
    ]
    evidence: str = Field(min_length=1)

    @field_validator("evidence")
    @classmethod
    def evidence_is_short_and_nonblank(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("evidence must be nonblank")
        return value[:MAX_EVIDENCE_CHARS]


#: What each state means to the browser. `expired` and `unknown` decide nothing
#: about the page itself, so the browser's own "nothing to operate" path takes
#: them and records the page text.
_STATES: Mapping[str, UnsubscribePageState | None] = {
    "done": UnsubscribePageState.DONE,
    "already_unsubscribed": UnsubscribePageState.ALREADY_UNSUBSCRIBED,
    "login_required": UnsubscribePageState.LOGIN_REQUIRED,
    "captcha": UnsubscribePageState.CAPTCHA,
    "payment": UnsubscribePageState.PAYMENT,
    "action_required": UnsubscribePageState.ACTION_REQUIRED,
    "expired": None,
    "unknown": None,
}


def parse_page_judgement(raw: str) -> UnsubscribePageJudgementResult:
    """Read the object out of a reply that may wrap it in prose or a code fence."""

    candidates = [raw.strip()]
    for line in raw.splitlines():
        try:
            payload = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping):
            for key in ("text", "output_text"):
                if isinstance(payload.get(key), str):
                    candidates.append(payload[key])
            item = payload.get("item")
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                candidates.append(item["text"])
    for candidate in reversed(candidates):
        variants = [candidate.strip()]
        if "```" in candidate:
            for block in candidate.split("```")[1::2]:
                stripped = block.strip()
                variants.append(
                    stripped[4:].lstrip()
                    if stripped.casefold().startswith("json")
                    else stripped
                )
        start, end = candidate.find("{"), candidate.rfind("}")
        if 0 <= start < end:
            variants.append(candidate[start : end + 1])
        for variant in variants:
            try:
                return UnsubscribePageJudgementResult.model_validate_json(variant)
            except ValueError:
                continue
    raise ValueError("Agent did not return an unsubscribe page judgement")


def build_prompt(host: str, text: str, controls: Sequence[str]) -> str:
    return _PROMPT.format(
        max_evidence=MAX_EVIDENCE_CHARS,
        host=host or "unknown",
        controls=", ".join(controls) if controls else "none",
        text=text[:MAX_PAGE_TEXT_CHARS],
    )


def build_page_judge(
    routed_execution: RoutedCodexExecution,
) -> Callable[[str, str, str, Sequence[str]], UnsubscribePageJudgement]:
    """The browser's `page_judge`: (action identity, host, page text, controls)."""

    def judge(
        action_identity: str,
        host: str,
        text: str,
        controls: Sequence[str],
    ) -> UnsubscribePageJudgement:
        page_digest = sha256(text.encode("utf-8")).hexdigest()
        result = routed_execution.execute(
            workload_kind="email_unsubscribe_page",
            workload_key=f"email-unsubscribe-page:{action_identity}:{page_digest}",
            prompt=build_prompt(host, text, controls),
            command_factory=CodexCommandFactory.standard(
                developer_instructions=_DEVELOPER_INSTRUCTIONS,
                use_output_schema=False,
            ),
            parser=lambda raw: parse_page_judgement(raw).model_dump_json(),
            result_codec=_RESULT_CODEC,
            conversation_id=None,
            required_capabilities=frozenset({"structured_output"}),
        )
        judgement = UnsubscribePageJudgementResult.model_validate_json(result.value)
        return UnsubscribePageJudgement(
            state=_STATES[judgement.state],
            evidence=judgement.evidence,
        )

    return judge
