"""Final Actions/Tasks physical ABI: stable type codes and declared value slots.

Type names and field meanings remain source-shaped at API boundaries. Only the
schema-versioned integer tags and JSONB arrays are stored. Callers supply the
existing SQLite scalar codec and use jsonb(?) for binding, json(payload_json)
when Python needs the encoded values. This module performs no database I/O.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class RecordTypes:
    names: tuple[str, ...]
    _codes: Mapping[str, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        if len(set(self.names)) != len(self.names):
            raise ValueError("duplicate physical record type")
        object.__setattr__(self, "_codes", MappingProxyType({name: index for index, name in enumerate(self.names, 1)}))

    def code(self, name: str) -> int:
        return self._codes[name]

    def declarations(self, kinds=None) -> tuple[str, str]:
        """One stored tag; the existing SQL name is a read-only projection."""
        names = self.names if kinds is None else tuple(kinds)
        if not names or len(set(names)) != len(names):
            raise ValueError("invalid physical type subset")
        codes = tuple(self.code(name) for name in names)
        return (
            "_kind integer not null check(_kind in (" + ",".join(map(str, codes)) + "))",
            "record_type text generated always as (case _kind " + " ".join(
                "when " + str(code) + " then " + repr(name)
                for name, code in zip(names, codes, strict=True)
            ) + " end) virtual",
        )

    def trigger_sql(self, sql: str) -> str:
        """Bind owned trigger discriminators to their stored type column.

        SQLite can omit a virtual NEW record_type during BEFORE UPDATE when
        only another column changes. Trigger events also name writable columns,
        so the immutable-type event must follow _kind rather than its view.
        Historical text declarations never use this compilation step.
        """
        sql = sql.replace("update of record_type", "update of _kind")
        sql = sql.replace("new.record_type<>old.record_type", "new._kind<>old._kind")
        for alias in ("new", "old"):
            column = alias + ".record_type"
            for name in self.names:
                sql = sql.replace(column + "=" + repr(name),
                                  alias + "._kind=" + str(self.code(name)))
            if column in sql:
                value = "(case " + alias + "._kind " + " ".join(
                    "when " + str(self.code(name)) + " then " + repr(name)
                    for name in self.names
                ) + " end)"
                sql = sql.replace(column, value)
        return sql

    def name(self, code: int) -> str:
        if type(code) is not int or not 1 <= code <= len(self.names):
            raise ValueError("unknown physical record type")
        return self.names[code - 1]


@dataclass(frozen=True, slots=True)
class PayloadLayout:
    fields: tuple[str, ...]

    def __post_init__(self):
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("duplicate physical payload field")

    def path(self, field: str) -> str:
        if field not in self.fields:
            raise KeyError(field)
        return f"$[{self.fields.index(field)}]"

    def pack(self, values: Mapping[str, Any], *, scalar_codec: Callable[[Any], Any]) -> str:
        if set(values) != set(self.fields):
            raise ValueError("payload fields differ from declared layout")
        return json.dumps([scalar_codec(values[name]) for name in self.fields],
                          ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    def unpack(self, values_json: str) -> dict[str, Any]:
        values = json.loads(values_json)
        if not isinstance(values, list) or len(values) != len(self.fields):
            raise ValueError("payload value count differs from declared layout")
        return dict(zip(self.fields, values, strict=True))

    def assignments_sql(self, base: str, expressions: Mapping[str, str]) -> str:
        """Set declared encoded scalar slots in the caller's original row."""
        if not expressions:
            return "jsonb(" + base + ")"
        return "jsonb_set(" + base + "," + ",".join(
            repr(self.path(name)) + ",json(" + expression + ")"
            for name, expression in expressions.items()
        ) + ")"

    def shape_sql(self, document: str = "payload_json") -> str:
        return (f"case when typeof({document})='blob' and json_valid({document},8) "
                f"then coalesce((json_type({document})='array' and "
                f"json_array_length({document})={len(self.fields)}),0) else 0 end")


# Actual complete candidate declarations: 61 Action kinds and 20 Task kinds,
# including currently empty but live-supported families. Codes are one-based;
# reorder/removal requires an explicit schema migration, never inference.

SOURCE_ACTION_RECORD_TYPES = RecordTypes((
    'review_candidates',
    'candidate_reviews',
    'candidate_selections',
    'candidate_supplements',
    'candidate_executions',
    'candidate_action_attempts',
    'external_action_results',
    'agent_effect_intents',
    'agent_execution_receipts',
    'outbound_postfixes',
    'outbound_postfix_receipts',
    'native_reply_dispatches',
    'sent_replies',
    'sent_reply_observers',
    'reply_attempts',
    'memory_write_events',
    'email_action_attempts',
    'email_action_plans',
    'email_actions',
    'email_unsubscribe_cancellations',
    'email_unsubscribe_claims',
    'email_unsubscribe_continuations',
    'email_unsubscribe_effects',
    'email_unsubscribe_receipts',
    'email_unsubscribe_steps',
    'email_direct_retry_requests',
    'email_feedback_requests',
    'email_reply_dispatch_claims',
    'email_reply_receipts',
    'work_updates',
    'follow_up_drafts',
    'follow_up_send_attempts',
    'business_task_follow_ups',
    'business_task_follow_up_send_attempts',
    'task_todo_sync_outbox',
    'business_task_todo_sync_outbox',
    'work_todo_dingtalk_links',
    'business_task_dingtalk_links',
    'business_task_events',
    'meeting_alignment_delivery_claims',
    'meeting_memory_write_events',
    'task_memory_write_events',
    'wechat_deliveries',
    'managed_skill_export_receipts',
    'runtime_skill_load_receipts',
    'errors',
    'feedback_events',
    'feedback_processing_transitions',
    'feedback_iteration_decision_items',
    'business_attention_events',
    'business_attention_proposal_tasks',
    'business_attention_tasks',
    'business_project_evidence',
    'business_task_anchor_links',
    'business_task_date_evidence',
    'business_task_evidence',
    'business_task_relations',
    'business_task_signals',
    'business_work_cluster_tasks',
    'email_historical_classification_history',
    'email_historical_operations',
))

ACTION_RECORD_TYPES = RecordTypes((*SOURCE_ACTION_RECORD_TYPES.names, 'candidate_execution_alias'))


TASK_RECORD_TYPES = RecordTypes((
    'reply_tasks',
    'reply_task_inputs',
    'work_summary_inputs',
    'meeting_alignment_jobs',
    'okr_review_requests',
    'weekly_okr_analysis_jobs',
    'workbench_tasks',
    'workbench_turns',
    'business_object_tasks',
    'email_agent_classification_tasks',
    'email_historical_candidates',
    'feedback_iteration_decisions',
    'feedback_processing_batches',
    'feedback_processing_items',
    'feedback_processing_rounds',
    'oa_notification_events',
    'okr_review_items',
    'wechat_memory_import_jobs',
    'workbench_artifacts',
    'workbench_attachments',
))
