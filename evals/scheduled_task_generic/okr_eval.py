"""Frozen native OKR workflow acceptance; real scripts with synthetic IO."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from app.consumer_purpose import purpose_output_model
from app.action_contract_catalog import system_action_contract_document
from evals.scheduled_task_generic import run
from evals.scheduled_task_generic.okr_fixture import (
    SKILL,
    digest,
    key,
    load_script,
    member_payload,
)

HERE = Path(__file__).parent
CASES_PATH = HERE / "okr_cases.v2.json"


def read_json(path):
    return json.loads(Path(path).read_text())


def calls(work):
    path = Path(work) / "tool-calls.jsonl"
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def _grade(case, work, output, prior=None):
    work = Path(work)
    expected = case["expected"]
    wire = purpose_output_model("general").model_validate(output)
    trace = calls(work)
    if expected.get("must_stop"):
        checks = {
            "technical_failure_not_success": wire.action in expected["allowed_actions"],
            "no_report_or_publication": not any(
                c["tool"] == "consumer_document_write"
                or (
                    c["tool"] == "consumer_run_skill_script"
                    and "render" in c["arguments"].get("arguments", [])
                )
                for c in trace
            ),
            "current_shared_source_failure_observed": any(
                c["tool"] == "read_okr_member"
                and c["result"].get("outcome", {}).get("scope") == "shared"
                for c in trace
            ),
            "no_outbound_proposal": wire.to_proposal() is None,
        }
        return {
            "passed": all(checks.values()),
            "checks": checks,
            "source_outcome": "shared_source_failure",
        }
    required = (
        "source.json",
        "plan.json",
        "manifest.json",
        "full.md",
        "main.md",
        "summary.md",
        "documents.json",
    )
    checks = {"complete_artifacts": all((work / name).is_file() for name in required)}
    if not checks["complete_artifacts"]:
        return {
            "passed": False,
            "checks": checks,
            "missing_artifacts": [n for n in required if not (work / n).is_file()],
        }
    report = load_script("report")
    source, plan, manifest = (
        read_json(work / name) for name in ("source.json", "plan.json", "manifest.json")
    )
    analysis, managers = report._analysis_from_files(source, work)
    cards = report._manager_scorecards(analysis, source["managers"])
    checks.update(
        exact_current_source_payloads=source["managers"] == [
            member_payload(m, case["facts"])
            for m in case["facts"]["roster"]["managers"]
        ],
        exact_current_roster={m.user_id for m in managers}
        == set(expected["roster_ids"]),
        exact_scored_members=set(cards) == set(expected["scored_ids"]),
        exact_unscored_gaps={g.user_id: g.kind for g in analysis.member_gaps}
        == expected["gaps"],
        exact_live_kr_identity=all(
            [r.kr_id for r in review.kr_reviews] == expected["kr_ids"][review.user_id]
            for review in analysis.manager_reviews
        ),
        current_scoring_date=source["scoreEnd"] == case["facts"]["report_date"]
        and source["scoreStart"] == "2026-10-01",
        independent_evidence_controls_scores=all(
            expected["base_score_ranges"][r.user_id][0]
            <= k.base_score
            <= expected["base_score_ranges"][r.user_id][1]
            for r in analysis.manager_reviews
            for k in r.kr_reviews
        ),
    )
    oracle = {}
    for review in analysis.manager_reviews:
        # These fixtures each have one business KR of weight100x100; the oracle
        # is independent Decimal arithmetic over the native model's actual scores.
        business = Decimal(str(review.kr_reviews[0].score))
        leadership = (
            sum(Decimal(str(x.score)) for x in review.leadership_dimensions) / 4
        )
        culture = sum(Decimal(str(x.score)) for x in review.culture_dimensions) / 3
        coefficient = next(
            Decimal(value)
            for floor, value in (
                (100, "1.2"),
                (90, "1.1"),
                (80, "1.05"),
                (70, "1"),
                (50, ".95"),
                (0, ".8"),
            )
            if culture >= floor
        )
        # Existing script rounds the binary float formula to one decimal.
        expected_final = round(
            (float(business) * 0.7 + float(leadership) * 0.3) * float(coefficient), 1
        )
        oracle[review.user_id] = expected_final
    checks["formula_matches_independent_oracle"] = all(
        cards[uid].final_score == score for uid, score in oracle.items()
    )
    reads = {c["arguments"]["user_id"] for c in trace if c["tool"] == "read_okr_member"}
    checks["all_members_collected"] = reads == set(expected["roster_ids"])
    prepare_at = next(
        (
            i
            for i, c in enumerate(trace)
            if c["tool"] == "consumer_run_skill_script"
            and "prepare" in c["arguments"].get("arguments", [])
        ),
        len(trace),
    )
    checks["collect_before_prepare"] = {
        c["arguments"]["user_id"]
        for c in trace[:prepare_at]
        if c["tool"] == "read_okr_member"
    } == set(expected["roster_ids"])
    actual_hashes = {
        uid: digest((work / f"analysis-{key(uid)}.json").read_bytes())
        for uid in expected["scored_ids"]
    }
    if prior:
        checks["unchanged_analysis_reused"] = all(
            actual_hashes[uid] == prior[uid] for uid in expected["preserve_ids"]
        )
        checks["changed_analysis_replaced"] = all(
            actual_hashes[uid] != prior[uid] for uid in expected["rewrite_ids"]
        )
    documents = read_json(work / "documents.json")
    mains = [
        (node, doc)
        for node, doc in documents.items()
        if doc["title"] == manifest["title"]
    ]
    checks["unique_main_document"] = len(mains) == 1
    published = {}
    if len(mains) == 1:
        main_id, main = mains[0]
        published[main_id] = main
        checks["exact_main_readback"] = (
            main["content"] == (work / "main.md").read_text()
        )
        appendices = {a["user_id"]: a for a in manifest["appendices"]}
        children = {
            node: doc for node, doc in documents.items() if doc["parent_id"] == main_id
        }
        checks["all_real_children"] = len(children) == len(expected["roster_ids"]) and {
            d["title"] for d in children.values()
        } == {a["title"] for a in appendices.values()}
        checks["exact_appendix_readbacks"] = all(
            doc["content"] == (work / doc["artifact_name"]).read_text()
            for doc in children.values()
        )
        checks["all_verified_online_links"] = all(
            "https://docs.example.invalid/" + node in main["content"]
            for node in children
        )
        published.update(children)
        consumed = {
            c["arguments"]["name"]
            for c in trace
            if c["tool"] == "read_task_artifact"
            and not c["result"].get("has_more", False)
        }
        checks["full_readbacks_consumed"] = all(
            "readback-" + node + ".md" in consumed for node in published
        )
    full_text = (work / "full.md").read_text()
    summary_text = (work / "summary.md").read_text()
    if expected.get("preserve_unscored_roster_titles"):
        checks["known_unscored_titles_preserved"] = all(
            f"| {report._md_cell(report._member_label(m, managers))} | {report._md_cell(m.title)} |"
            in full_text
            for m in managers
            if m.user_id in expected["gaps"]
        )
    if expected.get("summary_contains_actual_member_gaps"):
        checks["specific_gap_reasons_in_summary"] = all(
            g.evidence in summary_text and g.kind in summary_text
            for g in analysis.member_gaps
        )
    proposal = wire.to_proposal()
    actions = list(proposal.actions) if proposal else []
    checks["exact_summary_proposal"] = (
        len(actions) == 1
        and actions[0].capability == "dingtalk-chat"
        and actions[0].operation in ("send_message", "send_group_message")
        and actions[0].target.get("conversation_id") == expected["group_id"]
        and actions[0].payload.get("content") == (work / "summary.md").read_text()
    )
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "cards": {uid: asdict(c) for uid, c in cards.items()},
        "formula_oracle": oracle,
        "analysis_hashes": actual_hashes,
        "analyses": {
            uid: read_json(work / f"analysis-{key(uid)}.json")
            for uid in expected["scored_ids"]
        },
        "manifest": manifest,
        "rendered_full": (work / "full.md").read_text(),
        "mock_document_count": len(published),
    }


def grade(case, work, output, prior=None):
    # An unreadable artifact or invalid production wire remains a failed check,
    # with its exact diagnostic retained for trajectory investigation.
    try:
        return _grade(case, work, output, prior)
    except (ValueError, OSError, KeyError) as error:
        return {
            "passed": False,
            "checks": {"production_artifact_validation": False},
            "validation_error": str(error),
        }


def native(
    role, facts_file, artifacts, output_dir, flags, previous=None, feedback=None
):
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = (
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["purpose_output_json"],
            "properties": {"purpose_output_json": {"type": "string"}},
        }
        if role == "consumer"
        else {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "feedback"],
            "properties": {
                "verdict": {"type": "string", "enum": ["approve", "revise"]},
                "feedback": {"type": "array", "items": {"type": "string"}},
            },
        }
    )
    schema = output_dir / "schema.json"
    schema.write_text(json.dumps(settings))
    output = output_dir / "output.json"
    fixture_args = [
        "-m",
        "evals.scheduled_task_generic.okr_fixture",
        "--facts",
        str(facts_file),
        "--work",
        str(artifacts),
        "--role",
        "audit" if role == "audit" else "consumer",
    ]
    fixture_flags = [
        "-c",
        "mcp_servers.okr_acceptance.command=" + json.dumps(sys.executable),
        "-c",
        "mcp_servers.okr_acceptance.args=" + json.dumps(fixture_args),
        "-c",
        "mcp_servers.okr_acceptance.env.PYTHONPATH=" + json.dumps(str(run.ROOT)),
        "-c",
        "mcp_servers.okr_acceptance.enabled=true",
        "-c",
        'mcp_servers.okr_acceptance.default_tools_approval_mode="approve"',
        "-c",
        'approval_policy="never"',
    ]
    if role == "consumer":
        prompt = (
            "执行这次合成的 CEO-2 OKR 定时报告任务。按选定的 ceo-weekly-okr-report 与 dingtang-okr-review Skill 完成完整报告工作流。"
            "调用 read_task_skill 读取原文，事实、原始证据、可恢复产物和文档状态均由本轮工具提供。"
            "所有 provider 都是隔离合成接口；没有真实外部执行器。保留最终群消息为待审核提案，不声称已发出。"
            "工具接口、flat artifact filenames 和所选脚本的契约与服务一致，使用真实 prepare/render 输出；报告日期使用触发日期。"
            "最终按给定的生产 purpose-output schema 返回。\n"
            + system_action_contract_document()
        )
        if feedback:
            prompt += (
                "\n独立 Audit 对上一次候选的反馈："
                + json.dumps(feedback, ensure_ascii=False)
                + "\n上次候选："
                + json.dumps(previous, ensure_ascii=False)
                + "\n基于持久产物修订；不要覆盖不变来源的有效分析。"
            )
    else:
        prompt = (
            "作为独立 Audit，按所选两个 Skill、当前来源、实际生成产物与 mock provider 读回核查这次 OKR 报告。"
            "使用只读工具检查来源身份、同名成员、名单覆盖、证据评分、缺口、公式、来源摘要恢复与发布/通知边界。"
            "共享认证/运行时失败不能变成单成员缺口或成功报告。报告日期以触发为准。"
            "没有预期答案提供给你；发现错误返回 revise 与具体反馈，否则 approve。不能修改正文或执行外部动作。\n"
            "当前 Consumer 结果："
            + json.dumps(previous, ensure_ascii=False)
            + "\n"
            + system_action_contract_document()
        )
    if role == "consumer":
        prompt += (
            "\nNative transport envelope: purpose_output_json encodes this complete production purpose schema. Its conditional branches are enforced by the real service parser after decoding:\n"
            + json.dumps(
                purpose_output_model("general").model_json_schema(), ensure_ascii=False
            )
        )
    command = [
        shutil.which("codex") or "codex",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        *flags,
        *fixture_flags,
        "--output-schema",
        str(schema),
        "--output-last-message",
        str(output),
        "-",
    ]
    print(f"Native {facts_file.parent.name} {role}", flush=True)
    with (
        (output_dir / "events.jsonl").open("w") as stdout,
        (output_dir / "stderr.txt").open("w") as stderr,
    ):
        p = subprocess.run(
            command,
            input=prompt,
            text=True,
            cwd=output_dir,
            stdout=stdout,
            stderr=stderr,
            timeout=900,
        )
    if p.returncode or not output.exists():
        events = [
            json.loads(line)
            for line in (output_dir / "events.jsonl").read_text().splitlines()
            if line
        ]
        diagnostic = [
            line
            for line in (output_dir / "stderr.txt").read_text().splitlines()
            if line.startswith(("Error:", "error:"))
        ]
        diagnostic.extend(
            str(e.get("message") or e.get("error"))
            for e in events
            if e.get("type") in ("error", "turn.failed")
        )
        return {
            "exit_code": p.returncode,
            "output": None,
            "diagnostic": "\n".join(diagnostic)
            or "Native invocation failed before a final result",
            "prompt_sha256": digest(prompt.encode()),
        }
    raw = read_json(output)
    from jsonschema import Draft202012Validator

    Draft202012Validator(settings).validate(raw)
    if role == "consumer":
        raw = json.loads(raw["purpose_output_json"])
        purpose_output_model("general").model_validate(raw)
    events = [
        json.loads(line)
        for line in (output_dir / "events.jsonl").read_text().splitlines()
        if line
    ]
    tools = [
        e.get("item", {})
        for e in events
        if e.get("item", {}).get("type") == "mcp_tool_call"
    ]
    # Native inventory lists metadata from the only configured fixture server;
    # it does not read provider content. Keep these calls in the raw evidence.
    inventory = [
        t
        for t in tools
        if t.get("server") == "codex"
        and t.get("tool") in ("list_mcp_resources", "list_mcp_resource_templates")
    ]
    providers = [t for t in tools if t not in inventory]
    other = [
        e.get("item", {}).get("type")
        for e in events
        if e.get("item", {}).get("type")
        not in (
            None,
            "mcp_tool_call",
            "agent_message",
            "reasoning",
            "todo_list",
            "error",
        )
    ]
    return {
        "output": raw,
        "prompt_sha256": digest(prompt.encode()),
        "tool_calls": len(tools),
        "native_inventory_calls": len(inventory),
        "mcp_tool_calls": [
            {k: t.get(k) for k in ("server", "tool", "status")} for t in tools
        ],
        "unexpected_runtime_items": other,
        "all_tools_fixture_only": all(
            t.get("server") == "okr_acceptance" for t in providers
        ),
        "exit_code": p.returncode,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="okr_results.v1.json", help="Result filename in this evaluation directory")
    args = parser.parse_args()
    destination = HERE / args.output
    if not destination.parent.is_dir():
        raise ValueError("Result output directory must exist before native execution")
    if destination.exists():
        raise ValueError(
            "Preserve the existing result; use a distinct output name after a diagnosed repair"
        )
    manifest = read_json(CASES_PATH)
    settings, flags = run.configuration("production-config")
    # Same configured model/effort; only this fixture server is connected.
    isolated = []
    index = 0
    while index < len(flags):
        if flags[index] == "-c" and flags[index + 1].startswith("mcp_servers."):
            index += 2
            continue
        isolated.append(flags[index])
        index += 1
    flags = isolated + [
        "-c",
        "model_reasoning_effort=" + json.dumps(settings["configured_reasoning_effort"]),
    ]
    settings["ignore_user_config"] = True
    settings["ignore_rules"] = True
    result = {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "cases_sha256": digest(CASES_PATH.read_bytes()),
        "grader_sha256": digest(Path(__file__).read_bytes()),
        "cli_version": subprocess.check_output(
            ["codex", "--version"], text=True
        ).strip(),
        "candidate_git_ref": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=run.ROOT, text=True
        ).strip(),
        "settings": settings,
        "synthetic_only": True,
        "external_executor_available": False,
        "source_sha256": {
            str(p.relative_to(run.ROOT))
            if p.is_relative_to(run.ROOT)
            else "operation/dingtang-okr-review/SKILL.md": digest(p.read_bytes())
            for p in [
                SKILL / "SKILL.md",
                SKILL / "scripts/report.py",
                SKILL / "scripts/analysis.schema.json",
                SKILL / "scripts/destination.py",
                Path.home() / ".agents/skills/dingtang-okr-review/SKILL.md",
            ]
        },
        "cases": [],
    }
    directory = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="okr-native-acceptance-", dir="/tmp"
        ) as temp:
            directory = Path(temp)
            completed = {}
            for case in manifest["cases"]:
                root = directory / case["id"]
                root.mkdir()
                artifacts = root / "artifacts"
                prior = None
                if case.get("resume_from"):
                    shutil.copytree(completed[case["resume_from"]], artifacts)
                    (artifacts / "tool-calls.jsonl").unlink()
                    prior = {
                        uid: digest(
                            (artifacts / f"analysis-{key(uid)}.json").read_bytes()
                        )
                        for uid in case["expected"]["scored_ids"]
                    }
                else:
                    artifacts.mkdir()
                facts = root / "facts.json"
                facts.write_text(json.dumps(case["facts"], ensure_ascii=False))
                consumer = native(
                    "consumer", facts, artifacts, root / "consumer", flags
                )
                if consumer["output"] is None:
                    result["cases"].append(
                        {
                            "case_id": case["id"],
                            "consumer": consumer,
                            "passed": False,
                            "raw_tool_trace": calls(artifacts),
                        }
                    )
                    break
                first = grade(case, artifacts, consumer["output"], prior)
                audit = native(
                    "audit",
                    facts,
                    artifacts,
                    root / "audit",
                    flags,
                    previous=consumer["output"],
                )
                correction = None
                if audit["output"]["verdict"] == "revise":
                    correction = native(
                        "consumer",
                        facts,
                        artifacts,
                        root / "correction",
                        flags,
                        previous=consumer["output"],
                        feedback=audit["output"],
                    )
                final_audit = audit
                if correction:
                    final_audit = native(
                        "audit",
                        facts,
                        artifacts,
                        root / "audit-after-correction",
                        flags,
                        previous=correction["output"],
                    )
                final = grade(
                    case, artifacts, (correction or consumer)["output"], prior
                )
                phases = [
                    consumer,
                    audit,
                    *([correction, final_audit] if correction else []),
                ]
                passed = (
                    final["passed"]
                    and final_audit["output"]["verdict"] == "approve"
                    and all(
                        p["all_tools_fixture_only"]
                        and not p["unexpected_runtime_items"]
                        for p in phases
                    )
                )
                row = {
                    "case_id": case["id"],
                    "consumer": consumer,
                    "first_grade": first,
                    "audit": audit,
                    "correction": correction,
                    "final_audit": final_audit,
                    "final_grade": final,
                    "passed": passed,
                    "trace": [
                        {
                            "tool": c["tool"],
                            "arguments": c["arguments"],
                            "error": c["result"].get("stderr", "")
                            if isinstance(c.get("result"), dict)
                            else "",
                        }
                        for c in calls(artifacts)
                    ],
                    "raw_tool_trace": [
                        {
                            **c,
                            "result": {
                                "skill_source_sha256": digest(
                                    c["result"]["content"].encode()
                                )
                            },
                        }
                        if c["tool"] == "read_task_skill"
                        else c
                        for c in calls(artifacts)
                    ],
                }
                result["cases"].append(row)
                completed[case["id"]] = artifacts
                destination.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                )
                print(case["id"], "PASS" if passed else "FAIL", flush=True)
                if not passed:
                    break
    finally:
        result["temporary_artifacts_cleaned"] = (
            directory is None or not directory.exists()
        )
        result["status"] = (
            "passed"
            if len(result["cases"]) == len(manifest["cases"])
            and all(c["passed"] for c in result["cases"])
            else "failed"
        )
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"status": result["status"], "completed_cases": len(result["cases"])}
        )
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
