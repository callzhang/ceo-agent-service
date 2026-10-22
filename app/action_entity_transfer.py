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
    from app.action_storage import (
        REVIEW_CANDIDATE_SQL, CANDIDATE_EXECUTION_SQL, CANDIDATE_REVIEW_SQL,
        CANDIDATE_SELECTION_SQL,
    )
    from app.task20_definitions import task20_projection
    from app import shared_action_lifecycle

    task_sql = task20_projection("reply_tasks")
    cursor = db.execute(f"""select c.*,t.business_object_key,e.review_id,e.selection_id
        from ({CANDIDATE_EXECUTION_SQL}) e
        join ({REVIEW_CANDIDATE_SQL}) c on c.id=e.candidate_id
        join ({task_sql}) t on t.id=c.task_id where e.id=?""", (execution_id,))
    row = cursor.fetchone()
    if row is None:
        raise ValueError("candidate action has no exact candidate execution owner")
    candidate = _rowdict(cursor, row)
    review_cursor = db.execute(
        f"select * from ({CANDIDATE_REVIEW_SQL}) where id=? and candidate_id=?",
        (candidate["review_id"], candidate["id"]),
    )
    review_row = review_cursor.fetchone()
    review = None if review_row is None else _rowdict(review_cursor, review_row)
    if (review is None or review["decision"] != "approve"
            or review["candidate_digest"] != candidate["candidate_digest"]):
        raise ValueError("candidate action is not bound to its exact approved review")
    selection = None
    if candidate["selection_id"] is not None:
        cursor = db.execute(f"select * from ({CANDIDATE_SELECTION_SQL}) where id=?",
                            (candidate["selection_id"],))
        selected_row = cursor.fetchone()
        selected = None if selected_row is None else _rowdict(cursor, selected_row)
        if (selected is None or selected["candidate_id"] != candidate["id"]
                or selected["review_id"] != candidate["review_id"]):
            raise ValueError("candidate action selection is not bound to its exact approved review")
        selection = selected
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
            "operation": source["operation"],
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
        binding_id = append_action_receipt(db, action_id, values={
            "provider_receipt_id": receipt_id, "operation": source["operation"],
            "target": source["target_identifiers_json"],
            "result_digest": source["result_digest"],
            "agent_run_id": source["first_agent_run_id"],
            "created_at": source["created_at"], "observed_at": source["created_at"],
        })
        owners[key] = action_id
        receipts[key] = receipt_id
        _map(db, "external_action_results", key, action_id, binding_id)


def _delivery_key(business_key: str, identity: str, generation: str, revision: int) -> str:
    from app.service_message_sender import agent_message_delivery_key
    return agent_message_delivery_key(business_object_key=business_key, action_identity=identity,
                                      execution_generation=generation, proposal_revision=revision)


def _prepared_candidates(db: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    """Index only steps of an exact approved execution and selected branch."""
    from app.action_storage import CANDIDATE_EXECUTION_SQL, REVIEW_CANDIDATE_SQL, CANDIDATE_SELECTION_SQL
    from app.task20_definitions import task20_projection
    from app.shared_action_lifecycle import reviewed_action_steps

    result = {}
    executions = db.execute(f"select id from ({CANDIDATE_EXECUTION_SQL}) order by id").fetchall()
    for (execution_id,) in executions:
        cursor = db.execute(f"""select c.*,t.business_object_key,t.channel,e.review_id,e.selection_id
            from ({CANDIDATE_EXECUTION_SQL}) e
            join ({REVIEW_CANDIDATE_SQL}) c on c.id=e.candidate_id
            join ({task20_projection('reply_tasks')}) t
              on t.id=c.task_id where e.id=?""", (execution_id,))
        candidate = _rowdict(cursor, cursor.fetchone())
        candidate_channel = candidate["channel"]
        selection = None
        if candidate["selection_id"] is not None:
            selection_cursor = db.execute(
                f"select * from ({CANDIDATE_SELECTION_SQL}) where id=?",
                (candidate["selection_id"],),
            )
            selection_row = selection_cursor.fetchone()
            selection = None if selection_row is None else _rowdict(selection_cursor, selection_row)
            if (selection is None or selection["candidate_id"] != candidate["id"]
                    or selection["review_id"] != candidate["review_id"]):
                raise ValueError("candidate execution selection is not bound to its exact review")
        steps = reviewed_action_steps(db, candidate, selection=selection)
        for action_index, step in enumerate(steps):
            candidate, step = _candidate_action(db, execution_id, action_index)
            identity = step["action_identity"]
            if candidate.get("selection_id") is not None:
                from app.action_storage import CANDIDATE_SELECTION_SQL
                option_key = db.execute(
                    f"select option_key from ({CANDIDATE_SELECTION_SQL}) where id=?",
                    (candidate["selection_id"],),
                ).fetchone()[0]
                identity += ":" + option_key if option_key else ""
            index = (candidate_channel, _delivery_key(
                candidate["business_object_key"], identity,
                candidate["execution_generation"], candidate["proposal_revision"],
            ))
            fact = {"business_object_key": candidate["business_object_key"],
                    "action_identity": identity, "channel": candidate_channel}
            old = result.get(index)
            if old is not None and old != fact:
                raise ValueError("prepared delivery key resolves to conflicting approved execution steps")
            result[index] = fact
    return result


def _transfer_candidates(db: sqlite3.Connection, rows: Iterable[dict], owners: dict[str, int],
                         result_receipts: dict[str, int], prepared_candidates: dict) -> dict:
    delivery_owners = {}
    orphan_action_owners = {}
    for source in rows:
        key = source["external_action_key"] or None
        candidate, step = _candidate_action(db, source["execution_id"], source["action_index"])
        target = _json_text(step.get("target", {}))
        operation = step["operation"]
        values = {
            "external_action_key": key if key in owners else None,
            "business_object_key": candidate["business_object_key"],
            "action_identity": step["action_identity"],
        }
        action_id = owners.get(key) if key else None
        if action_id is not None:
            canonical = db.execute("select action_business_object_key,action_action_identity from actions where id=?",
                                   (action_id,)).fetchone()
            receipt_target = db.execute("select target from action_receipts where id=?", (result_receipts[key],)).fetchone()[0]
            if (tuple(canonical) != (values["business_object_key"], values["action_identity"])
                    or operation != db.execute("select operation from action_receipts where id=?",
                                                        (result_receipts[key],)).fetchone()[0]
                    or target != receipt_target):
                raise ValueError("candidate action conflicts with canonical external Action facts")
        else:
            if key:
                identity = (values["business_object_key"], values["action_identity"], operation, target)
                previous = orphan_action_owners.get(key)
                if previous is not None:
                    if previous[0] != identity:
                        raise ValueError("candidate action key resolves to conflicting source facts")
                    action_id = previous[1]
                else:
                    values.update({"external_action_key": key, "operation": operation})
                    action_id = create_action(db, values=values)["id"]
                    orphan_action_owners[key] = (identity, action_id)
            else:
                action_id = create_action(db, values=values)["id"]
        receipt_values = {
            "execution_id": source["execution_id"], "action_index": source["action_index"],
            "status": source["status"], "outcome_json": source["result_json"],
            "created_at": source["created_at"], "updated_at": source["updated_at"],
        }
        if key and key not in owners:
            receipt_values.update({"operation": operation, "target": target})
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
                "action_identity": candidate["action_identity"],
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
                        or current_identity[1] not in (None, candidate["action_identity"])):
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


def _identity_value(encoded: str):
    value = json.loads(encoded)
    if value[0] == "tuple":
        return tuple(_identity_part_value(part) for part in value[1])
    return _identity_part_value(value)


def _identity_part_value(part):
    kind, value = part
    if kind == "integer":
        return int(value)
    if kind == "text":
        return value
    if kind == "null":
        return None
    raise ValueError("unsupported typed Action source identity")


def _current_action_rows(db):
    from app.action_entity_definitions import ACTION_FIELDS

    cursor = db.execute("select id," + ",".join("action_" + field for field in ACTION_FIELDS)
                        + " from actions where record_type='action' order by id")
    names = ("id", *ACTION_FIELDS)
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _capture_inverse_facts(db):
    """Capture typed current facts before retiring the common Action rows."""
    maps = {}
    for source_type, encoded, action_id, receipt_id in db.execute(
            "select source_type,source_identity,action_id,receipt_id from action_entity_identity_map"):
        identity = _identity_value(encoded)
        maps[(source_type, identity)] = (action_id, receipt_id)
    mapped_source_by_action_receipt = {
        (kind, action_id, receipt_id): identity
        for (kind, identity), (action_id, receipt_id) in maps.items()
        if receipt_id is not None
    }
    mapped_source_by_action = {}
    for (kind, identity), (action_id, _receipt_id) in maps.items():
        mapped_source_by_action.setdefault((kind, action_id), identity)
    actions = _current_action_rows(db)
    by_id = {row["id"]: row for row in actions}
    # Use cursor metadata once; sqlite3.Row factories are not assumed here.
    receipt_cursor = db.execute("select * from action_receipts order by id")
    receipt_names = tuple(column[0] for column in receipt_cursor.description)
    receipts = {row[0]: dict(zip(receipt_names, row, strict=True)) for row in receipt_cursor.fetchall()}
    receipts_by_action = {}
    for receipt in receipts.values():
        receipts_by_action.setdefault(receipt["action_id"], []).append(receipt)
    def mapped_id(kind, identity):
        item = maps.get((kind, identity))
        return item[0] if item else None

    result = {kind: [] for kind in (
        "external_action_results", "candidate_action_attempts", "outbound_postfixes",
        "outbound_postfix_receipts", "native_reply_dispatches", "sent_replies",
        "sent_reply_observers", "email_actions", "email_action_attempts",
    )}
    for action in actions:
        action_receipts = receipts_by_action.get(action["id"], ())
        key = action["external_action_key"]
        canonical_body_json = None
        if key:
            binding_id = maps.get(("external_action_results", key), (None, None))[1]
            bindings = [r for r in action_receipts if r["provider_receipt_id"] is not None
                            and r["execution_id"] is None and r["attempt_number"] is None
                            and r["id"] not in (action["delivery_receipt_id"], action["prepared_receipt_id"])
                            and r["status"] is None and r["target"] is not None
                            and r["agent_run_id"] is not None and r["created_at"] is not None]
            binding = receipts.get(binding_id) if binding_id is not None else max(
                bindings, key=lambda r: r["id"], default=None)
            if binding is not None:
                body = receipts.get(binding["provider_receipt_id"])
                if body is None or body["provider_result_json"] is None:
                    raise ValueError("canonical Action current body receipt is unavailable")
                canonical_body_json = body["provider_result_json"]
                result["external_action_results"].append({
                    "external_action_key": key, "business_object_key": action["business_object_key"],
                    "action_identity": action["action_identity"], "operation": binding["operation"],
                    "target_identifiers_json": binding["target"],
                    "provider_result_json": canonical_body_json,
                    "result_digest": body["result_digest"], "first_agent_run_id": binding["agent_run_id"],
                    "created_at": binding["created_at"],
                })
            elif maps.get(("external_action_results", key)) is not None:
                raise ValueError("mapped canonical Action has no stable metadata receipt")

        if action["delivery_channel"] is not None and action["delivery_key"] is not None:
            pair = (action["delivery_channel"], action["delivery_key"])
            if action["prepared_body"] is not None:
                    result["outbound_postfixes"].append({
                    "channel": pair[0], "delivery_key": pair[1], "final_body": action["prepared_body"],
                    "feedback_token": action["feedback_token"] or "", "postfix_version": action["postfix_version"],
                    "created_at": action["prepared_at"],
                })
            prepared = receipts.get(action["prepared_receipt_id"])
            if prepared and prepared["provider_result_json"] is not None:
                result["outbound_postfix_receipts"].append({
                    "channel": pair[0], "delivery_key": pair[1],
                    "provider_result_json": prepared["provider_result_json"],
                    "created_at": prepared["observed_at"] or prepared["created_at"],
                })
            dispatch = next((r for r in action_receipts if r["dispatched_at"] is not None), None)
            if dispatch:
                result["native_reply_dispatches"].append({
                    "channel": pair[0], "delivery_key": pair[1], "created_at": dispatch["dispatched_at"],
                })
            parent = next((row for row in result["outbound_postfixes"]
                           if (row["channel"], row["delivery_key"]) == pair), None)
            if parent is not None:
                parent["_receipt_json"] = (prepared["provider_result_json"]
                                           if prepared and prepared["provider_result_json"] is not None else None)
                parent["_receipt_created_at"] = (prepared["observed_at"] or prepared["created_at"]
                                                   if prepared and prepared["provider_result_json"] is not None else None)
                parent["_dispatch_created_at"] = dispatch["dispatched_at"] if dispatch else None

        for receipt in action_receipts:
            if receipt["execution_id"] is not None and receipt["action_index"] is not None:
                identity = mapped_source_by_action_receipt.get(
                    ("candidate_action_attempts", action["id"], receipt["id"]))
                # Keep the observation's own SQL NULL or exact local JSON.
                # Canonical provider bodies remain separate external-result
                # facts and are never copied into an absent local outcome.
                outcome = receipt["outcome_json"]
                if outcome is None and receipt["provider_receipt_id"] is not None:
                    observed = receipts.get(receipt["provider_receipt_id"])
                    if observed is None or observed["provider_result_json"] is None:
                        raise ValueError("candidate observation points to an unavailable provider body")
                    observed_json = observed["provider_result_json"]
                    outcome = None if observed_json == canonical_body_json else observed_json
                row = {"execution_id": receipt["execution_id"], "action_index": receipt["action_index"],
                       "external_action_key": action["external_action_key"] or "", "status": receipt["status"],
                       "result_json": outcome, "created_at": receipt["created_at"], "updated_at": receipt["updated_at"]}
                if identity is not None:
                    row["id"] = identity
                result["candidate_action_attempts"].append(row)
            if receipt["attempt_number"] is not None and action["action_plan_id"] is not None:
                from hashlib import sha256
                direct_id = "email-action:" + sha256(
                    (action["action_plan_id"] + ":" + action["operation"]).encode()
                ).hexdigest()
                source_attempt = mapped_source_by_action_receipt.get(
                    ("email_action_attempts", action["id"], receipt["id"]))
                row = {"action_id": direct_id, "attempt_number": receipt["attempt_number"],
                       "status": receipt["status"], "provider_operation": receipt["operation"] or "",
                       "provider_target": receipt["target"] or "", "provider_result_id": receipt["provider_result_id"] or "",
                       "error": receipt["error_text"] or "", "started_at": receipt["started_at"] or "",
                       "finished_at": receipt["finished_at"] or ""}
                if source_attempt is not None:
                    row["id"] = source_attempt
                result["email_action_attempts"].append(row)

        if action["delivery_receipt_id"] is not None:
            sent = receipts.get(action["delivery_receipt_id"])
            if sent is None:
                raise ValueError("Sent Action points to a missing exact delivery receipt")
            identity = mapped_source_by_action_receipt.get(("sent_replies", action["id"], sent["id"]))
            result_json = sent["provider_result_json"]
            if sent["provider_receipt_id"] is not None:
                body = receipts.get(sent["provider_receipt_id"])
                if body is None or body["provider_result_json"] is None:
                    raise ValueError("Sent Action points to an unavailable provider body")
                result_json = None if body["provider_result_json"] == canonical_body_json else body["provider_result_json"]
            row = {"conversation_id": action["conversation_id"], "trigger_message_id": action["trigger_message_id"],
                   "reply_text": action["prepared_body"], "sent_at": sent["observed_at"],
                   "send_result_json": result_json, "recall_key": action["recall_key"] or "",
                   "recall_status": action["recall_status"] or "", "recall_error": action["recall_error"] or "",
                   "recalled_at": action["recalled_at"], "feedback_token": action["feedback_token"] or "",
                   "channel": action["delivery_channel"] or "dingtalk", "agent_run_id": sent["agent_run_id"],
                   "external_action_key": action["external_action_key"] or ""}
            if identity is not None:
                row["id"] = identity
            row["_action_id"] = action["id"]
            result["sent_replies"].append(row)

    from app.email_store import DIRECT_EMAIL_ACTION_SQL
    cursor = db.execute("select * from (" + DIRECT_EMAIL_ACTION_SQL + ")")
    names = tuple(c[0] for c in cursor.description)
    for values in cursor.fetchall():
        row = dict(zip(names, values, strict=True))
        old_identity = mapped_source_by_action.get(("email_actions", row["owner_id"]))
        if old_identity is None:
            from hashlib import sha256
            old_identity = "email-action:" + sha256(
                (row["action_plan_id"] + ":" + row["action_type"]).encode()
            ).hexdigest()
        result["email_actions"].append({
            "action_id": old_identity, "action_plan_id": row["action_plan_id"],
            "classification_id": row["classification_id"], "account_id": row["account_id"],
            "action_type": row["action_type"], "parameters_json": row["parameters_json"],
            "status": row["status"], "attempt_count": row["attempt_count"],
            "next_attempt_at": row["next_attempt_at"], "started_at": row["started_at"],
            "finished_at": row["finished_at"], "provider_operation": row["provider_operation"],
            "provider_target": row["provider_target"], "provider_result_id": row["provider_result_id"],
            "error": row["error"], "config_version": row["config_version"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "_action_id": row["owner_id"],
        })

    observers = db.execute("select action_id,agent_run_id,task_id,created_at from action_observers").fetchall()
    for action_id, agent_run_id, task_id, created_at in observers:
        sent_id = mapped_source_by_action.get(("sent_replies", action_id))
        if sent_id is None and by_id.get(action_id, {}).get("delivery_receipt_id") is None:
            raise ValueError("Action observer has no predecessor Sent identity")
        result["sent_reply_observers"].append({"_action_id": action_id, "agent_run_id": agent_run_id,
                                               "reply_task_id": task_id, "created_at": created_at,
                                               "_legacy_identity": (sent_id, agent_run_id) if sent_id else None})

    represented = {row["_action_id"] for row in result["sent_replies"]}
    represented.update(action_id for (kind, _identity), (action_id, _receipt) in maps.items()
                       if kind in {"external_action_results", "candidate_action_attempts",
                                   "outbound_postfixes", "email_actions"})
    represented.update(action["id"] for action in actions if action["external_action_key"]
                       or action["action_plan_id"] is not None
                       or action["delivery_channel"] is not None and action["delivery_key"] is not None)
    if {row["id"] for row in actions} - represented:
        raise ValueError("current Action has no predecessor source-family projection")

    history_path = __import__("app.current_action_schema", fromlist=["current_action_payloads"]).current_action_payloads().path(
        "email_historical_operations", "action_ids_json")
    history = []
    for physical_id, ids_json in db.execute(
            "select id,json_extract(payload_json,?) from actions where record_type='email_historical_operations'",
            (history_path,)):
        history.append((physical_id, json.loads(ids_json)))
    retry = db.execute("select id,legacy_key,email_action_id from actions "
                       "where record_type='email_direct_retry_requests' order by id").fetchall()
    return {"facts": result, "maps": maps, "actions": actions, "receipts": receipts,
            "observers": observers, "history": history, "retry": retry}


def restore_inverse_facts(db, captured):
    """Bind old source discriminator codes for one bounded inverse write."""
    from app import action_storage as storage

    source_types = storage.action_payloads(
        "fresh_memory_fk", physical_provenance=True, selection_references=True,
        execution_owner=True, shared_lifecycle=True, specialized_claims=True,
        postfix_owner=True,
    ).record_types
    previous_record_types = storage.ACTION_RECORD_TYPES
    storage.ACTION_RECORD_TYPES = source_types
    try:
        _restore_inverse_facts_bound(db, captured)
    finally:
        storage.ACTION_RECORD_TYPES = previous_record_types


def _restore_inverse_facts_bound(db, captured):
    """Write covered Action facts through the predecessor family allocators."""
    from app import action_storage as storage
    facts, maps = captured["facts"], captured["maps"]
    sent_by_action = {}
    email_by_target = {}

    def mapped(kind, identity):
        return identity if (kind, identity) in maps else None

    def insert_predecessor_email_action(values):
        """Write the exact predecessor Email payload with its rowid allocator."""
        storage._check_fields("email_actions", values)
        from app.shared_action_definitions import direct_step_layout

        layout = direct_step_layout()
        source_values = {field: values.get(field) for field in layout.fields}
        if source_values["status"] != "processing":
            # The predecessor contract retained the active start only while a
            # step was processing; completed attempt clocks live in its
            # email_action_attempts child family.
            source_values["started_at"] = ""
        payload = storage._action_payload(db, "email_actions", source_values, layout=layout)
        storage._email_lock(db, (*values.values(), None))
        rowid = storage._email_rowid_next(db, "email_actions")
        cursor = db.execute(
            "insert into actions(_kind,legacy_key,payload_json) values(?,?,?) returning id",
            (storage.ACTION_RECORD_TYPES.code("email_actions"), str(rowid), payload),
        )
        physical_id = cursor.fetchone()[0]
        cursor.fetchall()
        return storage.ActionWriteResult(rowid, 1), physical_id

    for row in facts["external_action_results"]:
        storage.insert_action(db, "external_action_results", row)

    for row in facts["candidate_action_attempts"]:
        storage.insert_action(db, "candidate_action_attempts", row, execution_owner=True)

    for row in facts["outbound_postfixes"]:
        receipt_json = row.pop("_receipt_json", None)
        receipt_clock = row.pop("_receipt_created_at", None)
        dispatch_clock = row.pop("_dispatch_created_at", None)
        storage.insert_action(db, "outbound_postfixes", row)
        # These are physical columns in the genuine predecessor declaration.
        # C1's common Action declaration embeds the same child facts as current
        # receipt rows, but the old source stores them on the prepared parent.
        child_values = {}
        if receipt_json is not None:
            child_values.update(postfix_receipt_provider_result_json=receipt_json,
                                postfix_receipt_created_at=receipt_clock)
        if dispatch_clock is not None:
            child_values["postfix_dispatch_created_at"] = dispatch_clock
        if child_values:
            db.execute("update actions set " + ",".join(f"{column}=?" for column in child_values)
                       + " where record_type='outbound_postfixes' and channel=? and delivery_key=?",
                       (*child_values.values(), row["channel"], row["delivery_key"]))

    for row in facts["sent_replies"]:
        action_id = row.pop("_action_id")
        result = storage.insert_action(db, "sent_replies", row)
        source_id = result.lastrowid
        sent_by_action[action_id] = source_id

    for row in facts["email_actions"]:
        values = dict(row)
        source_id = values.pop("action_id")
        current_owner = values.pop("_action_id")
        values["action_id"] = source_id
        _write_result, _physical_id = insert_predecessor_email_action(values)
        owner = db.execute("select id from actions where record_type='email_actions' and email_action_id=?",
                           (source_id,)).fetchone()
        if owner is None:
            raise ValueError("inverse direct Email writer did not create its exact source Action")
        # Current physical parent IDs are not source identities; retain only this
        # local translation for the explicitly covered retry/history references.
        email_by_target[current_owner] = (source_id, owner[0])

    for row in facts["email_action_attempts"]:
        storage.insert_action(db, "email_action_attempts", row)

    for action_id, agent_run_id, task_id, created_at in captured["observers"]:
        sent_id = sent_by_action.get(action_id)
        if sent_id is None:
            original = next((identity for (kind, identity), (owner, _receipt) in maps.items()
                             if kind == "sent_replies" and owner == action_id), None)
            if original is not None:
                sent_id = original
        if sent_id is None:
            raise ValueError("Action observer has no predecessor Sent identity")
        storage.insert_action(db, "sent_reply_observers", {
            "sent_reply_id": sent_id, "agent_run_id": agent_run_id,
            "reply_task_id": task_id, "created_at": created_at,
        }, execution_owner=True)

    # Historical operation arrays carry public direct-Email keys; translate the
    # current physical IDs back while preserving order and JSON string identity.
    from app.action_entity_schema_release import _drop_action_guards
    from app.current_action_schema_release import source_objects
    _drop_action_guards(db)
    db.execute("drop trigger if exists trg_actions_email_retry_immutable_update")
    for physical_id, ids in captured["history"]:
        if not isinstance(ids, list):
            raise ValueError("Email historical operation Action IDs are not an ordered JSON list")
        rewritten = []
        for value in ids:
            target_id = int(value)
            parent = email_by_target.get(target_id)
            if parent is None:
                raise ValueError("Email historical operation references an unmapped current Action")
            rewritten.append(parent[0])
        # The source payload path is the same typed Email field path used by its writer.
        from app.action_storage import _action_path
        db.execute("update actions set payload_json=jsonb_set(payload_json,?,json_quote(?)) where id=?",
                   (_action_path("email_historical_operations", "action_ids_json"),
                    json.dumps(rewritten, ensure_ascii=False, separators=(",", ":")), physical_id))

    for physical_id, legacy_key, old_parent_id in captured["retry"]:
        pair = json.loads(legacy_key)
        current_parent = int(pair[0])
        translated = email_by_target.get(current_parent)
        if translated is None:
            raise ValueError("direct retry request references an unmapped current Email Action")
        old_pair = json.dumps([translated[0], pair[1]], ensure_ascii=False, separators=(",", ":"))
        db.execute("update actions set legacy_key=? where id=? "
                   "and record_type='email_direct_retry_requests'",
                   (old_pair, physical_id))
    old_retry_guard = next(sql for kind, name, _table, sql in source_objects("fresh_memory_fk")
                           if kind == "trigger" and name == "trg_actions_email_retry_immutable_update")
    db.execute(old_retry_guard)
