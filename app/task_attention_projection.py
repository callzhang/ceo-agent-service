"""Persisted, evidence-backed business attention projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json

from app.store import AutoReplyStore
from app.task_semantic_models import (
    AttentionCategory,
    AttentionStatus,
    BusinessAttentionEventType,
    BusinessAttentionItem,
    BusinessRelevance,
    BusinessRelationStatus,
    BusinessTask,
    BusinessTaskStatus,
)


@dataclass(frozen=True)
class AttentionProposal:
    stable_key: str
    category: AttentionCategory
    title: str
    business_area: str
    why_attention: str
    current_state: str
    ceo_action: str
    anchor_id: int
    task_ids: tuple[int, ...]
    evidence_signal_id: int


class BusinessAttentionProjection:
    """Creates the CEO-facing projection from persisted business Task truth."""

    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _nonblank(value: str, *, field: str) -> None:
        if not value.strip():
            raise ValueError(f"{field} must not be blank")

    @staticmethod
    def _snapshot(item: BusinessAttentionItem, task_ids: tuple[int, ...] = ()) -> str:
        payload = item.model_dump(mode="json")
        payload["task_ids"] = list(sorted(task_ids))
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _item_changed(item: BusinessAttentionItem, proposal: AttentionProposal) -> bool:
        return any(
            (
                item.category is not proposal.category,
                item.title != proposal.title,
                item.business_area != proposal.business_area,
                item.why_attention != proposal.why_attention,
                item.current_state != proposal.current_state,
                item.ceo_action != proposal.ceo_action,
                item.anchor_id != proposal.anchor_id,
                item.evidence_signal_id != proposal.evidence_signal_id,
            )
        )

    def _validate_proposal(self, proposal: AttentionProposal, *, db) -> tuple[BusinessTask, ...]:
        for value, field in (
            (proposal.stable_key, "stable key"),
            (proposal.title, "attention title"),
            (proposal.why_attention, "why attention"),
            (proposal.current_state, "current state"),
            (proposal.ceo_action, "CEO action"),
        ):
            self._nonblank(value, field=field)
        if not proposal.task_ids:
            raise ValueError("attention requires at least one Task")
        if len(set(proposal.task_ids)) != len(proposal.task_ids):
            raise ValueError("attention Task IDs must be unique")
        signal = db.execute(
            "select 1 from business_task_signals where id=?", (proposal.evidence_signal_id,)
        ).fetchone()
        if signal is None:
            raise ValueError("attention evidence signal does not exist")
        anchor = db.execute(
            "select active from business_anchors where id=?", (proposal.anchor_id,)
        ).fetchone()
        if anchor is None:
            raise ValueError("attention anchor does not exist")
        if not bool(anchor["active"]):
            raise ValueError("attention anchor must be active")

        tasks: list[BusinessTask] = []
        confirmed_anchor = False
        evidence_linked = False
        for task_id in proposal.task_ids:
            task = self.store.get_business_task_in_transaction(task_id=task_id, _db=db)
            if task is None:
                raise ValueError(f"business task {task_id} does not exist")
            if task.status is BusinessTaskStatus.MERGED:
                raise ValueError("merged Task is not eligible for attention")
            tasks.append(task)
            confirmed_anchor = confirmed_anchor or db.execute(
                """select 1 from business_task_anchor_links
                   where task_id=? and anchor_id=? and status=? and active=1""",
                (task_id, proposal.anchor_id, BusinessRelationStatus.CONFIRMED.value),
            ).fetchone() is not None
            evidence_linked = evidence_linked or db.execute(
                "select 1 from business_task_evidence where task_id=? and signal_id=?",
                (task_id, proposal.evidence_signal_id),
            ).fetchone() is not None
        if not any(task.business_relevance is BusinessRelevance.RELEVANT for task in tasks):
            raise ValueError("attention requires at least one relevant Task")
        if not confirmed_anchor:
            raise ValueError("attention anchor must be confirmed for an underlying Task")
        if not evidence_linked:
            raise ValueError("attention evidence signal must be linked to an underlying Task")
        return tuple(tasks)

    def upsert(self, proposal: AttentionProposal) -> int:
        with self.store.business_task_transaction() as db:
            self._validate_proposal(proposal, db=db)
            existing = self.store.get_business_attention_item_by_stable_key_in_transaction(
                stable_key=proposal.stable_key, _db=db
            )
            now = self._timestamp()
            if existing is None:
                item_id = self.store.create_business_attention_item_in_transaction(
                    stable_key=proposal.stable_key, category=proposal.category, title=proposal.title,
                    business_area=proposal.business_area, why_attention=proposal.why_attention,
                    current_state=proposal.current_state, ceo_action=proposal.ceo_action,
                    anchor_id=proposal.anchor_id, evidence_signal_id=proposal.evidence_signal_id,
                    now=now, _db=db,
                )
                item = self.store.get_business_attention_item_in_transaction(item_id=item_id, _db=db)
                assert item is not None
                self.store.replace_business_attention_tasks_in_transaction(
                    attention_item_id=item_id, task_ids=proposal.task_ids, _db=db
                )
                self.store.append_business_attention_event_in_transaction(
                    attention_item_id=item_id, event_type=BusinessAttentionEventType.OPENED,
                    signal_id=proposal.evidence_signal_id, before_json="{}",
                    after_json=self._snapshot(item, proposal.task_ids),
                    reason="Business attention opened", _db=db,
                )
            else:
                item_id = existing.id
                before_task_ids = tuple(
                    link.task_id for link in self.store.list_business_attention_tasks_in_transaction(
                        attention_item_id=item_id, _db=db
                    )
                )
                membership_changed = before_task_ids != tuple(sorted(proposal.task_ids))
                fields_changed = self._item_changed(existing, proposal)
                if fields_changed or membership_changed:
                    updated = existing.model_copy(
                        update={
                            "category": proposal.category,
                            "status": AttentionStatus.ACTIVE if fields_changed else existing.status,
                            "title": proposal.title, "business_area": proposal.business_area,
                            "why_attention": proposal.why_attention,
                            "current_state": proposal.current_state, "ceo_action": proposal.ceo_action,
                            "anchor_id": proposal.anchor_id,
                            "evidence_signal_id": proposal.evidence_signal_id,
                            "resolution_signal_id": None if fields_changed else existing.resolution_signal_id,
                            "resolved_at": "" if fields_changed else existing.resolved_at,
                            "updated_at": now,
                        }
                    )
                    self.store.update_business_attention_item_in_transaction(item=updated, _db=db)
                    event_type = (
                        BusinessAttentionEventType.REOPENED
                        if existing.status is AttentionStatus.RESOLVED
                        else BusinessAttentionEventType.CATEGORY_CHANGED
                        if existing.category is not proposal.category
                        else BusinessAttentionEventType.UPDATED
                    )
                    self.store.replace_business_attention_tasks_in_transaction(
                        attention_item_id=item_id, task_ids=proposal.task_ids, _db=db
                    )
                    self.store.append_business_attention_event_in_transaction(
                        attention_item_id=item_id, event_type=event_type,
                        signal_id=proposal.evidence_signal_id,
                        before_json=self._snapshot(existing, before_task_ids),
                        after_json=self._snapshot(updated, proposal.task_ids),
                        reason="Business attention updated", _db=db,
                    )
            return item_id

    def resolve(self, *, item_id: int, resolution_signal_id: int, reason: str) -> None:
        self._nonblank(reason, field="resolution reason")
        with self.store.business_task_transaction() as db:
            item = self.store.get_business_attention_item_in_transaction(item_id=item_id, _db=db)
            if item is None:
                raise ValueError("business attention item does not exist")
            if db.execute(
                "select 1 from business_task_signals where id=?", (resolution_signal_id,)
            ).fetchone() is None:
                raise ValueError("resolution evidence signal does not exist")
            if db.execute(
                """select 1 from business_attention_tasks attention
                   join business_task_evidence evidence on evidence.task_id=attention.task_id
                   where attention.attention_item_id=? and evidence.signal_id=?""",
                (item_id, resolution_signal_id),
            ).fetchone() is None:
                raise ValueError("resolution evidence signal must be linked to an underlying Task")
            if item.status is AttentionStatus.RESOLVED:
                if item.resolution_signal_id == resolution_signal_id:
                    return
                raise ValueError("attention item is already resolved with different evidence")
            updated = item.model_copy(
                update={
                    "status": AttentionStatus.RESOLVED, "resolution_signal_id": resolution_signal_id,
                    "resolved_at": self._timestamp(), "updated_at": self._timestamp(),
                }
            )
            self.store.update_business_attention_item_in_transaction(item=updated, _db=db)
            self.store.append_business_attention_event_in_transaction(
                attention_item_id=item_id, event_type=BusinessAttentionEventType.RESOLVED,
                signal_id=resolution_signal_id, before_json=self._snapshot(item),
                after_json=self._snapshot(updated), reason=reason, _db=db,
            )

    def record_viewed(self, *, item_id: int, viewed_at: str) -> None:
        """A read is intentionally non-authoritative and leaves the projection unchanged."""
        self._nonblank(viewed_at, field="viewed at")
        if self.store.get_business_attention_item(item_id) is None:
            raise ValueError("business attention item does not exist")

    def recompute_for_tasks(self, task_ids: tuple[int, ...]) -> tuple[int, ...]:
        """Refresh membership of previously proposed attention items only."""
        if not task_ids:
            return ()
        requested = tuple(sorted(set(task_ids)))
        item_ids: list[int] = []
        with self.store.business_task_transaction() as db:
            rows = db.execute(
                f"""select distinct item.* from business_attention_items item
                    join business_attention_tasks member on member.attention_item_id=item.id
                    where member.task_id in ({', '.join('?' for _ in requested)}) order by item.id""",
                requested,
            ).fetchall()
            for row in rows:
                item = BusinessAttentionItem.model_validate(dict(row))
                before_task_ids = tuple(
                    link.task_id for link in self.store.list_business_attention_tasks_in_transaction(
                        attention_item_id=item.id, _db=db
                    )
                )
                eligible_task_ids: list[int] = []
                for task_id in before_task_ids:
                    task = self.store.get_business_task_in_transaction(task_id=task_id, _db=db)
                    if (
                        task is not None
                        and task.status in (BusinessTaskStatus.OPEN, BusinessTaskStatus.WAITING)
                        and task.business_relevance is BusinessRelevance.RELEVANT
                        and db.execute(
                            """select 1 from business_task_anchor_links link
                               join business_anchors anchor on anchor.id=link.anchor_id
                               where link.task_id=? and link.anchor_id=? and link.status='confirmed'
                                 and link.active=1 and anchor.active=1""",
                            (task_id, item.anchor_id),
                        ).fetchone() is not None
                    ):
                        eligible_task_ids.append(task_id)
                after_task_ids = tuple(eligible_task_ids)
                next_state = f"{len(after_task_ids)} 项开放任务"
                if before_task_ids == after_task_ids and item.current_state == next_state:
                    item_ids.append(item.id)
                    continue
                updated = item.model_copy(
                    update={"current_state": next_state, "updated_at": self._timestamp()}
                )
                self.store.update_business_attention_item_in_transaction(item=updated, _db=db)
                self.store.replace_business_attention_tasks_in_transaction(
                    attention_item_id=item.id, task_ids=after_task_ids, _db=db
                )
                self.store.append_business_attention_event_in_transaction(
                    attention_item_id=item.id, event_type=BusinessAttentionEventType.UPDATED,
                    signal_id=item.evidence_signal_id,
                    before_json=self._snapshot(item, before_task_ids),
                    after_json=self._snapshot(updated, after_task_ids),
                    reason="Business attention membership recomputed", _db=db,
                )
                item_ids.append(item.id)
        return tuple(item_ids)
