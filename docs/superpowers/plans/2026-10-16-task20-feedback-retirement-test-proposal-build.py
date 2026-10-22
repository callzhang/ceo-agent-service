"""Build and locally check an unselected feedback-retirement test proposal."""
import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
PREFIX = '2026-10-16-task20-feedback-retirement-test-proposal'
PYTHON = '/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python'
RUFF = '/Users/derek/.cache/uv/archive-v0/1vqK0HG0A7DbkHHlOjztN/bin/ruff'
PATCHES = (
    '2026-10-16-task8-store-caller-proposal.patch',
    '2026-10-16-task8-workbench-caller-proposal.patch',
    '2026-10-16-task12-email-native-caller-proposal.patch',
    '2026-10-16-task12-store-misc-caller-proposal.patch',
    '2026-10-16-task12-feedback-caller-proposal.patch',
    '2026-10-16-task12-workbench-children-caller-proposal.patch',
    '2026-10-16-task20-external-writers-caller-proposal.patch',
    '2026-10-16-task20-console-readers-caller-proposal.patch',
    '2026-10-16-task20-remaining-readers-caller-proposal.patch',
    '2026-10-16-task20-store-constructor-proposal.patch',
    '2026-10-16-task20-paired-closure-proposal.patch',
)
FILES = ('tests/feedback_processing_fixtures.py', 'tests/test_feedback_processing.py',
         'tests/test_audit_web.py', 'tests/test_console_web_api.py')
RETIRED = (
    'test_feedback_round_backfill_self_heals_current_schema_and_interruption',
    'test_feedback_round_reconciliation_requires_pointer_ownership_and_batch',
    'test_feedback_round_reconciliation_clears_ambiguous_matching_history',
    'test_round_reconciliation_clears_stale_non_latest_matching_pointer',
)
HISTORICAL = (
    'test_feedback_round_backfill_preserves_legacy_receipts_and_source',
    'test_migrated_v1_processing_round_resolves_idempotently_and_reopens',
)
FIXTURE = '''"""Synthetic resolved feedback through current public batch APIs; no external work."""
from app.feedback_processing import ResolutionEvidence
from app.store import AutoReplyStore


def resolve_feedback_fixture(
    store: AutoReplyStore, feedback_key: str, *, batch_id: str | None = None
) -> None:
    """Resolve an already seeded event without replacing its source content.

    Receipt values describe this synthetic database fixture. No tests, service
    restart, health request, commit lookup, Agent or provider is executed here.
    """
    selected_batch = batch_id or f"resolved-fixture:{feedback_key}"
    receipt = ResolutionEvidence(
        commit_sha="b" * 40,
        test_evidence={"pytest": {"exit_code": 0}},
        restart_evidence={
            "launchd_label": "com.ceo-agent-service.main",
            "before_pid": 10,
            "after_pid": 11,
        },
        health_evidence={
            "url": "http://127.0.0.1:8765/healthz",
            "status_code": 200,
            "ok": True,
        },
        backlog_evidence={"processing": 0, "failed": 0, "retryable": 0},
    )
    store.create_feedback_processing_batch([feedback_key], batch_id=selected_batch)
    assert store.claim_feedback_processing_items(selected_batch, [feedback_key])
    store.associate_feedback_processing_turn(
        feedback_key,
        workbench_task_id="task-fixture",
        workbench_turn_id="turn-fixture",
        attempt_id=71,
        agent_run_id=72,
    )
    store.patch_feedback_processing_item_evidence(
        feedback_key,
        commit_sha=receipt.commit_sha,
        test_evidence=receipt.test_evidence,
        restart_evidence=receipt.restart_evidence,
        health_evidence=receipt.health_evidence,
    )
    assert store.resolve_feedback_processing_batch(
        selected_batch, receipt, commit_is_ancestor=True
    )
'''
COLD = '''def test_feedback_cold_reopen_preserves_current_batch_rounds_and_sources(tmp_path: Path):
    db_path = tmp_path / "current-rounds.sqlite3"
    store = AutoReplyStore(db_path)
    store.upsert_feedback_event(key="pending", feedback_token="token-pending")
    _prepare_processing_round(store, "processing", "batch-processing")
    store.upsert_feedback_event(
        key="resolved", feedback_token="token-resolved", comment="original source"
    )
    resolve_feedback_fixture(store, "resolved", batch_id="batch-resolved")

    def snapshot(current):
        return {
            key: (
                current.get_feedback_event(key),
                current.get_feedback_processing_item(key),
                current.list_feedback_processing_rounds(key),
                current.list_feedback_processing_transitions(key),
            )
            for key in ("pending", "processing", "resolved")
        }, [
            current.get_feedback_processing_batch(batch_id)
            for batch_id in ("batch-processing", "batch-resolved")
        ]

    before = snapshot(store)
    assert before[0]["pending"][1].current_round_id == 0
    assert before[0]["pending"][2] == []
    assert before[0]["processing"][1].status == "processing"
    assert before[0]["resolved"][1].status == "resolved"
    for key in ("processing", "resolved"):
        item = before[0][key][1]
        rounds = before[0][key][2]
        assert len(rounds) == 1
        assert item.current_round_id == rounds[0].id
        assert rounds[0].receipt_version == 2
    for _ in range(2):
        store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
        assert snapshot(AutoReplyStore(db_path)) == before
'''
PROJECTION = '''def test_batch_resolved_event_projection_and_status_transition_are_consistent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolved.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    resolve_feedback_fixture(store, "feedback-1", batch_id="batch-1")
    item = store.get_feedback_processing_item("feedback-1")
    event = store.get_feedback_event("feedback-1")
    batch = store.get_feedback_processing_batch("batch-1")
    rounds = store.list_feedback_processing_rounds("feedback-1")
    assert item is not None
    assert event is not None
    assert batch is not None
    assert item.status == batch.status == rounds[0].status == "resolved"
    assert item.current_round_id == rounds[0].id
    assert item.resolved_at == event.resolved_at == batch.resolved_at == rounds[0].resolved_at
    assert item.resolved_at

    with pytest.raises(ValueError):
        store.patch_feedback_processing_item_evidence("feedback-1", status="pending")

    with pytest.raises(FeedbackProcessingBatchError):
        store.create_feedback_processing_batch(["feedback-1"], batch_id="batch-new")
    assert store.get_feedback_processing_batch("batch-new") is None
'''
NODES = (
    'tests/test_feedback_processing.py::test_claim_rejects_unknown_or_resolved_keys_atomically',
    'tests/test_feedback_processing.py::test_reopen_existing_batch_is_idempotent_after_source_resolution',
    'tests/test_feedback_processing.py::test_batch_resolved_event_projection_and_status_transition_are_consistent',
    'tests/test_feedback_processing.py::test_feedback_cold_reopen_preserves_current_batch_rounds_and_sources',
    'tests/test_audit_web.py::test_user_feedback_nav_badge_shows_pending_count',
    'tests/test_console_web_api.py::test_console_feedback_pending_badge_is_global_when_filtered_to_resolved',
    'tests/test_audit_web.py::test_user_feedback_resolve_route_requires_processing_batch',
    'tests/test_console_web_api.py::test_feedback_direct_resolve_requires_batch',
)


def digest(data):
    return hashlib.sha256(data.encode()).hexdigest()


def replace(text, old, new):
    assert text.count(old) == 1, (old, text.count(old))
    return text.replace(old, new)


def replace_function(text, name, replacement):
    node = next(node for node in ast.parse(text).body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    assert not node.decorator_list, name
    lines = text.splitlines(True)
    return ''.join(lines[:node.lineno - 1]) + replacement + ''.join(lines[node.end_lineno:])


owned = Path(tempfile.mkdtemp(prefix='task20-feedback-retirement-tests-', dir='/tmp')).resolve()
try:
    for directory in ('app', 'tests'):
        shutil.copytree(ROOT / directory, owned / directory,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(ROOT / 'data/config', owned / 'data/config')
    shutil.copyfile(ROOT / 'pyproject.toml', owned / 'pyproject.toml')
    before = {name: (ROOT / name).read_text() if (ROOT / name).exists() else '' for name in FILES}
    for name in PATCHES:
        subprocess.run(['git', 'apply', str(HERE / name)], cwd=owned, check=True)
    after = dict(before)
    after[FILES[0]] = FIXTURE
    s = after['tests/test_feedback_processing.py']
    s = replace(s, 'from app.store import AutoReplyStore, UserFeedbackItem\n',
                'from app.store import AutoReplyStore, UserFeedbackItem\n'
                'from tests.feedback_processing_fixtures import resolve_feedback_fixture\n')
    s = replace(s, '    assert store.resolve_feedback_event("feedback-resolved") is True\n',
                '    resolve_feedback_fixture(store, "feedback-resolved")\n')
    s = replace(s, '    assert store.resolve_feedback_event("feedback-1") is True\n    reopened =',
                '    resolve_feedback_fixture(store, "feedback-1", batch_id="batch-1")\n    reopened =')
    s = replace_function(s, 'test_resolved_event_projection_and_status_transition_are_consistent', PROJECTION)
    for index, name in enumerate(RETIRED):
        s = replace_function(s, name, COLD if index == 0 else '')
    after['tests/test_feedback_processing.py'] = s
    for path, key in (('tests/test_audit_web.py', 'event-1'),
                      ('tests/test_console_web_api.py', 'resolved-feedback')):
        s = after[path]
        s = replace(s, 'from tests.run_reader_fixtures import insert_native_run_fixture\n',
                    'from tests.run_reader_fixtures import insert_native_run_fixture\n'
                    'from tests.feedback_processing_fixtures import resolve_feedback_fixture\n')
        s = replace(s, f'    store.resolve_feedback_event("{key}")\n',
                    f'    resolve_feedback_fixture(store, "{key}")\n')
        after[path] = s
    changed_functions = {}
    for path in FILES[1:]:
        old_nodes = {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(before[path]).body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        new_nodes = {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(after[path]).body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        changed_functions[path] = sorted(name for name in old_nodes.keys() | new_nodes.keys()
                                         if old_nodes.get(name) != new_nodes.get(name))
        if path.endswith('test_feedback_processing.py'):
            for name in HISTORICAL:
                assert old_nodes[name] == new_nodes[name], name
        assert 'resolve_feedback_event(' not in after[path]
    patch = ''.join(''.join(difflib.unified_diff(before[name].splitlines(True), after[name].splitlines(True),
                     fromfile='/dev/null' if not before[name] else 'a/' + name, tofile='b/' + name))
                    for name in FILES)
    patch_path = HERE / (PREFIX + '.patch')
    patch_path.write_text(patch)
    subprocess.run(['git', 'apply', str(patch_path)], cwd=owned, check=True)
    assert all((owned / name).read_text() == after[name] for name in FILES)
    subprocess.run(['git', 'apply', '--reverse', str(patch_path)], cwd=owned, check=True)
    assert all((owned / name).read_text() == before[name] for name in FILES[1:])
    assert not (owned / FILES[0]).exists()
    subprocess.run(['git', 'apply', str(patch_path)], cwd=owned, check=True)
    for name in FILES:
        compile(after[name], name, 'exec')
    subprocess.run([RUFF, 'check', '--no-cache', *FILES], cwd=owned, check=True)
    for node in NODES:
        path, name = node.split('::')
        assert any(n.name == name for n in ast.parse((owned / path).read_text()).body
                   if isinstance(n, ast.FunctionDef)), node
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                   'CEO_WORKER_DB': str(owned / 'fixture.sqlite3'),
                   'CEO_ENV_FILE': str(owned / 'absent.env'),
                   'CEO_SERVICE_ROOT': str(owned / 'absent-service'),
                   'PYTHONPATH': str(owned)}
    result = subprocess.run([PYTHON, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                             '--basetemp', str(owned / 'pytest'), *NODES], cwd=owned, env=environment,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(result.stdout)
    evidence = {
        'scope': 'Unselected docs-only test proposal; synthetic owned copy only; no app/tests root edits or runtime activation',
        'prerequisite_patch_hashes': {name: digest((HERE / name).read_text()) for name in PATCHES},
        'files': {name: {'before_sha256': digest(before[name]), 'after_sha256': digest(after[name])} for name in FILES},
        'patch_sha256': digest(patch), 'changed_top_level_functions': changed_functions,
        'retired_current_startup_tests': list(RETIRED), 'historical_functions_ast_unchanged': list(HISTORICAL),
        'pytest_nodes': list(NODES), 'pytest_returncode': result.returncode, 'pytest_output': result.stdout,
        'compile_ruff_apply_reverse_reapply': 'pass',
    }
    retired = set(RETIRED) | {'test_resolved_event_projection_and_status_transition_are_consistent'}
    native_names = ('feedback_events', 'feedback_processing_batches',
                    'feedback_processing_items', 'feedback_processing_rounds')
    remaining = []
    for owner in ast.parse(before['tests/test_feedback_processing.py']).body:
        if not isinstance(owner, ast.FunctionDef) or owner.name in retired:
            continue
        sites = []
        for literal in ast.walk(owner):
            if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                matched = [name for name in native_names if name in literal.value]
                if matched:
                    sites.append({'line': literal.lineno, 'native_names': matched})
        if sites:
            remaining.append({'owner': owner.name, 'line': owner.lineno, 'literal_sites': sites})
    evidence['remaining_separate_native_literal_fixture_inventory'] = remaining
    evidence['remaining_separate_native_literal_owner_count'] = len(remaining)
    evidence['remaining_separate_native_literal_site_count'] = sum(len(n['literal_sites']) for n in remaining)
    evidence['scope_note'] = ('Inventory is lexical native-name literals in unchanged feedback file function owners; '
                              'not an execution failure inventory. Genuine historical source tests remain unchanged; '
                              'no passing claim for unchanged native fixtures.')
    (HERE / (PREFIX + '-analysis.json')).write_text(json.dumps(evidence, indent=2) + '\n')
finally:
    shutil.rmtree(owned)
    print('OWNED_TEMP_REMOVED', not owned.exists())
