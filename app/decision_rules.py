"""Rules every irreversible decision must satisfy, whatever the domain.

Three things went wrong on OA approvals, and none of them is about approvals:

- a turn scored itself `risk=high` with `rule_coverage=0.97` -- below its own
  band -- and took the decision anyway;
- a contract was terminated with a reason reading "请先补齐…并重新提交审阅",
  which is the reversible action written into the terminating command, and the
  reversible one was never even looked up;
- the provider marks the reason field optional, so a decision can reach the
  other party carrying no reason at all.

Each of those is a property of *deciding something for someone else*, so the
logic here is domain-neutral and the domain knowledge is data: a registry says
which commands decide, which of them end the matter, which flag carries the
reason, and what the reversible alternative is. A domain is onboarded by
adding rows, not by adding checks.

The checks read what the generation actually ran. They never read `capability`
or `operation`, which are free text the model writes -- the same action
appears under dozens of spellings.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

# `rule_coverage` required to decide at each risk level. `ic` must be 1.0 and
# `confidence` above 0.9 at every level. There is no category that may never be
# decided automatically (Derek, 2026-09-17). The bands were 0.8 / 0.9 / 1.0;
# on 2026-09-23 Derek set them to follow the generic OA Skill's decision table,
# which requires a fully covered rule at every risk level.
RULE_COVERAGE_BANDS = {"low": 1.0, "medium": 1.0, "high": 1.0}


class DecisionTier(StrEnum):
    """How far an action reaches, which is what decides the rules it answers to."""

    #: Ends the matter for the other party, who cannot reopen it themselves.
    TERMINAL = "terminal"
    #: Reaches a real person but they can respond or it can be undone.
    REACHES_PERSON = "reaches_person"
    #: Touches no external party.
    INTERNAL = "internal"


@dataclass(frozen=True)
class DecisionAction:
    """One command, and how far it reaches."""

    path: tuple[str, ...]
    tier: DecisionTier
    #: Flag that must carry a human-readable reason, "" when the command has none.
    reason_flag: str = ""
    #: Read that establishes whether a reversible route exists. A terminal
    #: action must not be taken without consulting it.
    reversible_check: tuple[str, ...] | None = None
    #: What that reversible route is, for the correction text.
    reversible_name: str = ""


def _terminal(
    *path: str,
    reason_flag: str = "",
    reversible_check: tuple[str, ...] | None = None,
    reversible_name: str = "",
) -> DecisionAction:
    return DecisionAction(
        path=path,
        tier=DecisionTier.TERMINAL,
        reason_flag=reason_flag,
        reversible_check=reversible_check,
        reversible_name=reversible_name,
    )


def _reaches_person(*path: str, reason_flag: str = "") -> DecisionAction:
    return DecisionAction(
        path=path, tier=DecisionTier.REACHES_PERSON, reason_flag=reason_flag
    )


def _internal(*path: str) -> DecisionAction:
    return DecisionAction(path=path, tier=DecisionTier.INTERNAL)


DECISION_ACTIONS: tuple[DecisionAction, ...] = (
    # Terminal: the other party cannot reopen these.
    _terminal(
        "oa", "approval", "approve",
        reason_flag="--remark",
    ),
    _terminal(
        "oa", "approval", "reject",
        reason_flag="--remark",
        reversible_check=("oa", "approval", "revert-activities"),
        reversible_name="退回（revert-task）",
    ),
    _terminal(
        # Withdrawing an approval ends it for everyone waiting on it. Not seen
        # in thirty days of production, which is exactly why it is classified
        # now rather than after it first runs.
        "oa", "approval", "revoke",
        reason_flag="--remark",
    ),
    _terminal(
        # Handing the task to someone else ends the principal's part of it and
        # the applicant cannot undo the reassignment.
        "oa", "approval", "redirect-task",
        reason_flag="--remark",
    ),
    _terminal("calendar", "event", "respond"),
    _terminal("todo", "task", "delete"),
    # Reaches a person, but answerable or undoable. The prepared-text rule
    # governs these; they do not answer to the score band.
    _reaches_person("chat", "+messages-reply"),
    _reaches_person("chat", "+messages-send"),
    _reaches_person("chat", "+send-to-group"),
    _reaches_person("chat", "+dm"),
    _reaches_person("chat", "message", "send"),
    _reaches_person("chat", "message", "reply"),
    _reaches_person("chat", "message", "edit"),
    _reaches_person("chat", "message", "recall"),
    _reaches_person("oa", "approval", "oa-comments"),
    _reaches_person("oa", "approval", "comment"),
    # A revert carries the gap the applicant has to close; without it they
    # are handed the matter back with no idea what to fix.
    _reaches_person("oa", "approval", "revert-task", reason_flag="--remark"),
    _reaches_person("doc", "+comment-create"),
    _reaches_person("doc", "+comment-reply"),
    _reaches_person("doc", "+comment-update"),
    _reaches_person("doc", "+comment-delete"),
    _reaches_person("doc", "+update"),
    _reaches_person("mail", "message", "reply"),
    _reaches_person("todo", "+create"),
    _reaches_person("todo", "task", "create"),
    _reaches_person("todo", "task", "update"),
    _reaches_person("todo", "+remind"),
    _reaches_person("calendar", "event", "update"),
    # Touches no external party.
    _internal("doc", "+move"),
    _internal("chat", "message", "reaction", "add"),
    _internal("chat", "+messages-add-emoji"),
    _internal("chat", "message", "add-emoji"),
)

DECISION_ACTION_BY_PATH = {action.path: action for action in DECISION_ACTIONS}

_READ_PATHS: tuple[tuple[str, ...], ...] = tuple(
    action.reversible_check
    for action in DECISION_ACTIONS
    if action.reversible_check is not None
)


@dataclass(frozen=True)
class DecisionViolation:
    code: str
    detail: str


def decision_violations(
    *,
    result: Mapping[str, object] | None,
    tool_events: Sequence[object],
) -> tuple[DecisionViolation, ...]:
    """Rules a decision in this generation broke, worst first.

    Returns empty immediately when the generation decided nothing, so this
    costs nothing on the turns that only read or only send.
    """
    ran = _commands_run(tool_events)
    # Only a terminal action answers to these rules. A message or a comment
    # reaches a person too, but they can answer it; those are governed by the
    # prepared-text rule, and holding them to a score band would stop the very
    # thing the rules tell a turn to do when its scores are low.
    registered = [(action, argv) for action, argv in ran if action is not None]
    if not registered:
        return ()
    decided = [
        (action, argv)
        for action, argv in registered
        if action.tier is DecisionTier.TERMINAL
    ]
    violations: list[DecisionViolation] = []
    if decided:
        violations.extend(_band_violations(result, [action for action, _ in decided]))
    violations.extend(_reversible_violations(decided, ran))
    # A reason is owed by every action that declares a field for one, whatever
    # its tier: a revert without one hands the matter back with no idea what to
    # fix.
    violations.extend(_reason_violations(registered))
    return tuple(violations)


def _band_violations(
    result: Mapping[str, object] | None,
    actions: Sequence[DecisionAction],
) -> list[DecisionViolation]:
    risk = str((result or {}).get("risk") or "").strip().lower()
    band = RULE_COVERAGE_BANDS.get(risk)
    if band is None:
        return [
            DecisionViolation(
                code="decision_without_risk_level",
                detail=(
                    "这一轮执行了决策动作，却没有给出 low/medium/high 的 risk，"
                    "无法判断它该满足哪一档门槛。"
                ),
            )
        ]
    completeness = _score(result, "information_completeness")
    coverage = _score(result, "rule_coverage")
    confidence = _score(result, "confidence")
    shortfalls: list[str] = []
    if completeness is None or completeness < 1.0:
        shortfalls.append(f"information_completeness={_shown(completeness)}（要求 1.0）")
    if coverage is None or coverage < band:
        shortfalls.append(f"rule_coverage={_shown(coverage)}（{risk} 风险要求 >= {band}）")
    if confidence is None or confidence <= 0.9:
        shortfalls.append(f"confidence={_shown(confidence)}（要求 > 0.9）")
    if not shortfalls:
        return []
    names = " / ".join(sorted({" ".join(action.path) for action in actions}))
    return [
        DecisionViolation(
            code="decision_below_score_band",
            detail=(
                f"本轮执行了 {names}，但分值未达 {risk} 风险档门槛："
                + "；".join(shortfalls)
                + "。分值不达标时应改为可逆动作或 needs_human，不得自行决定。"
            ),
        )
    ]


def _reversible_violations(
    decided: Sequence[tuple[DecisionAction, Sequence[str]]],
    ran: Sequence[tuple[DecisionAction | None, Sequence[str]]],
) -> list[DecisionViolation]:
    consulted = {_path_of(argv) for _, argv in ran}
    violations: list[DecisionViolation] = []
    for action, _argv in decided:
        if action.reversible_check is None:
            continue
        if action.reversible_check in consulted:
            continue
        violations.append(
            DecisionViolation(
                code="terminal_action_without_checking_reversible",
                detail=(
                    f"执行 `{' '.join(action.path)}` 之前必须先调用 "
                    f"`{' '.join(action.reversible_check)}`，并在结论里写明返回结果。"
                    f"{action.reversible_name}把事情交回给能修的人，"
                    "终结动作则让对方只能从头再来；要对方补材料就永远选可逆的那个。"
                ),
            )
        )
    return violations


def _reason_violations(
    decided: Sequence[tuple[DecisionAction, Sequence[str]]],
) -> list[DecisionViolation]:
    violations: list[DecisionViolation] = []
    for action, argv in decided:
        if not action.reason_flag or _flag_value(argv, action.reason_flag):
            continue
        violations.append(
            DecisionViolation(
                code="decision_without_reason",
                detail=(
                    f"`{' '.join(action.path)}` 没有带非空 `{action.reason_flag}`。"
                    "对方只会看到一个光秃秃的结论；理由必须写清依据的条文或事实，"
                    "以及对方接下来该怎么办。"
                ),
            )
        )
    return violations


def _commands_run(
    tool_events: Sequence[object],
) -> list[tuple[DecisionAction | None, tuple[str, ...]]]:
    """Every provider command this generation ran, decisions tagged."""
    by_path = {action.path: action for action in DECISION_ACTIONS}
    watched = set(by_path) | set(_READ_PATHS)
    ran: list[tuple[DecisionAction | None, tuple[str, ...]]] = []
    for event in tool_events or ():
        for argv in _argv_candidates(event):
            path = _path_of(argv)
            if path in watched:
                ran.append((by_path.get(path), argv))
    return ran


def _argv_candidates(event: object) -> list[tuple[str, ...]]:
    """Every provider argv this event ran, by shell or by the reviewed tool."""
    if not isinstance(event, dict):
        return []
    item = event.get("item")
    if not isinstance(item, dict):
        return []
    if item.get("type") == "mcp_tool_call":
        arguments = item.get("arguments")
        argv = arguments.get("argv") if isinstance(arguments, dict) else None
        if isinstance(argv, list) and all(isinstance(part, str) for part in argv):
            return [tuple(argv)]
        return []
    if item.get("type") != "command_execution":
        return []
    command = item.get("command")
    if not isinstance(command, str):
        return []
    try:
        parts = shlex.split(command)
    except ValueError:
        return []
    # A shell wrapper (`/bin/zsh -lc "dws ..."`) carries the real command as one
    # argument, and a chained command carries several stages. Look at every
    # stage, so a decision hidden behind `&&` is still seen.
    candidates: list[tuple[str, ...]] = []
    for part in [command, *parts]:
        if "dws" not in part:
            continue
        try:
            stage_parts = shlex.split(part)
        except ValueError:
            continue
        current: list[str] = []
        for token in stage_parts:
            if token in {"|", "&&", ";", "||"}:
                if current:
                    candidates.append(tuple(current))
                current = []
                continue
            current.append(token)
        if current:
            candidates.append(tuple(current))
    return candidates


def _path_of(argv: Sequence[str]) -> tuple[str, ...]:
    parts = [part for part in argv if not part.startswith("-")]
    if not parts:
        return ()
    if parts[0].rsplit("/", 1)[-1] != "dws":
        return ()
    return tuple(parts[1:4])


def _flag_value(argv: Sequence[str], flag: str) -> str:
    for index, token in enumerate(argv):
        if token == flag:
            following = argv[index + 1] if index + 1 < len(argv) else ""
            return following.strip() if not following.startswith("--") else ""
        if token.startswith(f"{flag}="):
            return token.split("=", 1)[1].strip()
    return ""


def _score(result: Mapping[str, object] | None, field: str) -> float | None:
    raw = (result or {}).get(field)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _shown(value: float | None) -> str:
    return "未给出" if value is None else f"{value:g}"
