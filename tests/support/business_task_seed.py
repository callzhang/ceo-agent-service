"""Seed current business Task facts through their transaction owners."""


def seed_task_signal(store, **values):
    with store.business_task_transaction() as db:
        return store.create_business_task_signal_in_transaction(**values, _db=db)


def seed_task(store, **values):
    with store.business_task_transaction() as db:
        return store.create_business_task_in_transaction(**values, _db=db)


def seed_task_evidence(store, **values):
    with store.business_task_transaction() as db:
        store.link_business_task_evidence_in_transaction(**values, _db=db)


def signal_snapshot(store):
    """Read exact persisted signal facts for assertions about their changes."""
    from app.provenance_storage import signal_projection

    with store._connect() as db:
        rows = db.execute(
            f"select signal.* from ({signal_projection()}) signal "
            "order by signal.id"
        ).fetchall()
        return tuple(store._business_task_signal_from_row(row) for row in rows)


def project_candidate_snapshot(store, candidate_id):
    with store._connect() as db:
        return store.get_business_project_candidate_in_transaction(candidate_id=candidate_id, _db=db)
