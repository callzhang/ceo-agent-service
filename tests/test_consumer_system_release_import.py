def test_consumer_system_release_imports_current_reply_task_sql_from_store():
    import app.consumer_system_release as release
    from app.store import NATIVE_REPLY_TASK_CURRENT_SQL

    assert release.NATIVE_REPLY_TASK_CURRENT_SQL is NATIVE_REPLY_TASK_CURRENT_SQL
