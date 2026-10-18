"""Synthetic retained Console facts; no database or original SourceDocument read."""

from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.business_source_reader import (
    AvailableSource, BusinessTaskSignalFact, RequestSourceReader,
    SourceUnavailableReason, SourceVersionBinding,
)
from app.task_semantic_models import BusinessTaskEvent, BusinessTaskSignal


TITLE = '旧「标题」🙂'
TIME = '2026-10-17T01:02:03+00:00'
REF = f'console:task:43:ignore:{TIME}'
BODY = f'操作员在控制台忽略了候选任务「{TITLE}」'


def facts(*, action='ignore', title=TITLE, before_time=TIME):
    item = BusinessTaskSignal(
        id=44, source_type='console', source_ref=REF,
        source_time='2026-10-18T01:00:00+00:00', conversation_id='',
        author_user_id='', author_name='操作员', author_kind='human',
        evidence_text=BODY, context_json=json.dumps({'action': action}),
        dedupe_key=REF, created_at='2026-10-18T01:00:01+00:00',
    )
    binding = SourceVersionBinding.from_signal(item)
    fact = BusinessTaskSignalFact.from_signal(item, binding=binding)
    event = BusinessTaskEvent(
        id=45, task_id=43, event_type='status_changed', signal_id=44,
        before_json=json.dumps({'title': title, 'updated_at': before_time}),
        after_json=json.dumps({'title': '当前标题', 'updated_at': 'later'}),
        reason='arbitrary repair text', created_at='2026-10-18T01:00:02+00:00',
    )
    return binding, fact, event


class Lookup:
    def __init__(self, pairs):
        self.pairs = pairs
        self.calls = []

    def __call__(self, source_ref):
        self.calls.append(source_ref)
        return self.pairs


def test_original_body_exact_and_request_checks():
    binding, fact, event = facts()
    assert binding.native_provenance_json is None
    assert binding.provider_locator_json is None
    assert not hasattr(fact, 'evidence_text')
    lookup = Lookup([(fact, event)])
    reader = RequestSourceReader(console_lookup=lookup)
    result = reader.resolve(binding, source_ref=REF, quote=TITLE)
    assert isinstance(result, AvailableSource)
    assert result.body == BODY
    assert reader.resolve(binding, quote='不存在').reason is SourceUnavailableReason.QUOTE_MISSING
    assert reader.resolve(binding, source_ref='wrong').reason is SourceUnavailableReason.SOURCE_REF_MISMATCH
    assert reader.resolve(replace(binding, body_sha256='0' * 64)).reason is SourceUnavailableReason.BINDING_CONFLICT
    assert lookup.calls == [REF]
    assert not hasattr(fact, "source_document_id")


@pytest.mark.parametrize('damage', [
    lambda f, e: (replace(f, context_json='{}'), e),
    lambda f, e: (replace(f, context_json='{'), e),
    lambda f, e: (replace(f, source=replace(f.source, author_name='other')), e),
    lambda f, e: (replace(f, source=replace(f.source, source_time='other')), e),
    lambda f, e: (replace(f, source=replace(f.source, source_ref='other')), e),
    lambda f, e: (replace(f, source=replace(f.source, body_utf8_bytes=0)), e),
    lambda f, e: (f, e.model_copy(update={'signal_id': 7})),
    lambda f, e: (f, e.model_copy(update={'task_id': 7})),
    lambda f, e: (f, e.model_copy(update={'event_type': 'details_changed'})),
    lambda f, e: (f, e.model_copy(update={'before_json': '{}'})),
    lambda f, e: (f, e.model_copy(update={'before_json': '{'})),
    lambda f, e: (f, e.model_copy(update={'before_json': json.dumps({'title': TITLE, 'updated_at': 'other'})})),
])
def test_wrong_link_binding_or_shape_unavailable(damage):
    binding, fact, event = facts()
    result = RequestSourceReader(console_lookup=Lookup([damage(fact, event)])).resolve(binding)
    assert result.reason in {
        SourceUnavailableReason.SOURCE_INCOMPLETE,
        SourceUnavailableReason.SOURCE_MISSING,
        SourceUnavailableReason.VERSION_MISMATCH,
    }


def test_unrelated_events_are_not_original_and_ambiguity_refused():
    binding, fact, event = facts()
    unrelated = event.model_copy(update={'id': 46, 'signal_id': 999})
    reader = RequestSourceReader(console_lookup=Lookup([(fact, unrelated), (fact, event)]))
    assert reader.resolve(binding).body == BODY
    assert RequestSourceReader(console_lookup=Lookup([])).resolve(binding).reason is SourceUnavailableReason.SOURCE_MISSING
    second = event.model_copy(update={'id': 47})
    assert RequestSourceReader(console_lookup=Lookup([(fact, event), (fact, second)])).resolve(binding).reason is SourceUnavailableReason.SOURCE_INCOMPLETE


def test_version_hash_bytes_key_and_source_kind_boundaries():
    binding, fact, event = facts()
    for changed in (replace(binding, body_sha256='0' * 64),
                    replace(binding, body_utf8_bytes=1),
                    replace(binding, identity_key='0' * 64)):
        assert RequestSourceReader(console_lookup=Lookup([(fact, event)])).resolve(changed).reason is SourceUnavailableReason.VERSION_MISMATCH
    for source_type in ('service_data_repair', 'other'):
        assert RequestSourceReader(console_lookup=Lookup([(fact, event)])).resolve(replace(binding, source_type=source_type)).reason is SourceUnavailableReason.MISSING_LOCATOR
    assert RequestSourceReader(console_lookup=Lookup([(fact, event)])).resolve(replace(binding, source_type='memory_provenance')).reason is SourceUnavailableReason.REFERENCE_ONLY


def test_explicit_locators_do_not_use_local_console_lookup():
    binding, fact, event = facts()
    lookup = Lookup([(fact, event)])
    provider = replace(binding, provider_locator_json='{"kind":"unsupported"}')
    assert RequestSourceReader(console_lookup=lookup).resolve(provider).reason is SourceUnavailableReason.READER_UNAVAILABLE
    native = replace(binding, native_provenance_json='{"source_type":"unsupported"}')
    with patch('app.business_source_reader.native_trajectory.read_native_source_body', return_value=BODY) as native_read:
        assert isinstance(RequestSourceReader(console_lookup=lookup).resolve(native), AvailableSource)
        native_read.assert_called_once()
    assert lookup.calls == []


def test_lookup_programmer_error_visible_and_no_backend_unavailable():
    binding, _, _ = facts()
    assert RequestSourceReader().resolve(binding).reason is SourceUnavailableReason.MISSING_LOCATOR
    def broken(_source_ref):
        raise RuntimeError('programmer bug')
    with pytest.raises(RuntimeError, match='programmer bug'):
        RequestSourceReader(console_lookup=broken).resolve(binding)


def test_malformed_console_reference_unavailable_at_reader_boundary():
    binding, _, _ = facts()
    lookup = Lookup([])
    oversized = replace(binding, source_ref=f"console:task:{'9' * 5000}:ignore:{TIME}")
    assert RequestSourceReader(console_lookup=lookup).resolve(oversized).reason is SourceUnavailableReason.SOURCE_INCOMPLETE
    nonstring = replace(binding, source_ref=42)
    assert RequestSourceReader(console_lookup=lookup).resolve(nonstring).reason is SourceUnavailableReason.SOURCE_INCOMPLETE
    assert lookup.calls == []


def test_restore_and_console_producer_signal_unchanged():
    from app import task_console_actions as actions
    from app.task_semantic_models import BusinessTaskStage, BusinessTaskStatus

    task = SimpleNamespace(
        stage=BusinessTaskStage.CANDIDATE, status=BusinessTaskStatus.CANCELLED,
        title=TITLE, updated_at=TIME,
    )
    store = SimpleNamespace(get_business_task=lambda task_id: task)
    captured = []

    class FakeService:
        def __init__(self, store):
            pass

        def update_task(self, command):
            captured.append(command)

    with patch.object(actions, 'TaskSemanticService', FakeService), patch.object(
        actions.BusinessAttentionProjection, 'recompute_for_tasks'
    ):
        assert actions.decide_candidate(store, 43, 'restore', operator_name='操作员') is BusinessTaskStatus.OPEN
    signal = captured[0].signal
    assert signal.source_ref == f'console:task:43:restore:{TIME}'
    assert signal.dedupe_key == signal.source_ref
    assert signal.evidence_text == f'操作员在控制台恢复了候选任务「{TITLE}」'
    assert signal.author_name == '操作员'
    assert json.loads(signal.context_json) == {'action': 'restore'}
    assert captured[0].reason == '操作员在控制台恢复了这个候选任务。'

    item = BusinessTaskSignal(
        id=51, source_type='console',
        source_ref=signal.source_ref, source_time=signal.source_time,
        author_name=signal.author_name, author_kind=signal.author_kind,
        evidence_text=signal.evidence_text, context_json=signal.context_json,
        dedupe_key=signal.dedupe_key, created_at='now',
    )
    binding = SourceVersionBinding.from_signal(item)
    fact = BusinessTaskSignalFact.from_signal(item, binding=binding)
    event = BusinessTaskEvent(
        id=52, task_id=43, event_type='status_changed', signal_id=51,
        before_json=json.dumps({'title': TITLE, 'updated_at': TIME}),
        after_json='{}', reason='irrelevant', created_at='now',
    )
    assert RequestSourceReader(console_lookup=Lookup([(fact, event)])).resolve(binding).body == signal.evidence_text
