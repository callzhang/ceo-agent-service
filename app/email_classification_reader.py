"""Source-backed Console classification view; originals stay request-local."""

import imaplib

from app.email_classifier_contracts import EmailClassificationStatus


class EmailClassificationReadError(LookupError):
    """A complete source-backed classification response could not be read."""


class EmailClassificationReader:
    def __init__(self, store, *, message_reader):
        self.store = store
        self.message_reader = message_reader

    @staticmethod
    def _key(row):
        return row["account_id"], row["stable_message_identity"]

    def _messages(self, rows):
        accounts = {}
        for row in rows:
            account, identity = self._key(row)
            accounts.setdefault(account, []).append(identity)
        try:
            return {
                (account, identity): view
                for account, identities in accounts.items()
                for identity, view in self.message_reader.read_views(
                    account, identities
                ).items()
            }
        except (LookupError, OSError, ValueError, imaplib.IMAP4.error) as exc:
            raise EmailClassificationReadError("email original unavailable") from exc

    def _list(self, *, q, limit, offset, **filters):
        if limit <= 0 or offset < 0:
            raise ValueError("classification pagination is invalid")
        query = q.strip().casefold()
        selection = dict(
            filters, limit=None if query else limit, offset=0 if query else offset
        )
        snapshot = self.store.classification_source_candidates(**selection)
        candidates, candidate_total = snapshot
        page = []
        if not query:
            total = candidate_total
            messages = self._messages(candidates)
            page = [
                {**row, "message_text": messages[self._key(row)]["message_text"]}
                for row in candidates
            ]
        else:
            total = 0
            for start in range(0, len(candidates), limit):
                batch = candidates[start : start + limit]
                body_rows = [
                    row
                    for row in batch
                    if query not in row["sender"].casefold()
                    and query not in row["subject"].casefold()
                ]
                messages = self._messages(body_rows)
                for row in batch:
                    message = messages.get(self._key(row))
                    body = None if message is None else message["message_text"]
                    if body is not None and query not in body.casefold():
                        continue
                    if offset <= total < offset + limit:
                        page.append({**row, "message_text": body})
                    total += 1
            # Metadata hits only need original text if they occur on the page.
            messages = self._messages(
                [row for row in page if row["message_text"] is None]
            )
            for row in page:
                if row["message_text"] is None:
                    row["message_text"] = messages[self._key(row)]["message_text"]
        if self.store.classification_source_candidates(**selection) != snapshot:
            raise EmailClassificationReadError(
                "email classification candidates changed during source read"
            )
        return page, total

    def list_classifications(
        self,
        *,
        status: EmailClassificationStatus | None,
        limit: int,
        offset: int,
        q="",
        category="",
        action_status="",
        source="",
    ):
        return self._list(
            q=q,
            limit=limit,
            offset=offset,
            status=status,
            category=category,
            action_status=action_status,
            source=source,
        )

    def list_unsubscribe_classifications(self, *, limit: int, offset: int, q=""):
        return self._list(
            q=q, limit=limit, offset=offset, status=None, unsubscribe=True
        )

    def get_classification(self, classification_id: int):
        selection = dict(status=None, classification_id=classification_id)
        snapshot = self.store.classification_source_candidates(**selection)
        rows, _ = snapshot
        if not rows:
            return None
        message = self._messages(rows)[self._key(rows[0])]
        if self.store.classification_source_candidates(**selection) != snapshot:
            raise EmailClassificationReadError(
                "email classification changed during source read"
            )
        return {**rows[0], **message}
