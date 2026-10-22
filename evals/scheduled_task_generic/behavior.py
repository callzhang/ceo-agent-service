"""Generate actual offline draft items and parse proposed actions with service codecs.

This supplements, and never replaces, the frozen seven annotation checks.
No provider or application executor is available in the native child.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import UTC, datetime
import json
import inspect
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator  # noqa: E402

from app.action_contract_catalog import (  # noqa: E402
    parse_action_contracts,
    system_action_contract_document,
)
from app.dws_client import DwsClient  # noqa: E402
from app.consumer_purpose import purpose_for_context, purpose_output_model  # noqa: E402
from app.email_classifier_agent import AgentClassificationWireResult  # noqa: E402
from evals.scheduled_task_generic import run  # noqa: E402


def business_material(sources, cases):
    """Supply the same platform wire contracts, never business answers, to both arms."""
    return {
        "skill_sources": sources,
        "cases": [{k: v for k, v in c.items() if k != "expected"} for c in cases],
        "system_action_contract": system_action_contract_document(),
        "calendar_adapter_contract": inspect.getsource(
            DwsClient.build_respond_calendar_event_command
        ),
    }


def purpose(case):
    return purpose_for_context(
        SimpleNamespace(
            skill_names=tuple(Path(p).parts[0] for p in case["skills"]),
            required_proposal_action={},
        )
    )


def output_schema(cases):
    variants = []
    for case in cases:
        ids = run.fact_identities(case["facts"])
        props = {
            "case_id": {"type": "string", "enum": [case["id"]]},
            "destination_id": {"type": ["string", "null"], "enum": [None, *ids]},
            "work_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "source_id",
                        "section",
                        "content",
                        "owner",
                        "verification",
                    ],
                    "properties": {
                        "source_id": {"type": "string", "enum": ids},
                        "section": {"type": "string"},
                        "content": {"type": "string"},
                        "owner": {"type": ["string", "null"]},
                        "verification": {
                            "type": "string",
                            "enum": ["verified", "unverified"],
                        },
                    },
                },
            },
            "purpose_output_json": {"type": "string"},
            "classification_json": {"type": ["string", "null"]},
        }
        variants.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": list(props),
                "properties": props,
            }
        )
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["deliverables"],
        "properties": {
            "deliverables": {
                "type": "array",
                "minItems": len(cases),
                "maxItems": len(cases),
                "items": {"anyOf": variants},
            }
        },
    }


def excluded_source_text(facts, excluded):
    """Read verbatim content from the excluded input records, not from labels."""
    found = []
    if isinstance(facts, dict):
        identities = {
            value
            for key, value in facts.items()
            if (key == "id" or key.endswith("_id")) and isinstance(value, str)
        }
        if identities & excluded:
            found.extend(
                facts[field]
                for field in ("body", "text", "subject")
                if isinstance(facts.get(field), str) and facts[field]
            )
        for value in facts.values():
            found.extend(excluded_source_text(value, excluded))
    elif isinstance(facts, list):
        for value in facts:
            found.extend(excluded_source_text(value, excluded))
    return found


def text_identities(content, identities):
    """Read complete literal input IDs from authored text, without classifying prose."""
    found = set()
    for identity in identities:
        start = content.find(identity)
        while start >= 0:
            end = start + len(identity)
            before = content[start - 1] if start else ""
            after = content[end] if end < len(content) else ""
            if not any(
                c and c.isascii() and (c.isalnum() or c in "_-")
                for c in (before, after)
            ):
                found.add(identity)
                break
            start = content.find(identity, end)
    return found


def check_work_product(case, product):
    """Check real draft rows/action targets, never infer success from annotations."""
    expected = case["expected"]
    output = purpose_output_model(purpose(case)).model_validate_json(
        product["purpose_output_json"]
    )
    plan = output.to_proposal()
    actions = list(plan.actions) if plan else []
    retained = {item["source_id"] for item in product["work_items"]}
    if product["classification_json"] is not None:
        retained.update(run.fact_identities(case["facts"]))
    if product["destination_id"] is not None:
        retained.add(product["destination_id"])
    authored_content = "\n".join(
        [item["content"] for item in product["work_items"]]
        + [json.dumps(action.payload, ensure_ascii=False) for action in actions]
    )
    retained.update(
        text_identities(authored_content, run.fact_identities(case["facts"]))
    )
    excluded_text = excluded_source_text(
        case["facts"], set(expected.get("exclude_ids", []))
    )
    refs = run.fact_identities([action.target for action in actions])
    retained.update(refs)
    registered = {
        (e.capability, e.operation)
        for e in parse_action_contracts(system_action_contract_document())
    }
    checks = {
        "registered_action_operations": all(
            (a.capability, a.operation) in registered for a in actions
        ),
        "required_facts_in_actual_draft_or_target": set(expected.get("include_ids", []))
        <= retained,
        "excluded_facts_absent_from_actual_draft_or_target": not (
            set(expected.get("exclude_ids", [])) & retained
        ),
        "unverified_claims_stay_unverified": set(expected.get("unverified_ids", []))
        <= {
            item["source_id"]
            for item in product["work_items"]
            if item["verification"] == "unverified"
        },
        "production_purpose_parser": True,
        "excluded_source_content_not_copied": not any(
            text in authored_content for text in excluded_text
        ),
    }
    dispositions = expected["dispositions"]
    if "respond" in dispositions:
        checks["real_response_proposal"] = any(
            a.capability == "dingtalk-chat" and bool(a.payload.get("content"))
            for a in actions
        )
    if "decline_updated_invitation" in dispositions:
        checks["current_calendar_decline"] = any(
            a.capability == "dingtalk-calendar"
            and a.operation == "respond_calendar_event"
            and a.target.get("event_id") == case["facts"]["event_id"]
            and a.payload.get("response_status") == "declined"
            for a in actions
        )
    if "category" in expected:
        classification = AgentClassificationWireResult.model_validate_json(
            product["classification_json"]
        )
        checks["classification_matches_original_oracle"] = all(
            getattr(classification, field) == expected[field]
            for field in ("category", "important", "unsubscribe_candidate_index")
        )
        checks["no_mail_actions"] = not actions
    if "owner" in expected:
        checks["source_owner_not_inferred"] = all(
            item["owner"] == expected["owner"] for item in product["work_items"]
        )
    if "applicant_omission" in expected:
        checks["source_failure_has_no_applicant_or_approval_action"] = (
            output.action == "failed" and not actions
        )
    markdown = "\n\n".join(
        f"## {item['section']}\n{item['content']}\n来源：{item['source_id']}"
        for item in product["work_items"]
    )
    return {
        "case_id": case["id"],
        "checks": checks,
        "passed": all(checks.values()),
        "rendered_draft": markdown,
        "production_output": output.model_dump(mode="json"),
        "proposed_actions": [a.model_dump(mode="json") for a in actions],
    }


def rescore_saved_result(result, cases):
    """Replay only deterministic readback; preserve native outputs and prior scores."""
    rescored = deepcopy(result)
    if result.get("scoring_revision", 1) >= 2:
        raise ValueError("this scoring revision is already recorded")
    for arm in rescored["arms"].values():
        arm.setdefault("score_history", []).append(
            {
                "scoring_revision": 1,
                "status": arm["status"],
                "scores": [
                    {k: row[k] for k in ("case_id", "checks", "passed")}
                    for row in arm["checks"]
                ],
            }
        )
        products = {p["case_id"]: p for p in arm["raw_deliverables"]["deliverables"]}
        arm["checks"] = [check_work_product(c, products[c["id"]]) for c in cases]
        arm["status"] = (
            "passed"
            if not arm["tool_item_count"] and all(c["passed"] for c in arm["checks"])
            else "failed"
        )
    rescored["scoring_revision"] = 2
    rescored["status"] = (
        "passed"
        if all(a["status"] == "passed" for a in rescored["arms"].values())
        else "failed"
    )
    return rescored


def execute(arm, sources, cases, flags, work):
    # No rubric or annotation result enters the actor prompt.
    material = business_material(sources, cases)
    contracts = {
        kind: purpose_output_model(kind).model_json_schema()
        for kind in {purpose(c) for c in cases}
    }
    prompt = (
        "Produce the actual business draft/work product for each supplied synthetic case under its Skill. "
        "Do not produce include/exclude/disposition annotations and do not use earlier evaluation outputs. "
        "All source reads are already represented by the supplied facts; use no tools and perform no external action. "
        "work_items are the substantive rows/paragraphs you would actually put in the report, response, source-candidate "
        "record or unresolved-evidence record. Each row cites its exact supplied source ID. Rows are final substantive "
        "content, not internal source inventories, background history, or explanations of why excluded records were "
        "not used. Coverage may count non-substantive input without turning it into a work_item; describe coverage "
        "in the output reason instead. verification means the underlying claim is established, not merely that "
        "someone made the statement. Never invent ownership or facts. "
        "destination_id is the actual supplied document target when preparing a document, otherwise null; "
        "never use a report date or source record as a missing document target. "
        "purpose_output_json must encode the real service purpose-output object under the supplied schema, not "
        "an evaluation label. For each context choose its normal business proposal, no-action or source-failure "
        "result. Reports are offline drafts only: no provider receipt exists, so do not claim publication or delivery. "
        "Do not turn ordinary report-document writes or internal candidate records into unsupported registered actions. "
        "For these offline drafts keep them in work_items and return no_action with the reason that only a draft exists. "
        "Proposals are not executed. When a message proposal needs conversation_id, use the synthetic fixture "
        "conversation eval-<case_id>, and use the actual supplied trigger/card identity as message_id. "
        "Calendar responses use dingtalk-calendar/respond_calendar_event with event_id and response_status. "
        "classification_json is the actual classifier output for classification requests, otherwise null. "
        "The envelope only stores draft artifacts and proposed production payloads.\n"
        + json.dumps(
            {
                "production_purpose_schemas": contracts,
                "classification_schema": AgentClassificationWireResult.model_json_schema(),
                **material,
            },
            ensure_ascii=False,
        )
    )
    schema = output_schema(cases)
    schema_path, final_path = work / "schema.json", work / f"{arm}.json"
    schema_path.write_text(json.dumps(schema))
    command = [
        shutil.which("codex") or "codex",
        "exec",
        *flags,
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(final_path),
        "-",
    ]
    print(
        f"Native {arm}: actual offline drafts and production action payloads",
        flush=True,
    )
    with (
        (work / f"{arm}-events.jsonl").open("w") as stdout,
        (work / f"{arm}-stderr.txt").open("w") as stderr,
    ):
        process = subprocess.run(
            command,
            input=prompt,
            text=True,
            stdout=stdout,
            stderr=stderr,
            cwd=work,
            timeout=300,
        )
    events = [
        json.loads(line)
        for line in (work / f"{arm}-events.jsonl").read_text().splitlines()
        if line
    ]
    tool_items = [
        e
        for e in events
        if (e.get("item") or {}).get("type")
        not in (None, "agent_message", "reasoning", "todo_list", "error")
    ]
    if process.returncode or not final_path.exists():
        raise RuntimeError(
            f"{arm} native draft process exited {process.returncode}; no draft accepted"
        )
    raw = json.loads(final_path.read_text())
    Draft202012Validator(schema).validate(raw)
    by_id = {p["case_id"]: p for p in raw["deliverables"]}
    if set(by_id) != {c["id"] for c in cases} or len(raw["deliverables"]) != len(cases):
        raise ValueError("native draft result does not cover exactly the frozen cases")
    checked = [check_work_product(c, by_id[c["id"]]) for c in cases]
    return {
        "prompt_sha256": run.digest(prompt.encode()),
        "raw_deliverables": raw,
        "checks": checked,
        "tool_item_count": len(tool_items),
        "status": "passed"
        if not tool_items and all(c["passed"] for c in checked)
        else "failed",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="Re-read saved drafts without another model call",
    )
    args = parser.parse_args()
    cases_bytes = (run.HERE / "cases.json").read_bytes()
    cases = json.loads(cases_bytes)["cases"]
    if args.rescore:
        path = run.HERE / "behavior-results.json"
        saved = json.loads(path.read_text())
        if saved["cases_sha256"] != run.digest(cases_bytes):
            raise ValueError("case source changed before deterministic readback")
        result = rescore_saved_result(saved, cases)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": result["status"], "model_calls": 0}))
        return 0 if result["status"] == "passed" else 1
    settings, flags = run.configuration("production-config")
    paths = sorted({p for c in cases for p in c["skills"]})
    sources = {
        "baseline": {
            p: subprocess.check_output(
                ["git", "show", f"{run.BASELINE_COMMIT}:ci/shared-skills/{p}"], cwd=ROOT
            ).decode()
            for p in paths
        },
        "candidate": {p: (run.SKILLS / p).read_text() for p in paths},
    }
    result = {
        "version": 2,
        "cases_sha256": run.digest(cases_bytes),
        "settings": settings,
        "created_at": datetime.now(UTC).isoformat(),
        "platform_contract_sha256": run.digest(
            json.dumps(business_material({}, []), sort_keys=True).encode()
        ),
        "source_sha256": {
            a: {p: run.digest(s.encode()) for p, s in docs.items()}
            for a, docs in sources.items()
        },
        "arms": {},
        "synthetic_only": True,
        "external_executor_available": False,
    }
    result_path = run.HERE / "behavior-results.json"
    if result_path.exists():
        raise ValueError(
            "behavior result already exists; preserve it before a new experiment"
        )
    temp = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="scheduled-business-drafts-", dir="/tmp"
        ) as directory:
            temp = Path(directory)
            for arm in sources:
                if run.configuration("production-config")[0] != settings:
                    raise ValueError("native settings changed between arms")
                result["arms"][arm] = execute(arm, sources[arm], cases, flags, temp)
                result_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                )
    finally:
        result["temporary_artifacts_cleaned"] = temp is None or not temp.exists()
        result["status"] = (
            "passed"
            if len(result["arms"]) == 2
            and all(a["status"] == "passed" for a in result["arms"].values())
            else "failed"
        )
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "arms": {a: x["status"] for a, x in result["arms"].items()},
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
