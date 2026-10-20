"""Historical Sent-only facts for recovery tests; no external receipt is invented."""

import json

from app.action_storage import SENT_REPLY_SQL, insert_action
from app.run_storage import AGENT_RUN_SQL
from app.store import SentReply


def historical_sent_delivery(store, *, agent_run_id, external_action_key,
                             conversation_id, trigger_message_id, reply_text, provider_result):
    with store._connect() as db:
        task_id = db.execute(f'select reply_task_id from ({AGENT_RUN_SQL}) where id=?', (agent_run_id,)).fetchone()[0]
        insert_action(db, 'sent_replies', dict(
            agent_run_id=agent_run_id, external_action_key=external_action_key,
            conversation_id=conversation_id, trigger_message_id=trigger_message_id,
            reply_text=reply_text, send_result_json=json.dumps(provider_result, ensure_ascii=False, sort_keys=True, separators=(',', ':'))), ignore=True)
        row = db.execute(f"select * from ({SENT_REPLY_SQL}) where external_action_key=? and external_action_key<>''", (external_action_key,)).fetchone()
        insert_action(db, 'sent_reply_observers', dict(sent_reply_id=row['id'], agent_run_id=agent_run_id, reply_task_id=task_id), ignore=True)
        return SentReply.model_validate(dict(row))
