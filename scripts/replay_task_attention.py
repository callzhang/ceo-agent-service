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
    if payload["version"] not in {1, 2, 3, 4}:
        raise ValueError("fixture version must be 1, 2, 3 or 4")
    cases = payload["cases"]
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("duplicate case_id")
    return cases


def scoped_runner(runner_type, codex):
    class AttentionEvaluationRunner(runner_type):
        def __init__(self, codex):
            super().__init__(codex)
            self.context_deliveries = []

        def decide(self, *args, **kwargs):
            payload, _ = json.JSONDecoder().raw_decode(args[1] if len(args) > 1 else kwargs["candidate_prompt"])
            documents = payload.get("source_documents")
            self.context_deliveries.append({
                "notice": "Actual delivered context ranges, not proof of what the Agent read.",
                "source_metrics": payload.get("source_metrics"),
                "source_documents": [{
                    **{key: document[key] for key in ("document_id", "full_length", "truncated", "citation_budget_exceeded") if key in document},
                    "visible_ranges": [{"start": span["start"], "end": span["end"]} for span in document["visible_ranges"]],
                    "decoded_excerpts": [{key: value for key, value in span.items() if key != "text"} for span in document.get("decoded_excerpts", [])],
                } for document in documents] if documents is not None else None,
            })
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

    context = case["existing_context"]
    resolution = BusinessResolutionService(store)
    anchors = {}
    projects = {}
    project_signals = []

    def source_signal_id(source_ref, excerpt):
        from app.task_source_documents import source_contains_quote
        matches = [signal.id for signal in project_signals
                   if signal.source_ref == source_ref and source_contains_quote(signal.evidence_text, excerpt)]
        if len(matches) != 1:
            raise ValueError("seed citation must identify exactly one original source version")
        return matches[0]

    def resolve_context_citations(value):
        if isinstance(value, list):
            return [resolve_context_citations(entry) for entry in value]
        if not isinstance(value, dict):
            return value
        result = {key: resolve_context_citations(entry) for key, entry in value.items()}
        if "source_ref" in result and "source_excerpt" in result and result.get("signal_id") is None:
            result["signal_id"] = source_signal_id(result["source_ref"], result["source_excerpt"])
        return result
    for project in context.get("projects", []):
        anchor = resolution.register_anchor(
            anchor_type="project", anchor_ref=project["ref"], title=project["title"]
        )
        project_id = resolution.register_official_project(
            anchor_id=anchor, registry_source=project["registry_source"]
        )
        anchors[project["title"]] = anchor
        projects[project["title"]] = project_id
        if project.get("evidence") or project.get("context"):
            from app.project_context_service import ProjectContextService
            from app.task_semantic_models import ProjectContext
            from dataclasses import asdict
            ids = []
            for source in project.get("evidence", []):
                signal_id = store.create_business_task_signal(**asdict(SourceSignal(**source)))
                ids.append(signal_id)
                project_signals.append(store.get_business_task_signal(signal_id))
            stored_context = ProjectContext.model_validate(resolve_context_citations(project["context"])) if project.get("context") is not None else None
            with store.business_task_transaction() as db:
                ProjectContextService(store).apply(project_id=project_id, context=stored_context, signal_ids=tuple(ids), db=db)
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
                from app.project_context_service import ProjectContextService
                ProjectContextService(store).apply(
                    project_id=projects[task["project_title"]], context=None,
                    signal_ids=(result.signal_id,), db=db,
                )
    for attention in context.get("attention", []):
        anchor = anchors[attention["project_title"]]
        names = attention["task_titles"]
        assessment_json = "{}"
        primary_signal_id = None
        if assessment := attention.get("assessment"):
            primary_signal_id = source_signal_id(assessment["evidence_source_ref"], assessment["source_excerpt"]) if "evidence_source_ref" in assessment else signals[assessment["evidence_task_title"]]
            assessment_json = json.dumps(
                {
                    "assessment_basis": assessment["assessment_basis"],
                    "material_trigger": assessment["material_trigger"],
                    "inference": attention["why_attention"],
                    "evidence": [
                        {
                            "signal_id": primary_signal_id,
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
                evidence_signal_id=primary_signal_id if primary_signal_id is not None else signals[names[0]],
                assessment_json=assessment_json,
            )
        )
    item = WorkItem.model_validate(case["work_item"])
    return store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )


def domain_snapshot(store) -> dict[str, tuple[str, ...]]:
    """Read exact persisted domain state; exclude Agent run/attempt bookkeeping."""
    with store._connect() as db:
        tables = [row[0] for row in db.execute(
            "select name from sqlite_master where type='table' and name glob 'business_*' order by name"
        )]
        return {
            table: tuple(sorted(json.dumps(dict(row), ensure_ascii=False, sort_keys=True)
                                for row in db.execute(f"select * from {table}")))
            for table in tables
        }


def comparison_capabilities(store):
    """Inspect actual schema without upgrading the runtime under comparison."""
    with store._connect() as db:
        tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
    required = {"business_project_context_revisions", "business_project_evidence", "business_source_documents"}
    return {"missing_tables": sorted(required - tables)}


def source_is_observed(source_type):
    """Frozen evaluation rule, independent of the runtime's installed modules."""
    return source_type not in {"memory_provenance", "session_provenance"}


def read_project_centered_expectations(store, before, expected):
    """Compare fixed offline expectations to actual Project/Task/Attention rows."""
    if comparison_capabilities(store)["missing_tables"]:
        return ("project_centered_storage_missing",)
    failures: list[str] = []
    with store._connect() as db:
        projects = [dict(row) for row in db.execute("select * from business_projects")]
        tasks = [dict(row) for row in db.execute("select * from business_tasks")]
        cards = [dict(row) for row in db.execute(
            "select * from business_attention_items where status='active'"
        )]
        memberships = {
            (int(row["attention_item_id"]), int(row["task_id"]))
            for row in db.execute("select attention_item_id,task_id from business_attention_tasks")
        }
        project_by_anchor = {int(row["canonical_anchor_id"]): row["title"] for row in projects}
        links: dict[int, set[str]] = {}
        for row in db.execute(
            "select task_id,anchor_id from business_task_anchor_links "
            "where status='confirmed' and active=1"
        ):
            title = project_by_anchor.get(int(row["anchor_id"]))
            if title is not None:
                links.setdefault(int(row["task_id"]), set()).add(title)
        evidence_refs = {
            row["source_ref"] for row in db.execute(
                "select distinct signal.source_ref from business_project_evidence proof "
                "join business_task_signals signal on signal.id=proof.signal_id"
            )
        }
        contexts = {
            row["id"]: db.execute(
                "select context_json from business_project_context_revisions "
                "where project_id=? order by id desc limit 1", (row["id"],)
            ).fetchone()
            for row in projects
        }

    if "project_titles" in expected and sorted(row["title"] for row in projects) != sorted(expected["project_titles"]):
        failures.append("project_titles_mismatch")
    for title, wanted in expected.get("project_contexts", {}).items():
        matching = [project for project in projects if project["title"] == title]
        if len(matching) > 1:
            failures.append("project_identity_ambiguous")
            continue
        row = contexts.get(matching[0]["id"]) if matching else None
        if row is None:
            failures.append("project_context_missing")
            continue
        actual = json.loads(row["context_json"])
        owner = actual.get("overall_owner")
        if (owner or {}).get("person_name") != wanted.get("overall_owner"):
            failures.append("project_owner_mismatch")
        wanted_roles = wanted.get("responsibilities", [])
        actual_roles = actual.get("responsibilities", [])
        if sorted(role["person_name"] for role in actual_roles) != sorted(role["person_name"] for role in wanted_roles) or any(
            not any(actual_role["person_name"] == wanted_role["person_name"] and (
                wanted_role["responsibility_contains"] in actual_role["responsibility"]
                if "responsibility_contains" in wanted_role else actual_role["responsibility"] == wanted_role["responsibility"]
            ) for actual_role in actual_roles) for wanted_role in wanted_roles
        ):
            failures.append("project_responsibilities_mismatch")
        fact_texts = [fact["text"] for fact in actual.get("facts", [])]
        if any(
            not any(phrase in text for text in fact_texts)
            for phrase in wanted.get("fact_text_contains", [])
        ):
            failures.append("project_facts_mismatch")

    actual_attention = sorted(project_by_anchor.get(int(card["anchor_id"])) for card in cards)
    if "attention_projects" in expected and actual_attention != sorted(expected["attention_projects"]):
        failures.append("attention_projects_mismatch")
    for title in expected.get("zero_task_attention_projects", []):
        matching = [card for card in cards if project_by_anchor.get(int(card["anchor_id"])) == title]
        if len(matching) != 1 or any(
            card_id == int(matching[0]["id"]) for card_id, _ in memberships
        ):
            failures.append("zero_task_attention_mismatch")
    for title, count in expected.get("attention_member_counts", {}).items():
        matching = [card for card in cards if project_by_anchor.get(int(card["anchor_id"])) == title]
        if len(matching) != 1 or sum(card_id == int(matching[0]["id"]) for card_id, _ in memberships) != count:
            failures.append("attention_member_count_mismatch")
    for title, required_titles in expected.get("attention_member_task_title_contains", {}).items():
        card_ids = {int(card["id"]) for card in cards if project_by_anchor.get(int(card["anchor_id"])) == title}
        member_ids = {task_id for card_id, task_id in memberships if card_id in card_ids}
        member_titles = [task["title"] for task in tasks if int(task["id"]) in member_ids]
        if any(not any(phrase in actual for actual in member_titles) for phrase in required_titles):
            failures.append("attention_member_task_mismatch")
    if "task_count" in expected and len(tasks) != expected["task_count"]:
        failures.append("task_count_mismatch")
    if "allowed_task_counts" in expected and len(tasks) not in expected["allowed_task_counts"]:
        failures.append("task_count_mismatch")
    for wanted in expected.get("task_expectations", []):
        def matches(task: dict[str, object]) -> bool:
            suggestion = json.loads(task["suggestion_json"])
            title_contains = wanted.get("title_contains", "")
            title_contains_any = wanted.get("title_contains_any", [])
            if "title_contains" in wanted:
                title_matches = title_contains in task["title"]
            elif "title_contains_any" in wanted:
                title_matches = bool(title_contains_any) and any(
                    phrase in task["title"] for phrase in title_contains_any
                )
            else:
                title_matches = True
            fields = (
                "origin", "stage", "owner_name", "commitment_status", "status",
            )
            return (
                all(task[field] == wanted[field] for field in fields if field in wanted)
                and title_matches
                and ("project_title" not in wanted
                     or wanted["project_title"] in links.get(int(task["id"]), set()))
                and ("suggested_owner_name" not in wanted
                     or suggestion.get("suggested_owner_name", "") == wanted["suggested_owner_name"])
            )
        if not any(matches(task) for task in tasks):
            failures.append("task_expectation_mismatch")
    if not set(
        expected.get("required_project_source_refs", [])
    ).issubset(evidence_refs):
        failures.append("project_evidence_mismatch")
    with store._connect() as db:
        for source_ref, count in expected.get("minimum_signal_versions_for_ref", {}).items():
            actual = db.execute("select count(distinct source_document_id) from business_task_signals where source_ref=?", (source_ref,)).fetchone()[0]
            if actual < count:
                failures.append("signal_version_count_mismatch")

    after = domain_snapshot(store)
    if "max_outbound_intent_delta" in expected:
        if before is None:
            raise ValueError("outbound delta expectation requires before snapshot")
        intents = ("business_task_todo_sync_outbox", "business_task_follow_ups")
        delta = sum(len(after[table]) - len(before[table]) for table in intents)
        if delta > expected["max_outbound_intent_delta"]:
            failures.append("outbound_intent_increase")
    if expected.get("repeat_domain_unchanged"):
        if before is None:
            raise ValueError("repeat comparison requires before snapshot")
        if after != before:
            failures.append("repeat_domain_changed")
    return tuple(dict.fromkeys(failures))


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


def readback(store, *, input_id, before, expected=None, project_before=None):
    capabilities = comparison_capabilities(store)
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
        } if not capabilities["missing_tables"] else set()
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
    if capabilities["missing_tables"]:
        failures.append("project_centered_storage_missing")
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
        if any(key in expected for key in ("project_contexts", "task_expectations", "zero_task_attention_projects", "max_outbound_intent_delta", "required_project_source_refs", "attention_member_counts", "attention_member_task_title_contains", "minimum_signal_versions_for_ref", "repeat_domain_unchanged")):
            failures.extend(read_project_centered_expectations(store, project_before, expected))
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
                    wanted_member_counts = (
                        wanted_assessment["allowed_task_counts"]
                        if "allowed_task_counts" in wanted_assessment
                        else [wanted_assessment["task_count"]]
                    )
                    wanted_statuses = (
                        wanted_assessment["allowed_application_statuses"]
                        if "allowed_application_statuses" in wanted_assessment
                        else [wanted_assessment["application_status"]]
                    )
                    receipt_task_ids_are_integers = all(
                        isinstance(task_id, int) and not isinstance(task_id, bool)
                        for task_id in receipt_task_ids
                    )
                    receipt_valid = (
                        receipt.get("status") in wanted_statuses
                        and len(receipt_task_ids) in wanted_member_counts
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
        "comparison_capabilities": capabilities,
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
    project_before = domain_snapshot(store) if expected else None
    error = None
    delivery_start = len(getattr(runner, "context_deliveries", []))
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
    result = readback(store, input_id=input_id, before=before, expected=expected, project_before=project_before)
    result["context_deliveries"] = getattr(runner, "context_deliveries", [])[delivery_start:]
    if error is not None:
        result["execution_error"] = error
        result["passed"] = False
    return result


def replay_case(store, runner, case):
    """Each original version is enqueued immediately before its existing turn."""
    from app.task_models import WorkItem

    inputs = case["source_inputs"] if "source_inputs" in case else [case["work_item"]]
    if not inputs or inputs[-1] != case["work_item"]:
        raise ValueError("source_inputs must end with the fixed work_item")
    initial = read_domain(store)
    initial_project = domain_snapshot(store)
    steps = []
    seen = set()
    for index, raw in enumerate(inputs):
        item = WorkItem.model_validate(raw)
        before_repeat = domain_snapshot(store)
        key = item.model_dump_json()
        input_id = seed_case(store, {**case, "work_item": raw}) if index == 0 else store.enqueue_work_summary_input(
            item.source.type.value, item.source.ref, item.model_dump_json()
        )
        step = replay_input(store, runner, input_id, source_ref=item.source.ref)
        step.update(source_ref=item.source.ref, source_time=item.source.created_at)
        if key in seen and case["expected"].get("repeat_domain_unchanged") and domain_snapshot(store) != before_repeat:
            step["failures"].append("repeat_domain_changed")
            step["passed"] = False
        steps.append(step)
        seen.add(key)
        if step.get("execution_error") or step["run_status"] != "completed" or "projection_not_successful" in step["failures"]:
            break
    expected = {key: value for key, value in case["expected"].items() if key != "repeat_domain_unchanged"}
    result = readback(store, input_id=input_id, before=initial, expected=expected, project_before=initial_project)
    result["source_steps"] = steps
    if len(steps) != len(inputs) or any(not step["passed"] for step in steps):
        incomplete = ["source_sequence_incomplete"] if len(steps) != len(inputs) else []
        result["failures"] = list(dict.fromkeys(result["failures"] + [failure for step in steps for failure in step["failures"]] + incomplete))
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
        / "tests/fixtures/task_project_centered_v4.json",
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
    result = replay_case(store, runner, case) if args.case_id else replay_input(
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
