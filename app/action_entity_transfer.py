"""Source-backed conversion of predecessor Action facts into common owners."""

from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from app import action_storage
from app.action_entity_storage import append_action_receipt, create_action, add_action_observer


def _projected_rows(db: sqlite3.Connection, record_type: str) -> list[dict]:
    if record_type in ("outbound_postfix_receipts", "native_reply_dispatches"):
        from app.prepared_action_definitions import prepared_child_projection
        cursor = db.execute(prepared_child_projection(record_type))
        fields = tuple(item[0] for item in cursor.description)
        return [dict(zip(fields, row, strict=True)) for row in cursor.fetchall()]
    projection = action_storage.ACTION_PROJECTIONS.get(record_type)
    if record_type == "candidate_action_attempts":
        payloads = action_storage.action_payloads("actual_no_memory_fk", physical_provenance=True,
                                                  execution_owner=True)
        projection = action_storage._projection(record_type, payloads=payloads, execution_owner=True)
    if record_type in ("email_actions", "email_action_attempts"):
        from app.action_email_definitions import EMAIL_ACTION_PROJECTIONS, EMAIL_FIELDS

        projection = EMAIL_ACTION_PROJECTIONS[record_type]
        if record_type == "email_actions":
            expressions = [
                ("(select count(*) from actions attempts where attempts.record_type='email_action_attempts' "
                 "and attempts.email_action_id=a.email_action_id)" if field == "attempt_count"
                 else "a.email_" + field) + " as " + field
                for field in EMAIL_FIELDS[record_type]
            ]
            projection = "select " + ",".join(expressions) + " from actions a where a.record_type='email_actions'"
    elif projection is None:
        from app.action_email_definitions import EMAIL_ACTION_PROJECTIONS
        projection = EMAIL_ACTION_PROJECTIONS[record_type]
    cursor = db.execute(projection)
    fields = tuple(item[0] for item in cursor.description)
    return [dict(zip(fields, row, strict=True)) for row in cursor.fetchall()]


def capture_source_facts(db: sqlite3.Connection) -> dict[str, list[dict]]:
    """Read exact old public projections before the compact Actions rebuild."""
    from app.current_action_schema import RETIRED_ACTION_TYPES

    facts = {kind: _projected_rows(db, kind) for kind in sorted(RETIRED_ACTION_TYPES)}
    facts["email_historical_operations"] = [
        {"physical_id": row[0], "action_ids_json": row[1]}
        for row in db.execute("select id,email_operation_action_ids_json from actions "
                              "where record_type='email_historical_operations'")
    ]
    return facts


def _identity_part(value: object) -> list[object]:
    if isinstance(value, bool):
        return ["integer", int(value)]
    if isinstance(value, int):
        return ["integer", value]
    if isinstance(value, str):
        return ["text", value]
    if value is None:
        return ["null", None]
    raise ValueError("unsupported composite Action source identity type")


def _identity(value: object) -> str:
    if isinstance(value, tuple):
        encoded = ["tuple", [_identity_part(item) for item in value]]
    else:
        encoded = _identity_part(value)
    return json.dumps(encoded, ensure_ascii=False, separators=(",", ":"))


def _map(db: sqlite3.Connection, record_type: str, source_identity: object,
         action_id: int, receipt_id: int | None) -> None:
    db.execute(
        "insert into action_entity_identity_map(source_type,source_identity,action_id,receipt_id) "
        "values(?,?,?,?)",
        (record_type, _identity(source_identity), action_id, receipt_id),
    )


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _rowdict(cursor, row) -> dict:
    return dict(zip((item[0] for item in cursor.description), row, strict=True))


def _candidate_action(db: sqlite3.Connection, execution_id: int, action_index: int) -> tuple[dict, dict]:
    """Resolve the action through its exact candidate, execution and selection FKs."""
    from app.action_storage import REVIEW_CANDIDATE_SQL, CANDIDATE_EXECUTION_SQL, CANDIDATE_SELECTION_SQL
    from app.task20_definitions import task20_projection
    from app import shared_action_lifecycle

    task_sql = task20_projection("reply_tasks")
    cursor = db.execute(f"""select c.*,t.business_object_key,e.selection_id
        from ({CANDIDATE_EXECUTION_SQL}) e
        join ({REVIEW_CANDIDATE_SQL}) c on c.id=e.candidate_id
        join ({task_sql}) t on t.id=c.task_id where e.id=?""", (execution_id,))
    row = cursor.fetchone()
    if row is None:
        raise ValueError("candidate action has no exact candidate execution owner")
    candidate = _rowdict(cursor, row)
    selection = None
    if candidate["selection_id"] is not None:
        cursor = db.execute(f"select * from ({CANDIDATE_SELECTION_SQL}) where id=?",
                            (candidate["selection_id"],))
        selected = cursor.fetchone()
        if selected is None:
            raise ValueError("candidate action selection owner is missing")
        selection = _rowdict(cursor, selected)
    steps = shared_action_lifecycle.reviewed_action_steps(db, candidate, selection=selection)
    if not 0 <= action_index < len(steps):
        raise ValueError("candidate action index is outside its exact approved plan")
    return candidate, steps[action_index]


def _transfer_external_results(db: sqlite3.Connection, rows: Iterable[dict], owners: dict[str, int],
                               receipts: dict[str, int]) -> None:
    for source in rows:
        if not source["external_action_key"]:
            raise ValueError("canonical external Action key is empty")
        key = source["external_action_key"]
        action_id = create_action(db, values={
            "external_action_key": key,
            "business_object_key": source["business_object_key"],
            "action_identity": source["action_identity"],
        })["id"]
        receipt_id = append_action_receipt(db, action_id, values={
            "operation": source["operation"], "target": source["target_identifiers_json"],
            "provider_result_json": source["provider_result_json"],
            "result_digest": source["result_digest"], "agent_run_id": source["first_agent_run_id"],
            "observed_at": source["created_at"],
        })
        owners[key] = action_id
        receipts[key] = receipt_id
        _map(db, "external_action_results", key, action_id, receipt_id)


def _delivery_key(business_key: str, identity: str, generation: str, revision: int) -> str:
    from app.service_message_sender import agent_message_delivery_key
    return agent_message_delivery_key(business_object_key=business_key, action_identity=identity,
                                      execution_generation=generation, proposal_revision=revision)


def _prepared_candidates(db: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    """Index only candidate action identities proven by the frozen delivery-key equation."""
    from app.action_storage import REVIEW_CANDIDATE_SQL
    from app.task20_definitions import task20_projection

    result = {}
    cursor = db.execute(f"""select c.*,t.business_object_key,t.channel from ({REVIEW_CANDIDATE_SQL}) c
        join ({task20_projection('reply_tasks')}) t on t.id=c.task_id""")
    for row in cursor:
        candidate = _rowdict(cursor, row)
        data = json.loads(candidate["candidate_json"])
        plans = [("", data.get("proposal"))]
        options = data.get("decision_options")
        if options is not None:
            if not isinstance(options, list):
                raise ValueError("candidate decision options are not an array")
            plans.extend((option.get("key", ""), option.get("plan"))
                         for option in options if isinstance(option, dict))
        for option_key, plan in plans:
            actions = plan.get("actions", []) if isinstance(plan, dict) else []
            if not isinstance(actions, list):
                raise ValueError("candidate action plan is not an array")
            for step in actions:
                identity = step.get("action_identity") if isinstance(step, dict) else None
                if not isinstance(identity, str) or not identity:
                    raise ValueError("prepared candidate action has no stable identity")
                identity = identity + (":" + option_key if option_key else "")
                key = _delivery_key(candidate["business_object_key"], identity,
                                    candidate["execution_generation"], candidate["proposal_revision"])
                fact = {"business_object_key": candidate["business_object_key"],
                        "action_identity": identity, "operation": step.get("operation"),
                        "target": step.get("target", {}), "channel": candidate["channel"]}
                index = (fact["channel"], key)
                old = result.get(index)
                if old is not None and old != fact:
                    raise ValueError("prepared delivery key resolves to conflicting candidate actions")
                result[index] = fact
    return result


def _transfer_candidates(db: sqlite3.Connection, rows: Iterable[dict], owners: dict[str, int],
                         result_receipts: dict[str, int], prepared_candidates: dict) -> dict:
    delivery_owners = {}
    for source in rows:
        key = source["external_action_key"] or None
        candidate, step = _candidate_action(db, source["execution_id"], source["action_index"])
        values = {
            "external_action_key": key if key in owners else None,
            "business_object_key": candidate["business_object_key"],
            "action_identity": step["action_identity"],
        }
        action_id = owners.get(key) if key else None
        if action_id is not None:
            canonical = db.execute("select action_business_object_key,action_action_identity from actions where id=?",
                                   (action_id,)).fetchone()
            target = _json_text(step.get("target", {}))
            receipt_target = db.execute("select target from action_receipts where id=?", (result_receipts[key],)).fetchone()[0]
            if (tuple(canonical) != (values["business_object_key"], values["action_identity"])
                    or step["operation"] != db.execute("select operation from action_receipts where id=?",
                                                        (result_receipts[key],)).fetchone()[0]
                    or target != receipt_target):
                raise ValueError("candidate action conflicts with canonical external Action facts")
        else:
            values["external_action_key"] = None
            action_id = create_action(db, values=values)["id"]
        receipt_values = {
            "execution_id": source["execution_id"], "action_index": source["action_index"],
            "status": source["status"], "outcome_json": source["result_json"],
            "created_at": source["created_at"], "updated_at": source["updated_at"],
        }
        if source["result_json"] is None and key in result_receipts:
            receipt_values["provider_receipt_id"] = result_receipts[key]
        receipt_id = append_action_receipt(db, action_id, values=receipt_values)
        _map(db, "candidate_action_attempts", source["id"], action_id, receipt_id)
        from app.task20_definitions import task20_projection
        task = db.execute(f"select channel,business_object_key from ({task20_projection('reply_tasks')}) "
                          "where id=?", (candidate["task_id"],)).fetchone()
        generation = candidate["execution_generation"]
        revision = candidate["proposal_revision"]
        option_key = ""
        if candidate.get("selection_id") is not None:
            from app.action_storage import CANDIDATE_SELECTION_SQL
            selected = db.execute(f"select option_key from ({CANDIDATE_SELECTION_SQL}) where id=?",
                                  (candidate["selection_id"],)).fetchone()
            option_key = selected[0]
        identity = step["action_identity"] + (":" + option_key if option_key else "")
        key_tuple = (task[0], _delivery_key(task[1], identity, generation, revision))
        previous_owner = delivery_owners.get(key_tuple)
        if previous_owner is not None and previous_owner != action_id:
            raise ValueError("reviewed delivery identity resolves to conflicting Action owners")
        delivery_owners[key_tuple] = action_id
    return delivery_owners


def _transfer_postfixes(db: sqlite3.Connection, facts: dict[str, list[dict]], owners: dict[str, int],
                        prepared_candidates: dict, delivery_owners: dict) -> None:
    receipts_by_key = {(row["channel"], row["delivery_key"]): row for row in facts["outbound_postfix_receipts"]}
    dispatch_by_key = {(row["channel"], row["delivery_key"]): row for row in facts["native_reply_dispatches"]}
    parents_by_key = {(row["channel"], row["delivery_key"]): row for row in facts["outbound_postfixes"]}
    identities = set(parents_by_key) | set(receipts_by_key) | set(dispatch_by_key)
    for identity in sorted(identities):
        source = parents_by_key.get(identity)
        candidate = prepared_candidates.get(identity)
        action_id = delivery_owners.get(identity)
        if action_id is None and candidate is not None:
            action_id = create_action(db, values={"business_object_key": candidate["business_object_key"],
                "action_identity": candidate["action_identity"], "operation": candidate["operation"],
                "delivery_channel": source["channel"], "delivery_key": source["delivery_key"]})["id"]
        if action_id is None:
            action_id = create_action(db, values={"delivery_channel": identity[0],
                "delivery_key": identity[1]})["id"]
        current = db.execute("select action_prepared_body,action_delivery_channel,action_delivery_key from actions where id=?",
                             (action_id,)).fetchone()
        if source is not None and current[0] is not None and current[0] != source["final_body"]:
            raise ValueError("prepared postfix body conflicts with the same proven Action identity")
        if current[1] is not None and current[1] != identity[0] or current[2] is not None and current[2] != identity[1]:
            raise ValueError("prepared postfix delivery identity conflicts with its Action owner")
        if source is not None:
            if candidate is not None:
                current_identity = db.execute("select action_business_object_key,action_action_identity,action_operation from actions where id=?",
                                              (action_id,)).fetchone()
                if (current_identity[0] not in (None, candidate["business_object_key"])
                        or current_identity[1] not in (None, candidate["action_identity"])
                        or current_identity[2] not in (None, candidate["operation"])):
                    raise ValueError("prepared candidate identity conflicts with its exact delivery-key owner")
            for field, value in {"prepared_body": source["final_body"], "delivery_channel": source["channel"],
                                 "delivery_key": source["delivery_key"], "feedback_token": source["feedback_token"],
                                 "postfix_version": source["postfix_version"], "prepared_at": source["created_at"]}.items():
                db.execute("update actions set action_data=jsonb_set(action_data,?,json(?)) where id=?",
                           ("$." + field, json.dumps(value, ensure_ascii=False), action_id))
        receipt_id = None
        prepared = receipts_by_key.get(identity)
        dispatch = dispatch_by_key.get(identity)
        if prepared is not None or dispatch is not None:
            receipt_values = {}
            if prepared is not None:
                receipt_values.update(provider_result_json=prepared["provider_result_json"],
                                      observed_at=prepared["created_at"])
            if dispatch is not None:
                receipt_values["dispatched_at"] = dispatch["created_at"]
            receipt_id = append_action_receipt(db, action_id, values=receipt_values)
            if prepared is not None and prepared["provider_result_json"] is not None:
                db.execute("update actions set action_data=jsonb_set(action_data,'$.prepared_receipt_id',json(?)) where id=?",
                           (json.dumps(receipt_id), action_id))
        if source is not None:
            _map(db, "outbound_postfixes", identity, action_id, receipt_id)
        if prepared is not None:
            _map(db, "outbound_postfix_receipts", identity, action_id, receipt_id)
        if dispatch is not None:
            _map(db, "native_reply_dispatches", identity, action_id, receipt_id)


def _transfer_sent(db: sqlite3.Connection, rows: Iterable[dict], owners: dict[str, int],
                   result_receipts: dict[str, int]) -> dict[int, int]:
    sent_map = {}
    for source in rows:
        key = source["external_action_key"] or None
        action_id = owners.get(key) if key else None
        if action_id is None:
            action_id = create_action(db, values={
                "external_action_key": None, "prepared_body": source["reply_text"],
                "delivery_channel": source["channel"], "feedback_token": source["feedback_token"],
                "conversation_id": source["conversation_id"], "trigger_message_id": source["trigger_message_id"],
                "recall_key": source["recall_key"], "recall_status": source["recall_status"],
                "recall_error": source["recall_error"], "recalled_at": source["recalled_at"],
                "agent_run_id": source["agent_run_id"],
            })["id"]
        elif source["external_action_key"]:
            existing = db.execute("select action_prepared_body,action_conversation_id,action_trigger_message_id from actions where id=?",
                                  (action_id,)).fetchone()
            if any(old is not None and old != new for old, new in zip(existing,
                    (source["reply_text"], source["conversation_id"], source["trigger_message_id"]))):
                raise ValueError("Sent reply conflicts with canonical Action owner")
            updates = {"prepared_body": source["reply_text"], "delivery_channel": source["channel"],
                       "feedback_token": source["feedback_token"], "conversation_id": source["conversation_id"],
                       "trigger_message_id": source["trigger_message_id"], "recall_key": source["recall_key"],
                       "recall_status": source["recall_status"], "recall_error": source["recall_error"],
                       "recalled_at": source["recalled_at"]}
            for field, value in updates.items():
                db.execute("update actions set action_data=jsonb_set(action_data,?,json(?)) where id=?",
                           ("$." + field, json.dumps(value, ensure_ascii=False), action_id))
        receipt_values = {"observed_at": source["sent_at"], "agent_run_id": source["agent_run_id"]}
        if source["send_result_json"] is not None:
            receipt_values["provider_result_json"] = source["send_result_json"]
        receipt_id = append_action_receipt(db, action_id, values=receipt_values)
        if source["send_result_json"] is None and key:
            canonical_id = result_receipts.get(key)
            if canonical_id is not None:
                db.execute("update action_receipts set provider_receipt_id=? where id=?", (canonical_id, receipt_id))
        db.execute("update actions set action_data=jsonb_set(action_data,'$.delivery_receipt_id',json(?)) where id=?",
                   (json.dumps(receipt_id), action_id))
        _map(db, "sent_replies", source["id"], action_id, receipt_id)
        sent_map[source["id"]] = action_id
    return sent_map


def _transfer_observers(db: sqlite3.Connection, facts: dict[str, list[dict]], sent_map: dict[int, int]) -> None:
    for source in facts["sent_reply_observers"]:
        action_id = sent_map.get(source["sent_reply_id"])
        if action_id is None:
            raise ValueError("Sent observer has no mapped Sent owner")
        add_action_observer(db, action_id, agent_run_id=source["agent_run_id"],
                            task_id=source["reply_task_id"], created_at=source["created_at"])
        _map(db, "sent_reply_observers", (source["sent_reply_id"], source["agent_run_id"]), action_id, None)


def _transfer_email(db: sqlite3.Connection, facts: dict[str, list[dict]]) -> None:
    owners = {}
    receipts = {}
    for source in facts["email_actions"]:
        action_id = create_action(db, values={
            "action_plan_id": source["action_plan_id"], "classification_id": source["classification_id"],
            "account_id": source["account_id"], "operation": source["action_type"],
            "parameters_json": source["parameters_json"], "status": source["status"],
            "next_attempt_at": source["next_attempt_at"], "started_at": source["started_at"],
            "created_at": source["created_at"], "updated_at": source["updated_at"],
        })["id"]
        owners[source["action_id"]] = action_id
        _map(db, "email_actions", source["action_id"], action_id, None)
    for source in facts["email_action_attempts"]:
        action_id = owners.get(source["action_id"])
        if action_id is None:
            raise ValueError("Email attempt has no exact direct Action owner")
        receipt_id = append_action_receipt(db, action_id, values={
            "attempt_number": source["attempt_number"], "status": source["status"],
            "operation": source["provider_operation"], "target": source["provider_target"],
            "provider_result_id": source["provider_result_id"], "error_text": source["error"],
            "started_at": source["started_at"], "finished_at": source["finished_at"],
        })
        receipts[source["id"]] = receipt_id
        _map(db, "email_action_attempts", source["id"], action_id, receipt_id)
    for source in facts["email_historical_operations"]:
        action_ids = json.loads(source["action_ids_json"])
        if not isinstance(action_ids, list):
            raise ValueError("Email historical operation Action IDs are not an ordered JSON list")
        rewritten = []
        for source_id in action_ids:
            if not isinstance(source_id, str) or source_id not in owners:
                raise ValueError("Email historical operation references an unmapped direct Action")
            rewritten.append(str(owners[source_id]))
        from app.current_action_schema import current_action_payloads
        history_path = current_action_payloads().path("email_historical_operations", "action_ids_json")
        db.execute("update actions set payload_json=jsonb_set(payload_json,?,json_quote(?)) where id=? "
                   "and record_type='email_historical_operations'",
                   (history_path, _json_text(rewritten), source["physical_id"]))


def _rewrite_direct_retry_parents(db: sqlite3.Connection) -> None:
    """Retarget the surviving native retry FK through the typed source map."""
    from app.current_action_schema import current_action_objects

    trigger = next(
        obj["sql"] for obj in current_action_objects()
        if obj["type"] == "trigger" and obj["name"] == "trg_actions_email_retry_immutable_update"
    )
    # Retry rows are immutable at runtime. This exact parent-FK rewrite occurs
    # only inside the release transaction, from a covered typed key to its
    # mapped physical common Action ID.
    db.execute("drop trigger if exists trg_actions_email_retry_immutable_update")
    try:
        rows = db.execute(
            "select id,legacy_key,email_action_id from actions "
            "where record_type='email_direct_retry_requests' order by id"
        ).fetchall()
        for physical_id, legacy_key, source_action_id in rows:
            if not isinstance(source_action_id, str):
                raise ValueError("direct retry source Action key is not text")
            mapping = db.execute(
                "select action_id from action_entity_identity_map "
                "where source_type='email_actions' and source_identity=?",
                (_identity(source_action_id),),
            ).fetchone()
            if mapping is None:
                raise ValueError("direct retry source Action has no typed common-owner mapping")
            mapped_id = str(mapping[0])
            composite_identity = json.loads(legacy_key)
            if (not isinstance(composite_identity, list) or len(composite_identity) != 2
                    or composite_identity[0] != source_action_id):
                raise ValueError("direct retry source composite key disagrees with its typed parent projection")
            composite_identity[0] = mapped_id
            db.execute(
                "update actions set legacy_key=? "
                "where id=? and record_type='email_direct_retry_requests'",
                (json.dumps(composite_identity, ensure_ascii=False, separators=(",", ":")), physical_id),
            )
            if db.execute(
                "select email_operation_parent_type,email_action_id from actions where id=?",
                (physical_id,),
            ).fetchone() != ("action", mapped_id):
                raise ValueError("direct retry target Action FK did not resolve to the mapped common owner")
    finally:
        db.execute(trigger)


def transfer_source_facts(db: sqlite3.Connection, facts: dict[str, list[dict]]) -> None:
    """Transfer each covered source family into the common physical owners."""
    owners: dict[str, int] = {}
    result_receipts: dict[str, int] = {}
    _transfer_external_results(db, facts["external_action_results"], owners, result_receipts)
    prepared_candidates = _prepared_candidates(db)
    delivery_owners = _transfer_candidates(db, facts["candidate_action_attempts"], owners,
                                            result_receipts, prepared_candidates)
    _transfer_postfixes(db, facts, owners, prepared_candidates, delivery_owners)
    sent_map = _transfer_sent(db, facts["sent_replies"], owners, result_receipts)
    _transfer_observers(db, facts, sent_map)
    _transfer_email(db, facts)
    _rewrite_direct_retry_parents(db)
