import sqlite3

import pytest

from app.email_unsubscribe import UnsubscribeBrowserFailure
from app.external_failures import (
    EXTERNAL_UNSUBSCRIBE_CATEGORIES,
    external_task_error_sql,
    is_external_task_error,
)


@pytest.mark.parametrize("category", EXTERNAL_UNSUBSCRIBE_CATEGORIES)
def test_detailed_external_unsubscribe_failure_stays_out_of_internal_attention(
    category,
):
    error = (
        "email_unsubscribe_browser_failed: "
        f"category={category.value};detail=provider connection reset;"
        "operation=open_entry"
    )

    assert is_external_task_error(error)

    db = sqlite3.connect(":memory:")
    db.execute("create table reply_tasks (error text)")
    db.execute("insert into reply_tasks values (?)", (error,))
    assert db.execute(
        f"select {external_task_error_sql()} from reply_tasks"
    ).fetchone()[0]


def test_detailed_internal_unsubscribe_failure_stays_in_internal_attention():
    error = (
        "email_unsubscribe_browser_failed: "
        f"category={UnsubscribeBrowserFailure.RUNTIME_UNAVAILABLE.value};"
        "detail=browser runtime missing;operation=open_entry"
    )

    assert not is_external_task_error(error)

    db = sqlite3.connect(":memory:")
    db.execute("create table reply_tasks (error text)")
    db.execute("insert into reply_tasks values (?)", (error,))
    assert not db.execute(
        f"select {external_task_error_sql()} from reply_tasks"
    ).fetchone()[0]
