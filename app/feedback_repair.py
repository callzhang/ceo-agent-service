"""Feed user feedback into the existing native repair coordinator."""

import logging
import time
from urllib.parse import quote

from app.automatic_repair_store import enqueue_in_transaction, save_repair
from app.consumer_purpose_retry import utc_time
from app.feedback_events import sync_feedback_events_for_sent_replies
from app.feedback_processing import FeedbackIterationDisabledError, FeedbackProcessingClaimError
from app.store import ManualRerunConflict

SOURCE = 'user_feedback'
BATCH_PREFIX = 'automatic-feedback:'
_SYNC_CURSOR = 'automatic-feedback-sync-cursor'
_LOGGER = logging.getLogger(__name__)


class FeedbackRepairQueue:
    def __init__(
        self, store, *, sync=sync_feedback_events_for_sent_replies,
        clock=time.monotonic, initial_delay_seconds=0,
    ):
        self.store = store
        self.sync = sync
        self.clock = clock
        self.next_intake = clock() + initial_delay_seconds

    def prepare(self, *, active):
        """Recover claims first, then claim at most one more unhandled item."""
        if self.clock() < self.next_intake:
            return
        self.next_intake = self.clock() + 60
        try:
            cursor = int(self.store.get_service_state(_SYNC_CURSOR) or 0)
            page = self.store.list_sent_replies_waiting_for_feedback_events(
                limit=10, before_id=cursor or None, ordered_by_id=True,
            )
            self.sync(self.store, page, timeout_seconds=0.5, limit_per_token=5, configured_only=True)
            self.store.set_service_state(_SYNC_CURSOR, str(page[-1].id if len(page) == 10 else 0))
        except Exception:
            _LOGGER.exception('Automatic user feedback sync failed')
        with self.store._connect() as db:
            pending = db.execute("""
                select item.feedback_key, item.task_id,
                       (select coalesce(max(round_number),0) + 1 from feedback_processing_rounds
                        where feedback_key=item.feedback_key) as next_round
                from feedback_processing_items item join tasks task on task.id=item.task_id
                where task.status='pending'
                order by task.created_at desc, task.id desc limit 1
                """).fetchone()
            claimed = db.execute("""
                select round.id
                from feedback_processing_items item join feedback_processing_rounds round
                  on round.id=item.current_round_id
                where round.status='processing' and round.batch_id like ?
                  and not exists (select 1 from state
                                  where record_type='service_state' and key=? || round.id)
                order by round.id limit 1
                """, (BATCH_PREFIX + '%', f'automatic_repair:{SOURCE}:')).fetchone()
        if claimed is None and not active and pending is not None and self.store.feedback_iteration_enabled():
            batch = f"{BATCH_PREFIX}{pending['task_id']}:{pending['next_round']}"
            try:
                items = self.store.claim_feedback_processing_items(batch, [pending['feedback_key']])
            except (FeedbackIterationDisabledError, FeedbackProcessingClaimError):
                return
            claimed = (items[0].current_round_id,) if items else None
        if claimed is not None:
            with self.store._immediate_write_transaction() as db:
                enqueue_in_transaction(db, SOURCE, claimed[0])

    def binding(self, entry):
        with self.store._connect() as db:
            return db.execute("""
                select round.feedback_key, round.batch_id, round.workbench_turn_id
                from feedback_processing_rounds round
                join feedback_processing_items item on item.current_round_id=round.id
                join tasks task on task.id=item.task_id
                where round.id=? and round.status='processing' and task.status='processing'
                  and round.batch_id like ?
                """, (entry['source_id'], BATCH_PREFIX + '%')).fetchone()

    def context(self, entry):
        row = self.binding(entry)
        if row is None:
            return None
        feedback = self.store.list_user_feedback_items_by_keys([row['feedback_key']])
        if not feedback:
            return None
        item = feedback[0]
        path = f"/api/console/feedback/{quote(item.key, safe='')}"
        return path, item.attempt_id, item.received_at or item.updated_at

    def associate(self, entry):
        row = self.binding(entry)
        if row is not None and entry['turn_ids'] and row['workbench_turn_id'] != entry['turn_ids'][-1]:
            self.store.associate_feedback_processing_turn(
                row['feedback_key'], expected_batch_id=row['batch_id'],
                workbench_task_id=entry['task_id'], workbench_turn_id=entry['turn_ids'][-1],
            )

    def advance_business(self, entry):
        """Use the source-revision retry and observe the exact task's receipts."""
        retry = entry['business_retry']
        attempt = self.store.get_reply_attempt(retry['attempt_id'])
        if attempt is None:
            self._finish(entry, 'blocked', '找不到用户反馈关联的原处理记录。')
            return
        task = self.store.get_reply_task_for_message(
            attempt.conversation_id, attempt.trigger_message_id, channel=attempt.channel,
        )
        if task is None:
            self._finish(entry, 'blocked', '找不到与原消息绑定的业务任务。')
            return
        revision_key = f"{BATCH_PREFIX}revision:{entry['source_id']}"
        sent = self.store.get_sent_reply(
            attempt.conversation_id, attempt.trigger_message_id, channel=attempt.channel,
        )
        if sent is not None:
            if task.status in {'pending', 'processing'}:
                entry.update(next_readback_at=time.time() + 30)
                save_repair(self.store, entry)
                return
            after_feedback = retry.get('received_at') and self._sent_after_feedback(sent.sent_at, retry['received_at'])
            if task.status == 'done' and sent.provider_receipt_id and (
                task.manual_rerun_revision_key == revision_key or after_feedback
            ):
                self._finish(
                    entry, 'business_delivered',
                    f'原任务完成，已核验本次反馈之后的发送回执（发送记录 {sent.id}）。',
                )
            else:
                self._finish(
                    entry, 'blocked',
                    '原消息已有发送记录；本轮没有独立核验的新发送回执。',
                )
            return
        if task.manual_rerun_revision_key == revision_key:
            if task.status in {'pending', 'processing'}:
                entry.update(next_readback_at=time.time() + 30)
                save_repair(self.store, entry)
            else:
                self._finish(
                    entry, 'blocked',
                    f'原任务续修已结束（{task.status}）；尚无发送回执。{task.error}',
                )
            return
        if task.status in {'pending', 'processing'}:
            entry.update(next_readback_at=time.time() + 30)
            save_repair(self.store, entry)
            return
        try:
            queued = self.store.requeue_failed_reply_task_for_source_revision(
                task.id, expected_execution_generation=task.execution_generation,
                source_revision_key=revision_key, source_attempt_id=attempt.id,
                reviewer_feedback=retry['diagnosis'],
            )
        except ManualRerunConflict:
            queued = False
        if not queued:
            self._finish(entry, 'blocked', '原任务当前不符合未发送任务的续修条件。')
            return
        entry.update(summary='已交回原任务，等待独立审核和发送回执。', next_readback_at=time.time() + 30)
        save_repair(self.store, entry)

    @staticmethod
    def _sent_after_feedback(sent_at, received_at):
        try:
            return utc_time(sent_at) > utc_time(received_at)
        except ValueError:
            return False

    def _finish(self, entry, status, summary):
        entry.update(status=status, summary=summary)
        save_repair(self.store, entry)
