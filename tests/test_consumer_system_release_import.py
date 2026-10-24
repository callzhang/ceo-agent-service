def test_consumer_system_release_imports_frozen_run_sql_from_run_storage():
    import app.consumer_system_release as release
    from app.run_storage import SCHEDULED_RUN_SQL

    assert release.SCHEDULED_RUN_SQL is SCHEDULED_RUN_SQL
