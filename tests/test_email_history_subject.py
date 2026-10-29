"""History searches current Email Action facts, including failed-only views."""

import pytest

from app.store import AutoReplyStore
from tests.test_audit_web import _failed_email_action
from tests.test_console_web_api import _client


@pytest.mark.parametrize('status', ['', 'failed'])
def test_history_email_subject_search_respects_current_status(tmp_path, status):
    store = AutoReplyStore(tmp_path / 'worker.sqlite3')
    action_id = _failed_email_action(store, attempt_count=3, next_attempt_at='')
    with _client(tmp_path) as client:
        params = {'status': status, 'q': 'technical DEEPDIVE', 'page_size': 1}
        response = client.get('/api/console/history', params=params)
        assert response.status_code == 200
        result = response.json()
        assert result['meta']['total'] == 1
        assert len(result['items']) == 1
        assert result['items'][0]['id'] == str(action_id)
        assert result['items'][0]['status'] == 'failed'
        assert 'MorningStar Technical Deepdive' in str(result['items'][0])
        page_two = client.get('/api/console/history', params={**params, 'page': 2}).json()
        assert page_two['meta']['total'] == 1 and page_two['items'] == []
        no_match = client.get('/api/console/history', params={**params, 'q': 'unrelated subject'}).json()
        assert no_match['meta']['total'] == 0 and no_match['items'] == []
        wrong_status = client.get('/api/console/history', params={**params, 'status': 'done'}).json()
        assert wrong_status['meta']['total'] == 0 and wrong_status['items'] == []
