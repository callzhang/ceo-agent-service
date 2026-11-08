from app.sqlite_connections import _IDLE_CONNECTIONS
from app.store import AutoReplyStore

# The two tests rely on file order: the first leaves an idle connection behind,
# the autouse fixture in conftest.py has to have closed it before the second.


def test_a_store_call_leaves_an_idle_connection_in_the_pool(tmp_path):
    store = AutoReplyStore(tmp_path / "idle.sqlite3")
    with store._connect() as db:
        db.execute("select 1")

    assert _IDLE_CONNECTIONS.pool


def test_the_previous_tests_idle_connection_was_closed():
    assert not getattr(_IDLE_CONNECTIONS, "pool", None)
