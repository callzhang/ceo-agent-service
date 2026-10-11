"""Separate native message resource identities from download capabilities."""
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MessageResourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    conversation_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    resource_id_type: Literal["mediaId", "fileId"]
    resource_type: str = Field(min_length=1)


class MessageResourceDigest(MessageResourceReference):
    size_bytes: int = Field(gt=0)
    sha256: str = Field(min_length=64, max_length=64)


class _NativeResource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    resourceId: str = Field(min_length=1)
    resourceIdType: Literal["mediaId", "fileId"]
    resourceType: str = Field(min_length=1)
    url: str | None = None
    expireTimeMillis: int | None = None
    downloadCommand: str | None = None


def message_source_facts(context):
    raw = deepcopy(context.trigger_raw_payload)
    references = []
    if getattr(context, "channel", "") != "dingtalk" or not isinstance(raw, dict):
        return raw, references
    messages = [raw]
    if isinstance(raw.get("quotedMessage"), dict):
        messages.append(raw["quotedMessage"])
    for message in messages:
        if "resources" not in message:
            continue
        resources = message["resources"]
        if not isinstance(resources, list):
            raise ValueError("native message resources must be a list")
        if resources and (message.get("openConversationId") != context.conversation_id
                          or not isinstance(message.get("openMessageId"), str)
                          or not message["openMessageId"]):
            raise ValueError("message resource identity unavailable")
        if resources and message is raw and message["openMessageId"] != context.trigger_message_id:
            raise ValueError("message resource trigger identity mismatch")
        facts = []
        for value in resources:
            resource = _NativeResource.model_validate(value)
            reference = MessageResourceReference(
                conversation_id=message["openConversationId"],
                message_id=message["openMessageId"],
                resource_id=resource.resourceId,
                resource_id_type=resource.resourceIdType,
                resource_type=resource.resourceType,
            )
            references.append(reference)
            facts.append({"resourceId": resource.resourceId,
                          "resourceIdType": resource.resourceIdType,
                          "resourceType": resource.resourceType})
        message["resources"] = facts
    return raw, references
