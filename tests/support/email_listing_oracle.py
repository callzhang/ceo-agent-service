"""Old stored-body SQL semantics, frozen as a test-only comparison oracle.

Production uses metadata plus exact external sources. This class reads only
synthetic test fixtures; it is never imported by application code.
"""
from email.parser import Parser
import sqlite3
from typing import Any

from app.email_classifier_contracts import EmailClassificationStatus
from app.email_store import (
    EmailStore, EMAIL_CLASSIFICATION_SQL, EMAIL_MESSAGE_SQL, _display_message_body,
)


class StoredEmailListingOracle(EmailStore):
    def __init__(self, store):
        self.path = store.path
        self._connect = store._connect

    def get_classification(self, classification_id):
        item = super().get_classification(classification_id)
        if item is None:
            return None
        with self._connect() as db:
            row = db.execute(
                "select message_normalized_text from emails where classification_id=?",
                (classification_id,),
            ).fetchone()
        parsed = Parser().parsestr(row["message_normalized_text"] or "")
        return item | {"message_text": _display_message_body(parsed), "cc": parsed.get("Cc", "")}

    def list_classifications(
        self,
        *,
        status: EmailClassificationStatus | None,
        limit: int,
        offset: int,
        q: str = "",
        category: str = "",
        action_status: str = "",
        source: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of classifications, newest first, optionally filtered.

        `status=None` lists everything the owner sees on the "all" tab: mail
        that is processed and mail waiting for a label, as one ordered list.
        """

        with self._connect() as db:
            search, search_params = self._classification_search(db, q)
            filters, filter_params = self._classification_filters(
                category=category, action_status=action_status, source=source
            )
            statuses = (
                (EmailClassificationStatus.PROCESSED, EmailClassificationStatus.PENDING_FEEDBACK)
                if status is None
                else (status,)
            )
            where = (
                "classifications.status in ("
                + ",".join("?" for _ in statuses)
                + ")"
                + search
                + filters
            )
            params = (*(item.value for item in statuses), *search_params, *filter_params)
            total = int(
                db.execute(
                    f"select count(*) from ({EMAIL_CLASSIFICATION_SQL}) as classifications where "
                    + where,
                    params,
                ).fetchone()[0]
            )
            rows = db.execute(
                f"""
                select classifications.*,
                       messages.normalized_text as message_text,
                       messages.attachment_metadata_json
                           as message_attachment_metadata_json
                from ({EMAIL_CLASSIFICATION_SQL}) as classifications
                left join ({EMAIL_MESSAGE_SQL}) as messages
                  on messages.account_id=classifications.account_id
                 and messages.stable_message_identity=
                     classifications.stable_message_identity
                where """ + where + """
                order by classifications.updated_at desc, classifications.id desc
                limit ? offset ?
                """,
                (*params, limit, offset),
            ).fetchall()
            plan_rows = self._current_action_plan_rows(db, rows)
        items = []
        for row in rows:
            message = Parser().parsestr(row["message_text"] or "")
            item = self._classification_evidence_row(row, plan_rows)
            item["message_text"] = _display_message_body(message)
            items.append(item)
        return items, total


    @staticmethod
    def _classification_search(db: sqlite3.Connection, q: str) -> tuple[str, tuple[str, ...]]:
        query = q.strip().casefold()
        if not query:
            return "", ()
        db.create_function("email_casefold", 1, lambda value: (value or "").casefold(), deterministic=True)
        db.create_function("email_visible_body", 1, lambda value: _display_message_body(Parser().parsestr(value or "")).casefold(), deterministic=True)
        return f"""
            and (
                instr(email_casefold(classifications.sender), ?) > 0
                or instr(email_casefold(classifications.subject), ?) > 0
                or exists (
                    select 1 from ({EMAIL_MESSAGE_SQL}) as search_message
                    where search_message.account_id=classifications.account_id
                      and search_message.stable_message_identity=classifications.stable_message_identity
                      and instr(email_visible_body(search_message.normalized_text), ?) > 0
                )
            )
        """, (query, query, query)


    def list_unsubscribe_classifications(
        self, *, limit: int, offset: int, q: str = ""
    ) -> tuple[list[dict[str, Any]], int]:
        """List every email with a durable unsubscribe lifecycle record.

        A claim exists once Audit has accepted an effect; a receipt exists for
        a terminal outcome.  Queued email tasks are included when the shared
        task tables are available, so the Console can expose work before Audit
        reaches the browser phase.
        """
        if limit <= 0 or offset < 0:
            raise ValueError("unsubscribe list pagination is invalid")
        with self._connect() as db:
            search, search_params = self._classification_search(db, q)
            where = self._unsubscribe_classification_membership(db) + search
            total = int(
                db.execute(
                    f"select count(*) from ({EMAIL_CLASSIFICATION_SQL}) as classifications "
                    f"where {where}", search_params
                ).fetchone()[0]
            )
            rows = db.execute(
                f"""
                select classifications.*, messages.normalized_text as message_text,
                       messages.attachment_metadata_json
                           as message_attachment_metadata_json
                from ({EMAIL_CLASSIFICATION_SQL}) as classifications
                left join ({EMAIL_MESSAGE_SQL}) as messages
                  on messages.account_id=classifications.account_id
                 and messages.stable_message_identity=
                     classifications.stable_message_identity
                where """ + where + """
                order by classifications.updated_at desc, classifications.id desc
                limit ? offset ?
                """,
                (*search_params, limit, offset),
            ).fetchall()
            plan_rows = self._current_action_plan_rows(db, rows)
        items = []
        for row in rows:
            message = Parser().parsestr(row["message_text"] or "")
            item = self._classification_evidence_row(row, plan_rows)
            item["message_text"] = _display_message_body(message)
            items.append(item)
        return items, total
