"""Compare complete and managed-only Meeting Skill inputs on fictional cases."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.business_skills import codex_skill_config_override
from app.meeting_alignment_agent import (
    build_meeting_alignment_prompt,
    parse_meeting_alignment_decision,
)
from app.meeting_alignment_models import MeetingSource


MODEL = "gpt-5.6-luna"
SKILLS = (
    ("ceo-meeting-work", "Managed", "bded378210a24cedf4fc0464ccd5cecc416bba4291164f66f96f5332f476686e"),
    ("dingtalk-minutes", "Operation", "153cf6e6bcaf3c8af045e001b10457b02d69f7bfd13a3da782189a6e5d952c87"),
    ("dingtalk-calendar", "Operation", "f42f6fe6433656a1ae3dd5ff3f040bfc44e2d6b1bd0ce760aeb2d151b900dd1c"),
)


def _base_source() -> dict[str, object]:
    return {
        "meeting_id": "synthetic-minutes-1",
        "title": "虚构会议",
        "status": "ended",
        "started_at": "2026-07-14T10:00:00+08:00",
        "ended_at": "2026-07-14T11:00:00+08:00",
        "participants": [
            {"name": "Derek", "user_id": "derek"},
            {"name": "Alex", "user_id": "alex"},
            {"name": "Avery", "user_id": "mina"},
        ],
        "attendee_evidence": "calendar",
        "attendee_roster_complete": True,
        "current_user_id": "derek",
        "summary": "",
        "transcript": [],
        "source_url": "https://example.test/synthetic-minutes-1",
    }


def _group(*, sensitive: bool) -> dict[str, object]:
    members = ["derek", "alex", "mina", *( ["external"] if sensitive else [])]
    topic = "客户验收和供应商付款" if sensitive else "上线范围与灰度指标"
    candidate = {
        "conversation_id": "synthetic-group-2" if sensitive else "synthetic-group-1",
        "title": "星桥项目交付组",
        "member_count": len(members),
        "member_open_dingtalk_ids": members,
        "member_roster_complete": True,
        "reference_audience_complete": True,
        "history_read_status": "read",
        "member_precision": f"3/{len(members)}",
        "topic_discussion_evidence": [{
            "create_time": "2026-07-13T09:00:00+08:00",
            "text": f"项目组近期讨论{topic}，由会议参会人承接。",
            "shared_topic_terms": 4,
            "topic_match_score": 1.0,
        }],
        "member_authorization_evidence": [
            {
                "user_id": member,
                "authorized_for": (
                    "项目交付与供应商付款，不含人员计划"
                    if sensitive else "星桥项目业务上线内容"
                ),
            }
            for member in members
        ],
    }
    return candidate


def _cases() -> tuple[tuple[str, MeetingSource, list[dict[str, object]]], ...]:
    personal = _base_source()
    personal.update(
        title="虚构的跨时区个人交流",
        participants=personal["participants"][:2],
        summary="两人确认下次通话按各自时区表达；尚未创建日历日程。",
        transcript=[
            {"speaker_name": "Derek", "speaker_user_id": "derek", "text": "下次按太平洋时间 7 月 20 日 09:00 通话，你在纽约对应 12:00，对吗？"},
            {"speaker_name": "Alex", "speaker_user_id": "alex", "text": "对，纽约当天 12:00；请在总结里写清双方时区，日程以后再建。"},
        ],
    )
    business = _base_source()
    business.update(
        title="虚构星桥项目上线评审",
        summary="Alex 主张全量；Avery 主张灰度；Derek 设定风险预算后，各方明确同意先 10% 灰度。",
        transcript=[
            {"speaker_name": "Alex", "speaker_user_id": "alex", "text": "我建议全量上线，以便尽快验证收入。"},
            {"speaker_name": "Avery", "speaker_user_id": "mina", "text": "我建议先 10% 灰度，故障面更可控。"},
            {"speaker_name": "Derek", "speaker_user_id": "derek", "text": "以可接受故障面为准，先 10% 灰度。"},
            {"speaker_name": "Alex", "speaker_user_id": "alex", "text": "我同意先 10% 灰度，再按指标扩大。"},
            {"speaker_name": "Avery", "speaker_user_id": "mina", "text": "同意，我负责核对故障指标。"},
        ],
    )
    sensitive = _base_source()
    sensitive.update(
        title="虚构星桥项目交付协调",
        summary="确认客户交付时间与负责人；另有人提及裁员计划，群内有未获授权成员，尚无核实可披露的私聊接收人。",
        transcript=[
            {"speaker_name": "Alex", "speaker_user_id": "alex", "text": "客户交付仍按 7 月 25 日推进，我负责验收材料。"},
            {"speaker_name": "Avery", "speaker_user_id": "mina", "text": "我负责供应商付款进度。另有裁员计划，不能向项目群披露。"},
            {"speaker_name": "Derek", "speaker_user_id": "derek", "text": "交付分工就按这个执行。人员事项暂不对项目群或任何未核实对象披露。"},
        ],
    )
    return (
        ("personal_timezone", MeetingSource.model_validate(personal), []),
        ("business_alignment", MeetingSource.model_validate(business), [_group(sensitive=False)]),
        ("mixed_sensitive", MeetingSource.model_validate(sensitive), [_group(sensitive=True)]),
    )


def _materials(root: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    blocks: list[str] = []
    digests: dict[str, str] = {}
    for name, kind, expected_digest in SKILLS:
        body = (root / name / "SKILL.md").read_text()
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if digest != expected_digest:
            raise ValueError(f"frozen Skill changed: {name}")
        digests[name] = digest
        blocks.append(f"## {kind} Skill: {name}\nsha256: {digest}\n\n{body}")
    return tuple(blocks), digests


def _checks(name: str, decision: object) -> dict[str, bool]:
    target = decision.target
    message = decision.final_message
    common = {"send": decision.action == "send", "no_private": decision.sensitive_private_message is None}
    if name == "personal_timezone":
        return {**common, "direct_counterpart": decision.audience_scope == "personal" and target is not None and target.direct_user_id == "alex", "both_timezones": "太平洋" in message and "纽约" in message}
    if name == "business_alignment":
        return {**common, "business_group": decision.audience_scope == "business" and target is not None and target.conversation_id == "synthetic-group-1", "aligned": any(topic.state == "aligned" for topic in decision.topics), "agreed_rollout": "10%" in message, "only_assigned_owners": set(decision.mention_names) == {"Avery"}}
    return {**common, "business_group": decision.audience_scope == "business" and target is not None and target.conversation_id == "synthetic-group-2", "business_assignments": "验收" in message and "付款" in message, "no_sensitive_hint": not any(term in decision.message_title + message for term in ("裁员", "减员", "人员优化", "人员事项"))}


def _run(prompt: str, *, directory: str, config: str | None, schema: Path) -> dict[str, object]:
    command = [
        "codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
        "--skip-git-repo-check", "-C", directory, "-s", "read-only",
        "-m", MODEL,
        *(["-c", config] if config else []),
        "--json", "--output-schema", str(schema), "-",
    ]
    result = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=240)
    events = []
    for line in result.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    messages = [
        event.get("item", {}).get("text", "")
        for event in events
        if event.get("item", {}).get("type") == "agent_message"
    ]
    validation_error = ""
    decision = None
    if messages:
        try:
            decision = parse_meeting_alignment_decision(messages[-1])
        except Exception as exc:
            validation_error = str(exc)[:400]
    return {
        "exit_code": result.returncode,
        "prompt_chars": len(prompt),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "pydantic_valid": decision is not None,
        "validation_error": validation_error,
        "skill_budget_warning": any(
            "skills context budget" in event.get("item", {}).get("message", "")
            for event in events
        ),
        "tool_calls": sum(
            event.get("item", {}).get("type") in {"mcp_tool_call", "function_call"}
            for event in events
        ),
        "decision": decision.model_dump(mode="json") if decision is not None else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    root = Path.home() / ".agents" / "skills"
    materials, hashes = _materials(root)
    candidate_config = codex_skill_config_override(())
    schema = Path(__file__).resolve().parents[2] / "app/schemas/meeting_alignment_decision.schema.json"
    results = []
    with tempfile.TemporaryDirectory(prefix="meeting-skill-scope-", dir="/tmp") as directory:
        for name, source, groups in _cases():
            for repetition in range(1, args.repetitions + 1):
                for arm, protocol, config in (
                    ("full", "\n\n".join(materials), None),
                    ("managed_only", materials[0], candidate_config),
                ):
                    prompt = build_meeting_alignment_prompt(
                        source, work_profile="", work_profile_source="",
                        group_candidates=groups, consumer_prompt="只依据本次虚构会议和给定的虚构证据判断。",
                        skill_protocol=protocol,
                    )
                    result = _run(prompt, directory=directory, config=config, schema=schema)
                    decision = result.pop("decision")
                    result.update(case=name, repetition=repetition, arm=arm, checks=_checks(name, parse_meeting_alignment_decision(json.dumps(decision, ensure_ascii=False))) if decision is not None else {}, decision=decision)
                    results.append(result)
                    print(json.dumps({"case": name, "repetition": repetition, "arm": arm, "checks": result["checks"], "valid": result["pydantic_valid"]}, ensure_ascii=False), flush=True)
    output = {
        "model": MODEL,
        "skill_sha256": hashes,
        "baseline_catalog": "native_default",
        "candidate_catalog_config_sha256": hashlib.sha256(candidate_config.encode("utf-8")).hexdigest(),
        "scope": "Anonymous read-only Codex OAuth; no Work Profile, business MCP or external action",
        "repetitions": args.repetitions,
        "results": results,
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
