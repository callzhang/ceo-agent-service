import copy
import hashlib
import json
from pathlib import Path

from app import agent_cli
from evals.scheduled_task_generic import okr_eval
from evals.scheduled_task_generic.okr_fixture import (
    DocumentProvider,
    Fixture,
    member_payload,
)

CASES = json.loads((Path(okr_eval.__file__).parent / "okr_cases.v8.json").read_text())["cases"]
BASE_CASES = CASES[:4]


def test_okr_skill_resumes_saved_inputs_and_does_not_use_failure_for_missing_evidence():
    root = Path(okr_eval.__file__).parents[2]
    skill = " ".join(
        (root / "ci/shared-skills/ceo-weekly-okr-report/SKILL.md")
        .read_text()
        .split()
    )
    assert "Do not invent or require a separate local performance-policy" in skill
    assert "the missing document does not block member analysis or publication" in skill
    assert "An empty search is an evidence gap, not a reason to stop" in skill
    assert "Process live-OKR managers in roster order" in skill
    assert "do not preload a later manager's files" in skill
    assert "Do not read the next manager's source or schema" in skill
    assert "write and read back the complete schema-valid analysis artifact" in skill
    assert "The discrete bands above apply only to each KR's `base_score`" in skill
    assert "70–79 is the valid role-standard band" in skill
    assert "replace the proposal content with those exact bytes" in skill
    assert "Do not copy provider status codes, machine error labels or raw diagnostics into analyses, report text or the message proposal" in skill
    assert "an unreadable current-quarter record does not establish that the member has no goal" in skill
    assert "`read_scheduled_context.run_id` is this execution's scheduled run ID" in skill
    assert "call `read_okr_roster` plus `read_okr_member` once for every current roster member" in skill
    assert "search independent DingTalk documents using the" in skill
    assert "`search_dingtalk_documents`" in skill
    assert "A concrete current-quarter `member_not_found`/404 receipt is member-specific" in skill
    assert "Use `reply_to_message` only when the trigger is an actual message" in skill
    assert "compare each summary bullet and risk item by manager and KR" in skill
    assert "Call a result a \"this-week\" change only when a source includes a date" in skill
    assert "in `ceo_attention_items` when it needs a specific CEO decision or in that manager's `risks`, not in both" in skill
    assert "missing positive evidence with no concrete" in skill
    assert "A score below `50` requires repeated or high-impact" in skill
    assert "A Consumer retry, resumed turn or Audit correction for the same `read_scheduled_context.run_id`" in skill
    assert "reuse its verified roster and member source receipts instead of rereading all members" in skill
    assert "do not call `read_okr_roster` again" in skill
    assert "Never infer that a member source is unchanged from an earlier scheduled execution" in skill
    assert "A request to fix a shared report, appendix set, notification summary" in skill
    assert "preserve those bytes through the correction" in skill
    assert "use each affected member's exact `subject_label`" in skill
    assert "Report only source categories that this execution actually read" in skill
    assert "include it as a concise, owner-attributed progress highlight" in skill
    assert "Keep each material risk once in the summary" in skill
    assert "choose only an allowed `dingtang-okr-review` evidence band" in skill
    assert "never invent intermediate values such as 30, 50 or 70" in skill
    assert "state each coverage boundary once" in skill
    assert "On a retry of the same scheduled execution, read only missing/failed member receipts" in skill
    assert "Never repeat a successful member read within the same scheduled execution" in skill
    assert "call `list_task_artifacts`" in skill
    assert "reuse listed roster and member receipt artifacts" in skill
    assert "`prepare` validates their userId, period, outcome and source digest" in skill
    assert "read `plan.json`" in skill
    assert "Do not try to open the Python script source or task artifacts with `read_text_file`" in skill
    assert "resume at the first missing, changed or Audit-identified analysis" in skill
    assert "Zero reported progress is not evidence that no outcome occurred" in skill
    assert "It is not a shortcut for unfinished analysis" in skill
    assert "A file containing `analysis_failure` is not a complete analysis" in skill
    assert "return a retryable `analysis_incomplete` result" in skill
    assert "Score each dimension independently" in skill
    assert "Do not transfer evidence between dimensions" in skill
    assert "Audit must independently check all seven dimension/evidence/score" in skill
    assert "A missed or incomplete business KR does not lower a leadership or culture dimension" in skill
    assert "a below-standard score requires dimension-specific evidence of an unmet behavior or contrary pattern" in skill
    assert "Innovation requires a specific improvement, validation and observed effect" in skill
    assert "A `scope: member` label or a diagnostic containing “member-bound” is not proof" in skill
    assert "Independently inspect every score of 80 or more" in skill
    assert "routine same-team adoption" in skill
    assert "For each manifest appendix run the same destination script" in skill
    assert "confirm successful provider write receipts for the current manifest" in skill
    assert "each appendix's parent is the main node" in skill
    assert "No automatic content readback or full-body comparison is required" in skill
    assert "create that dated document if it does not exist" in skill
    assert "Do not write a new-date body into a previous-date node" in skill
    correction = skill.split("### Correct an Audit return before publication", 1)[1].split(
        "## Collect and prepare", 1
    )[0]
    assert "Audit correction is not a prose-only edit" in correction
    assert "Update the affected `analysis-<userId>.json` or `appendix-links.json`" in correction
    assert "rerun `scripts/report.py render`" in correction
    assert "Never write generated report outputs with `consumer_artifact_write`" in correction


def test_18_manager_case_keeps_21_beijing_trigger_cutoff_and_unscored_gaps():
    case = next(c for c in CASES if c["id"] == "empty-search-18-managers-incremental-analysis")
    facts, expected = case["facts"], case["expected"]
    assert len(facts["roster"]["managers"]) == 18
    assert len(expected["scored_ids"]) == 15
    assert len(expected["gaps"]) == 3
    assert expected["require_incremental_member_analysis"] is True
    missing = set(expected["gaps"])
    assert all(member_payload(m, facts)["sourceOutcome"]["kind"] == "goals_missing"
               for m in facts["roster"]["managers"] if m["user_id"] in missing)


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
    for node_id, document in f.independent_documents().items():
        trace.extend(
            [
                {
                    "tool": "search_dingtalk_documents",
                    "arguments": {"query": document["title"]},
                    "result": {"documents": [{"nodeId": node_id, "name": document["title"]}]},
                },
                {
                    "tool": "read_dingtalk_document",
                    "arguments": {"node_id": node_id},
                    "result": {"content": document["content"]},
                },
            ]
        )
    report = okr_eval.load_script("report")
    plan = report.prepare(
        roster_file=tmp_path / "okr-roster.json",
        members_dir=tmp_path,
        period_label=facts["period_label"],
        report_date=facts["report_date"],
        output_dir=tmp_path,
    )
    trace.append({"tool": "consumer_run_skill_script", "arguments": {"arguments": ["prepare"]}, "result": {"exit_code": 0}})
    for entry in plan["managers"]:
        if entry["outcome"] != "collected":
            continue
        uid = entry["user_id"]

        def dims(names, score):
            return [
                {
                    "dimension": dimension,
                    "required_behavior": "Current level standard",
                    "positive_evidence": (
                        "behavior-review supports current standard"
                        if score >= 70
                        else "No dimension-specific evidence in behavior-review"
                    ),
                    "missing_or_contrary_evidence": (
                        "No exemplar scope" if score >= 70 else "Current standard is not established"
                    ),
                    "score": score,
                    "next_band_evidence": "Need independently verified extra effect",
                }
                for dimension in names
            ]

        score = 100 if uid == "eval-a" else 40
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
                    "leadership_dimensions": dims(
                        case["expected"]["leadership_dimensions"],
                        60,
                    ),
                    "culture_dimensions": dims(
                        case["expected"]["culture_dimensions"],
                        60,
                    ),
                }
            ],
        }
        for dimension in analysis["manager_reviews"][0]["leadership_dimensions"] + analysis["manager_reviews"][0]["culture_dimensions"]:
            low, high = case["expected"]["behavioral_score_ranges"][dimension["dimension"]]
            dimension["score"] = low if high <= 60 else low
            dimension["positive_evidence"] = (
                "behavior-review: dimension-specific current-standard evidence"
                if high >= 70
                else "No dimension-specific current-standard evidence"
            )
            dimension["missing_or_contrary_evidence"] = (
                "No above-standard exemplar evidence"
                if high >= 70
                else "Current-standard behavior is not established"
            )
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
    f.docs_path.write_text(json.dumps(docs))
    for node, doc in docs.items():
        payload = {"complete": True, "status": "success", "content": {
            "markdown": doc["content"], "nodeId": node, "revision": doc["revision"]}}
        raw = json.dumps(payload, ensure_ascii=False).encode()
        name = okr_eval.digest(raw) + ".readback.json"
        doc["readback_name"] = name
        (tmp_path / name).write_bytes(raw)
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
                        "content_sha256": okr_eval.digest(doc["content"].encode()),
                        "readback": {"artifact_name": name},
                    },
                },
                {
                    "tool": "read_dingtalk_document_info",
                    "arguments": {"node_id": node},
                    "result": {"data": {"document": {"nodeId": node, "docUrl": "https://docs.example.invalid/" + node}}},
                },
                {
                    "tool": "read_task_artifact",
                    "arguments": {"name": name, "offset": 0},
                    "result": {"offset": 0, "total_chars": len(raw.decode()), "next_offset": None,
                               "sha256": okr_eval.digest(raw), "content": raw.decode()},
                },
            ]
        )
    f.docs_path.write_text(json.dumps(docs))
    f.trace_path.write_text("\n".join(json.dumps(c) for c in trace) + "\n")
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


def test_okr_base_scores_use_only_formal_evidence_bands():
    assert all(
        okr_eval.allowed_kr_evidence_band(score)
        for score in (0, 20, 40, 60, 80, 100)
    )
    assert not okr_eval.allowed_kr_evidence_band(30)


def test_preliminary_native_pass_does_not_satisfy_formal_behavior_rubric():
    saved = json.loads((Path(okr_eval.__file__).parent / "okr_results.v12.json").read_text())
    case = json.loads((Path(okr_eval.__file__).parent / "okr_cases.v5.json").read_text())["cases"][0]
    reviews = [r for a in saved["cases"][0]["final_grade"]["analyses"].values()
               for r in a["manager_reviews"]]
    checks = okr_eval.behavior_checks(reviews, case["expected"])
    assert not checks["formal_behavioral_dimensions"]
    assert not checks["behavioral_scores_match_evidence"]


def test_member_source_failure_fixture_proves_isolated_failure_scope():
    case = CASES[2]
    member = next(
        item for item in case["facts"]["roster"]["managers"]
        if item["user_id"] == "eval-failed"
    )
    payload = member_payload(member, case["facts"])
    outcome = payload["sourceOutcome"]
    receipt = payload["providerReceipt"]
    assert outcome["kind"] == "source_failed"
    assert outcome["scope"] == "member"
    assert "404" in outcome["diagnostic"]
    assert "roster and eval-a/eval-b reads succeeded" in outcome["diagnostic"]
    assert receipt["authorization_status"] == "granted"
    assert receipt["http_status"] == 404
    assert receipt["request_id"] == "fixture-okr-member-404"


def test_native_resume_skips_incomplete_saved_case_without_audit_verdict():
    pending = {"case_id": "recover-changed-date", "consumer": {"exit_code": 1}}
    approved = {
        "case_id": "composite-coverage",
        "passed": True,
        "final_audit": {"output": {"verdict": "approve"}},
    }
    audit_only = {
        "case_id": "recover-changed-source",
        "passed": False,
        "final_audit": {"output": {"verdict": "approve"}},
    }
    assert okr_eval.approved_saved_case([pending, audit_only, approved], pending["case_id"]) is None
    assert okr_eval.approved_saved_case([pending, audit_only, approved], audit_only["case_id"]) is None
    assert okr_eval.approved_saved_case([pending, audit_only, approved], approved["case_id"]) == approved


def test_native_consumer_output_requires_evidence_refs(tmp_path):
    import pytest
    from pydantic import ValidationError

    schema = okr_eval.purpose_output_model("general").model_json_schema()
    assert "evidence_refs" in schema["required"]
    assert "Required on every purpose output" in schema["properties"]["evidence_refs"]["description"]
    _, output = complete_fixture(tmp_path)
    output.pop("evidence_refs")
    with pytest.raises(ValidationError, match="evidence_refs"):
        okr_eval.parse_consumer_purpose_output(
            {"purpose_output_json": json.dumps(output, ensure_ascii=False)}
        )


def test_behavioral_rubric_requires_standard_scores_only_where_source_supports_them():
    case = json.loads((Path(okr_eval.__file__).parent / "okr_cases.v5.json").read_text())["cases"][0]
    expected = case["expected"]
    review = {
        "leadership_dimensions": [
            {"dimension": dimension, "score": 75 if dimension != "创新能力" else 60}
            for dimension in expected["leadership_dimensions"]
        ],
        "culture_dimensions": [
            {"dimension": dimension, "score": 60 if dimension == "AI时代原生能力" else 75}
            for dimension in expected["culture_dimensions"]
        ],
    }
    assert okr_eval.behavior_checks([review], expected)["behavioral_scores_match_evidence"]

    # Unsupported innovation or AI scores at the role-standard band are an
    # overclaim; a blanket 70-79 check used to reward this exact inflation.
    review["leadership_dimensions"][-1]["score"] = 70
    review["culture_dimensions"][0]["score"] = 70
    assert not okr_eval.behavior_checks([review], expected)["behavioral_scores_match_evidence"]


def test_native_document_tools_use_actual_service_receipt_contract(
    tmp_path, monkeypatch
):
    f = Fixture(CASES[0]["facts"], tmp_path)
    body = "# Synthetic report\n\nExact body.\n"
    (tmp_path / "report.md").write_text(body)
    monkeypatch.setattr(agent_cli, "_bound_scheduled_run", lambda *_args: (None, None))
    monkeypatch.setattr(
        agent_cli,
        "_current_task_generation",
        lambda *_args: "fixture",
    )
    monkeypatch.setattr(
        agent_cli,
        "_task_file_root",
        lambda *_args, **_kwargs: tmp_path,
    )
    monkeypatch.setattr("app.dws_client.DwsClient", lambda: DocumentProvider(f))
    tool = agent_cli.build_role_server(
        "consumer",
        task_id=1,
        db_path=Path("/unused-fixture-db"),
        execution_generation="fixture",
    )._tool_manager.get_tool("consumer_document_write")

    result = tool.fn(
        "report.md",
        "Protocol report",
        parent_id="eval-folder",
        workspace_id="eval-workspace",
    )

    assert result["content_sha256"] == okr_eval.digest(body.encode())
    assert result["ok"] is True and result["status"] == "success"
    assert result["operation"] == "created"
    assert result["provider_result"] == {
        "status": "success",
        "complete": True,
        "data": {
            "nodeId": result["node_id"],
            "verified": True,
            "success": True,
        },
    }
    assert result["readback"] is None
    assert f.documents()[result["node_id"]]["content"] == body


def test_native_fixture_exposes_searchable_independent_evidence(tmp_path):
    f = Fixture(CASES[0]["facts"], tmp_path)
    with f.production_tools("consumer") as tools:
        matches = tools["search_dingtalk_documents"]("林辰 eval-a")
        nodes = {item["node_id"] for item in matches["documents"]}
        assert "eval-evidence-2b0652a9fd02acfb" in nodes
        assert "eval-behavior-2b0652a9fd02acfb" in nodes
        evidence = tools["read_dingtalk_document"]("eval-evidence-2b0652a9fd02acfb")
        assert evidence["content"]["markdown"] == f.facts["evidence"]["eval-a"]["content"]


def test_native_fixture_exposes_previous_scheduled_run_id(tmp_path):
    f = Fixture(CASES[1]["facts"], tmp_path)
    context = f.scheduled_context()
    assert context["run_id"] == 2
    assert context["history"] == [{"run_id": 1, "status": "completed"}]


def test_native_audit_revision_receives_previous_review_context():
    prior = {
        "verdict": "revise",
        "feedback": ["Keep the customer-acceptance risk in one report section."],
    }
    context = okr_eval.audit_revision_context(prior)
    assert "Previous Audit revision context" in context
    assert "not source facts or authorization" in context
    assert "Keep the customer-acceptance risk in one report section." in context
    assert "judge the current candidate independently" in context


def test_actual_artifact_reader_reports_zero_based_ranges_on_full_reads(tmp_path):
    f = Fixture(CASES[0]["facts"], tmp_path)
    text = "## Heading\n"
    (tmp_path / "sample.md").write_text(text)
    with f.production_tools("audit") as tools:
        whole = tools["read_task_artifact"]("sample.md")
        assert whole["offset"] == 0 and whole["total_chars"] == len(text)
        assert whole["next_offset"] is None and whole["content"] == text
        cropped = tools["read_task_artifact"]("sample.md", offset=1)
        assert cropped["offset"] == 1 and cropped["content"] == text[1:]
        assert whole["sha256"] == cropped["sha256"] == okr_eval.digest(text.encode())
        assert "zero-based" in tools["read_task_artifact"].__doc__


def test_cropped_artifact_page_is_not_a_full_readback(tmp_path):
    text = "## Heading\n"
    (tmp_path / "sample.md").write_text(text)
    trace = [{"tool": "read_task_artifact", "arguments": {"name": "sample.md", "offset": 1},
              "result": {"sha256": okr_eval.digest(text.encode()), "content": text[1:]}}]
    assert not okr_eval.fully_read(trace, tmp_path, "sample.md")
    trace.append({"tool": "read_task_artifact", "arguments": {"name": "sample.md", "offset": 0},
                  "result": {"sha256": okr_eval.digest(text.encode()), "content": text[:1]}})
    assert okr_eval.fully_read(trace, tmp_path, "sample.md")


def test_prepared_schema_gives_the_subject_unique_owner_label(tmp_path):
    complete_fixture(tmp_path)
    plan = json.loads((tmp_path / "plan.json").read_text())
    for entry in plan["managers"]:
        if entry["outcome"] == "collected":
            assert entry["subject_label"] == f"林辰 [{entry['user_id']}]"
            schema = json.loads(Path(entry["schema_file"]).read_text())
            assert entry["subject_label"] in json.dumps(schema,ensure_ascii=False)


def test_hand_editing_canonical_summary_does_not_make_a_proposal_valid(tmp_path):
    case, output = complete_fixture(tmp_path)
    path = tmp_path / "summary.md"
    path.write_text(path.read_text()[1:])
    output["payload"]["actions"][0]["payload"]["content"] = path.read_text()
    result = okr_eval.grade(case, tmp_path, output)
    assert not result["passed"]
    assert not result["checks"]["canonical_render_artifacts"]


def test_accepted_bytes_survive_stricter_canonical_and_owner_recheck(tmp_path):
    saved = json.loads((Path(okr_eval.__file__).parent / "okr_results.v13.json").read_text())
    import pytest

    with pytest.raises(ValueError, match="fixed facts or expectations changed"):
        okr_eval.restore_accepted_candidate(saved, CASES[0], tmp_path)


def test_failed_saved_candidate_cannot_be_reused(tmp_path):
    import pytest

    saved = json.loads((Path(okr_eval.__file__).parent / "okr_results.v13.json").read_text())
    frozen_case = json.loads(
        (Path(okr_eval.__file__).parent / "okr_cases.v5.json").read_text()
    )["cases"][2]
    with pytest.raises(ValueError, match="failed candidate"):
        okr_eval.restore_accepted_candidate(saved, frozen_case, tmp_path)


def test_manifest_paths_must_belong_to_current_artifact_directory(tmp_path):
    case, output = complete_fixture(tmp_path)
    path = tmp_path / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["main_file"] = "/tmp/another-execution/main.md"
    path.write_text(json.dumps(manifest))
    result = okr_eval.grade(case, tmp_path, output)
    assert not result["passed"]
    assert result["checks"]["manifest_artifact_binding"] is False


def test_publication_cannot_assume_canonical_urls_without_provider_info(tmp_path):
    case, output = complete_fixture(tmp_path)
    f = Fixture(case["facts"], tmp_path)
    trace = [c for c in okr_eval.calls(tmp_path) if c["tool"] != "read_dingtalk_document_info"]
    f.trace_path.write_text("".join(json.dumps(c) + "\n" for c in trace))
    assert okr_eval.grade(case, tmp_path, output)["checks"]["all_canonical_urls_observed"] is False


def test_collection_without_real_prepare_call_fails(tmp_path):
    case, output = complete_fixture(tmp_path)
    f = Fixture(case["facts"], tmp_path)
    trace = [c for c in okr_eval.calls(tmp_path) if c["tool"] != "consumer_run_skill_script"]
    f.trace_path.write_text("".join(json.dumps(c) + "\n" for c in trace))
    assert okr_eval.grade(case, tmp_path, output)["checks"]["collect_before_prepare"] is False


def test_exact_full_provider_reads_are_equivalent_to_receipt_artifact_reads(tmp_path):
    case, output = complete_fixture(tmp_path)
    f = Fixture(case["facts"], tmp_path)
    trace = [c for c in okr_eval.calls(tmp_path) if c["tool"] != "read_task_artifact"]
    for node, doc in f.documents().items():
        trace.append({"tool": "read_dingtalk_document", "arguments": {"node_id": node},
                      "result": json.loads((tmp_path / doc["readback_name"]).read_text())})
    f.trace_path.write_text("".join(json.dumps(c) + "\n" for c in trace))
    assert okr_eval.grade(case, tmp_path, output)["passed"]


def test_stale_or_partial_direct_provider_reads_do_not_count(tmp_path):
    case, output = complete_fixture(tmp_path)
    f = Fixture(case["facts"], tmp_path)
    trace = [c for c in okr_eval.calls(tmp_path) if c["tool"] != "read_task_artifact"]
    for node, doc in f.documents().items():
        payload = json.loads((tmp_path / doc["readback_name"]).read_text())
        payload["content"]["revision"] -= 1
        trace.append({"tool": "read_dingtalk_document", "arguments": {"node_id": node}, "result": payload})
    f.trace_path.write_text("".join(json.dumps(c) + "\n" for c in trace))
    assert not okr_eval.grade(case, tmp_path, output)["checks"]["full_readbacks_consumed"]


def test_fixture_trace_is_not_a_business_artifact(tmp_path):
    f = Fixture(CASES[0]["facts"], tmp_path)
    f.record("read_okr_member", {"user_id": "eval-a"}, {"ok": True})
    assert f.trace_path.parent != f.work
    assert not (f.work / "tool-calls.jsonl").exists()
    assert okr_eval.calls(tmp_path)[0]["tool"] == "read_okr_member"


def test_fixture_provider_state_is_not_a_business_artifact(tmp_path):
    case, output = complete_fixture(tmp_path)
    f = Fixture(case["facts"], tmp_path)
    assert f.docs_path.parent != f.work
    (tmp_path / "documents.json").write_text('{"authored_publication_summary": true}')
    assert okr_eval.grade(case, tmp_path, output)["passed"]


def test_saved_trace_skill_redaction_is_idempotent():
    call = {"tool": "read_task_skill", "arguments": {"name": "selected"},
            "result": {"content": "exact selected Skill"}}
    first = okr_eval.persisted_call(call)
    assert first["result"] == {"skill_source_sha256": okr_eval.digest(b"exact selected Skill")}
    assert okr_eval.persisted_call(first) == first


def test_native_output_directory_is_checked_before_model_configuration(monkeypatch):
    import sys
    import pytest

    monkeypatch.setattr(sys, "argv", ["okr_eval", "--output", "absent-eval-directory/result.json"])
    def unexpected_configuration(*args):
        raise AssertionError("Model configuration reached before output path validation")
    monkeypatch.setattr(okr_eval.run, "configuration", unexpected_configuration)
    with pytest.raises(ValueError, match="output directory"):
        okr_eval.main()


def test_recovery_cannot_grade_old_source_as_current(tmp_path):
    _, output = complete_fixture(tmp_path)
    result = okr_eval.grade(CASES[1], tmp_path, output)
    assert not result["passed"]
    assert result["checks"]["exact_current_source_payloads"] is False


def test_prepare_marks_exact_analysis_reuse_without_rewriting(tmp_path):
    case, _ = complete_fixture(tmp_path)
    report = okr_eval.load_script("report")
    plan = json.loads((tmp_path / "plan.json").read_text())
    before = {e["analysis_file"]: (tmp_path / e["analysis_file"]).read_bytes()
              for e in plan["managers"] if e["outcome"] == "collected"}

    def prepare(date):
        return report.prepare(roster_file=tmp_path / "okr-roster.json",
                              members_dir=tmp_path, period_label=case["facts"]["period_label"],
                              report_date=date, output_dir=tmp_path)

    repeated = prepare(case["facts"]["report_date"])
    assert all(e["analysis_reusable"] for e in repeated["managers"] if e["outcome"] == "collected")
    changed_member = next(m for m in case["facts"]["roster"]["managers"] if m["user_id"] == "eval-b")
    Fixture(CASES[1]["facts"], tmp_path).write_json(
        "okr-member-eval-b.json", member_payload(changed_member, CASES[1]["facts"])
    )
    changed_source = prepare(case["facts"]["report_date"])
    assert {e["user_id"]: e["analysis_reusable"] for e in changed_source["managers"]
            if e["outcome"] == "collected"} == {"eval-a": True, "eval-b": False}
    changed = prepare("2026-10-20")
    assert not any(e["analysis_reusable"] for e in changed["managers"] if e["outcome"] == "collected")
    assert all((tmp_path / name).read_bytes() == body for name, body in before.items())


def test_prepare_does_not_reuse_analysis_failure_placeholder(tmp_path):
    case, _ = complete_fixture(tmp_path)
    report = okr_eval.load_script("report")
    plan = json.loads((tmp_path / "plan.json").read_text())
    entry = next(e for e in plan["managers"] if e["user_id"] == "eval-a")
    failure = {
        "_source_sha256": entry["source_sha256"],
        "analysis_failure": {"diagnostic": "analysis not completed"},
    }
    path = tmp_path / entry["analysis_file"]
    original = path.read_bytes()
    path.write_text(json.dumps(failure, ensure_ascii=False))
    repeated = report.prepare(
        roster_file=tmp_path / "okr-roster.json",
        members_dir=tmp_path,
        period_label=case["facts"]["period_label"],
        report_date=case["facts"]["report_date"],
        output_dir=tmp_path,
    )
    assert not next(
        e for e in repeated["managers"] if e["user_id"] == "eval-a"
    )["analysis_reusable"]
    assert next(e for e in repeated["managers"] if e["user_id"] == "eval-b")["analysis_reusable"]
    assert path.read_text() == json.dumps(failure, ensure_ascii=False)
    assert original


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
        fixture_args = next(
            json.loads(value.split("=", 1)[1])
            for value in command
            if value.startswith("mcp_servers.okr_acceptance.args=")
        )
        assert fixture_args[-2:] == ["--run-id", "42"]
        assert '"allOf"' in kwargs["input"]
        Path(command[command.index("--output-last-message") + 1]).write_text(
            json.dumps({"purpose_output_json": json.dumps(wire)})
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(okr_eval.subprocess, "run", accepted)
    phase = okr_eval.native(
        "consumer", facts, tmp_path / "artifacts", tmp_path / "native", [],
        scheduled_run_id=42,
    )
    assert phase["output"] == wire


def test_synthetic_audit_prompt_states_its_offline_execution_boundary(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    facts = tmp_path / "facts.json"
    facts.write_text("{}")
    inputs = []

    def accepted(command, **kwargs):
        inputs.append(kwargs["input"])
        Path(command[command.index("--output-last-message") + 1]).write_text(
            json.dumps({"verdict": "approve", "feedback": []})
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(okr_eval.subprocess, "run", accepted)
    okr_eval.native("audit", facts, tmp_path / "artifacts", tmp_path / "audit", [])

    assert "本评测只评估合成 verdict" in inputs[0]


def test_native_feedback_continues_after_audit_returns_another_revision(
    tmp_path, monkeypatch
):
    calls = []
    outputs = iter(
        [
            {"output": {"candidate": "second correction"}},
            {"output": {"verdict": "approve", "feedback": []}},
        ]
    )

    def fake_native(role, *args, **kwargs):
        calls.append((role, args, kwargs))
        return next(outputs)

    monkeypatch.setattr(okr_eval, "native", fake_native)
    latest_consumer = {"output": {"candidate": "first correction"}}
    corrections = [{"output": {"candidate": "first correction"}}]
    audits = [
        {"output": {"verdict": "revise", "feedback": ["first finding"]}},
        {"output": {"verdict": "revise", "feedback": ["second finding"]}},
    ]

    latest_consumer, corrections, audits = okr_eval.continue_native_feedback(
        tmp_path / "facts.json",
        tmp_path / "artifacts",
        tmp_path,
        [],
        latest_consumer,
        corrections,
        audits,
        scheduled_run_id=42,
    )

    assert latest_consumer["output"]["candidate"] == "second correction"
    assert len(corrections) == 2
    assert audits[-1]["output"]["verdict"] == "approve"
    assert calls[0][2]["feedback"] == audits[1]["output"]
    assert all(call[2]["scheduled_run_id"] == 42 for call in calls)


def test_native_feedback_keeps_the_production_cycle_bound(tmp_path, monkeypatch):
    calls = []
    outputs = iter(
        [
            {"output": {"candidate": "second correction"}},
            {"output": {"verdict": "revise", "feedback": ["third finding"]}},
            {"output": {"candidate": "third correction"}},
            {"output": {"verdict": "revise", "feedback": ["fourth finding"]}},
        ]
    )

    def fake_native(role, *args, **kwargs):
        calls.append((role, args, kwargs))
        return next(outputs)

    monkeypatch.setattr(okr_eval, "native", fake_native)
    _, corrections, audits = okr_eval.continue_native_feedback(
        tmp_path / "facts.json",
        tmp_path / "artifacts",
        tmp_path,
        [],
        {"output": {"candidate": "first correction"}},
        [{"output": {"candidate": "first correction"}}],
        [
            {"output": {"verdict": "revise", "feedback": ["first finding"]}},
            {"output": {"verdict": "revise", "feedback": ["second finding"]}},
        ],
        scheduled_run_id=42,
    )

    assert len(corrections) == 3
    assert len(audits) == 4
    assert len(calls) == 4


def test_unscored_current_roster_titles_are_not_erased(tmp_path):
    case, _ = complete_fixture(tmp_path)
    full = (tmp_path / "full.md").read_text()
    for member in case["facts"]["roster"]["managers"]:
        if member["user_id"] in case["expected"]["gaps"]:
            assert f"| {member['name']} | {member['title']} |" in full


def test_group_summary_keeps_exact_member_gap_evidence(tmp_path):
    complete_fixture(tmp_path)
    report = okr_eval.load_script("report")
    source = json.loads((tmp_path / "source.json").read_text())
    analysis, _ = report._analysis_from_files(source, tmp_path)
    summary = (tmp_path / "summary.md").read_text()
    for gap in analysis.member_gaps:
        assert gap.evidence in summary
    assert "404" not in summary
    assert "OKR 目标记录未能读取" in summary


def test_cases_are_versioned_without_rewriting_failed_oracle():
    import hashlib

    def stable_non_behavior_facts(facts):
        stable = copy.deepcopy(facts)
        stable["evidence"].pop("behavior", None)
        stable["evidence"].pop("behavior_by_manager", None)
        stable.pop("run_id", None)
        stable.pop("previous_run_id", None)
        return stable

    root = Path(okr_eval.__file__).parent
    old = root / "okr_cases.v1.json"
    assert (
        hashlib.sha256(old.read_bytes()).hexdigest()
        == "b85fb6cc46628683f3bc18d8dc8def3760fd128543240b6ce9a1b76ade05d92a"
    )
    first = json.loads(old.read_text())["cases"]
    second = root / "okr_cases.v2.json"
    assert hashlib.sha256(second.read_bytes()).hexdigest() == "646a1857f50fd16a45d2f9520df906ee9dafbf0bb7864f4cd1226c961469bf04"
    old_v2 = json.loads(second.read_text())["cases"]
    for before, after in zip(old_v2, BASE_CASES, strict=True):
        assert stable_non_behavior_facts(before["facts"]) == stable_non_behavior_facts(after["facts"])
    assert hashlib.sha256((root / "okr_cases.v3.json").read_bytes()).hexdigest() == "c8ed2093242a7cef57f3025a2f062a7aa4a82da3b67901fcb92a7843b8569438"
    fourth = root / "okr_cases.v4.json"
    assert hashlib.sha256(fourth.read_bytes()).hexdigest() == "de85506221508bee7aa4db312d5c566a8f28b96a9ebac87d919ac34dbfe0ca5c"
    fifth = root / "okr_cases.v5.json"
    assert fifth.is_file()
    assert hashlib.sha256(fifth.read_bytes()).hexdigest() == "ae2cd3caecb562d6efa305f4249a4f39b1cad68e8dbbe02cd7a65a1f0ef3b448"
    sixth = root / "okr_cases.v6.json"
    assert sixth.is_file()
    sixth_cases = json.loads(sixth.read_text())["cases"]
    assert [case["id"] for case in sixth_cases] == [
        "composite-coverage",
        "fresh-source-snapshot",
        "fresh-date-snapshot",
        "shared-source-auth-failure",
    ]
    seventh = root / "okr_cases.v7.json"
    seventh_cases = json.loads(seventh.read_text())["cases"]
    assert [case["id"] for case in seventh_cases[:4]] == [case["id"] for case in sixth_cases]
    empty_search = seventh_cases[4]
    assert empty_search["id"] == "empty-independent-search-continues-analysis"
    assert empty_search["facts"]["no_independent_document_ids"] == ["eval-a", "eval-b"]
    assert empty_search["expected"]["independent_searches_without_matches"] == [
        {"user_id": "eval-a", "display_name": "林辰"},
        {"user_id": "eval-b", "display_name": "林辰"},
    ]
    assert empty_search["expected"]["behavioral_score_ranges"] == CASES[0]["expected"]["behavioral_score_ranges"]
    old_fifth = json.loads(fifth.read_text())["cases"]
    for before, after in zip(old_fifth, sixth_cases, strict=True):
        assert stable_non_behavior_facts(before["facts"]) == stable_non_behavior_facts(after["facts"])
        if after["id"] in {"fresh-source-snapshot", "fresh-date-snapshot"}:
            assert "resume_from" not in after
    old_v3 = json.loads((root / "okr_cases.v3.json").read_text())["cases"]
    for before, after in zip(old_v3, BASE_CASES, strict=True):
        assert stable_non_behavior_facts(before["facts"]) == stable_non_behavior_facts(after["facts"])
        behavior = after["facts"]["evidence"]["behavior_by_manager"]
        assert set(behavior) == {"eval-a", "eval-b"}
        assert behavior["eval-a"]["id"] != behavior["eval-b"]["id"]
        assert "林辰 [eval-a]" in behavior["eval-a"]["content"]
        assert "林辰 [eval-b]" in behavior["eval-b"]["content"]
        assert "负责人轮流主持并独立跟进跨团队依赖" in behavior["eval-a"]["content"]
        assert "每周带两名工程师复盘定位方法" in behavior["eval-b"]["content"]
    date_case = next(c for c in CASES if c["id"] == "fresh-date-snapshot")
    assert "没有AI使用及工作增益证据" in date_case["facts"]["evidence"]["behavior_by_manager"]["eval-b"]["content"]
    for before, after in zip(first, BASE_CASES, strict=True):
        assert stable_non_behavior_facts(before["facts"]) == stable_non_behavior_facts(after["facts"])
        if "base_score_ranges" in before["expected"]:
            assert before["expected"]["base_score_ranges"]["eval-a"] == [60, 80]
            assert after["expected"]["base_score_ranges"]["eval-a"] == [100, 100]
            assert (
                before["expected"]["base_score_ranges"]["eval-b"]
                == after["expected"]["base_score_ranges"]["eval-b"]
            )


def test_actual_analysis_cannot_reuse_a_stale_source_digest(tmp_path):
    case, output = complete_fixture(tmp_path)
    plan = json.loads((tmp_path / "plan.json").read_text())
    entry = next(e for e in plan["managers"] if e["outcome"] == "collected")
    path = tmp_path / entry["analysis_file"]
    value = json.loads(path.read_text())
    value["_source_sha256"] = "stale"
    path.write_text(json.dumps(value))
    result = okr_eval.grade(case, tmp_path, output)
    assert not result["passed"] and "source changed" in result["validation_error"]


def test_actual_publication_requires_current_parent_and_full_readback(tmp_path):
    case, output = complete_fixture(tmp_path)
    path = Fixture(case["facts"], tmp_path).docs_path
    docs = json.loads(path.read_text())
    node = next(n for n, d in docs.items() if d["parent_id"] == "main")
    docs[node]["parent_id"] = "other-document"
    path.write_text(json.dumps(docs))
    assert not okr_eval.grade(case, tmp_path, output)["passed"]


def test_actual_summary_body_cannot_be_substituted(tmp_path):
    case, output = complete_fixture(tmp_path)
    output["payload"]["actions"][0]["payload"]["content"] = "Different summary"
    assert not okr_eval.grade(case, tmp_path, output)["passed"]


def test_shared_failure_cannot_be_an_unscored_success_or_publish(tmp_path):
    case = next(c for c in CASES if c["id"] == "shared-source-auth-failure")
    f = Fixture(case["facts"], tmp_path)
    f.record("read_okr_member", {"user_id": "eval-a"}, {"source_outcome": {"scope": "shared"}})
    wire = {
        "action": "failed",
        "reason": "Shared source unavailable",
        "target": {},
        "payload": {},
        "evidence_refs": [],
    }
    assert okr_eval.grade(case, tmp_path, wire)["passed"]
    wire["action"] = "no_action"
    assert not okr_eval.grade(case, tmp_path, wire)["passed"]
    wire["action"] = "failed"
    f.record("consumer_document_write", {}, {})
    assert not okr_eval.grade(case, tmp_path, wire)["passed"]


def test_filtered_native_evaluation_status_uses_the_selected_case_set():
    assert okr_eval.evaluation_status([{"passed": True}], ["one-selected-case"]) == "passed"
    assert okr_eval.evaluation_status([], ["one-selected-case"]) == "failed"
    assert okr_eval.evaluation_status([{"passed": True}], ["a", "b"]) == "failed"
    assert okr_eval.evaluation_status([{"passed": False}], ["one-selected-case"]) == "failed"


def test_builtin_resource_inventory_is_distinct_from_provider_access(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    facts = tmp_path / "facts.json"
    facts.write_text("{}")
    wire = {
        "action": "no_action",
        "reason": "Draft only",
        "target": {},
        "payload": {},
        "evidence_refs": [],
    }
    server = "codex"

    def accepted(command, **kwargs):
        kwargs["stdout"].write(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "server": server,
                        "tool": "list_mcp_resources",
                        "status": "completed",
                    },
                }
            )
            + "\n"
        )
        Path(command[command.index("--output-last-message") + 1]).write_text(
            json.dumps({"purpose_output_json": json.dumps(wire)})
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(okr_eval.subprocess, "run", accepted)
    phase = okr_eval.native(
        "consumer", facts, tmp_path / "artifacts", tmp_path / "native-one", []
    )
    assert phase["all_tools_fixture_only"]
    server = "external-provider"
    phase = okr_eval.native(
        "consumer", facts, tmp_path / "artifacts", tmp_path / "native-two", []
    )
    assert not phase["all_tools_fixture_only"]


def test_summary_markdown_cannot_be_rewritten_after_render(tmp_path):
    case, output = complete_fixture(tmp_path)
    body = output["payload"]["actions"][0]["payload"]["content"]
    output["payload"]["actions"][0]["payload"]["content"] = body.replace(
        "[打开完整周报](https://docs.example.invalid/main)",
        "https://docs.example.invalid/main",
    )
    assert not okr_eval.grade(case, tmp_path, output)["passed"]


def test_release_manifest_pins_verified_current_installation():
    root = Path(__file__).resolve().parents[1]
    expected = json.loads(
        (root / "evals/scheduled_task_generic/okr-release-preconditions.json").read_text()
    )
    entries = {
        row["source"]: row for row in json.loads(
            (root / "ci/consumer-system-contract-release.json").read_text()
        )["files"]
    }
    assert set(entries) == {row["source"] for row in expected["files"]}
    for row in expected["files"]:
        entry = entries[row["source"]]
        source_digest = hashlib.sha256((root / row["source"]).read_bytes()).hexdigest()
        assert entry["new_sha256"] == source_digest, row["source"]
        assert len(entry["old_sha256"]) == 64
        assert set(entry["old_sha256"]) <= set("0123456789abcdef")
        if "old_managed_sha256" in entry:
            assert entry["old_managed_sha256"] == entry["old_sha256"]


def test_synthetic_scheduled_context_exposes_current_artifacts_without_contents(tmp_path):
    fixture = Fixture(CASES[0]["facts"], tmp_path)
    content = '{"private":"must not be exposed"}'
    (tmp_path / "analysis.json").write_text(content)

    tool = fixture.build_server("consumer", {})._tool_manager.get_tool(
        "read_scheduled_context"
    ).fn

    context = tool()

    assert {
        "name": "analysis.json",
        "sha256": okr_eval.digest(content.encode()),
    } in context["task_artifacts"]
    assert all("content" not in artifact for artifact in context["task_artifacts"])
    assert "must not be exposed" not in json.dumps(context)


def test_synthetic_scheduled_context_uses_requested_run_id(tmp_path):
    fixture = Fixture(CASES[0]["facts"], tmp_path, scheduled_run_id=42)
    tool = fixture.build_server("consumer", {})._tool_manager.get_tool(
        "read_scheduled_context"
    ).fn

    assert tool()["run_id"] == 42


def test_native_phase_failure_keeps_diagnostic_and_fixture_tool_trace(tmp_path):
    fixture = Fixture(CASES[0]["facts"], tmp_path)
    fixture.record("read_scheduled_context", {}, {"task_artifacts": []})
    audit = {
        "output": None,
        "diagnostic": "Native final output failed the production contract",
    }

    result = okr_eval.native_failure_case(
        "composite-coverage", tmp_path, "audit", audit=audit
    )

    assert result["failed_phase"] == "audit"
    assert result["audit"]["diagnostic"] == audit["diagnostic"]
    assert result["raw_tool_trace"][0]["tool"] == "read_scheduled_context"


def test_okr_eval_pins_scheduled_context_implementation_hash():
    assert "read_scheduled_context" in okr_eval.application_contract_hashes()
