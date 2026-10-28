"""Common purpose output, with action contracts selected before inference."""

from functools import lru_cache
from hashlib import sha256
import json
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Empty(_Strict):
    pass


class OATask(_Strict):
    process_instance_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)


class OAReturnTarget(OATask):
    target_activity_id: str = Field(min_length=1)


class OAAppendTarget(OATask):
    appender_user_id: str = Field(min_length=1)


class AppendPayload(_Strict):
    type: Literal['before', 'parallel']


class OAProcess(_Strict):
    process_instance_id: str = Field(min_length=1)


class OAMaterialTarget(OAProcess):
    file_id: str = Field(min_length=1)


class Remark(_Strict):
    remark: str = Field(min_length=1)


class ReturnPayload(Remark):
    revert_action: Literal['REVERT_FOR_APPROVAL', 'REVERT_FOR_RESUBMIT']


class Content(_Strict):
    content: str = Field(min_length=1)


class MessageTarget(_Strict):
    conversation_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)


class Emoji(_Strict):
    emoji: str = Field(min_length=1)


def action_contracts(purpose: str):
    from app.agent_contracts import ConsumerProposal

    common = {
        'plan': (Empty, ConsumerProposal, '', ''),
        'no_action': (Empty, Empty, '', ''),
        'retry': (Empty, Empty, '', ''),
        # These two use the existing typed assessment/decision metadata.
        'failed': (Empty, Empty, '', ''),
        'needs_human': (Empty, Empty, '', ''),
    }
    if purpose == 'oa':
        return {**common,
            'approve': (OATask, Remark, 'dingtalk-oa', 'approve'),
            'reject': (OATask, Remark, 'dingtalk-oa', 'reject'),
            'return': (OAReturnTarget, ReturnPayload, 'dingtalk-oa', 'revert_task'),
            'add_approver': (OAAppendTarget, AppendPayload, 'dingtalk-oa', 'append_task'),
            'comment': (OAProcess, Content, 'dingtalk-oa', 'comment'),
            'request_access': (OAMaterialTarget, Content, 'dingtalk-oa', 'comment'),
        }
    if purpose == 'general':
        return {**common,
            'reply': (MessageTarget, Content, 'dingtalk-chat', 'reply_to_message'),
            'react': (MessageTarget, Emoji, 'dingtalk-chat', 'add_message_emoji'),
            'request_access': (MessageTarget, Content, 'dingtalk-chat', 'reply_to_message'),
        }
    raise ValueError(f'unknown Consumer purpose: {purpose}')


def _purpose_schema(schema, model):
    definitions = action_contracts(model.purpose)
    schema['properties']['action']['enum'] = list(definitions)
    branches = []
    for name, (target, payload, _, _) in definitions.items():
        fields = {'action': {'const': name}}
        for field, definition in [('target', target), ('payload', payload)]:
            shape = definition.model_json_schema(ref_template='#/$defs/{model}')
            schema.setdefault('$defs', {}).update(shape.pop('$defs', {}))
            fields[field] = shape
        if name == 'retry':
            fields['evidence_refs'] = {'minItems': 1}
        branches.append({'properties': fields, 'required': ['action', 'target', 'payload']})
    schema['allOf'] = [{'oneOf': branches}]


class ConsumerPurposeOutput(_Strict):
    purpose: ClassVar[str] = 'general'

    action: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    target: dict[str, JsonValue]
    payload: dict[str, JsonValue]
    evidence_refs: list[str] = Field(
        description=(
            "Required on every purpose output. List the source or artifact "
            "references supporting this action when available; use an empty "
            "list when none apply. For retry, include at least one reference "
            "that verifies the access request."
        )
    )

    @classmethod
    def model_json_schema(cls, **kwargs):
        schema = super().model_json_schema(**kwargs)
        _purpose_schema(schema, cls)
        return schema

    @model_validator(mode='after')
    def validate_action(self):
        definition = action_contracts(self.purpose).get(self.action)
        if definition is None:
            raise ValueError(f'action {self.action!r} is not available for {self.purpose}')
        definition[0].model_validate(self.target)
        definition[1].model_validate(self.payload)
        if any(not ref.strip() for ref in self.evidence_refs):
            raise ValueError('evidence references must be nonempty')
        if self.action == 'retry' and not self.evidence_refs:
            raise ValueError('retry requires verified request evidence')
        return self

    def to_proposal(self):
        from app.agent_contracts import ConsumerProposal

        if self.action == 'plan':
            return ConsumerProposal.model_validate(self.payload)
        _, _, capability, operation = action_contracts(self.purpose)[self.action]
        if not capability:
            return None
        identity = sha256(json.dumps(
            [capability, operation, self.action, self.target],
            ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        ).encode()).hexdigest()[:24]
        return ConsumerProposal.model_validate({
            'objective': self.reason,
            'actions': [{'description': self.reason, 'action_identity': identity,
                         'capability': capability, 'operation': operation,
                         'target': self.target, 'payload': self.payload}],
            'sourced_facts': [], 'authored_judgment': self.reason,
        })


@lru_cache
def purpose_output_model(purpose: str):
    action_contracts(purpose)
    return type(f'{purpose.title()}PurposeOutput', (ConsumerPurposeOutput,), {'purpose': purpose})


def purpose_for_context(context, task=None) -> str:
    if ((task is not None and task.oa_url)
            or 'dingtalk-oa-approval' in context.skill_names
            or context.required_proposal_action.get('capability') == 'dingtalk-oa'):
        return 'oa'
    return 'general'


def code_mode_handoff_for_task(task) -> bool:
    """WeChat turns read a voice and transcribe it inside one CodeMode call."""
    return task is not None and task.channel == 'wechat'
