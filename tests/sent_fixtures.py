"""Historical Sent-only facts for recovery tests; no external receipt is invented."""

import json

from app.action_storage import SENT_REPLY_SQL
from app.store import AGENT_RUN_SQL, REPLY_TASK_SQL, SentReply
from app import shared_action_lifecycle


def historical_sent_delivery(store, *, agent_run_id, external_action_key,
                             conversation_id, trigger_message_id, reply_text, provider_result,
                             provider_result_json=None, observed_at=None):
    with store._connect() as db:
        db.execute('begin immediate')
        run = db.execute(
            f'''select runs.reply_task_id,reply_tasks.business_object_key
                from ({AGENT_RUN_SQL}) runs join ({REPLY_TASK_SQL}) reply_tasks
                  on reply_tasks.id=runs.reply_task_id where runs.id=?''',
            (agent_run_id,),
        ).fetchone()
        existing = db.execute(
            f"select * from ({SENT_REPLY_SQL}) where external_action_key=? and external_action_key<>''",
            (external_action_key,),
        ).fetchone()
        if existing is not None:
            conversation_id = existing['conversation_id']
            trigger_message_id = existing['trigger_message_id']
            reply_text = existing['reply_text']
            action = db.execute(
                "select action_business_object_key,action_action_identity,action_parameters_json "
                "from actions where id=? and record_type='action'", (existing['id'],),
            ).fetchone()
            business_object_key = action['action_business_object_key']
            action_identity = action['action_action_identity']
            target = action['action_parameters_json']
        else:
            business_object_key = run['business_object_key']
            action_identity = None
            target = None
        provider_json = (provider_result_json if provider_result_json is not None else
                         json.dumps(provider_result, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')))
        common = shared_action_lifecycle.record_sent_action(
            db, operation=None, target_json=target,
            provider_json=provider_json, result_digest=None,
            external_action_key=external_action_key,
            business_object_key=business_object_key,
            action_identity=action_identity, channel='dingtalk',
            conversation_id=conversation_id, trigger_message_id=trigger_message_id,
            reply_text=reply_text, feedback_token='', agent_run_id=agent_run_id,
            task_id=run['reply_task_id'], observed_at=observed_at,
        )
        row = db.execute(f'select * from ({SENT_REPLY_SQL}) where id=?',
                         (common['action']['id'],)).fetchone()
        return SentReply.model_validate(dict(row))
