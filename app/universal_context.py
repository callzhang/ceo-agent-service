import hashlib
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.dws_client import DwsClient
from app.oa_approval import extract_oa_url


@dataclass(frozen=True)
class UniversalContextMessage:
    sender_name: str
    open_message_id: str
    content: str
    sender_open_dingtalk_id: str | None = None
    sender_user_id: str | None = None
    message_type: str | None = None
    create_time: str = ""
    mentioned_user_ids: tuple[str, ...] = ()
    quoted_message_id: str | None = None
    quoted_content: str | None = None
    raw_payload_json: str = "{}"

    def __post_init__(self) -> None:
        if not isinstance(self.mentioned_user_ids, tuple):
            raise TypeError("mentioned_user_ids must be a tuple")
        try:
            raw_payload = json.loads(self.raw_payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("raw_payload_json must contain valid JSON") from exc
        if not isinstance(raw_payload, dict):
            raise ValueError("raw_payload_json must contain a JSON object")
        object.__setattr__(
            self,
            "raw_payload_json",
            json.dumps(
                raw_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )


@dataclass(frozen=True)
class UniversalMaterialReference:
    kind: str
    reference: str
    source_message_id: str
    source_sender: str
    source_time: str
    read_command: str = ""


@dataclass(frozen=True)
class UniversalTaskContext:
    task_id: int
    conversation_id: str
    conversation_title: str
    single_chat: bool
    trigger_message_id: str
    trigger_sender: str
    trigger_text: str
    context_messages: tuple[UniversalContextMessage, ...]
    required_dependencies: tuple[str, ...]
    force_new_decision: bool
    dry_run: bool
    execution_generation: str = "initial"
    trusted_oa_process_instance_id: str = ""
    trusted_oa_task_id: str = ""
    trusted_mail_mailbox: str = ""
    trusted_mail_message_id: str = ""
    trusted_mail_subject: str = ""
    trusted_calendar_event_id: str = ""
    trusted_calendar_response_status: str = ""
    trusted_calendar_organizer: str = ""
    trigger_create_time: str = ""
    trusted_document_url: str = ""
    trusted_task_context: str = ""
    image_paths: tuple[str, ...] = ()
    image_sha256s: tuple[str, ...] = ()
    material_references: tuple[UniversalMaterialReference, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.execution_generation, str)
            or not self.execution_generation.strip()
        ):
            raise ValueError("execution_generation must be non-empty")
        if not isinstance(self.image_paths, tuple) or any(
            not isinstance(path, str) or not path.strip() for path in self.image_paths
        ):
            raise ValueError("image_paths must be a tuple of non-empty strings")
        if not isinstance(self.image_sha256s, tuple) or any(
            not isinstance(value, str)
            or len(value) != 64
            or not set(value) <= set("0123456789abcdef")
            for value in self.image_sha256s
        ):
            raise ValueError("image_sha256s must be a tuple of SHA-256 hex strings")
        if len(self.image_paths) != len(self.image_sha256s):
            raise ValueError("image_paths and image_sha256s must have equal length")
        if not isinstance(self.material_references, tuple) or any(
            not isinstance(reference, UniversalMaterialReference)
            for reference in self.material_references
        ):
            raise TypeError(
                "material_references must be a tuple[UniversalMaterialReference, ...]"
            )

    def render_for_agent(self) -> str:
        message_lines = [
            self._render_message_for_agent(message)
            for message in self.context_messages
        ]
        if not message_lines:
            message_lines.append("- No context messages.")

        return "\n".join(
            [
                f"Task ID: {self.task_id}",
                f"Conversation ID: {self.conversation_id}",
                f"Conversation title: {self.conversation_title}",
                f"Single chat: {str(self.single_chat).lower()}",
                f"Trigger message ID: {self.trigger_message_id}",
                f"Trigger sender: {self.trigger_sender}",
                f"Trigger text: {self.trigger_text}",
                f"Trigger create time: {self.trigger_create_time or 'unknown'}",
                "Trusted OA process instance ID: "
                + (self.trusted_oa_process_instance_id or "none"),
                "Trusted OA task ID: " + (self.trusted_oa_task_id or "none"),
                "Trusted mail target: "
                + (
                    f"{self.trusted_mail_mailbox} / {self.trusted_mail_message_id} / "
                    f"{self.trusted_mail_subject}"
                    if self.trusted_mail_message_id
                    else "none"
                ),
                "Trusted calendar target: "
                + (
                    f"{self.trusted_calendar_event_id} / "
                    f"{self.trusted_calendar_response_status or 'unknown'} / "
                    f"{self.trusted_calendar_organizer or 'unknown'}"
                    if self.trusted_calendar_event_id
                    else "none"
                ),
                "Trusted document URL: " + (self.trusted_document_url or "none"),
                "Trusted task details: "
                + (self.trusted_task_context or "none"),
                f"Attached image count: {len(self.image_paths)}",
                "Attached image SHA-256: "
                + (", ".join(self.image_sha256s) if self.image_sha256s else "none"),
                f"Required dependencies: {', '.join(self.required_dependencies)}",
                f"Execution generation: {self.execution_generation}",
                f"Force new decision: {str(self.force_new_decision).lower()}",
                f"Dry run: {str(self.dry_run).lower()}",
                "Material references:",
                *self._render_material_references_for_agent(),
                "Recent messages:",
                *message_lines,
            ]
        )

    def _render_material_references_for_agent(self) -> list[str]:
        if not self.material_references:
            return ["- none"]
        lines: list[str] = [
            "- If the decision depends on a material body, use the read_command or an equivalent read-only CLI/tool before concluding the material is unreadable.",
            "- Do not say a material is inaccessible until its supplied read path has been tried or the tool reports a concrete permission/login error.",
        ]
        for index, material in enumerate(self.material_references, start=1):
            command = material.read_command or "none"
            lines.append(
                f"- [{index}] kind={material.kind}; reference={material.reference}; "
                f"source_message_id={material.source_message_id}; "
                f"source_sender={material.source_sender}; source_time={material.source_time}; "
                f"read_command={command}"
            )
        return lines

    @staticmethod
    def _render_message_for_agent(message: UniversalContextMessage) -> str:
        identity_parts = [message.open_message_id]
        if message.sender_user_id:
            identity_parts.append(f"sender_user_id={message.sender_user_id}")
        return (
            f"- {message.sender_name} ({', '.join(identity_parts)}): "
            f"{message.content}"
        )


def canonical_universal_context_json(context: UniversalTaskContext) -> str:
    if not isinstance(context, UniversalTaskContext):
        raise TypeError("context must be UniversalTaskContext")
    if not isinstance(context.task_id, int) or isinstance(context.task_id, bool):
        raise TypeError("task_id must be an int")
    for field_name in (
        "conversation_id",
        "conversation_title",
        "trigger_message_id",
        "trigger_sender",
        "trigger_text",
        "execution_generation",
        "trusted_oa_process_instance_id",
        "trusted_oa_task_id",
        "trusted_mail_mailbox",
        "trusted_mail_message_id",
        "trusted_mail_subject",
        "trusted_calendar_event_id",
        "trusted_calendar_response_status",
        "trusted_calendar_organizer",
        "trigger_create_time",
        "trusted_document_url",
        "trusted_task_context",
    ):
        if not isinstance(getattr(context, field_name), str):
            raise TypeError(f"{field_name} must be a str")
    for field_name in ("single_chat", "force_new_decision", "dry_run"):
        if not isinstance(getattr(context, field_name), bool):
            raise TypeError(f"{field_name} must be a bool")
    if not isinstance(context.context_messages, tuple):
        raise TypeError("context_messages must be a tuple")
    if not isinstance(context.required_dependencies, tuple):
        raise TypeError("required_dependencies must be a tuple")

    context_messages: list[dict[str, object]] = []
    for message in context.context_messages:
        if not isinstance(message, UniversalContextMessage):
            raise TypeError("context_messages items must be UniversalContextMessage")
        for field_name in (
            "sender_name",
            "open_message_id",
            "content",
            "create_time",
            "raw_payload_json",
        ):
            if not isinstance(getattr(message, field_name), str):
                raise TypeError(f"context message {field_name} must be a str")
        for field_name in (
            "sender_open_dingtalk_id",
            "sender_user_id",
            "message_type",
            "quoted_message_id",
            "quoted_content",
        ):
            value = getattr(message, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"context message {field_name} must be str or None")
        if not isinstance(message.mentioned_user_ids, tuple) or any(
            not isinstance(user_id, str) for user_id in message.mentioned_user_ids
        ):
            raise TypeError("context message mentioned_user_ids must be tuple[str, ...]")
        raw_payload = json.loads(message.raw_payload_json)
        if not isinstance(raw_payload, dict):
            raise TypeError("context message raw_payload_json must contain an object")
        context_messages.append(
            {
                "sender_name": message.sender_name,
                "sender_open_dingtalk_id": message.sender_open_dingtalk_id,
                "sender_user_id": message.sender_user_id,
                "open_message_id": message.open_message_id,
                "message_type": message.message_type,
                "create_time": message.create_time,
                "content": message.content,
                "mentioned_user_ids": list(message.mentioned_user_ids),
                "quoted_message_id": message.quoted_message_id,
                "quoted_content": message.quoted_content,
                "raw_payload": raw_payload,
            }
        )

    required_dependencies: list[str] = []
    for dependency in context.required_dependencies:
        if not isinstance(dependency, str):
            raise TypeError("required_dependencies items must be str")
        required_dependencies.append(dependency)

    material_references: list[dict[str, str]] = []
    for reference in context.material_references:
        if not isinstance(reference, UniversalMaterialReference):
            raise TypeError("material_references items must be UniversalMaterialReference")
        for field_name in (
            "kind",
            "reference",
            "source_message_id",
            "source_sender",
            "source_time",
            "read_command",
        ):
            if not isinstance(getattr(reference, field_name), str):
                raise TypeError(f"material reference {field_name} must be a str")
        material_references.append(
            {
                "kind": reference.kind,
                "reference": reference.reference,
                "source_message_id": reference.source_message_id,
                "source_sender": reference.source_sender,
                "source_time": reference.source_time,
                "read_command": reference.read_command,
            }
        )

    return json.dumps(
        {
            "task_id": context.task_id,
            "conversation_id": context.conversation_id,
            "conversation_title": context.conversation_title,
            "single_chat": context.single_chat,
            "trigger_message_id": context.trigger_message_id,
            "trigger_sender": context.trigger_sender,
            "trigger_text": context.trigger_text,
            "context_messages": context_messages,
            "required_dependencies": required_dependencies,
            "force_new_decision": context.force_new_decision,
            "dry_run": context.dry_run,
            "execution_generation": context.execution_generation,
            "trusted_oa_process_instance_id": context.trusted_oa_process_instance_id,
            "trusted_oa_task_id": context.trusted_oa_task_id,
            "trusted_mail_mailbox": context.trusted_mail_mailbox,
            "trusted_mail_message_id": context.trusted_mail_message_id,
            "trusted_mail_subject": context.trusted_mail_subject,
            "trusted_calendar_event_id": context.trusted_calendar_event_id,
            "trusted_calendar_response_status": context.trusted_calendar_response_status,
            "trusted_calendar_organizer": context.trusted_calendar_organizer,
            "trigger_create_time": context.trigger_create_time,
            "trusted_document_url": context.trusted_document_url,
            "trusted_task_context": context.trusted_task_context,
            "image_sha256s": list(context.image_sha256s),
            "material_references": material_references,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def universal_context_sha256(context: UniversalTaskContext) -> str:
    canonical = canonical_universal_context_json(context)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_universal_context(
    *,
    conversation: DingTalkConversation,
    trigger: DingTalkMessage,
    context_messages: list[DingTalkMessage],
    task_id: int,
    force_new_decision: bool,
    dry_run: bool,
    execution_generation: str = "initial",
    reply_task_oa_url: str = "",
    trusted_calendar_event_id_override: str = "",
    trusted_calendar_response_status_override: str = "",
    trusted_calendar_organizer_override: str = "",
    trusted_task_context: str = "",
    image_paths: tuple[str, ...] = (),
    image_sha256s: tuple[str, ...] = (),
    material_references: tuple[UniversalMaterialReference, ...] = (),
) -> UniversalTaskContext:
    trigger_snapshot = _snapshot_message(trigger)
    messages: list[UniversalContextMessage] = []
    trigger_added = False
    for message in context_messages:
        if message.open_message_id == trigger.open_message_id:
            if not trigger_added:
                messages.append(trigger_snapshot)
                trigger_added = True
            continue
        messages.append(_snapshot_message(message))

    if not trigger_added:
        messages.append(trigger_snapshot)

    trusted_process_id, trusted_task_id = _trusted_oa_target(
        trigger,
        reply_task_oa_url=reply_task_oa_url,
    )
    trusted_mailbox, trusted_mail_message_id, trusted_mail_subject = (
        _trusted_mail_target(trigger)
    )
    (
        trusted_calendar_event_id,
        trusted_calendar_response_status,
        trusted_calendar_organizer,
    ) = _trusted_calendar_target(trigger)
    trusted_calendar_event_id = (
        trusted_calendar_event_id_override.strip() or trusted_calendar_event_id
    )
    trusted_calendar_response_status = (
        trusted_calendar_response_status_override.strip()
        or trusted_calendar_response_status
    )
    trusted_calendar_organizer = (
        trusted_calendar_organizer_override.strip() or trusted_calendar_organizer
    )
    trusted_document_url = _trusted_document_url(trigger)
    return UniversalTaskContext(
        task_id=task_id,
        conversation_id=conversation.open_conversation_id,
        conversation_title=conversation.title,
        single_chat=conversation.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        context_messages=tuple(messages),
        required_dependencies=("dws",),
        force_new_decision=force_new_decision,
        dry_run=dry_run,
        execution_generation=execution_generation,
        trusted_oa_process_instance_id=trusted_process_id,
        trusted_oa_task_id=trusted_task_id,
        trusted_mail_mailbox=trusted_mailbox,
        trusted_mail_message_id=trusted_mail_message_id,
        trusted_mail_subject=trusted_mail_subject,
        trusted_calendar_event_id=trusted_calendar_event_id,
        trusted_calendar_response_status=trusted_calendar_response_status,
        trusted_calendar_organizer=trusted_calendar_organizer,
        trigger_create_time=trigger.create_time,
        trusted_document_url=trusted_document_url,
        trusted_task_context=trusted_task_context,
        image_paths=image_paths,
        image_sha256s=image_sha256s,
        material_references=material_references,
    )


def _trusted_document_url(trigger: DingTalkMessage) -> str:
    candidates: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                visit(nested)
            return
        if isinstance(value, list):
            for nested in value:
                visit(nested)
            return
        if not isinstance(value, str):
            return
        for token in (value, *value.split()):
            parsed = urlparse(token.strip("()[]<>.,;，。；"))
            if (
                parsed.scheme == "https"
                and parsed.hostname in {
                    "alidocs.dingtalk.com",
                    "docs.dingtalk.com",
                }
                and parsed.path.startswith("/i/nodes/")
            ):
                candidates.add(parsed._replace(query="", fragment="").geturl())

    visit(trigger.content)
    visit(trigger.raw_payload)
    if len(candidates) != 1:
        return ""
    return next(iter(candidates))


def _trusted_mail_target(trigger: DingTalkMessage) -> tuple[str, str, str]:
    candidates: set[tuple[str, str, str]] = set()

    def first_scalar(value: dict[str, Any], *keys: str) -> str:
        for key in keys:
            item = value.get(key)
            normalized = _trusted_scalar(item)
            if normalized:
                return normalized
        return ""

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            mailbox = first_scalar(value, "mailbox", "from", "fromAddress")
            message_id = first_scalar(value, "messageId", "message_id", "mailId")
            subject = first_scalar(value, "subject", "title")
            if mailbox and message_id and subject:
                candidates.add((mailbox, message_id, subject))
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(trigger.raw_payload)
    if len(candidates) != 1:
        return "", "", ""
    return next(iter(candidates))


def _trusted_calendar_target(trigger: DingTalkMessage) -> tuple[str, str, str]:
    candidates: dict[str, tuple[set[str], set[str]]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            event_id = ""
            for key in (
                "eventId",
                "eventID",
                "calendarEventId",
                "scheduleId",
                "event_id",
            ):
                event_id = _trusted_scalar(value.get(key))
                if event_id:
                    break
            if event_id:
                statuses, organizers = candidates.setdefault(
                    event_id, (set(), set())
                )
                for key in (
                    "selfResponseStatus",
                    "self_response_status",
                    "selfStatus",
                ):
                    status = _trusted_scalar(value.get(key))
                    if status:
                        statuses.add(status)
                organizer = _trusted_calendar_person(value.get("organizer"))
                if organizer:
                    organizers.add(organizer)
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(trigger.raw_payload)
    event_id_from_text = DwsClient._calendar_event_id_from_message(trigger)
    if event_id_from_text:
        candidates.setdefault(event_id_from_text, (set(), set()))
    if len(candidates) != 1:
        return "", "", ""
    event_id, (statuses, organizers) = next(iter(candidates.items()))
    return (
        event_id,
        next(iter(statuses)) if len(statuses) == 1 else "",
        next(iter(organizers)) if len(organizers) == 1 else "",
    )


def _trusted_calendar_person(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("displayName", "name", "nickName"):
            normalized = _trusted_scalar(value.get(key))
            if normalized:
                return normalized
        return ""
    return _trusted_scalar(value)


def _trusted_scalar(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return str(value).strip()


def _trusted_oa_target(
    trigger: DingTalkMessage,
    *,
    reply_task_oa_url: str,
) -> tuple[str, str]:
    process_ids: set[str] = set()
    task_ids: set[str] = set()

    def add_url(value: str) -> None:
        oa_url = extract_oa_url(value)
        if not oa_url:
            return
        query = parse_qs(urlparse(oa_url).query)
        for key in ("procInstId", "processInstanceId", "process_instance_id"):
            process_ids.update(item.strip() for item in query.get(key, []) if item.strip())
        for key in ("taskId", "task_id"):
            task_ids.update(item.strip() for item in query.get(key, []) if item.strip())

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in {"procInstId", "processInstanceId", "process_instance_id"}:
                    normalized = _trusted_oa_id(nested)
                    if normalized:
                        process_ids.add(normalized)
                elif key in {"taskId", "task_id"}:
                    normalized = _trusted_oa_id(nested)
                    if normalized:
                        task_ids.add(normalized)
                visit(nested)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            add_url(value)

    add_url(trigger.content)
    add_url(reply_task_oa_url)
    visit(trigger.raw_payload)
    if len(process_ids) != 1 or len(task_ids) != 1:
        return "", ""
    return next(iter(process_ids)), next(iter(task_ids))


def _trusted_oa_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return str(value).strip()


def _snapshot_message(message: DingTalkMessage) -> UniversalContextMessage:
    return UniversalContextMessage(
        sender_name=message.sender_name,
        open_message_id=message.open_message_id,
        content=message.content,
        sender_open_dingtalk_id=message.sender_open_dingtalk_id,
        sender_user_id=message.sender_user_id,
        message_type=message.message_type,
        create_time=message.create_time,
        mentioned_user_ids=tuple(message.mentioned_user_ids),
        quoted_message_id=message.quoted_message_id,
        quoted_content=message.quoted_content,
        raw_payload_json=json.dumps(
            message.raw_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
