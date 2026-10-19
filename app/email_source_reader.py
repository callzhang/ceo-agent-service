"""Accurate Email original reads outside SQLite transactions, with no cache."""

import json

from app.email_classifier_contracts import EmailProviderLocator


def build_email_source_reader(store, *, environment_factory):
    from app.email_account_connector import ConnectorPriority
    from app.email_worker import (
        _build_email_connector_registry,
        _build_registry_source_factory,
    )

    registry = _build_email_connector_registry(
        store, environment_factory=environment_factory
    )
    return EmailSourceReader(
        store,
        source_factory=_build_registry_source_factory(registry, ConnectorPriority.HIGH),
    )


class EmailSourceReader:
    def __init__(self, store, *, source_factory):
        self.store = store
        self.source_factory = source_factory

    def read_view(
        self, account_id: str, stable_message_identity: str
    ) -> dict[str, object]:
        return self.read_views(account_id, [stable_message_identity])[
            stable_message_identity
        ]

    def read_views(
        self, account_id: str, stable_message_identities: list[str]
    ) -> dict[str, dict[str, object]]:
        return {
            identity: self._view(message)
            for identity, message in self.read_many(
                account_id, stable_message_identities
            ).items()
        }

    @staticmethod
    def _view(message) -> dict[str, object]:
        """Existing Console text/recipient fields from an accurate source read."""
        from email.parser import Parser
        from app.email_classifier_scan import (
            _normalized_message_text,
            _named_recipients,
        )
        from app.email_store import _display_message_body

        parsed = Parser().parsestr(_normalized_message_text(message))
        return {
            "message_text": _display_message_body(parsed),
            "cc": parsed.get("Cc", ""),
            "recipients": list(_named_recipients(message, "toRecipients")),
        }

    def read(self, account_id: str, stable_message_identity: str) -> dict[str, object]:
        return self.read_many(account_id, [stable_message_identity])[
            stable_message_identity
        ]

    def read_many(
        self, account_id: str, stable_message_identities: list[str]
    ) -> dict[str, dict[str, object]]:
        """Read a finite account batch through one source lease; retain no cache."""
        identities = list(dict.fromkeys(stable_message_identities))
        if not identities:
            return {}
        keys = [(account_id, identity) for identity in identities]
        locations = self.store.get_email_source_locations(keys)
        if any(key not in locations for key in keys):
            raise LookupError("email source location is unavailable")
        account = self.store.get_account(account_id)
        if account is None:
            raise LookupError("email source account is unavailable")
        source = self.source_factory(account)
        try:
            messages = self._read_locations(source, account_id, locations, identities)
            from app.email_body_source import original_from_message

            for identity, message in messages.items():
                reference = locations[(account_id, identity)].get("message_source_reference_json")
                if reference is not None:
                    reference = json.loads(reference)
                    if reference.get('kind') is not None:
                        original_from_message(message, reference)
        finally:
            source.logout()
        if self.store.get_email_source_locations(keys) != locations:
            raise LookupError("email source location changed during read")
        return messages

    @classmethod
    def _read_locations(cls, source, account_id, locations, identities):
        if len(identities) == 1:
            identity = identities[0]
            return {identity: cls._read_location(source, locations[(account_id, identity)], identity)}
        resolved = {identity: cls._resolve_location(source, locations[(account_id, identity)])
                    for identity in identities}
        try:
            messages = source.fetch_messages([(resolved[identity][0], identity) for identity in identities])
        except LookupError as exc:
            messages = dict.fromkeys(identities, exc)
        for identity in identities:
            message = messages[identity]
            if isinstance(message, LookupError):
                locator, same_folder_lookup, folder = resolved[identity]
                if not same_folder_lookup:
                    raise message
                current = source.locate_message(locator, folder=folder)
                messages[identity] = source.fetch_message(current, stable_message_identity=identity)
        return messages

    @staticmethod
    def _resolve_location(source, location):
        locator = EmailProviderLocator(
            account_id=location["account_id"],
            folder=location["folder"],
            uidvalidity=location["uidvalidity"],
            uid=location["uid"],
            rfc_message_id=location["rfc_message_id"] or None,
            thread_id=location["thread_identity"] or None,
        )
        folder = (
            location["provider_folder_name"]
            if location["state"] == "available"
            else None
        )
        same_folder_lookup = (
            folder == locator.folder and locator.rfc_message_id is not None
        )
        if folder and folder != locator.folder:
            locator = source.locate_message(locator, folder=folder)
        return locator, same_folder_lookup, folder

    @classmethod
    def _read_location(cls, source, location, stable_message_identity):
        locator, same_folder_lookup, folder = cls._resolve_location(source, location)
        try:
            return source.fetch_message(
                locator, stable_message_identity=stable_message_identity
            )
        except LookupError:
            if not same_folder_lookup:
                raise
            current = source.locate_message(locator, folder=folder)
            return source.fetch_message(
                current, stable_message_identity=stable_message_identity
            )
