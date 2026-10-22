from app.cli import WorkerSettings
from app.dws_client import DwsClient
from app.email_unsubscribe import TRANSIENT_BROWSER_UNSUBSCRIBE_RETRIES
from app.external_retry import MAX_AUTOMATIC_RETRIES
from app.runtime_fallback import CAPACITY_RETRIES_ON_SAME_ROUTE


def test_automatic_retry_policies_share_three_retry_cap():
    assert MAX_AUTOMATIC_RETRIES == 3
    assert CAPACITY_RETRIES_ON_SAME_ROUTE == MAX_AUTOMATIC_RETRIES
    assert TRANSIENT_BROWSER_UNSUBSCRIBE_RETRIES == MAX_AUTOMATIC_RETRIES
    assert DwsClient().transient_retry_attempts == MAX_AUTOMATIC_RETRIES
    assert WorkerSettings().dws_transient_retry_attempts == MAX_AUTOMATIC_RETRIES
