"""Current51 Email runtime regressions; run against the cumulative copied app."""
from datetime import datetime, timedelta, timezone

from app.email_store import EmailStore
from app.quality_gate import scan_hourly_quality
from app.runs11_definitions import run11_projection
from app.store import AutoReplyStore


def _ready(tmp_path):
    store = AutoReplyStore(tmp_path / 'email.sqlite3')
    email = EmailStore(store.path)
    with email._connect() as db:
        assert db.execute("select 1 from sqlite_schema where name='email_classifier_runtime_samples'").fetchone() is None
    return store, email

def _sample(email, model='model-a', outcome='success', fallback=''):
    email.record_classifier_runtime_sample(
        model_id=model, outcome=outcome, fallback_code=fallback,
        cache_hit=False, runtime_warm=True, queue_ms=1.0,
        http_ms=2.0, embedding_ms=3.0, head_ms=4.0, total_ms=10.0,
    )


def test_writer_global_retention_and_other_runs_survive(tmp_path):
    store, email = _ready(tmp_path)
    with email._connect() as db:
        original = db.execute('select count(*) from runs').fetchone()[0]
        stamp = datetime.now(timezone.utc).isoformat()
        # Seed through the source-shaped writer before the one public write.
        from app.runs11_storage import insert_run11_fact
        for index in range(2050):
            insert_run11_fact(db, 'email_classifier_runtime_samples', {
                'model_id': 'model-a' if index % 2 else 'model-b',
                'outcome': 'success', 'cache_hit': 0, 'runtime_warm': 1,
                'queue_ms': 1.0, 'http_ms': 2.0, 'embedding_ms': 3.0,
                'head_ms': 4.0, 'total_ms': 10.0, 'recorded_at': stamp,
            })
    _sample(email)
    projection = '(' + run11_projection('email_classifier_runtime_samples') + ')'
    with email._connect() as db:
        ids = [row['id'] for row in db.execute(f'select id from {projection} order by id')]
        assert len(ids) == 2048 and ids[0] == 4 and ids[-1] == 2051
        assert db.execute("select count(*) from runs where record_type!='email_classifier_runtime_samples'").fetchone()[0] == original
        assert db.execute("select seq from sqlite_sequence where name='runs:email_classifier_runtime_samples'").fetchone()[0] == 2051
    assert email.classifier_runtime_observability(model_id='model-b')['timing']['all']['sample_count'] == 1023
    assert email.classifier_runtime_observability(model_id='model-a')['timing']['all']['sample_count'] == 1025


def test_rate_window_and_last_200_fallback(tmp_path):
    _, email = _ready(tmp_path)
    from app.runs11_storage import insert_run11_fact
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with email._connect() as db:
        for index in range(201):
            insert_run11_fact(db, 'email_classifier_runtime_samples', {
                'model_id': 'model-a', 'outcome': 'success',
                'cache_hit': 0, 'runtime_warm': 1,
                'queue_ms': 0.0, 'http_ms': 0.0, 'embedding_ms': 0.0,
                'head_ms': 0.0, 'total_ms': float(index), 'recorded_at': old,
            })
    rate = email.classifier_runtime_rate(window_seconds=600)
    assert rate['evaluated'] == 0 and rate['latency_from_last_batch'] is True
    assert rate['latency_ms']['mean'] == 100.5
    _sample(email)
    rate = email.classifier_runtime_rate(window_seconds=600)
    assert rate['evaluated'] == 1 and rate['latency_from_last_batch'] is False
    assert rate['latency_ms']['mean'] == 10.0


def test_observability_flags_fallback_and_gate_labels(tmp_path):
    store, email = _ready(tmp_path)
    _sample(email, outcome='failure', fallback='ValueError')
    _sample(email, outcome='failure', fallback='ValueError')
    view = email.classifier_runtime_observability(model_id='model-a')
    assert view['timing']['all']['sample_count'] == 2
    assert view['timing']['warm_success']['sample_count'] == 0
    assert view['fallback_counts'] == {'ValueError': 2}
    report = scan_hourly_quality(store.path, now=datetime.now(timezone.utc))
    assert 'email_classifier_runtime_samples' in report.checked_sources
    assert any((issue.source, issue.code, issue.count, issue.severity) ==
               ('email_classifier_runtime_samples', 'model_failing', 2, 'error')
               for issue in report.violations)


def test_gate_empty_runs_source_is_checked(tmp_path):
    store, _ = _ready(tmp_path)
    report = scan_hourly_quality(store.path, now=datetime.now(timezone.utc))
    assert 'email_classifier_runtime_samples' in report.checked_sources
    assert not any(issue.source == 'email_classifier_runtime_samples'
                   for issue in (*report.violations, *report.attention))


def test_retention_uses_bounded_sample_index_without_planner_stats(tmp_path):
    _, email = _ready(tmp_path)
    from app.runs11_storage import insert_run11_fact

    stamp = datetime.now(timezone.utc).isoformat()
    with email._connect() as db:
        assert not db.execute("select 1 from sqlite_schema where name='sqlite_stat1'").fetchone()
        for index in range(120):
            insert_run11_fact(db, 'email_classifier_runtime_samples', {
                'model_id': 'model-a', 'outcome': 'success',
                'cache_hit': 0, 'runtime_warm': 1,
                'queue_ms': 0.0, 'http_ms': 0.0, 'embedding_ms': 0.0,
                'head_ms': 0.0, 'total_ms': 0.0, 'recorded_at': stamp,
            })
        for index in range(3000):
            insert_run11_fact(db, 'setup_wizard_events', {
                'id': 100000 + index, 'step_id': 'other',
                'action_id': 'other', 'status': 'running',
                'started_at': stamp,
            })
    statements = []
    vm_steps = [0]
    active = [False]
    original_open = email._open_connection

    def traced_open():
        db = original_open()

        def on_statement(sql):
            statements.append(sql)
            if sql.lstrip().lower().startswith('delete from runs'):
                active[0] = True
                vm_steps[0] = 0

        def on_steps():
            if active[0]:
                vm_steps[0] += 100
            return 0

        db.set_trace_callback(on_statement)
        db.set_progress_handler(on_steps, 100)
        return db

    email._open_connection = traced_open
    _sample(email)
    deletion = next(sql for sql in statements if sql.lstrip().lower().startswith('delete from runs'))
    with original_open() as db:
        plan = [row[3] for row in db.execute('explain query plan ' + deletion)]
        assert db.execute("select count(*) from runs where record_type='setup_wizard_events'").fetchone()[0] == 3000
        assert db.execute("select count(*) from runs where record_type='email_classifier_runtime_samples'").fetchone()[0] == 121
    assert any('idx_runs11_sample_id' in detail for detail in plan), plan
    assert vm_steps[0] < 10000, vm_steps[0]
