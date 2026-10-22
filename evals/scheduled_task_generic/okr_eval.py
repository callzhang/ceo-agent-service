"""Frozen native OKR workflow acceptance; real scripts with synthetic IO."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from pydantic import ValidationError

from app.consumer_purpose import purpose_output_model
from app.action_contract_catalog import system_action_contract_document
from evals.scheduled_task_generic import run
from evals.scheduled_task_generic.okr_fixture import (
    Fixture,
    SKILL,
    digest,
    key,
    load_script,
    member_payload,
)

HERE = Path(__file__).parent
CASES_PATH = HERE / "okr_cases.v4.json"


def read_json(path):
    return json.loads(Path(path).read_text())


def parse_consumer_purpose_output(raw):
    """Decode the native string envelope and enforce the production contract."""
    output = json.loads(raw["purpose_output_json"])
    purpose_output_model("general").model_validate(output)
    return output


def calls(work):
    path = Path(work).with_name(Path(work).name + "-tool-calls.jsonl")
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def persisted_call(call):
    if call["tool"] == "read_task_skill" and "content" in call["result"]:
        return {**call, "result": {"skill_source_sha256": digest(call["result"]["content"].encode())}}
    return call


def application_contract_hashes():
    names = {"_write_document_artifact", "_read_task_file", "_task_file_path",
             "consumer_document_write", "consumer_artifact_write", "read_task_artifact",
             "list_task_artifacts", "read_dingtalk_document_info", "read_dingtalk_document"}
    tree = ast.parse((run.ROOT / "app/agent_cli.py").read_text())
    return {node.name: digest(ast.dump(node, include_attributes=False).encode())
            for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names}


def fully_read(trace, work, name):
    text = (work / name).read_text()
    expected = digest(text.encode())
    intervals = []
    for call in trace:
        if call["tool"] != "read_task_artifact" or call["arguments"]["name"] != name:
            continue
        result = call["result"]
        if result.get("sha256") == expected:
            start = call["arguments"].get("offset", 0)
            intervals.append((start, start + len(result["content"])))
    end = 0
    for start, stop in sorted(intervals):
        if start > end:
            return False
        end = max(end, stop)
    return end == len(text)


def readback_observed(trace, work, node, doc):
    writes = [
        i
        for i, call in enumerate(trace)
        if call["tool"] == "consumer_document_write" and call["result"]["node_id"] == node
    ]
    following = trace[writes[-1] + 1 :] if writes else trace
    expected = digest((work / doc["readback_name"]).read_bytes()) if doc.get("readback_name") else None
    return (expected is not None and fully_read(following, work, doc["readback_name"])) or any(
        call["tool"] == "read_dingtalk_document"
        and call["arguments"]["node_id"] == node
        and call["result"].get("complete") is True
        and call["result"].get("status") == "success"
        and call["result"]["content"]["nodeId"] == node
        and call["result"]["content"]["revision"] == doc["revision"]
        and (
            expected is None
            or digest(json.dumps(call["result"], ensure_ascii=False).encode()) == expected
        )
        for call in following)


def restore_accepted_candidate(saved, case, work, prior=None):
    """Restore actual saved bytes/receipts for stricter checks and a new Audit."""
    row = next(r for r in saved["cases"] if r["case_id"] == case["id"])
    if row["final_audit"]["output"]["verdict"] != "approve":
        raise ValueError("A failed candidate cannot be reused")
    if saved["cases_sha256"] != digest(CASES_PATH.read_bytes()):
        raise ValueError("Saved fixed facts or expectations changed")
    fixture = Fixture(case["facts"], work)
    for call in row["raw_tool_trace"]:
        tool, args, result = call["tool"], call["arguments"], call["result"]
        if tool == "consumer_artifact_write":
            name, content = args["name"], args["content"]
        elif tool == "read_task_artifact":
            name, content = args["name"], result["content"]
        elif tool == "read_dingtalk_document":
            content = json.dumps(result, ensure_ascii=False)
            name = digest(content.encode()) + ".readback.json"
            (work / name).write_text(content)
            continue
        else:
            continue
        if digest(content.encode()) != result["sha256"]:
            continue  # A paginated slice is not a complete file.
        if Path(name).name != name:
            raise ValueError("Saved artifact name is not flat")
        (work / name).write_text(content)
    fixture.write_json("okr-roster.json", case["facts"]["roster"])
    for member in case["facts"]["roster"]["managers"]:
        fixture.write_json("okr-member-" + digest(member["user_id"].encode()) + ".json",
                           member_payload(member, case["facts"]))
    load_script("report").prepare(roster_file=work / "okr-roster.json", members_dir=work,
        period_label=case["facts"]["period_label"], report_date=case["facts"]["report_date"], output_dir=work)
    infos = {c["arguments"]["node_id"]: c["result"]["data"]["document"]
             for c in row["raw_tool_trace"] if c["tool"] == "read_dingtalk_document_info"}
    docs = {}
    latest = {c["result"]["node_id"]: c for c in row["raw_tool_trace"] if c["tool"] == "consumer_document_write"}
    for call in latest.values():
        result = call["result"]
        node, name = result["node_id"], result["readback"]["artifact_name"]
        payload = read_json(work / name)["content"]
        if digest((work / name).read_bytes()) != result["readback"]["sha256"]:
            raise ValueError("Saved provider receipt bytes changed")
        body = payload["markdown"].encode()
        if digest(body) != result["content_sha256"]:
            raise ValueError("Provider body does not match the actual submitted artifact SHA")
        (work / call["arguments"]["artifact_name"]).write_bytes(body)
        docs[node] = {"title": payload["title"], "content": payload["markdown"],
            "parent_id": infos[node]["folderId"], "revision": payload["revision"],
            "artifact_name": call["arguments"]["artifact_name"], "readback_name": name}
    fixture.docs_path.write_text(json.dumps(docs, ensure_ascii=False))
    fixture.trace_path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in row["raw_tool_trace"]))
    output = (row["correction"] or row["consumer"])["output"]
    (work / "full.md").write_text(row["final_grade"]["rendered_full"])
    (work / "summary.md").write_text(output["payload"]["actions"][0]["payload"]["content"])
    manifest = read_json(work / "manifest.json")
    main_id = next(node for node, doc in docs.items() if doc["title"] == manifest["title"])
    body_names = ["full.md", "main.md", "summary.md", *[a["artifact_name"] for a in manifest["appendices"]]]
    before = {name: digest((work / name).read_bytes()) for name in body_names}
    load_script("report").render(source_file=work / "source.json", analysis_dir=work, output_dir=work,
        appendix_links_file=work / "appendix-links.json", document_url=infos[main_id]["docUrl"])
    if before != {name: digest((work / name).read_bytes()) for name in body_names}:
        raise ValueError("Current renderer changes saved published body bytes")
    checked = grade(case, work, output, prior)
    if not checked["passed"] or checked["analysis_hashes"] != row["final_grade"]["analysis_hashes"]:
        raise ValueError("Saved candidate fails current checks: " + str([k for k,v in checked["checks"].items() if not v]))
    return row


def approved_saved_case(saved_cases, case_id):
    """Return only an actually audited saved candidate; pending rows are not reusable."""
    return next(
        (
            row
            for row in saved_cases
            if row.get("case_id") == case_id
            and row.get("passed") is True
            and row.get("final_audit", {}).get("output", {}).get("verdict") == "approve"
        ),
        None,
    )



def behavior_checks(reviews, expected):
    expected_ranges = expected["behavioral_score_ranges"]
    return {
        "formal_behavioral_dimensions": all(
            [d["dimension"] for d in r["leadership_dimensions"]] == expected["leadership_dimensions"]
            and [d["dimension"] for d in r["culture_dimensions"]] == expected["culture_dimensions"]
            for r in reviews),
        "behavioral_scores_match_evidence": all(
            d["dimension"] in expected_ranges
            and expected_ranges[d["dimension"]][0]
            <= d["score"]
            <= expected_ranges[d["dimension"]][1]
            for r in reviews
            for d in r["leadership_dimensions"] + r["culture_dimensions"]
        ),
    }


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
                and c["result"].get("source_outcome", {}).get("scope") == "shared"
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
    )
    checks = {"complete_artifacts": all((work / name).is_file() for name in required)}
    checks["complete_provider_state"] = Fixture(case["facts"], work).docs_path.is_file()
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
    manifest_paths = [manifest["main_file"], manifest["full_file"], manifest["summary_file"],
                      *[a["path"] for a in manifest["appendices"]]]
    checks["manifest_artifact_binding"] = all(Path(path).parent.resolve() == work.resolve() for path in manifest_paths)
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
    checks.update(behavior_checks([r.model_dump() for r in analysis.manager_reviews], expected))
    duplicate_names = {m.name for m in managers if sum(n.name == m.name for n in managers) > 1}
    checks["same_name_owners_disambiguated"] = all(
        owner not in duplicate_names for item in analysis.ceo_attention_items for owner in item.owner_names)
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
        # The published one-decimal score uses conventional half-up rounding.
        raw_final = (business * Decimal("0.7") + leadership * Decimal("0.3")) * coefficient
        expected_final = float(
            raw_final.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
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
        None,
    )
    checks["collect_before_prepare"] = prepare_at is not None and {
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
    documents = Fixture(case["facts"], work).documents()
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
        infos = {c["result"]["data"]["document"]["nodeId"]:
                 c["result"]["data"]["document"]["docUrl"]
                 for c in trace if c["tool"] == "read_dingtalk_document_info"}
        checks["all_canonical_urls_observed"] = all(
            infos.get(node) == "https://docs.example.invalid/" + node for node in published)
        receipts = {c["result"]["node_id"]: c["result"]
                    for c in trace if c["tool"] == "consumer_document_write"}
        checks["exact_artifact_submission"] = all(
            (receipt := receipts.get(node)) is not None
            and receipt["content_sha256"] == digest(doc["content"].encode())
            and receipt["readback"]["artifact_name"] == doc.get("readback_name")
            for node, doc in published.items())
        checks["full_readbacks_consumed"] = all(
            readback_observed(trace, work, node, doc) for node, doc in published.items()
        )
        checks["provider_receipt_payloads_match"] = all(
            (payload := read_json(work / doc["readback_name"])).get("complete") is True
            and payload.get("status") == "success"
            and payload["content"]["markdown"] == doc["content"]
            and payload["content"]["revision"] == doc["revision"]
            and digest((work / doc["readback_name"]).read_bytes()) + ".readback.json" == doc["readback_name"]
            for doc in published.values())
        checks["canonical_render_artifacts"] = False
        if checks["all_canonical_urls_observed"]:
            with tempfile.TemporaryDirectory(prefix="okr-canonical-check-", dir="/tmp") as directory:
                canonical = Path(directory)
                report.render(source_file=work / "source.json", analysis_dir=work, output_dir=canonical,
                              appendix_links_file=work / "appendix-links.json", document_url=infos[main_id])
                names = ["full.md", "main.md", "summary.md", *[a["artifact_name"] for a in manifest["appendices"]]]
                checks["canonical_render_artifacts"] = all(
                    (canonical / name).read_bytes() == (work / name).read_bytes() for name in names)
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
    schema.write_text(json.dumps(settings))
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
    from jsonschema import Draft202012Validator, ValidationError as JSONSchemaValidationError

    try:
        Draft202012Validator(settings).validate(raw)
        if role == "consumer":
            raw = parse_consumer_purpose_output(raw)
    except (
        JSONSchemaValidationError,
        ValidationError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        return {
            "exit_code": p.returncode,
            "output": None,
            "diagnostic": f"Native final output failed the production contract: {error}",
            "raw_output": raw,
            "prompt_sha256": digest(prompt.encode()),
        }
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
    parser.add_argument("--recheck-accepted-prefix-from", help="Verify accepted bytes and rerun only Audit under current tools/Skill")
    parser.add_argument("--resume-reviewed-from", help="Rescore unchanged reviewed native outputs after an evaluator-only repair")
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
    if args.recheck_accepted_prefix_from and args.resume_reviewed_from:
        raise ValueError("Select one checkpoint mode")
    saved_name = args.recheck_accepted_prefix_from or args.resume_reviewed_from
    saved = read_json(HERE / saved_name) if saved_name else None
    if saved and saved["settings"] != settings:
        raise ValueError("Saved native model/runtime settings changed")
    result = {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "cases_sha256": digest(CASES_PATH.read_bytes()),
        "grader_sha256": digest(Path(__file__).read_bytes()),
        "fixture_sha256": digest((HERE / "okr_fixture.py").read_bytes()),
        "application_contract_sha256": application_contract_hashes(),
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
    if args.resume_reviewed_from and (saved["source_sha256"] != result["source_sha256"]
            or saved["application_contract_sha256"] != result["application_contract_sha256"]):
        raise ValueError("Reviewed outputs require unchanged Skill and production tool sources")
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
                    source_provider = Fixture(case["facts"], completed[case["resume_from"]]).docs_path
                    shutil.copyfile(source_provider, Fixture(case["facts"], artifacts).docs_path)
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
                previous = approved_saved_case(saved["cases"], case["id"]) if saved else None
                rechecked = restore_accepted_candidate(saved, case, artifacts, prior) if previous else None
                if rechecked and args.resume_reviewed_from:
                    row = dict(rechecked)
                    row["evaluator_rescore"] = {"source_result": saved_name,
                        "sha256": digest((HERE / saved_name).read_bytes()),
                        "original_passed": row["passed"], "original_final_grade": row["final_grade"],
                        "consumer_rerun": False, "audit_rerun": False, "additional_corrections": 0}
                    row["final_grade"] = grade(case, artifacts, (row["correction"] or row["consumer"])["output"], prior)
                    row["passed"] = row["final_grade"]["passed"]
                    result["cases"].append(row)
                    completed[case["id"]] = artifacts
                    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
                    print(case["id"], "PASS (same native outputs rescored)", flush=True)
                    continue
                consumer = rechecked["consumer"] if rechecked else native(
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
                first = rechecked["first_grade"] if rechecked else grade(case, artifacts, consumer["output"], prior)
                audit = rechecked["audit"] if rechecked else native(
                    "audit",
                    facts,
                    artifacts,
                    root / "audit",
                    flags,
                    previous=consumer["output"],
                )
                correction = rechecked["correction"] if rechecked else None
                if not rechecked and audit["output"]["verdict"] == "revise":
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
                if correction or rechecked:
                    final_audit = native(
                        "audit",
                        facts,
                        artifacts,
                        root / "audit-after-correction",
                        flags,
                        previous=(correction or consumer)["output"],
                    )
                result["pending_case"] = {
                    "case_id": case["id"], "consumer": consumer,
                    "audit": audit, "correction": correction, "final_audit": final_audit,
                }
                destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
                final = grade(
                    case, artifacts, (correction or consumer)["output"], prior
                )
                phases = [
                    consumer,
                    audit,
                    *([correction] if correction else []),
                    *([final_audit] if final_audit is not audit else []),
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
                    "raw_tool_trace": [persisted_call(c) for c in calls(artifacts)],
                }
                if rechecked:
                    row["rechecked_saved_candidate"] = {"result": args.recheck_accepted_prefix_from,
                        "sha256": digest((HERE / args.recheck_accepted_prefix_from).read_bytes()),
                        "consumer_rerun": False, "additional_corrections": 0,
                        "producer_source_sha256": saved["source_sha256"],
                        "original_final_audit": rechecked["final_audit"]["output"]}
                result["cases"].append(row)
                del result["pending_case"]
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
