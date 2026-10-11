"""Native, typed handlers for reviewed service-owned actions."""

import json
from datetime import datetime, timezone
from hashlib import sha256

from app.agent_contracts import ProposedAction
from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.service_message_sender import ServiceMessageSender, agent_message_delivery_key
from app.system_executor import ActionOutcome


class DingTalkMessageHandler:
    def __init__(self, store: object, dws: object, *, sender: ServiceMessageSender | None = None) -> None:
        self.store = store
        self.dws = dws
        self.sender = sender or ServiceMessageSender(store=store, dingtalk=dws)

    def _prepared(self, action: ProposedAction, candidate: dict[str, object]):
        task = self.store.get_reply_task(candidate["task_id"])
        if task is None:
            return None, None
        delivery_key = agent_message_delivery_key(
            business_object_key=task.business_object_key,
            action_identity=(
                action.action_identity + ":" + str(candidate["option_key"])
                if candidate.get("selection_id") is not None else action.action_identity
            ),
            execution_generation=task.execution_generation,
            proposal_revision=candidate["proposal_revision"],
        )
        prepared = self.store.get_outbound_postfix("dingtalk", delivery_key)
        body = action.payload.get("content", action.payload.get("text", action.payload.get("reply_text")))
        if prepared is None or not isinstance(body, str) or body != prepared.final_body:
            return task, None
        return task, prepared

    def _verified(self, provider_result: object) -> ActionOutcome:
        if not isinstance(provider_result, dict):
            return ActionOutcome("uncertain", {"reason": "provider_result_invalid"})
        verification = self.dws.verify_message_send_result(provider_result)
        state = verification.get("state")
        if state == "sent":
            return ActionOutcome("verified", {"provider_result": provider_result, "verification": verification})
        if state == "failed":
            return ActionOutcome("failed", {"provider_result": provider_result, "verification": verification})
        return ActionOutcome("uncertain", {"provider_result": provider_result, "verification": verification})

    def dispatch(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        task, prepared = self._prepared(action, candidate)
        if prepared is None:
            return ActionOutcome("failed", {"reason": "reviewed_message_preparation_mismatch"})
        target = action.target
        if action.operation == "reply_to_message":
            if target.get("conversation_id") != task.conversation_id or target.get("message_id") != task.trigger_message_id:
                return ActionOutcome("failed", {"reason": "reply_target_mismatch"})
            trigger = DingTalkMessage.model_validate_json(task.trigger_message_json)
            if trigger.open_conversation_id != task.conversation_id or trigger.open_message_id != task.trigger_message_id:
                return ActionOutcome("failed", {"reason": "reply_trigger_mismatch"})
            conversation = DingTalkConversation(
                open_conversation_id=task.conversation_id, title=task.conversation_title,
                single_chat=task.single_chat, unread_point=0,
            )
            receipt = self.sender.send_dingtalk_reply_to_trigger_prepared(
                prepared, conversation=conversation, trigger=trigger,
            )
        else:
            conversation_id = target.get("conversation_id")
            user_id = target.get("user_id")
            open_dingtalk_id = target.get("open_dingtalk_id")
            if action.operation == "send_direct_message":
                conversation_id = None
            elif action.operation == "send_group_message":
                user_id = None
                open_dingtalk_id = None
            if conversation_id:
                user_id = None
                open_dingtalk_id = None
            elif user_id:
                open_dingtalk_id = None
            if not conversation_id and not user_id and not open_dingtalk_id:
                return ActionOutcome("failed", {"reason": "exact_message_target_missing"})
            receipt = self.sender.send_dingtalk_prepared(
                prepared, conversation_id=conversation_id, user_id=user_id,
                open_dingtalk_id=open_dingtalk_id,
            )
        return self._verified(receipt.provider_result)

    def reconcile(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome | None:
        task, prepared = self._prepared(action, candidate)
        if prepared is None:
            return ActionOutcome("failed", {"reason": "reviewed_message_preparation_mismatch"})
        receipt = self.store.get_outbound_postfix_receipt("dingtalk", prepared.delivery_key)
        if receipt is not None:
            return self._verified(receipt)
        if action.operation != "reply_to_message":
            target = action.target
            conversation_id = target.get("conversation_id")
            user_id = target.get("user_id")
            open_id = target.get("open_dingtalk_id")
            if action.operation == "send_direct_message":
                conversation_id = None
            if conversation_id:
                user_id = None
                open_id = None
            elif user_id:
                open_id = None
            conversation = DingTalkConversation(
                open_conversation_id=conversation_id or "", title=task.conversation_title,
                single_chat=not bool(conversation_id), unread_point=0,
                direct_user_id=user_id or "", direct_open_dingtalk_id=open_id or "",
            )
            boundary = datetime.fromisoformat(candidate["action_attempt"]["created_at"])
            if boundary.tzinfo is None:
                # Old second-precision rows cannot establish the dispatch instant.
                return None
            receipt = self.dws.reconcile_message_send(
                conversation, prepared.final_body, not_before=boundary,
            )
            if receipt is None:
                return None
            result = self._verified(receipt)
            if result.status == "verified":
                self.store.record_outbound_postfix_receipt("dingtalk", prepared.delivery_key, receipt)
            return result
        trigger = DingTalkMessage.model_validate_json(task.trigger_message_json)
        conversation = DingTalkConversation(
            open_conversation_id=task.conversation_id, title=task.conversation_title,
            single_chat=task.single_chat, unread_point=0,
        )
        receipt = self.dws.reconcile_reply_to_trigger(conversation, trigger, prepared.final_body)
        if receipt is None:
            return None
        result = self._verified(receipt)
        if result.status == "verified":
            self.store.record_outbound_postfix_receipt("dingtalk", prepared.delivery_key, receipt)
        return result


def native_dingtalk_message_handlers(store: object, dws: object) -> dict[tuple[str, str], DingTalkMessageHandler]:
    handler = DingTalkMessageHandler(store, dws)
    return {
        ("dingtalk-chat", "send_message"): handler,
        ("dingtalk-chat", "send_group_message"): handler,
        ("dingtalk-chat", "send_direct_message"): handler,
        ("dingtalk-chat", "reply_to_message"): handler,
    }


class CalendarResponseHandler:
    """A calendar response is complete only after the event reflects it."""

    def __init__(self, dws: object) -> None:
        self.dws = dws

    def _readback(self, action: ProposedAction) -> ActionOutcome:
        event_id = action.target.get("event_id")
        response = action.payload.get("response_status")
        if not isinstance(event_id, str) or not event_id or not isinstance(response, str) or not response:
            return ActionOutcome("failed", {"reason": "calendar_response_identity_missing"})
        event = self.dws.get_calendar_event(event_id)
        if event is not None and event.event_id == event_id and event.self_response_status == response:
            return ActionOutcome("verified", {"event_id": event_id, "self_response_status": response})
        return ActionOutcome("uncertain", {"event_id": event_id, "expected_response": response})

    def dispatch(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        event_id = action.target.get("event_id")
        response = action.payload.get("response_status")
        if not isinstance(event_id, str) or not event_id or not isinstance(response, str) or not response:
            return ActionOutcome("failed", {"reason": "calendar_response_identity_missing"})
        receipt = self.dws.respond_calendar_event(event_id, response)
        if not isinstance(receipt, dict) or receipt.get("success") is False:
            return ActionOutcome("failed", {"reason": "calendar_response_provider_rejected"})
        return self._readback(action)

    def reconcile(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        return self._readback(action)


class OaDecisionHandler:
    """Execute an exact OA node decision and verify that node's provider state."""

    _decisions = {"approve": ("通过", "AGREE"), "reject": ("拒绝", "REFUSE")}

    def __init__(self, dws: object) -> None:
        self.dws = dws

    def _task_state(self, action: ProposedAction) -> tuple[str, dict[str, object]]:
        process_id = action.target.get("process_instance_id")
        task_id = action.target.get("task_id")
        if not isinstance(process_id, str) or not process_id or not isinstance(task_id, str) or not task_id:
            return "invalid", {}
        data = self.dws.read_oa_approval_detail(process_id)
        detail = data.get("result") if isinstance(data, dict) else None
        if not isinstance(detail, dict) or str(detail.get("processInstanceId") or "") != process_id:
            return "unknown", {}
        tasks = detail.get("tasks")
        if not isinstance(tasks, list):
            return "unknown", {}
        matching = [item for item in tasks if isinstance(item, dict) and str(item.get("taskId")) == task_id]
        if len(matching) != 1:
            return "unknown", {}
        task = matching[0]
        owner_id = str(task.get("userid") or task.get("userId") or "").strip()
        current_user_id = self.dws.get_current_user_id()
        if not owner_id or not current_user_id:
            return "unknown", task
        if owner_id != current_user_id:
            return "conflict", task
        status = str(task.get("taskStatus") or "").upper()
        result = str(task.get("taskResult") or "").upper()
        expected = self._decisions[action.operation][1]
        if status == "COMPLETED":
            return ("verified" if result == expected else "conflict"), task
        if status == "RUNNING":
            return "running", task
        return "unknown", task

    def dispatch(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        if action.operation not in self._decisions:
            return ActionOutcome("failed", {"reason": "unsupported_oa_decision"})
        remark = action.payload.get("remark")
        if not isinstance(remark, str) or not remark.strip():
            return ActionOutcome("failed", {"reason": "oa_remark_missing"})
        state, task = self._task_state(action)
        if state == "verified":
            return ActionOutcome("verified", {"readback": task})
        if state == "conflict":
            return ActionOutcome("business_state_changed", {"reason": "oa_task_state_conflict", "readback": task})
        if state == "invalid":
            return ActionOutcome("failed", {"reason": "oa_task_target_invalid"})
        if state != "running":
            return ActionOutcome("uncertain", {"reason": "oa_current_task_unresolved"})
        receipt = self.dws.execute_oa_approval_action(
            action.target["process_instance_id"], action.target["task_id"],
            self._decisions[action.operation][0], remark,
        )
        if not isinstance(receipt, dict) or receipt.get("success") is False:
            return ActionOutcome("failed", {"reason": "oa_provider_rejected"})
        return self.reconcile(action, action_key=action_key, candidate=candidate)

    def reconcile(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        state, task = self._task_state(action)
        if state == "verified":
            return ActionOutcome("verified", {"readback": task})
        if state == "conflict":
            return ActionOutcome("business_state_changed", {"reason": "oa_task_state_conflict", "readback": task})
        if state == "invalid":
            return ActionOutcome("failed", {"reason": "oa_task_target_invalid"})
        return ActionOutcome("uncertain", {"reason": "oa_task_result_unverified", "readback": task})


class OaRevertHandler(OaDecisionHandler):
    _decisions = {"revert_task": ("退回", "REDIRECT_PROCESS")}

    def dispatch(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        target_activity = action.target.get("target_activity_id")
        revert_action = action.payload.get("revert_action")
        remark = action.payload.get("remark")
        if not all(isinstance(value, str) and value.strip() for value in (target_activity, revert_action, remark)):
            return ActionOutcome("failed", {"reason": "oa_revert_parameters_missing"})
        state, task = self._task_state(action)
        if state == "verified":
            return ActionOutcome("verified", {"readback": task})
        if state == "conflict":
            return ActionOutcome("business_state_changed", {"reason": "oa_task_state_conflict", "readback": task})
        if state == "invalid":
            return ActionOutcome("failed", {"reason": "oa_task_target_invalid"})
        if state != "running":
            return ActionOutcome("uncertain", {"reason": "oa_current_task_unresolved"})
        receipt = self.dws.revert_oa_approval_task(
            process_instance_id=action.target["process_instance_id"],
            task_id=action.target["task_id"],
            target_activity_id=target_activity,
            revert_action=revert_action,
            remark=remark,
        )
        if not isinstance(receipt, dict) or receipt.get("success") is False:
            return ActionOutcome("failed", {"reason": "oa_revert_provider_rejected"})
        return self.reconcile(action, action_key=action_key, candidate=candidate)


class OaRedirectHandler:
    def __init__(self, dws: object) -> None:
        self.dws = dws

    def _state(self, action: ProposedAction) -> ActionOutcome:
        process_id = action.target.get("process_instance_id")
        task_id = action.target.get("task_id")
        new_owner = action.target.get("to_actioner_id")
        if not all(isinstance(value, str) and value.strip() for value in (process_id, task_id, new_owner)):
            return ActionOutcome("failed", {"reason": "oa_redirect_target_invalid"})
        response = self.dws.read_oa_approval_detail(process_id)
        detail = response.get("result") if isinstance(response, dict) else None
        if not isinstance(detail, dict) or str(detail.get("processInstanceId") or "") != process_id:
            return ActionOutcome("uncertain", {"reason": "oa_detail_unavailable"})
        tasks = detail.get("tasks")
        if not isinstance(tasks, list):
            return ActionOutcome("uncertain", {"reason": "oa_tasks_unavailable"})
        source = next((item for item in tasks if isinstance(item, dict) and str(item.get("taskId")) == task_id), None)
        if source is None:
            return ActionOutcome("uncertain", {"reason": "oa_source_task_missing"})
        owner = source.get("userid", source.get("userId"))
        current_user = self.dws.get_current_user_id()
        if not owner or not current_user:
            return ActionOutcome("uncertain", {"reason": "oa_redirect_owner_unavailable", "readback": source})
        if owner != current_user:
            return ActionOutcome("business_state_changed", {"reason": "oa_redirect_source_owner_conflict", "readback": source})
        status = str(source.get("taskStatus") or "").upper()
        result = str(source.get("taskResult") or "").upper()
        new_task = next((item for item in tasks if isinstance(item, dict)
                         and str(item.get("taskId")) != task_id
                         and item.get("userid", item.get("userId")) == new_owner
                         and str(item.get("taskStatus") or "").upper() == "RUNNING"), None)
        if status == "COMPLETED" and result in {"REDIRECT", "REDIRECT_PROCESS"} and new_task:
            return ActionOutcome("verified", {"source_task": source, "new_task": new_task})
        if status == "RUNNING":
            return ActionOutcome("confirmed_no_effect", {"source_task": source})
        if status == "COMPLETED":
            return ActionOutcome("business_state_changed", {"reason": "oa_redirect_result_conflict", "source_task": source})
        return ActionOutcome("uncertain", {"source_task": source})

    def dispatch(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        remark = action.payload.get("remark", "")
        if not isinstance(remark, str):
            return ActionOutcome("failed", {"reason": "oa_redirect_remark_invalid"})
        before = self._state(action)
        if before.status != "confirmed_no_effect":
            return before
        response = self.dws.redirect_oa_approval_task(
            action.target["task_id"], action.target["to_actioner_id"], remark,
        )
        if not isinstance(response, dict) or response.get("success") is False:
            return ActionOutcome("failed", {"provider_result": response if isinstance(response, dict) else {}})
        after = self._state(action)
        return ActionOutcome("uncertain", after.provider_result) if after.status == "confirmed_no_effect" else after

    def reconcile(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        return self._state(action)


def _provider_identity(response: object, field: str) -> str:
    if not isinstance(response, dict) or response.get("success") is False or response.get("errcode") not in (None, 0):
        return ""
    direct = response.get(field)
    nested = response.get("result")
    value = direct if isinstance(direct, str) else nested.get(field) if isinstance(nested, dict) else None
    return value.strip() if isinstance(value, str) else ""


class DocumentCreateHandler:
    """Verify a newly created document by ID, name, and exact body."""

    def __init__(self, dws: object) -> None:
        self.dws = dws

    def _readback(self, action: ProposedAction, node_id: str, response: dict[str, object] | None = None) -> ActionOutcome:
        name = action.target["name"]
        content = action.payload["content"]
        info = self.dws.doc_info(node_id)
        doc = self.dws.read_doc(node_id)
        info_result = info.get("result", info) if isinstance(info, dict) else None
        doc_result = doc.get("result", doc) if isinstance(doc, dict) else None
        if (isinstance(info_result, dict) and info_result.get("nodeId") == node_id
                and info_result.get("name") == name and isinstance(doc_result, dict)
                and doc_result.get("markdown") == content):
            return ActionOutcome("verified", {"nodeId": node_id, "provider_result": response or {},
                                               "readback": {"nodeId": node_id, "name": name}})
        return ActionOutcome("uncertain", {"reason": "document_readback_mismatch", "nodeId": node_id})

    def dispatch(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        name = action.target.get("name")
        content = action.payload.get("content")
        if set(action.target) != {"name"} or not isinstance(name, str) or not name.strip() or not isinstance(content, str) or not content.strip():
            return ActionOutcome("failed", {"reason": "document_creation_parameters_invalid"})
        response = self.dws.create_markdown_doc(name, content)
        node_id = _provider_identity(response, "nodeId")
        if node_id:
            return self._readback(action, node_id, response)
        if isinstance(response, dict) and response.get("success") is False:
            return ActionOutcome("failed", {"provider_result": response})
        return ActionOutcome("uncertain", {"reason": "document_node_id_missing"})

    def reconcile(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome | None:
        attempt = candidate.get("action_attempt")
        raw = attempt.get("result_json") if isinstance(attempt, dict) else None
        try:
            prior = json.loads(raw) if isinstance(raw, str) else {}
        except json.JSONDecodeError:
            prior = {}
        node_id = prior.get("nodeId") if isinstance(prior, dict) else None
        if isinstance(node_id, str) and node_id:
            return self._readback(action, node_id)
        # A bounded document search cannot prove absence after an unknown create.
        return None


class DocumentCommentHandler:
    def __init__(self, dws: object) -> None:
        self.dws = dws

    def dispatch(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        node_id = action.target.get("node_id")
        content = action.payload.get("content")
        if not isinstance(node_id, str) or not node_id.strip() or not isinstance(content, str) or not content.strip():
            return ActionOutcome("failed", {"reason": "document_comment_parameters_invalid"})
        response = self.dws.create_doc_comment(node_id, content)
        comment_key = _provider_identity(response, "commentKey")
        if comment_key:
            return ActionOutcome("verified", {"commentKey": comment_key, "node_id": node_id, "provider_result": response})
        if isinstance(response, dict) and response.get("success") is False:
            return ActionOutcome("failed", {"provider_result": response})
        return ActionOutcome("uncertain", {"reason": "document_comment_key_missing"})

    def reconcile(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome | None:
        return ActionOutcome("uncertain", {
            "reason": "comment_operation_identity_unavailable",
            "node_id": action.target["node_id"],
            "readback": self.dws.read_doc_comments(action.target["node_id"]),
        })


class OaCommentHandler:
    def __init__(self, dws: object) -> None:
        self.dws = dws

    def dispatch(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        process_id = action.target.get("process_instance_id")
        content = action.payload.get("content")
        if not isinstance(process_id, str) or not process_id.strip() or not isinstance(content, str) or not content.strip():
            return ActionOutcome("failed", {"reason": "oa_comment_parameters_invalid"})
        response = self.dws.comment_oa_approval(process_id, content)
        comment_key = _provider_identity(response, "commentKey") or _provider_identity(response, "commentId")
        if comment_key:
            return ActionOutcome("verified", {"comment_id": comment_key, "process_instance_id": process_id, "provider_result": response})
        if isinstance(response, dict) and response.get("success") is False:
            return ActionOutcome("failed", {"provider_result": response})
        return ActionOutcome("uncertain", {"reason": "oa_comment_id_missing"})

    def reconcile(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome | None:
        process_id = action.target.get("process_instance_id")
        content = action.payload.get("content")
        unresolved = ActionOutcome("uncertain", {
            "reason": "comment_operation_identity_unavailable",
            "process_instance_id": process_id,
        })
        if not isinstance(process_id, str) or not process_id.strip() or not isinstance(content, str) or not content.strip():
            return unresolved
        # The records endpoint contains only operation summaries. The native
        # detail owns complete remarks, authors and millisecond event times.
        response = self.dws.read_oa_approval_detail(process_id)
        detail = response.get("result") if isinstance(response, dict) else None
        if (
            not isinstance(response, dict) or response.get("success") is not True
            or not isinstance(detail, dict) or detail.get("processInstanceId") != process_id
            or response.get("hasMore") is True or detail.get("hasMore") is True
        ):
            return unresolved
        records = detail.get("operationRecords")
        attempt = candidate.get("action_attempt")
        if (
            not isinstance(records, list) or not isinstance(attempt, dict)
            or attempt.get("status") != "uncertain"
            or attempt.get("external_action_key") != action_key
        ):
            return unresolved
        try:
            boundary = datetime.fromisoformat(attempt["created_at"])
        except (KeyError, TypeError, ValueError):
            return unresolved
        if boundary.tzinfo is None:
            return unresolved
        principal = self.dws.get_current_user_id()
        if not isinstance(principal, str) or not principal:
            return unresolved
        matches = []
        observed_at = datetime.now(timezone.utc).timestamp() * 1000
        for record in records:
            if not isinstance(record, dict) or record.get("type") != "ADD_REMARK":
                continue
            author = record.get("userId")
            remark = record.get("remark")
            if isinstance(author, str) and author and author != principal:
                continue
            if isinstance(remark, str) and remark and remark != content:
                continue
            event_time = record.get("date")
            if type(event_time) is not int or event_time <= 0 or event_time > observed_at:
                return unresolved
            if event_time < boundary.timestamp() * 1000:
                continue
            if author != principal or remark != content:
                return unresolved
            matches.append(record)
        if len(matches) != 1:
            return unresolved
        record = matches[0]
        return ActionOutcome("verified", {
            "process_instance_id": process_id,
            "operation_record": {
                "type": record["type"], "user_id": principal, "date": record["date"],
            },
            "content_sha256": sha256(content.encode("utf-8")).hexdigest(),
        })


class MessageEmotionHandler:
    def __init__(self, dws: object) -> None:
        self.dws = dws

    def _emoji_readback(self, action: ProposedAction) -> ActionOutcome:
        response = self.dws.list_message_emotion_replies(action.target["message_id"])
        result = response.get("result") if isinstance(response, dict) else None
        messages = result.get("messages") if isinstance(result, dict) else None
        if not isinstance(messages, list):
            return ActionOutcome("uncertain", {"reason": "reaction_readback_unavailable"})
        current_user = self.dws.get_current_user_id()
        for message in messages:
            if not isinstance(message, dict) or message.get("openMessageId") != action.target["message_id"]:
                continue
            replies = message.get("emotionReplyList")
            if not isinstance(replies, list):
                break
            for reply in replies:
                if (isinstance(reply, dict) and reply.get("emoji") == action.payload.get("emoji")
                        and isinstance(reply.get("replyUsers"), list)
                        and current_user in reply["replyUsers"]):
                    return ActionOutcome("verified", {"readback": reply, "message_id": action.target["message_id"]})
        return ActionOutcome("uncertain", {"reason": "reaction_not_in_readback"})

    def dispatch(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        conversation_id = action.target.get("conversation_id")
        message_id = action.target.get("message_id")
        if not isinstance(conversation_id, str) or not conversation_id.strip() or not isinstance(message_id, str) or not message_id.strip():
            return ActionOutcome("failed", {"reason": "reaction_target_invalid"})
        if action.operation == "add_message_emoji":
            emoji = action.payload.get("emoji")
            if not isinstance(emoji, str) or not emoji.strip():
                return ActionOutcome("failed", {"reason": "emoji_missing"})
            response = self.dws.add_message_emoji(conversation_id, message_id, emoji)
            if isinstance(response, dict) and response.get("success") is False:
                return ActionOutcome("failed", {"provider_result": response})
            return self._emoji_readback(action)
        elif action.operation == "add_message_text_emotion":
            fields = ("text", "emotion_id", "emotion_name", "background_id")
            values = {field: action.payload.get(field) for field in fields}
            if not all(isinstance(value, str) and value.strip() for value in values.values()):
                return ActionOutcome("failed", {"reason": "text_emotion_parameters_invalid"})
            response = self.dws.add_message_text_emotion(
                conversation_id, message_id, **values,
            )
        else:
            return ActionOutcome("failed", {"reason": "unsupported_reaction_operation"})
        reaction_id = _provider_identity(response, "reactionId")
        if reaction_id:
            return ActionOutcome("verified", {"reactionId": reaction_id, "message_id": message_id, "provider_result": response})
        if isinstance(response, dict) and response.get("success") is False:
            return ActionOutcome("failed", {"provider_result": response})
        return ActionOutcome("uncertain", {"reason": "reaction_id_missing"})

    def reconcile(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome | None:
        if action.operation == "add_message_emoji":
            return self._emoji_readback(action)
        return None


class TextEmotionCreateHandler:
    def __init__(self, dws: object) -> None:
        self.dws = dws

    def dispatch(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        text = action.payload.get("text")
        emotion_name = action.payload.get("emotion_name")
        background_id = action.payload.get("background_id", "")
        if action.target != {"resource": "text_emotion_template"} or not isinstance(text, str) or not text.strip() or not isinstance(emotion_name, str) or not emotion_name.strip() or not isinstance(background_id, str):
            return ActionOutcome("failed", {"reason": "text_emotion_creation_parameters_invalid"})
        response = self.dws.create_message_text_emotion(
            text=text, emotion_name=emotion_name, background_id=background_id,
        )
        emotion_id = _provider_identity(response, "emotionId")
        if emotion_id:
            return ActionOutcome("verified", {"emotionId": emotion_id, "provider_result": response})
        if isinstance(response, dict) and response.get("success") is False:
            return ActionOutcome("failed", {"provider_result": response})
        return ActionOutcome("uncertain", {"reason": "emotion_id_missing"})

    def reconcile(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome | None:
        return None


def native_dws_handlers(store: object, dws: object) -> dict[tuple[str, str], object]:
    handlers: dict[tuple[str, str], object] = native_dingtalk_message_handlers(store, dws)
    handlers[("dingtalk-calendar", "respond_calendar_event")] = CalendarResponseHandler(dws)
    oa = OaDecisionHandler(dws)
    handlers[("dingtalk-oa", "approve")] = oa
    handlers[("dingtalk-oa", "reject")] = oa
    handlers[("dingtalk-oa", "revert_task")] = OaRevertHandler(dws)
    handlers[("dingtalk-oa", "redirect_task")] = OaRedirectHandler(dws)
    handlers[("dingtalk-oa", "comment")] = OaCommentHandler(dws)
    handlers[("dingtalk-chat", "add_message_emoji")] = MessageEmotionHandler(dws)
    handlers[("dingtalk-chat", "add_message_text_emotion")] = MessageEmotionHandler(dws)
    handlers[("dingtalk-chat", "create_message_text_emotion")] = TextEmotionCreateHandler(dws)
    handlers[("dingtalk-doc", "create_doc_comment")] = DocumentCommentHandler(dws)
    handlers[("dingtalk-doc", "create_document")] = DocumentCreateHandler(dws)
    return handlers
