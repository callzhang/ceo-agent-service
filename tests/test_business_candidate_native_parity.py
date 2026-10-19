"""Public Business candidate behavior against the deployed 83c1 predecessor.

The predecessor checkout and both databases exist only inside pytest's owned
temporary directory. No provider or Memory backend is called.
"""

import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

import pytest

from app.action_email_schema_release import prepare_action_email_schema_release
from app.store import STORE_SCHEMA_VERSION
from app.email_store import EMAIL_SCHEMA_VERSION


PREDECESSOR = "83c1d7f030bb16cb6ef1fc729efc322be409dda2"
ROOT = Path(__file__).parents[1]
EXPLICIT_TIME = "2026-10-17T18:00:00+00:00"

DRIVER = r'''
import json, sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.task_business_resolution import BusinessResolutionService
from app.wechat.memory import ExtractedMemoryCandidate

path, stage = sys.argv[1:]
path = Path(path)
s = AutoReplyStore(path)
EmailStore(path)
if stage == 'initialize':
    raise SystemExit(0)
service = BusinessResolutionService(s)
if stage in ('inverse_native_write', 'reforward_read'):
    if stage == 'inverse_native_write':
        with s.business_task_transaction() as db:
            second_project_candidate = s.create_business_project_candidate_in_transaction(
                cluster_id=1, title='Native second candidate', reason='Native inverse write', _db=db)
            second_attention = s.create_business_attention_item_in_transaction(
                stable_key='parity:second-attention', category='watch',
                title='Native second attention', business_area='parity',
                why_attention='Native inverse write', current_state='Open',
                ceo_action='Review', anchor_id=1, evidence_signal_id=1,
                now='2026-10-17T20:00:00+00:00', _db=db)
        second_memory = s.add_wechat_memory_candidate(
            import_run_id='parity:second-import', account_id='parity:second-account',
            candidate=ExtractedMemoryCandidate(
                statement='Native second memory candidate', category='fact', confidence=0.8,
                sensitivity='normal', source_message_ids=['message:native'],
                source_conversation_ids=['conversation:native'],
                source_time_start='2026-10-17T20:00:00+00:00',
                source_time_end='2026-10-17T20:00:00+00:00',
                evidence_excerpt='Native second memory candidate', cleanup_notes='Synthetic parity'))
        assert [second_project_candidate, second_attention, second_memory] == [2, 2, 3]
    print('PARITY_JSON=' + json.dumps({
        'project_candidates': [s.get_business_project_candidate(i).model_dump(mode='json')
                               for i in (1, 2)],
        'attention_items': [s.get_business_attention_item(i).model_dump(mode='json')
                            for i in (1, 2)],
        'wechat_candidates': [s.get_wechat_memory_candidate(i) for i in (1, 2, 3)],
    }, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0)
anchor = service.register_anchor(anchor_type='project', anchor_ref='parity:project', title='Parity project')
project = service.register_official_project(anchor_id=anchor, registry_source='parity:registry')
task = s.create_business_task(title='Parity task', stage='candidate')
signal = s.create_business_task_signal(source_type='meeting', source_ref='parity:meeting',
    evidence_text='Synthetic local parity evidence', dedupe_key='parity:signal')
cluster = service.create_cluster(title='Parity cluster', task_ids=[task])
candidate = service.propose_project(cluster_id=cluster, title='Parity candidate', reason='Synthetic parity')
service.confirm_project_candidate(candidate_id=candidate, project_id=project, evidence_signal_id=signal)
confirmed = s.get_business_project_candidate(candidate).model_dump(mode='json')
service.confirm_project_candidate(candidate_id=candidate, project_id=project, evidence_signal_id=signal)
other_anchor = service.register_anchor(anchor_type='project', anchor_ref='parity:other',
    title='Other parity project')
other_project = service.register_official_project(anchor_id=other_anchor,
    registry_source='parity:other-registry')
try:
    service.confirm_project_candidate(candidate_id=candidate, project_id=other_project,
        evidence_signal_id=signal)
except ValueError as exc:
    conflict = str(exc)
else:
    raise AssertionError('different-project confirmation unexpectedly succeeded')
with s.business_task_transaction() as db:
    attention = s.create_business_attention_item_in_transaction(
        stable_key='parity:attention', category='decision', title='Parity attention',
        business_area='parity', why_attention='Synthetic proof', current_state='Open',
        ceo_action='Review', anchor_id=anchor, evidence_signal_id=signal,
        assessment_json='{}', now='2026-10-17T18:00:00+00:00', _db=db)
    s.link_business_attention_task_in_transaction(attention_item_id=attention, task_id=task, _db=db)
    s.replace_business_attention_proposal_tasks_in_transaction(attention_item_id=attention,
        task_ids=(task,), _db=db)
    event = s.append_business_attention_event_in_transaction(attention_item_id=attention,
        event_type='opened', signal_id=signal, before_json='{}', after_json='{}',
        reason='Synthetic parity', _db=db)
    attention_row = s.get_business_attention_item_in_transaction(item_id=attention,
        _db=db).model_dump(mode='json')
statement = 'Synthetic parity memory candidate'
def extracted(messages, conversations, start, end):
    return ExtractedMemoryCandidate(statement=statement, category='fact', confidence=0.9,
        sensitivity='normal', source_message_ids=messages,
        source_conversation_ids=conversations, source_time_start=start,
        source_time_end=end, evidence_excerpt=statement, cleanup_notes='Synthetic parity')
memory = s.add_wechat_memory_candidate(import_run_id='parity:import', account_id='parity:account',
    candidate=extracted(['message:1'], ['conversation:1'], '2026-10-17T18:00:00+00:00',
        '2026-10-17T18:00:00+00:00'))
merged = s.add_wechat_memory_candidate(import_run_id='parity:import', account_id='parity:account',
    candidate=extracted(['message:2'], ['conversation:2'], '2026-10-17T17:00:00+00:00',
        '2026-10-17T19:00:00+00:00'))
memory_merged = s.get_wechat_memory_candidate(memory)
approved = s.review_wechat_memory_candidate(memory, 'approve', reviewer='parity',
    final_statement=statement)
claimed = s.claim_wechat_memory_candidate_write(memory)
writing = s.claim_wechat_memory_candidate_write(memory)
s.finish_wechat_memory_candidate_write(memory, status='failed', error='synthetic failure')
failed = s.get_wechat_memory_candidate(memory)
reclaimed = s.claim_wechat_memory_candidate_write(memory)
s.finish_wechat_memory_candidate_write(memory, status='written', memory_id='fake-local-receipt')
written = s.get_wechat_memory_candidate(memory)
revoked = s.review_wechat_memory_candidate(memory, 'revoke', reviewer='parity')
rejected_id = s.add_wechat_memory_candidate(import_run_id='parity:import2',
    account_id='parity:account2', candidate=extracted(['message:3'], ['conversation:3'],
    '2026-10-17T18:00:00+00:00', '2026-10-17T18:00:00+00:00'))
rejected = s.review_wechat_memory_candidate(rejected_id, 'reject', reviewer='parity')
print('PARITY_JSON=' + json.dumps({
    'ids': [anchor, project, task, signal, cluster, candidate, attention, event, memory, rejected_id],
    'project_candidate': confirmed, 'confirmation_conflict': conflict,
    'attention': attention_row,
    'attention_list': [x.model_dump(mode='json') for x in s.list_business_attention_items()],
    'merged_result': merged, 'memory_merged': memory_merged,
    'approved': approved, 'claimed_outcome': claimed['outcome'],
    'writing': writing, 'failed': failed, 'reclaimed_outcome': reclaimed['outcome'],
    'written': written, 'revoked': revoked, 'rejected': rejected,
    'memory_list': s.list_wechat_memory_candidates(),
}, ensure_ascii=False, sort_keys=True))
'''


def _driver(root: Path, database: Path, stage: str):
    environment = {**os.environ, "PYTHONPATH": str(root), "TMPDIR": str(database.parent)}
    completed = subprocess.run([sys.executable, "-c", DRIVER, str(database), stage],
                               cwd=root, env=environment, check=True, text=True,
                               capture_output=True)
    if stage == "initialize":
        return None
    return json.loads(next(line.removeprefix("PARITY_JSON=") for line in
                           completed.stdout.splitlines() if line.startswith("PARITY_JSON=")))


def _normalize_generated_clocks(value, path=(), observed=None):
    """Mask only generated candidate clock fields; retain all source times."""
    if observed is None:
        observed = []
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            item_path = (*path, key)
            generated = (key in {"created_at", "updated_at", "reviewed_at"}
                         and path and path[0] in {"project_candidate", "memory_merged",
                          "approved", "failed", "written", "revoked", "rejected", "memory_list"})
            if generated and item:
                observed.append((item_path, item))
                result[key] = "<generated-clock>"
            else:
                result[key] = _normalize_generated_clocks(item, item_path, observed)
        return result
    if isinstance(value, list):
        return [_normalize_generated_clocks(item, (*path, index), observed)
                for index, item in enumerate(value)]
    return value


def _assert_clock_contract(result, clocks, before, after):
    for state in ("approved", "revoked", "rejected"):
        assert result[state]["reviewed_at"] == result[state]["updated_at"]
    for _path, stamp in clocks:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        assert before.replace(microsecond=0) <= parsed <= after


def test_deployed_native_public_candidate_behavior_matches_selected_current(tmp_path):
    archive = subprocess.run(["git", "archive", "--format=tar", PREDECESSOR],
                             cwd=ROOT, check=True, capture_output=True).stdout
    old_root = tmp_path / "native83"
    old_root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        source.extractall(old_root, filter="data")
    original = tmp_path / "native.sqlite3"
    selected = tmp_path / "selected.sqlite3"
    _driver(old_root, original, "initialize")
    with sqlite3.connect(original) as src, sqlite3.connect(selected) as dst:
        src.backup(dst)
    release = prepare_action_email_schema_release(
        ROOT, selected, target_store_version=STORE_SCHEMA_VERSION,
        target_email_version=EMAIL_SCHEMA_VERSION)
    release.apply()
    before = datetime.now(timezone.utc)
    native = _driver(old_root, original, "exercise")
    current = _driver(ROOT, selected, "exercise")
    after = datetime.now(timezone.utc)
    native_clocks, current_clocks = [], []
    native_shape = _normalize_generated_clocks(native, observed=native_clocks)
    current_shape = _normalize_generated_clocks(current, observed=current_clocks)
    assert native_shape == current_shape
    assert native_clocks and current_clocks
    assert {path for path, _ in native_clocks} == {path for path, _ in current_clocks}
    _assert_clock_contract(native, native_clocks, before, after)
    _assert_clock_contract(current, current_clocks, before, after)
    # Explicit fixture timestamps must survive exact comparison, including
    # the source interval merged across canonical duplicates.
    assert current["attention"]["created_at"] == EXPLICIT_TIME
    assert current["memory_merged"]["source_time_start"] == "2026-10-17T17:00:00+00:00"
    assert current["memory_merged"]["source_time_end"] == "2026-10-17T19:00:00+00:00"
    altered = json.loads(json.dumps(current))
    altered["memory_merged"]["source_time_start"] = "2026-10-17T16:00:00+00:00"
    assert _normalize_generated_clocks(altered) != current_shape
    outside = [(path, "2000-01-01 00:00:00") if path == current_clocks[0][0]
               else (path, stamp) for path, stamp in current_clocks]
    with pytest.raises(AssertionError):
        _assert_clock_contract(current, outside, before, after)
    # Current candidate writes must survive inverse into the genuine archived
    # constructor, further native writes, and a second cold selected opener.
    release.rollback()
    inverse_native = _driver(old_root, selected, "inverse_native_write")
    release.apply()
    reforward_current = _driver(ROOT, selected, "reforward_read")
    assert inverse_native == reforward_current
