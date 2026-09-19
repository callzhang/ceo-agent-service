"""OA approval decision rules, as checks the gate can run.

The approval rules live in the `dingtalk-oa-approval` Skill, where Derek can
change them without touching code.  Three of them are not safe to leave as
Skill text, because a turn that ignores them has already changed something
irreversible in DingTalk by the time anyone reads its summary:

- A turn scored itself `risk=high`, `rule_coverage=0.97` -- below its own
  band -- and approved a labour-contract renewal anyway.
- A contract was rejected outright with a remark reading "请先补齐上述既有事实
  并重新提交审阅", which is a revert written into the wrong command, and
  `revert-activities` was never called in that generation.
- `remark` is optional in DWS's schema, so a decision can carry no reason at
  all and the applicant sees a bare verdict.

The checks are pure: a Consumer candidate and the tool events of the same
generation in, a list of violations out.  They read the decision from the
commands the generation actually ran, never from `capability` / `operation`,
which are free text the model writes (the same action appears under dozens of
spellings).
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

APPROVE_PATH = ("oa", "approval", "approve")
REJECT_PATH = ("oa", "approval", "reject")
REVERT_PATH = ("oa", "approval", "revert-task")
REVERT_ACTIVITIES_PATH = ("oa", "approval", "revert-activities")

# Every decision the applicant sees must carry a reason. DWS marks `remark`
# optional; this does not.
REMARK_REQUIRED_PATHS = (APPROVE_PATH, REJECT_PATH, REVERT_PATH)

# `rc` required to decide at each risk level. `ic` must be 1.0 and
# `confidence` above 0.9 at every level.
RULE_COVERAGE_BANDS = {"low": 0.8, "medium": 0.9, "high": 1.0}


@dataclass(frozen=True)
class OaDecisionViolation:
    code: str
    detail: str


def oa_decision_violations(
    *,
    result: Mapping[str, object] | None,
    tool_events: Sequence[object],
) -> tuple[OaDecisionViolation, ...]:
    """Rules an OA decision broke in this generation, worst first."""
    commands = _decision_commands(tool_events)
    if not commands:
        return ()
    violations: list[OaDecisionViolation] = []
    decided = [argv for argv in commands if _path_of(argv) in (APPROVE_PATH, REJECT_PATH)]

    if decided:
        violations.extend(_score_band_violations(result, decided))
    if any(_path_of(argv) == REJECT_PATH for argv in commands) and not any(
        _path_of(argv) == REVERT_ACTIVITIES_PATH for argv in commands
    ):
        violations.append(
            OaDecisionViolation(
                code="oa_reject_without_checking_revert",
                detail=(
                    "拒绝前必须先调用 `dws oa approval revert-activities` 并在结论里写明返回结果。"
                    "退回是把审批交回给能修的人，拒绝是终结它；要对方补材料就永远是退回。"
                ),
            )
        )
    violations.extend(_missing_remark_violations(commands))
    return tuple(violations)


def _score_band_violations(
    result: Mapping[str, object] | None,
    decided: Sequence[Sequence[str]],
) -> list[OaDecisionViolation]:
    risk = str((result or {}).get("risk") or "").strip().lower()
    band = RULE_COVERAGE_BANDS.get(risk)
    if band is None:
        return [
            OaDecisionViolation(
                code="oa_decision_without_risk_level",
                detail=(
                    "执行同意或拒绝的这一轮没有给出 low/medium/high 的 risk，"
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
    action = " / ".join(sorted({_path_of(argv)[-1] for argv in decided}))
    return [
        OaDecisionViolation(
            code="oa_decision_below_score_band",
            detail=(
                f"本轮执行了 {action}，但分值未达 {risk} 风险档门槛："
                + "；".join(shortfalls)
                + "。分值不达标时应改为退回或 needs_human，不得自行决定。"
            ),
        )
    ]


def _missing_remark_violations(
    commands: Sequence[Sequence[str]],
) -> list[OaDecisionViolation]:
    violations: list[OaDecisionViolation] = []
    for argv in commands:
        path = _path_of(argv)
        if path not in REMARK_REQUIRED_PATHS:
            continue
        if _flag_value(argv, "--remark"):
            continue
        violations.append(
            OaDecisionViolation(
                code="oa_decision_without_remark",
                detail=(
                    f"`{' '.join(path)}` 没有带非空 `--remark`。"
                    "申请人只会看到一个光秃秃的结论；理由必须写清依据的条文或事实，"
                    "以及对方接下来该怎么办。"
                ),
            )
        )
    return violations


def _decision_commands(tool_events: Sequence[object]) -> list[tuple[str, ...]]:
    commands: list[tuple[str, ...]] = []
    watched = (
        APPROVE_PATH,
        REJECT_PATH,
        REVERT_PATH,
        REVERT_ACTIVITIES_PATH,
    )
    for event in tool_events or ():
        for argv in _argv_candidates(event):
            if _path_of(argv) in watched:
                commands.append(tuple(argv))
    return commands


def _argv_candidates(event: object) -> list[tuple[str, ...]]:
    """Every provider argv this event ran, whether by shell or reviewed tool."""
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
    # argument; a piped command carries several. Look at every stage rather than
    # only the first, so a decision hidden behind `&&` is still seen.
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
    binary = parts[0].rsplit("/", 1)[-1]
    if binary != "dws":
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
