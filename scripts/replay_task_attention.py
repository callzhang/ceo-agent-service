"""One native Task Agent turn on a database copy, or one fixed semantic case.

Run baseline and candidate in separate processes with explicit --code-root and
CEO_SKILLS_ROOT. Expected labels are evaluated only after persisted readback.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

EVALUATION_SCOPE = "task-agent:attention-eval:v1"
W39_SOURCE_REF = "dingtalk-doc:a9E05BDRVQvy7QEacPZLB4anJ63zgkYA#sha256=21661643562265ca27e3369112a7ce3e91d9cbb6d21733050b5c3e7a9d42bf1e"


def require_copy(path):
    from app.config import worker_db_path

    if path.resolve() == worker_db_path().resolve():
        raise ValueError("evaluation cannot open the worker database; supply a copy")


def load_cases(path):
    payload = json.loads(path.read_text())
    if payload["version"] != 1:
        raise ValueError("fixture version must be 1")
    cases = payload["cases"]
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("duplicate case_id")
    return cases


def scoped_runner(runner_type, codex):
    class AttentionEvaluationRunner(runner_type):
        def decide(self, *args, **kwargs):
            kwargs["session_scope_id"] = EVALUATION_SCOPE
            return super().decide(*args, **kwargs)

    return AttentionEvaluationRunner(codex)


def seed_case(store, case):
    """Seed stored facts with domain commands; no expected response enters state."""
    from app.task_models import WorkItem
    from app.task_semantic_service import (
        RecordCandidate,
        SourceSignal,
        TaskSemanticService,
    )
    from app.task_business_resolution import BusinessResolutionService
    from app.task_attention_projection import (
        AttentionProposal,
        BusinessAttentionProjection,
    )
    from app.task_semantic_models import AttentionCategory
    from app.project_context_service import ProjectContextService

    context = case["existing_context"]
    resolution = BusinessResolutionService(store)
    anchors = {}
    projects = {}
    for project in context.get("projects", []):
        anchor = resolution.register_anchor(
            anchor_type="project", anchor_ref=project["ref"], title=project["title"]
        )
        project_id = resolution.register_official_project(
            anchor_id=anchor, registry_source=project["registry_source"]
        )
        anchors[project["title"]] = anchor
        projects[project["title"]] = project_id
    tasks = {}
    signals = {}
    for task in context.get("tasks", []):
        result = TaskSemanticService(store).record_candidate(
            RecordCandidate(
                title=task["title"],
                description=task["description"],
                signal=SourceSignal(**task["signal"]),
            )
        )
        tasks[task["title"]] = result.task_id
        signals[task["title"]] = result.signal_id
        if task.get("project_title"):
            resolution.confirm_anchor_match(
                task_id=result.task_id,
                anchor_id=anchors[task["project_title"]],
                evidence_signal_id=result.signal_id,
            )
            with store.business_task_transaction() as db:
                ProjectContextService(store).apply(
                    project_id=projects[task["project_title"]], context=None,
                    signal_ids=(result.signal_id,), db=db,
                )
    for attention in context.get("attention", []):
        anchor = anchors[attention["project_title"]]
        names = attention["task_titles"]
        assessment_json = "{}"
        if assessment := attention.get("assessment"):
            evidence_task_title = assessment["evidence_task_title"]
            assessment_json = json.dumps(
                {
                    "assessment_basis": assessment["assessment_basis"],
                    "material_trigger": assessment["material_trigger"],
                    "inference": attention["why_attention"],
                    "evidence": [
                        {
                            "signal_id": signals[evidence_task_title],
                            "source_ref": assessment["source_ref"],
                            "source_excerpt": assessment["source_excerpt"],
                            "source_time": assessment["source_time"],
                            "source_link": assessment["source_link"],
                        }
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        BusinessAttentionProjection(store).upsert(
            AttentionProposal(
                stable_key=f"project:{anchor}",
                category=AttentionCategory.WATCH,
                title=attention["title"],
                business_area="",
                why_attention=attention["why_attention"],
                current_state=attention["current_state"],
                ceo_action=attention["ceo_action"],
                anchor_id=anchor,
                task_ids=tuple(tasks[name] for name in names),
                evidence_signal_id=signals[names[0]],
                assessment_json=assessment_json,
            )
        )
    item = WorkItem.model_validate(case["work_item"])
    return store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )


def read_domain(store):
    with store._connect() as db:
        return {
            name: [
                dict(row) for row in db.execute(f"select * from {table} order by id")
            ]
            for name, table in (
                ("tasks", "business_tasks"),
                ("projects", "business_projects"),
                ("attention", "business_attention_items"),
                ("attention_events", "business_attention_events"),
                ("task_events", "business_task_events"),
            )
        }


def evidence_contains(text, quote):
    if not isinstance(text, str) or not isinstance(quote, str) or not quote.strip():
        return False
    if quote in text:
        return True
    if not text.lstrip().startswith(("{", "[")):
        return False
    value = json.loads(text)

    def contains(value):
        if isinstance(value, str):
            return quote in value
        if isinstance(value, dict):
            return any(contains(child) for child in value.values())
        if isinstance(value, list):
            return any(contains(child) for child in value)
        return False

    return contains(value)


def readback(store, *, input_id, before, expected=None):
    from app.task_source_documents import source_is_observed

    after = read_domain(store)
    with store._connect() as db:
        run = db.execute(
            "select * from task_agent_runs where summary_input_id=? order by id desc limit 1",
            (input_id,),
        ).fetchone()
        attempts = [
            dict(row)
            for row in db.execute(
                "select id,route_name,runtime_kind,model,status,failure_code,attempt_purpose "
                "from agent_runtime_attempts where workload_kind='task' and workload_key=? order by id",
                (str(run["id"]) if run is not None else "",),
            )
        ]
        links = [
            dict(row) for row in db.execute("select * from business_task_anchor_links")
        ]
        project_evidence = {
            (row["canonical_anchor_id"], row["signal_id"])
            for row in db.execute(
                "select p.canonical_anchor_id,e.signal_id from business_projects p "
                "join business_project_evidence e on e.project_id=p.id"
            )
        }
        active_anchors = {
            row["id"] for row in db.execute("select id from business_anchors where active=1")
        }
    project_by_anchor = {p["canonical_anchor_id"]: p for p in after["projects"]}
    task_by_id = {task["id"]: task for task in after["tasks"]}
    active = [card for card in after["attention"] if card["status"] == "active"]
    cited_ids = {
        quote["signal_id"]
        for card in active
        for quote in json.loads(card.get("assessment_json", "{}")).get("evidence", [])
    }
    signals = {
        signal_id: signal.model_dump(mode="json")
        for signal_id in cited_ids
        if (signal := store.get_business_task_signal(signal_id)) is not None
    }
    evidence_valid = True
    source_counts = []
    cards = []
    for card in active:
        assessment = json.loads(card.get("assessment_json", "{}"))
        evidence = assessment.get("evidence", [])
        members = [
            link.task_id for link in store.list_business_attention_tasks(card["id"])
        ]
        valid = (
            bool(evidence) and card["anchor_id"] in project_by_anchor
            and card["anchor_id"] in active_anchors
            and card["evidence_signal_id"] in {quote["signal_id"] for quote in evidence}
        )
        for quote in evidence:
            signal = signals.get(quote["signal_id"])
            valid = (
                valid
                and bool(signal)
                and source_is_observed(signal["source_type"])
                and signal["source_ref"] == quote["source_ref"]
                and evidence_contains(signal["evidence_text"], quote["source_excerpt"])
            )
            valid = valid and quote.get("source_time") == signal["source_time"]
            valid = valid and quote.get("source_link") == json.loads(
                signal["context_json"]
            ).get("source_link", "")
            valid = valid and (card["anchor_id"], quote["signal_id"]) in project_evidence
        for task_id in members:
            task = task_by_id[task_id]
            valid = (
                valid
                and task["status"] in ("open", "waiting")
                and task["business_relevance"] == "relevant"
            )
            valid = valid and any(
                link["task_id"] == task_id
                and link["anchor_id"] == card["anchor_id"]
                and link["status"] == "confirmed"
                and link["active"]
                for link in links
            )
        evidence_valid = evidence_valid and bool(valid)
        source_counts.append(len({quote["source_ref"] for quote in evidence}))
        cards.append(
            {
                "id": card["id"],
                "project_title": project_by_anchor.get(card["anchor_id"], {}).get(
                    "title"
                ),
                "anchor_id": card["anchor_id"],
                "task_ids": members,
                "assessment": assessment,
                "current_state": card["current_state"],
                "why_attention": card["why_attention"],
                "evidence_valid": bool(valid),
            }
        )
    decision = json.loads(run["decision_json"]) if run is not None else {}
    proposal_count = sum(
        bool(row.get("attention_proposal"))
        for row in decision.get("project_assessments", [])
    )
    projection = (
        json.loads(run["projection_json"])
        if run is not None and "projection_json" in run.keys()
        else {}
    )
    failures = []
    if proposal_count and not projection:
        failures.append("projection_not_successful")
    if projection and (
        projection["status"]
        not in (("completed",) if proposal_count else ("completed", "no_proposal"))
        or any(outcome["status"] != "applied" for outcome in projection["outcomes"])
        or projection["recompute_error"]
    ):
        failures.append("projection_not_successful")
    proposal_indexes = {
        index for index, assessment in enumerate(decision.get("project_assessments", []))
        if assessment.get("attention_proposal")
    }
    outcomes = projection.get("outcomes", [])
    if proposal_count and (
        len(outcomes) != proposal_count
        or {entry.get("assessment_index") for entry in outcomes} != proposal_indexes
        or any(
            not any(card["id"] == entry.get("attention_id")
                    and card["anchor_id"] == entry.get("anchor_id")
                    and card["status"] == "active" for card in after["attention"])
            for entry in outcomes if entry.get("assessment_index") in proposal_indexes
        )
    ):
        failures.append("projection_not_successful")
    if "project_decisions" in decision:
        assessments = decision.get("project_assessments", [])
        receipts = projection.get("project_assessments", [])
        if len(receipts) != len(assessments) or {
            entry.get("assessment_index") for entry in receipts
        } != set(range(len(assessments))):
            failures.append("project_assessment_receipt_mismatch")
    duplicate_cards = len(active) - len({card["anchor_id"] for card in active})
    if duplicate_cards:
        failures.append("duplicate_project_cards")
    if not evidence_valid:
        failures.append("unverifiable_attention_evidence")
    if run is not None and run["status"] != "completed":
        failures.append("task_agent_run_failed")
    if input_id is not None and run is None:
        failures.append("task_agent_run_missing")
    if expected is not None:
        actual_projects = sorted(card["project_title"] for card in cards)
        wanted = sorted(expected["attention_projects"])
        if actual_projects != wanted:
            failures.append("attention_project_mismatch")
        if sorted(p["title"] for p in after["projects"]) != sorted(
            expected["project_titles"]
        ):
            failures.append("official_project_mismatch")
        wanted_task_counts = (
            expected["allowed_task_counts"]
            if "allowed_task_counts" in expected
            else [expected["task_count"]]
        )
        if len(after["tasks"]) not in wanted_task_counts:
            failures.append("task_count_mismatch")
        if any(count < expected["minimum_evidence_sources"] for count in source_counts):
            failures.append("missing_evidence_source")
        required_sources = set(expected.get("required_source_refs", []))
        if any(
            not required_sources.issubset(
                {
                    quote["source_ref"]
                    for quote in card["assessment"].get("evidence", [])
                }
            )
            for card in cards
            if card["project_title"] in wanted
        ):
            failures.append("missing_required_source")
        required_members = expected.get("required_project_member_counts", {})
        if any(
            len(card["task_ids"]) < required_members.get(card["project_title"], 0)
            for card in cards
        ):
            failures.append("missing_project_task_member")
        initial_ids = {card["id"] for card in before["attention"]}
        if (
            expected.get("reuse_attention")
            and {card["id"] for card in active} != initial_ids
        ):
            failures.append("attention_identity_changed")
        if (
            expected.get("preserve_project_registry")
            and before["projects"] != after["projects"]
        ):
            failures.append("project_registry_changed")
        if "project_assessments" in expected and run is not None:
            raw_assessments = decision.get("project_assessments")
            if "project_assessments" not in decision:
                failures.append("project_assessments_missing")
                raw_assessments = []
            expected_assessments = expected["project_assessments"]
            if len(raw_assessments) != len(expected_assessments):
                failures.append("project_assessment_coverage_mismatch")
            receipt_assessments = projection.get("project_assessments")
            if not isinstance(receipt_assessments, list):
                receipt_assessments = []
            receipt_indexes = [
                entry.get("assessment_index")
                for entry in receipt_assessments
                if isinstance(entry, dict)
            ]
            receipt_index_coverage_valid = (
                len(receipt_assessments) == len(raw_assessments)
                and len(receipt_indexes) == len(receipt_assessments)
                and all(
                    isinstance(index, int) and not isinstance(index, bool)
                    for index in receipt_indexes
                )
                and len(set(receipt_indexes)) == len(receipt_indexes)
                and set(receipt_indexes) == set(range(len(raw_assessments)))
            )
            if not receipt_index_coverage_valid:
                failures.append("project_assessment_receipt_mismatch")
            receipt_by_index = {
                entry["assessment_index"]: entry
                for entry in receipt_assessments
                if (
                    isinstance(entry, dict)
                    and isinstance(entry.get("assessment_index"), int)
                    and not isinstance(entry.get("assessment_index"), bool)
                )
            }
            input_row = (
                store.get_work_summary_input(input_id) if input_id is not None else None
            )
            work_item = (
                json.loads(input_row.payload_json) if input_row is not None else {}
            )
            source = work_item.get("source", {})
            source_text = work_item.get("summary", "")
            raw_by_title = {
                assessment.get("project_title"): (index, assessment)
                for index, assessment in enumerate(raw_assessments)
            }
            expected_titles = {
                assessment["project_title"] for assessment in expected_assessments
            }
            if (
                len(raw_by_title) != len(raw_assessments)
                or set(raw_by_title) != expected_titles
            ):
                failures.append("project_assessment_coverage_mismatch")
            for wanted_assessment in expected_assessments:
                matched = raw_by_title.get(wanted_assessment["project_title"])
                if matched is None:
                    continue
                index, actual_assessment = matched
                if actual_assessment.get("outcome") != wanted_assessment["outcome"]:
                    failures.append("project_assessment_outcome_mismatch")
                reason = actual_assessment.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    failures.append("project_assessment_reason_missing")
                wanted_evidence = wanted_assessment["evidence"]
                actual_evidence = actual_assessment.get("evidence", [])
                citations_valid = all(
                    any(
                        actual.get("source_ref") == required["source_ref"]
                        and required["source_excerpt"]
                        in actual.get("source_excerpt", "")
                        for actual in actual_evidence
                    )
                    for required in wanted_evidence
                )
                for evidence in actual_assessment.get("evidence", []):
                    signal_id = evidence.get("signal_id")
                    if signal_id is None:
                        citations_valid = (
                            citations_valid
                            and evidence.get("source_ref") == source.get("ref")
                            and evidence_contains(
                                source_text, evidence.get("source_excerpt", "")
                            )
                        )
                    else:
                        signal = store.get_business_task_signal(signal_id)
                        citations_valid = (
                            citations_valid
                            and signal is not None
                            and source_is_observed(signal.source_type)
                            and signal.source_ref == evidence.get("source_ref")
                            and evidence_contains(
                                signal.evidence_text, evidence.get("source_excerpt", "")
                            )
                        )
                receipt = receipt_by_index.get(index)
                receipt_valid = receipt is not None
                if receipt is not None:
                    receipt_task_ids = receipt.get("task_ids", [])
                    receipt_task_ids_are_integers = all(
                        isinstance(task_id, int) and not isinstance(task_id, bool)
                        for task_id in receipt_task_ids
                    )
                    receipt_valid = (
                        receipt.get("status") == wanted_assessment["application_status"]
                        and len(receipt_task_ids) == wanted_assessment["task_count"]
                        and receipt_task_ids_are_integers
                        and len(set(receipt_task_ids)) == len(receipt_task_ids)
                        and set(receipt_task_ids).issubset(task_by_id)
                    )
                    anchor_id = receipt.get("anchor_id")
                    if wanted_assessment.get("anchor_required", True):
                        receipt_valid = (
                            receipt_valid
                            and project_by_anchor.get(anchor_id, {}).get("title")
                            == wanted_assessment["project_title"]
                        )
                    else:
                        receipt_valid = receipt_valid and anchor_id is None
                    receipt_evidence = receipt.get("evidence", [])
                    receipt_valid = receipt_valid and all(
                        any(
                            actual.get("source_ref") == required["source_ref"]
                            and required["source_excerpt"]
                            in actual.get("source_excerpt", "")
                            for actual in receipt_evidence
                        )
                        for required in wanted_evidence
                    )
                    for evidence in receipt.get("evidence", []):
                        signal_id = evidence.get("signal_id")
                        if signal_id is None:
                            receipt_valid = (
                                receipt_valid
                                and evidence.get("source_ref") == source.get("ref")
                                and evidence_contains(
                                    source_text, evidence.get("source_excerpt", "")
                                )
                                and evidence.get("source_time", "")
                                == source.get("created_at", "")
                                and evidence.get("source_link", "") == ""
                            )
                        else:
                            signal = store.get_business_task_signal(signal_id)
                            signal_link = (
                                json.loads(signal.context_json).get("source_link", "")
                                if signal is not None
                                else ""
                            )
                            receipt_valid = (
                                receipt_valid
                                and signal is not None
                                and source_is_observed(signal.source_type)
                                and signal.source_ref == evidence.get("source_ref")
                                and evidence_contains(
                                    signal.evidence_text,
                                    evidence.get("source_excerpt", ""),
                                )
                                and signal.source_time
                                == evidence.get("source_time", "")
                                and signal_link == evidence.get("source_link", "")
                            )
                            receipt_valid = receipt_valid and (
                                anchor_id is None or (anchor_id, signal_id) in project_evidence
                            )
                    for evidence in actual_assessment.get("evidence", []):
                        signal_id = evidence.get("signal_id")
                        if signal_id is None:
                            continue
                        receipt_has_citation = any(
                            actual.get("signal_id") == signal_id
                            and actual.get("source_ref") == evidence.get("source_ref")
                            and (
                                evidence_contains(
                                    actual.get("source_excerpt", ""),
                                    evidence.get("source_excerpt", ""),
                                )
                                or evidence_contains(
                                    evidence.get("source_excerpt", ""),
                                    actual.get("source_excerpt", ""),
                                )
                            )
                            for actual in receipt_evidence
                        )
                        citations_valid = (
                            citations_valid
                            and receipt_has_citation
                            and (anchor_id is None or (anchor_id, signal_id) in project_evidence)
                        )
                    if wanted_assessment.get("attention_required"):
                        attention_id = receipt.get("attention_id")
                        card = next(
                            (card for card in cards if card["id"] == attention_id), None
                        )
                        receipt_valid = (
                            receipt_valid
                            and card is not None
                            and card["anchor_id"] == anchor_id
                            and set(receipt.get("task_ids", [])).issubset(
                                card["task_ids"]
                            )
                        )
                    else:
                        receipt_valid = (
                            receipt_valid and receipt.get("attention_id") is None
                        )
                if not citations_valid:
                    failures.append("project_assessment_evidence_mismatch")
                if not receipt_valid:
                    failures.append("project_assessment_receipt_mismatch")
    visible_task_ids = {task_id for card in cards for task_id in card["task_ids"]}
    before_tasks = {task["id"]: task for task in before["tasks"]}
    visible_task_ids.update(
        task["id"]
        for task in after["tasks"]
        if task["id"] not in before_tasks or task != before_tasks[task["id"]]
    )
    return {
        "project_decisions": decision.get("project_decisions")
        if "project_decisions" in decision
        else None,
        "input_id": input_id,
        "input_status": store.get_work_summary_input(input_id).status
        if input_id is not None
        else None,
        "run_id": run["id"] if run is not None else None,
        "run_status": run["status"] if run is not None else None,
        "run_error": run["error"] if run is not None else "",
        "runtime_attempts": attempts,
        "proposal_count": proposal_count,
        "projection": projection,
        "project_assessments": decision.get("project_assessments")
        if "project_assessments" in decision
        else None,
        "persisted_attention_count": len(active),
        "duplicate_cards": duplicate_cards,
        "evidence_valid": evidence_valid,
        "cards": cards,
        "tasks": [
            {
                "id": t["id"],
                "title": t["title"],
                "stage": t["stage"],
                "status": t["status"],
            }
            for t in after["tasks"]
            if t["id"] in visible_task_ids
        ],
        "projects": [
            {
                "id": p["id"],
                "title": p["title"],
                "canonical_anchor_id": p["canonical_anchor_id"],
            }
            for p in after["projects"]
        ],
        "changes": {
            name: {
                "before_count": len(before[name]),
                "after_count": len(after[name]),
                "created_ids": [
                    row["id"]
                    for row in after[name]
                    if row["id"] not in {old["id"] for old in before[name]}
                ],
                "changed_ids": [
                    row["id"]
                    for row in after[name]
                    if any(
                        old["id"] == row["id"] and old != row for old in before[name]
                    )
                ],
            }
            for name in after
        },
        "passed": not failures,
        "failures": failures,
    }


def replay_input(store, runner, input_id, *, source_ref, expected=None):
    from app.task_agent import process_work_item
    from app.task_agent_session import TaskAgentSessionLease

    item = store.get_work_summary_input(input_id)
    if item is None or item.source_ref != source_ref:
        raise ValueError("input missing or source_ref does not match fixed source")
    if item.status not in ("pending", "done", "skipped", "failed"):
        raise ValueError("input is already processing")
    lease = TaskAgentSessionLease.try_acquire(store)
    if lease is None:
        raise RuntimeError("database-copy Task Agent session lease is occupied")
    before = read_domain(store)
    error = None
    with lease:
        lease.assert_owned()
        with store._connect() as db:
            db.execute(
                "update work_summary_inputs set status='processing', error='', available_at='', updated_at=current_timestamp where id=?",
                (input_id,),
            )
        try:
            process_work_item(
                store,
                runner,
                store.get_work_summary_input(input_id),
                session_lease=lease,
            )
        except Exception as exc:
            # Failed model/schema turns are comparison evidence, never normalized.
            error = f"{type(exc).__name__}: {exc}"
    result = readback(store, input_id=input_id, before=before, expected=expected)
    if error is not None:
        result["execution_error"] = error
        result["passed"] = False
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--input-id", type=int)
    mode.add_argument("--case-id")
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "tests/fixtures/task_attention_multisource.json",
    )
    parser.add_argument(
        "--code-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--source-ref", default=W39_SOURCE_REF)
    args = parser.parse_args()
    sys.path.insert(0, str(args.code_root.resolve()))
    from app.config import load_env_file, workspace_path

    load_env_file()
    from app.store import AutoReplyStore
    from app.task_agent import (
        TaskAgentRunner,
        TaskAgentCodexRunner,
        TASK_AGENT_MAX_TIMEOUT_SECONDS,
        TASK_AGENT_MAX_IDLE_TIMEOUT_SECONDS,
        WORK_TRACKING_SKILL_PATH,
    )
    from app.agent_runtime_production import build_production_routed_codex_execution
    from app.agent_runtime_config import load_runtime_config

    require_copy(args.db)
    expected = None
    if args.case_id:
        if args.db.exists():
            raise ValueError("fixed case requires a fresh database path")
        (case,) = [
            case
            for case in load_cases(args.fixtures)
            if case["case_id"] == args.case_id
        ]
        store = AutoReplyStore(args.db)
        input_id = seed_case(store, case)
        source_ref = case["work_item"]["source"]["ref"]
        expected = case["expected"]
    else:
        if not args.db.is_file():
            raise ValueError("input replay requires an existing database copy")
        store = AutoReplyStore(args.db)
        input_id, source_ref = args.input_id, args.source_ref
    total_timeout = min(
        int(os.environ["CEO_TASK_CODEX_TIMEOUT_SECONDS"]),
        TASK_AGENT_MAX_TIMEOUT_SECONDS,
    )
    idle_timeout = min(
        int(os.environ["CEO_TASK_CODEX_IDLE_TIMEOUT_SECONDS"]),
        TASK_AGENT_MAX_IDLE_TIMEOUT_SECONDS,
    )
    routed = build_production_routed_codex_execution(
        store=store,
        workspace=workspace_path(),
        total_timeout_seconds=total_timeout,
        idle_timeout_seconds=idle_timeout,
    )
    runner = scoped_runner(
        TaskAgentRunner, TaskAgentCodexRunner(routed_execution=routed)
    )
    result = replay_input(
        store, runner, input_id, source_ref=source_ref, expected=expected
    )
    result.update(
        case_id=args.case_id,
        concurrency=1,
        code_revision=subprocess.check_output(
            ["git", "-C", str(args.code_root), "rev-parse", "HEAD"], text=True
        ).strip(),
        skill_root=os.environ.get("CEO_SKILLS_ROOT", "installed-default"),
        skill_path=str(WORK_TRACKING_SKILL_PATH),
        skill_sha256=hashlib.sha256(WORK_TRACKING_SKILL_PATH.read_bytes()).hexdigest(),
        routes=[
            {"name": route.name, "model": route.model}
            for route in load_runtime_config(os.environ).routes
        ],
        total_timeout_seconds=total_timeout,
        idle_timeout_seconds=idle_timeout,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
