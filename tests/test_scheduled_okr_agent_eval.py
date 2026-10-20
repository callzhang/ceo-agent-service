import copy
import json
from pathlib import Path

from evals.scheduled_task_generic import okr_eval
from evals.scheduled_task_generic.okr_fixture import Fixture, member_payload

CASES = json.loads((Path(okr_eval.__file__).parent / "okr_cases.v1.json").read_text())[
    "cases"
]


def complete_fixture(tmp_path, case=None):
    case = copy.deepcopy(case or CASES[0])
    facts = case["facts"]
    f = Fixture(facts, tmp_path)
    f.write_json("okr-roster.json", facts["roster"])
    trace = []
    for m in facts["roster"]["managers"]:
        f.write_json("okr-member-" + m["user_id"] + ".json", member_payload(m, facts))
        trace.append(
            {
                "tool": "read_okr_member",
                "arguments": {"user_id": m["user_id"]},
                "result": {},
            }
        )
    report = okr_eval.load_script("report")
    plan = report.prepare(
        roster_file=tmp_path / "okr-roster.json",
        members_dir=tmp_path,
        period_label=facts["period_label"],
        report_date=facts["report_date"],
        output_dir=tmp_path,
    )
    for entry in plan["managers"]:
        if entry["outcome"] != "collected":
            continue
        uid = entry["user_id"]

        def dims(n, score):
            return [
                {
                    "dimension": str(i),
                    "required_behavior": "Current level standard",
                    "positive_evidence": "behavior-review",
                    "missing_or_contrary_evidence": "No exemplar scope",
                    "score": score,
                    "next_band_evidence": "Need independently verified extra effect",
                }
                for i in range(n)
            ]

        score = 80 if uid == "eval-a" else 40
        analysis = {
            "_source_sha256": entry["source_sha256"],
            "executive_summary": "Fixture analysis",
            "company_progress": ["Verified progress"],
            "ceo_attention_items": [],
            "source_coverage": ["Current independent records"],
            "warnings": [],
            "manager_reviews": [
                {
                    "name": entry["name"],
                    "role_level": "总监" if uid == "eval-a" else "经理",
                    "role_level_evidence": "Current title",
                    "progress_summary": "Sourced progress",
                    "key_progress": [],
                    "independent_evidence": [facts["evidence"][uid]["id"]],
                    "evidence_assessment": "Independent source controls score",
                    "risks": [],
                    "next_week_focus": [],
                    "data_gaps": [],
                    "kr_reviews": [
                        {
                            "kr_id": "kr-" + uid,
                            "objective_title": "交付目标 " + uid,
                            "kr_title": "交付并验收 " + uid,
                            "category": "业务OKR",
                            "system_progress": "100% self report",
                            "independent_evidence": facts["evidence"][uid]["id"],
                            "evidence_assessment": "Current source",
                            "base_score": score,
                            "time_discount": "No date stated",
                            "score": score,
                            "improvement": "Verify effects",
                        }
                    ],
                    "leadership_dimensions": dims(4, 70),
                    "culture_dimensions": dims(3, 80),
                }
            ],
        }
        f.write_json(entry["analysis_file"], analysis)
    links = {
        m["user_id"]: "https://docs.example.invalid/child-" + m["user_id"]
        for m in facts["roster"]["managers"]
    }
    f.write_json("appendix-links.json", links)
    manifest = report.render(
        source_file=tmp_path / "source.json",
        analysis_dir=tmp_path,
        output_dir=tmp_path,
        appendix_links_file=tmp_path / "appendix-links.json",
        document_url="https://docs.example.invalid/main",
    )
    docs = {
        "main": {
            "title": manifest["title"],
            "parent_id": "eval-folder",
            "revision": 2,
            "content": (tmp_path / "main.md").read_text(),
            "artifact_name": "main.md",
        }
    }
    for appendix in manifest["appendices"]:
        node = "child-" + appendix["user_id"]
        docs[node] = {
            "title": appendix["title"],
            "parent_id": "main",
            "revision": 1,
            "content": (tmp_path / appendix["artifact_name"]).read_text(),
            "artifact_name": appendix["artifact_name"],
        }
    f.write_json("documents.json", docs)
    for node, doc in docs.items():
        name = "readback-" + node + ".md"
        (tmp_path / name).write_text(doc["content"])
        trace.extend(
            [
                {
                    "tool": "consumer_document_write",
                    "arguments": {
                        "node_id": node,
                        "artifact_name": doc["artifact_name"],
                    },
                    "result": {
                        "node_id": node,
                        "readback_artifact": {"artifact_name": name},
                    },
                },
                {
                    "tool": "read_task_artifact",
                    "arguments": {"name": name, "offset": 0},
                    "result": {"has_more": False},
                },
            ]
        )
    (tmp_path / "tool-calls.jsonl").write_text("\n".join(json.dumps(c) for c in trace))
    output = {
        "action": "plan",
        "reason": "Synthetic report ready after exact mock provider readback",
        "target": {},
        "payload": {
            "objective": "Notify group",
            "sourced_facts": [],
            "authored_judgment": "Ready",
            "actions": [
                {
                    "description": "Notify group",
                    "action_identity": "synthetic-notify",
                    "capability": "dingtalk-chat",
                    "operation": "send_group_message",
                    "target": {"conversation_id": facts["roster"]["conversation_id"]},
                    "payload": {"content": (tmp_path / "summary.md").read_text()},
                }
            ],
        },
        "evidence_refs": [],
    }
    return case, output


def test_complete_actual_skill_output_passes_okr_acceptance(tmp_path):
    case, output = complete_fixture(tmp_path)
    assert okr_eval.grade(case, tmp_path, output)["passed"]


def test_okr_acceptance_rejects_wrong_group_or_body(tmp_path):
    case, output = complete_fixture(tmp_path)
    output["payload"]["actions"][0]["target"]["conversation_id"] = "other-group"
    assert not okr_eval.grade(case, tmp_path, output)["passed"]


def test_native_failure_retains_diagnostic_and_never_accepts_output(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    facts = tmp_path / "facts.json"
    facts.write_text("{}")

    def rejected(*args, **kwargs):
        kwargs["stderr"].write("Error: synthetic native configuration rejected\n")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(okr_eval.subprocess, "run", rejected)
    phase = okr_eval.native(
        "consumer", facts, tmp_path / "artifacts", tmp_path / "native", []
    )
    assert phase["exit_code"] == 1 and phase["output"] is None
    assert "synthetic native configuration rejected" in phase["diagnostic"]


def test_native_structured_error_is_retained_without_stderr(tmp_path, monkeypatch):
    from types import SimpleNamespace

    facts = tmp_path / "facts.json"
    facts.write_text("{}")

    def rejected(*args, **kwargs):
        kwargs["stdout"].write(
            json.dumps({"type": "error", "message": "synthetic output schema rejected"})
            + "\n"
        )
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(okr_eval.subprocess, "run", rejected)
    phase = okr_eval.native(
        "consumer", facts, tmp_path / "artifacts", tmp_path / "native", []
    )
    assert "synthetic output schema rejected" in phase["diagnostic"]


def test_native_transport_keeps_real_purpose_validation_inside_envelope(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    wire = {
        "action": "no_action",
        "reason": "Draft only",
        "target": {},
        "payload": {},
        "evidence_refs": [],
    }
    facts = tmp_path / "facts.json"
    facts.write_text("{}")

    def accepted(command, **kwargs):
        schema = json.loads(
            Path(command[command.index("--output-schema") + 1]).read_text()
        )
        assert "allOf" not in schema
        assert "purpose_output_json" in schema["properties"]
        assert (
            'mcp_servers.okr_acceptance.default_tools_approval_mode="approve"'
            in command
        )
        assert '"allOf"' in kwargs["input"]
        Path(command[command.index("--output-last-message") + 1]).write_text(
            json.dumps({"purpose_output_json": json.dumps(wire)})
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(okr_eval.subprocess, "run", accepted)
    phase = okr_eval.native(
        "consumer", facts, tmp_path / "artifacts", tmp_path / "native", []
    )
    assert phase["output"] == wire


def test_unscored_current_roster_titles_are_not_erased(tmp_path):
    case,_=complete_fixture(tmp_path)
    full=(tmp_path/'full.md').read_text()
    for member in case['facts']['roster']['managers']:
        if member['user_id'] in case['expected']['gaps']:
            assert f"| {member['name']} | {member['title']} |" in full


def test_group_summary_keeps_exact_member_gap_evidence(tmp_path):
    complete_fixture(tmp_path)
    report=okr_eval.load_script('report')
    source=json.loads((tmp_path/'source.json').read_text())
    analysis,_=report._analysis_from_files(source,tmp_path)
    summary=(tmp_path/'summary.md').read_text()
    for gap in analysis.member_gaps:
        assert gap.evidence in summary
