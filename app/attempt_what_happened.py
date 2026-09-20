"""The three questions an attempt page has to answer, for every channel.

Derek, 2026-09-20, looking at /attempts/9698: he could not read it. The page
said the item was waiting on his decision with no reply recorded, that the
runtime had returned nothing verifiable four times, and that information
completeness was 86%. All three were wrong. The leave had been approved in
DingTalk and Claire had the notification; nothing was wrong with the runtime,
a gate had refused the result; and the 86% came from the last failing turn,
while the turn that actually decided reported 100%.

So a reader drew three wrong conclusions: nothing happened, the environment
broke, and we decided on incomplete material.

The answers are computed from the generation's own record, not from any one
channel's fields. A calendar response, a WeChat delivery and an email
unsubscribe each either reached the outside world or did not, and each either
stopped on a rule or on the environment. Reading OA's fields specifically
would put us back here the first time another channel goes wrong.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

#: Failure codes that mean the environment did not deliver a result. Anything
#: else that carries a written reason is a rule refusing what the turn
#: returned, which is a different thing and reads differently to a person.
ENVIRONMENT_FAILURE_CODES = frozenset(
    {
        "runtime_provider_unreachable",
        "runtime_route_unavailable",
        "runtime_provider_auth_failed",
        "runtime_capability_missing",
        "codex_total_timeout",
        "codex_idle_timeout",
        "codex_process_failed",
        "codex_provider_overloaded",
        "codex_transport_disconnected",
        "codex_login_required",
        "service_restart_interrupted",
        "service_restart_before_effect",
        "service_dependency_unavailable",
        "runtime_lease_expired",
        "runtime_recovery_lease_expired",
    }
)

_SCORE_FIELDS = ("risk", "confidence", "rule_coverage", "information_completeness")


def _loads(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def external_actions(runs: Sequence[Any], *, store: Any = None) -> list[dict[str, str]]:
    """Every action of this generation that reached the outside world.

    Empty is itself an answer, and a true one: nothing left the service.
    """

    from app.dingtalk_send_evidence import completed_provider_writes
    from app.outbound_text_authority import delivered_shell_send_commands

    found: list[dict[str, str]] = []
    for run in runs:
        events = list(getattr(run, "tool_events", None) or [])
        if not events:
            continue
        at = str(getattr(run, "completed_at", "") or getattr(run, "started_at", "") or "")
        for command in dict.fromkeys(delivered_shell_send_commands(events)):
            found.append({"what": command, "at": at, "recorded_by": "turn"})
        try:
            identifiers = completed_provider_writes(events, store=store)
        except Exception:  # noqa: BLE001 - a page must render without the writer
            identifiers = ()
        for identifier in identifiers:
            if identifier:
                found.append(
                    {"what": str(identifier), "at": at, "recorded_by": "service"}
                )
    return found


def stopped_because(runs: Sequence[Any]) -> dict[str, str]:
    """Where it stopped and why, saying whether a rule or the environment did it.

    The page called both of these "the runtime returned nothing verifiable",
    which is true of one and false of the other.
    """

    for run in reversed(list(runs)):
        if str(getattr(run, "status", "")) != "failed":
            continue
        payload = _loads(getattr(run, "structured_error_json", ""))
        code = str(payload.get("code") or "")
        detail = str(payload.get("detail") or "").strip()
        if code in ENVIRONMENT_FAILURE_CODES:
            return {
                "kind": "environment",
                "code": code,
                "sentence": detail or "运行环境没有返回结果。",
            }
        if detail:
            return {"kind": "rule", "code": code, "sentence": detail}
        return {"kind": "unknown", "code": code, "sentence": ""}
    return {"kind": "none", "code": "", "sentence": ""}


def deciding_scores(runs: Sequence[Any], *, acting_run_id: int | None = None) -> dict[str, Any]:
    """The scores of the turn the action was taken on, not of the last turn.

    Attributing the final run's numbers to the whole attempt reads as "we
    approved on 86% complete material", which is false and is worse than
    showing nothing: on attempt 9698 the turn that acted was working from a
    proposal scored 100% complete, and the 86% belonged to a later revision
    that never reached anyone.

    The action is taken on the proposal standing at that moment, so the scores
    that mattered are the last ones recorded at or before the acting turn.
    """

    ordered = list(runs)
    if acting_run_id is not None:
        ordered = [
            run
            for run in ordered
            if getattr(run, "id", 0) and int(getattr(run, "id")) <= int(acting_run_id)
        ]
    for run in reversed(ordered):
        result = _loads(getattr(run, "final_result_json", ""))
        if not result or not any(field in result for field in _SCORE_FIELDS):
            continue
        scores = {field: result.get(field) for field in _SCORE_FIELDS}
        scores["from_run_id"] = getattr(run, "id", None)
        scores["from_role"] = str(getattr(run, "role", "") or "")
        return scores
    return {}


def acting_run_id(runs: Sequence[Any], *, store: Any = None) -> int | None:
    """The first turn whose work reached the outside world."""

    from app.outbound_text_authority import delivered_shell_send_commands

    from app.dingtalk_send_evidence import completed_provider_writes

    for run in runs:
        events = list(getattr(run, "tool_events", None) or [])
        if not events:
            continue
        if delivered_shell_send_commands(events):
            return int(getattr(run, "id", 0)) or None
        try:
            if any(completed_provider_writes(events, store=store)):
                return int(getattr(run, "id", 0)) or None
        except Exception:  # noqa: BLE001 - a page must render without the writer
            continue
    return None


def build_what_happened(
    runs: Iterable[Any], *, store: Any = None
) -> dict[str, Any]:
    """The three answers, in the order a person needs them."""

    ordered = list(runs)
    actions = external_actions(ordered, store=store)
    stopped = stopped_because(ordered)
    acted_in = acting_run_id(ordered, store=store)
    return {
        "external_actions": actions,
        "reached_the_outside_world": bool(actions),
        "acted_in_run_id": acted_in,
        "stopped_because": stopped,
        "deciding_scores": deciding_scores(ordered, acting_run_id=acted_in),
        # An action already completed is not a question. Only say a person is
        # needed when something is genuinely still undecided.
        "open_for_human": not actions and stopped["kind"] != "none",
    }
