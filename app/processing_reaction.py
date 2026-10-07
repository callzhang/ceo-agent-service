"""Service-owned temporary DingTalk progress reactions."""
from __future__ import annotations

import hashlib
import json
import logging
from threading import RLock

logger = logging.getLogger(__name__)
TEMPLATE_KEY = 'dingtalk_processing_reaction_template'
SOURCE_PREFIX = 'dingtalk_processing_reaction:'
_LOCK = RLock()


class ProcessingReaction:
    def __init__(self, store, dws):
        self.store = store
        self.dws = dws

    @staticmethod
    def _key(conversation_id, message_id):
        source = json.dumps([conversation_id, message_id], ensure_ascii=False)
        return SOURCE_PREFIX + hashlib.sha256(source.encode()).hexdigest()

    @staticmethod
    def _require_success(result):
        if not isinstance(result, dict) or result.get('success') is not True:
            raise RuntimeError('DingTalk progress reaction was not confirmed')

    def _template(self):
        saved = self.store.get_service_state(TEMPLATE_KEY)
        if saved:
            return json.loads(saved)
        result = self.dws.create_message_text_emotion(text='处理中', emotion_name='处理中')
        self._require_success(result)
        template = {
            'text': '处理中', 'emotion_name': '处理中',
            'emotion_id': str(result['result']['emotionId']),
            'background_id': str(result['result']['backgroundId']),
        }
        self.store.set_service_state(TEMPLATE_KEY, json.dumps(template, ensure_ascii=False))
        return template

    def start(self, conversation_id, message_id):
        """Persist add intent first; failed cosmetics never fail a business task."""
        try:
            with _LOCK:
                task = self.store.get_reply_task_for_message(conversation_id, message_id)
                if task is None or task.status not in {'pending', 'processing'}:
                    return
                if self.store.has_sent_reply_for_trigger(conversation_id, message_id):
                    return
                key = self._key(conversation_id, message_id)
                saved = self.store.get_service_state(key)
                state = json.loads(saved) if saved else {
                    'conversation_id': conversation_id, 'message_id': message_id,
                    'template': self._template(), 'added': False, 'removing': False,
                }
                if state['added'] or state['removing']:
                    return
                self.store.set_service_state(key, json.dumps(state, ensure_ascii=False))
                result = self.dws.add_message_text_emotion(conversation_id, message_id,
                                                         **state['template'])
                self._require_success(result)
                state['added'] = True
                self.store.set_service_state(key, json.dumps(state, ensure_ascii=False))
        except Exception:
            logger.warning('Could not add processing reaction for %s', message_id, exc_info=True)

    def finish(self, conversation_id, message_id):
        """Keep removal intent until provider confirmation, including across restart."""
        try:
            with _LOCK:
                key = self._key(conversation_id, message_id)
                saved = self.store.get_service_state(key)
                if not saved:
                    return
                state = json.loads(saved)
                state['removing'] = True
                self.store.set_service_state(key, json.dumps(state, ensure_ascii=False))
                result = self.dws.remove_message_text_emotion(conversation_id, message_id,
                                                            **state['template'])
                self._require_success(result)
                with self.store._connect() as db:
                    db.execute('delete from service_state where key=?', (key,))
        except Exception:
            logger.warning('Could not remove processing reaction for %s', message_id, exc_info=True)

    def sync(self):
        """Recover progress using existing delivery/task facts on normal passes."""
        try:
            with _LOCK:
                with self.store._connect() as db:
                    rows = db.execute('select value from service_state where key like ?',
                                      (SOURCE_PREFIX + '%',)).fetchall()
                for row in rows:
                    state = json.loads(row['value'])
                    cid, mid = state['conversation_id'], state['message_id']
                    task = self.store.get_reply_task_for_message(cid, mid)
                    if (state['removing'] or task is None
                            or task.status not in {'pending', 'processing'}
                            or self.store.has_sent_reply_for_trigger(cid, mid)):
                        self.finish(cid, mid)
                    elif not state['added']:
                        self.start(cid, mid)
        except Exception:
            logger.warning('Could not refresh processing reactions', exc_info=True)
