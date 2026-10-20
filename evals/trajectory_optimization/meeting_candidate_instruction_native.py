"""Compare repeated and shared candidate-audience instructions on fictional meetings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.business_skills import codex_skill_config_override
from app.meeting_alignment_agent import build_meeting_alignment_prompt
from meeting_skill_scope_native import _cases, _checks, _materials, _run, MODEL


RULE = (
    "Before selecting effects, verify live full relevant discussion, full group roster, "
    "and each member's actual authorization for this exact content. Layoff and workforce "
    "reduction plans are sensitive even without names or counts. Business relevance, "
    "matching attendees, member precision and titles never establish permission. "
    "Candidate excerpts and prior deliveries "
    "do not verify the audience. Denied history is unresolved access evidence, not empty "
    "discussion, no-group evidence, or permission to send; never use a self fallback."
)
GROUP_HEADER = "实时钉钉群搜索候选（仅供进一步核验，不是直接发送授权）：\n"


def _groups(selected: dict[str, object]) -> list[dict[str, object]]:
    distractors = []
    for index in range(15):
        distractors.append({
            "conversation_id": f"synthetic-unrelated-{index}",
            "title": f"虚构的无关讨论群 {index}",
            "member_count": 2,
            "member_open_dingtalk_ids": [f"outside-{index}", "outside-other"],
            "member_roster_complete": True,
            "reference_audience_complete": True,
            "history_read_status": "read",
            "member_precision": "0/2",
            "topic_discussion_evidence": [{
                "create_time": "2026-07-13T09:00:00+08:00",
                "text": "近期只讨论无关的办公用品采购。",
                "shared_topic_terms": 0,
                "topic_match_score": 0.0,
            }],
        })
    return [*distractors[:7], selected, *distractors[7:]]


def _prompt(source, groups: list[dict[str, object]], protocol: str, *, repeated: bool) -> str:
    supplied = (
        [{**group, "audience_verification_required": RULE} for group in groups]
        if repeated else groups
    )
    prompt = build_meeting_alignment_prompt(
        source, work_profile="", work_profile_source="",
        group_candidates=supplied,
        consumer_prompt="只依据本次虚构会议和给定的虚构证据判断。",
        skill_protocol=protocol,
    )
    if repeated:
        prompt = prompt.replace(GROUP_HEADER + RULE + "\n", GROUP_HEADER, 1)
    return prompt


def _candidate_facts(prompt: str) -> list[dict[str, object]]:
    candidate_block = prompt.split(GROUP_HEADER, 1)[1].split(
        "\n\n完整会议来源 JSON：", 1
    )[0]
    candidate_block = candidate_block.removeprefix(RULE + "\n")
    return [
        {key: value for key, value in group.items() if key != "audience_verification_required"}
        for group in json.loads(candidate_block)
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    root = Path(__file__).resolve().parents[2]
    materials, hashes = _materials(Path.home() / ".agents" / "skills")
    schema = root / "app/schemas/meeting_alignment_decision.schema.json"
    config = codex_skill_config_override(())
    results = []
    with tempfile.TemporaryDirectory(prefix="meeting-candidate-rule-", dir="/tmp") as directory:
        for name, source, selected_groups in _cases():
            if name not in {"business_alignment", "mixed_sensitive"}:
                continue
            groups = _groups(selected_groups[0])
            prompts = {
                "baseline": _prompt(source, groups, materials[0], repeated=True),
                "candidate": _prompt(source, groups, materials[0], repeated=False),
            }
            assert prompts["baseline"].count(RULE) == len(groups)
            assert prompts["candidate"].count(RULE) == 1
            assert _candidate_facts(prompts["baseline"]) == groups
            assert _candidate_facts(prompts["candidate"]) == groups
            for repetition in range(1, args.repetitions + 1):
                for arm, prompt in prompts.items():
                    result = _run(prompt, directory=directory, config=config, schema=schema)
                    decision = result.pop("decision")
                    checks = _checks(name, json_decision(decision)) if decision else {}
                    result.update(
                        case=name, repetition=repetition, arm=arm,
                        candidate_count=len(groups), checks=checks,
                        accepted=(result["exit_code"] == 0 and result["pydantic_valid"]
                                  and result["tool_calls"] == 0 and all(checks.values())),
                        decision=decision,
                    )
                    results.append(result)
                    print(json.dumps({
                        "case": name, "repetition": repetition, "arm": arm,
                        "accepted": result["accepted"], "checks": checks,
                    }, ensure_ascii=False), flush=True)
    args.output.write_text(json.dumps({
        "model": MODEL,
        "scope": "Anonymous read-only Codex OAuth, fictional meeting and groups, no business MCP or external action",
        "skill_sha256": hashes["ceo-meeting-work"],
        "repetitions": args.repetitions,
        "baseline_accepted": sum(
            bool(item["accepted"]) for item in results if item["arm"] == "baseline"
        ),
        "candidate_accepted": sum(
            bool(item["accepted"]) for item in results if item["arm"] == "candidate"
        ),
        "results": results,
    }, ensure_ascii=False, indent=2) + "\n")


def json_decision(value: dict[str, object]):
    from app.meeting_alignment_agent import parse_meeting_alignment_decision

    return parse_meeting_alignment_decision(json.dumps(value, ensure_ascii=False))


if __name__ == "__main__":
    main()
