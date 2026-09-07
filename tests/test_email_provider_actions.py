from __future__ import annotations

from importlib import import_module
import ssl
from typing import Mapping

import pytest

from app.email_classifier_contracts import EmailAction
from app.email_provider_folders import FolderRole, ProviderFolder


class StatefulFakeImapProvider:
    def __init__(
        self,
        *,
        labels: set[str] | None = None,
        is_read: bool = False,
        archived: bool = False,
        folder: str = "INBOX",
        trashed: bool = False,
        ignore_write: bool = False,
        timeout_after_write: bool = False,
        fail_reads: set[int] | None = None,
    ) -> None:
        self.labels = set(labels or ())
        self.is_read = is_read
        self.archived = archived
        self.folder = folder
        self.trashed = trashed
        self.ignore_write = ignore_write
        self.timeout_after_write = timeout_after_write
        self.fail_reads = set(fail_reads or ())
        self.read_count = 0
        self.revision = 0
        self.command_log: list[str] = []

    def read_state(self, locator: object) -> object:
        module = import_module("app.email_provider_actions")
        self.command_log.append("READ")
        self.read_count += 1
        if self.read_count in self.fail_reads:
            raise TimeoutError("provider read timed out")
        return module.ProviderMessageState(
            revision=f"revision-{self.revision}",
            labels=frozenset(self.labels),
            is_read=self.is_read,
            archived=self.archived,
            folder=self.folder,
            trashed=self.trashed,
        )

    def apply(
        self,
        locator: object,
        action_type: EmailAction,
        parameters: object,
    ) -> None:
        operations = {
            EmailAction.LABEL: "STORE LABELS",
            EmailAction.MARK_READ: "STORE \\Seen",
            EmailAction.ARCHIVE: "MOVE ARCHIVE",
            EmailAction.MOVE: "MOVE",
            EmailAction.TRASH: "MOVE TRASH",
        }
        self.command_log.append(operations[action_type])
        if not self.ignore_write:
            if action_type is EmailAction.LABEL:
                self.labels.update(parameters["labels"])
            elif action_type is EmailAction.MARK_READ:
                self.is_read = True
            elif action_type is EmailAction.ARCHIVE:
                self.archived = True
                self.folder = "Archive"
            elif action_type is EmailAction.MOVE:
                self.folder = parameters["target_folder"]
            elif action_type is EmailAction.TRASH:
                self.trashed = True
                self.folder = "Trash"
            self.revision += 1
        if self.timeout_after_write:
            self.timeout_after_write = False
            raise TimeoutError("provider timed out after accepting the write")


def _action(
    action_type: EmailAction,
    parameters: dict[str, object],
    *,
    attempt_number: int = 1,
) -> object:
    store_module = import_module("app.email_store")
    locator = store_module.StoredEmailLocator(
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=7,
        rfc_message_id="<message@example.com>",
        thread_id=None,
        stable_message_identity="account-1:message-id:<message@example.com>",
    )
    return store_module.StoredEmailAction(
        action_id=f"action-{action_type.value}",
        action_plan_id="plan-1",
        classification_id=1,
        account_id="account-1",
        action_type=action_type,
        parameters=parameters,
        config_version="config-v1",
        locator=locator,
        attempt_number=attempt_number,
        claim_started_at=f"2026-08-30T12:0{attempt_number}:00+00:00",
    )


def test_label_reads_before_write_and_requires_matching_readback() -> None:
    module = import_module("app.email_provider_actions")
    action = _action(EmailAction.LABEL, {"labels": ("work",)})
    provider = StatefulFakeImapProvider()

    result = module.DeterministicEmailActionExecutor(provider).execute(action)

    assert result == module.ProviderActionResult(
        status="done",
        provider_operation="STORE LABELS",
        provider_target=action.locator.stable_message_identity,
        provider_result_id="revision-1",
    )
    assert provider.command_log == ["READ", "STORE LABELS", "READ"]


def test_mark_read_archive_move_and_trash_use_verified_provider_state() -> None:
    module = import_module("app.email_provider_actions")
    cases = (
        (EmailAction.MARK_READ, {}, "STORE \\Seen", "STORE \\Seen"),
        (EmailAction.ARCHIVE, {}, "MOVE ARCHIVE", "MOVE ARCHIVE"),
        (EmailAction.MOVE, {"target_folder": "Projects"}, "MOVE", "MOVE"),
        (EmailAction.TRASH, {}, "move_to_trash", "MOVE TRASH"),
    )

    for action_type, parameters, receipt_operation, provider_command in cases:
        provider = StatefulFakeImapProvider()
        action = _action(action_type, parameters)

        result = module.DeterministicEmailActionExecutor(provider).execute(action)

        assert result.status == "done"
        assert result.provider_operation == receipt_operation
        assert result.provider_result_id == "revision-1"
        assert provider.command_log == ["READ", provider_command, "READ"]


def test_already_satisfied_actions_are_readback_noops() -> None:
    module = import_module("app.email_provider_actions")
    cases = (
        (
            EmailAction.LABEL,
            {"labels": ("work",)},
            StatefulFakeImapProvider(labels={"work"}),
        ),
        (EmailAction.MARK_READ, {}, StatefulFakeImapProvider(is_read=True)),
        (EmailAction.ARCHIVE, {}, StatefulFakeImapProvider(archived=True)),
        (
            EmailAction.MOVE,
            {"target_folder": "Projects"},
            StatefulFakeImapProvider(folder="Projects"),
        ),
        (EmailAction.TRASH, {}, StatefulFakeImapProvider(trashed=True)),
    )

    for action_type, parameters, provider in cases:
        action = _action(action_type, parameters)

        result = module.DeterministicEmailActionExecutor(provider).execute(action)

        assert result == module.ProviderActionResult(
            status="done",
            provider_operation="readback_noop",
            provider_target=action.locator.stable_message_identity,
            provider_result_id="revision-0",
        )
        assert provider.command_log == ["READ"]


def test_timeout_after_write_is_reconciled_by_retry_without_duplicate_write() -> None:
    module = import_module("app.email_provider_actions")
    provider = StatefulFakeImapProvider(timeout_after_write=True)
    executor = module.DeterministicEmailActionExecutor(provider)
    first_action = _action(EmailAction.LABEL, {"labels": ("work",)})

    failed = executor.execute(first_action)
    retried = executor.execute(
        _action(
            EmailAction.LABEL,
            {"labels": ("work",)},
            attempt_number=2,
        )
    )

    assert failed.status == "failed"
    assert failed.provider_operation == "STORE LABELS"
    assert failed.provider_result_id == ""
    assert failed.error == "provider_apply_failed:TimeoutError"
    assert retried.status == "done"
    assert retried.provider_operation == "readback_noop"
    assert retried.provider_result_id == "revision-1"
    assert provider.command_log == ["READ", "STORE LABELS", "READ"]


def test_successful_command_with_readback_mismatch_is_failed() -> None:
    module = import_module("app.email_provider_actions")
    provider = StatefulFakeImapProvider(ignore_write=True)
    action = _action(EmailAction.MOVE, {"target_folder": "Projects"})

    result = module.DeterministicEmailActionExecutor(provider).execute(action)

    assert result == module.ProviderActionResult(
        status="failed",
        provider_operation="MOVE",
        provider_target=action.locator.stable_message_identity,
        provider_result_id="revision-0",
        error="provider_readback_mismatch",
    )


def test_initial_read_failure_returns_failed_result_without_writing() -> None:
    module = import_module("app.email_provider_actions")
    provider = StatefulFakeImapProvider(fail_reads={1})
    action = _action(EmailAction.MARK_READ, {})

    result = module.DeterministicEmailActionExecutor(provider).execute(action)

    assert result == module.ProviderActionResult(
        status="failed",
        provider_operation="READ",
        provider_target=action.locator.stable_message_identity,
        provider_result_id="",
        error="provider_read_failed:TimeoutError",
    )
    assert provider.command_log == ["READ"]


def test_readback_failure_is_reconciled_by_retry_without_duplicate_write() -> None:
    module = import_module("app.email_provider_actions")
    provider = StatefulFakeImapProvider(fail_reads={2})
    executor = module.DeterministicEmailActionExecutor(provider)
    first = _action(EmailAction.MARK_READ, {})

    failed = executor.execute(first)
    retried = executor.execute(
        _action(EmailAction.MARK_READ, {}, attempt_number=2)
    )

    assert failed == module.ProviderActionResult(
        status="failed",
        provider_operation="STORE \\Seen",
        provider_target=first.locator.stable_message_identity,
        provider_result_id="",
        error="provider_readback_failed:TimeoutError",
    )
    assert retried.status == "done"
    assert retried.provider_operation == "readback_noop"
    assert retried.provider_result_id == "revision-1"
    assert provider.command_log == ["READ", "STORE \\Seen", "READ", "READ"]


def test_trash_never_uses_expunge_or_permanent_delete() -> None:
    module = import_module("app.email_provider_actions")
    provider = StatefulFakeImapProvider()
    provider_executor = module.DeterministicEmailActionExecutor(provider)

    result = provider_executor.execute(_action(EmailAction.TRASH, {}))

    assert result.status == "done"
    assert result.provider_operation == "move_to_trash"
    assert "EXPUNGE" not in provider_executor.supported_operations
    command_text = " ".join(provider.command_log).upper()
    assert "EXPUNGE" not in command_text
    assert "PERMANENT" not in command_text
    assert provider.trashed is True
    assert provider.folder == "Trash"


class FakeWritableImapSession:
    def __init__(
        self,
        *,
        capabilities: tuple[bytes, ...] = (b"IMAP4rev1", b"MOVE", b"UIDPLUS"),
        include_copyuid: bool = True,
        timeout_after_move: bool = False,
        permanent_flags: set[str] | None = None,
        mailboxes: Mapping[str, tuple[set[str], int]] | None = None,
        messages: Mapping[str, Mapping[int, tuple[str | None, set[str]]]] | None = None,
    ) -> None:
        self.capabilities = capabilities
        self.include_copyuid = include_copyuid
        self.timeout_after_move = timeout_after_move
        self.copyuid_response: bytes | None = None
        self.permanent_flags = set(
            {"\\Seen", "\\*"} if permanent_flags is None else permanent_flags
        )
        self.mailboxes = {
            name: (set(flags), uidvalidity)
            for name, (flags, uidvalidity) in (
                mailboxes
                or {
                    "INBOX": (set(), 42),
                    "Archive": ({"\\Archive"}, 84),
                    "Projects": (set(), 126),
                    "Trash": ({"\\Trash"}, 168),
                }
            ).items()
        }
        self.messages = {
            folder: {
                uid: (message_id, set(flags))
                for uid, (message_id, flags) in folder_messages.items()
            }
            for folder, folder_messages in (
                messages
                or {"INBOX": {7: ("<message@example.com>", set())}}
            ).items()
        }
        self.selected = ""
        self.calls: list[tuple[object, ...]] = []
        self.logged_out = False
        self.shutdown_called = False

    def list(self, reference_name: str = "", pattern: str = "*"):
        self.calls.append(("list", reference_name, pattern))
        data = []
        for name, (flags, _uidvalidity) in self.mailboxes.items():
            flag_text = " ".join(sorted(flags | {"\\HasNoChildren"}))
            data.append(f'({flag_text}) "/" "{name}"'.encode("ascii"))
        return "OK", data

    def create(self, mailbox: str):
        self.calls.append(("create", mailbox))
        return "OK", [b"created"]

    def select(self, mailbox: str, readonly: bool = False):
        self.calls.append(("select", mailbox, readonly))
        mailbox = self._mailbox_name(mailbox)
        if mailbox not in self.mailboxes:
            return "NO", [b"missing"]
        self.selected = mailbox
        return "OK", [str(len(self.messages.get(mailbox, {}))).encode("ascii")]

    def response(self, code: str):
        self.calls.append(("response", code))
        if code == "UIDVALIDITY":
            return code, [str(self.mailboxes[self.selected][1]).encode("ascii")]
        if code == "COPYUID":
            return code, [self.copyuid_response]
        if code == "PERMANENTFLAGS":
            flags = " ".join(sorted(self.permanent_flags))
            return code, [f"({flags})".encode("ascii")]
        return code, [None]

    def uid(self, command: str, *args: object):
        self.calls.append(("uid", command, *args))
        if command == "FETCH":
            uid = int(args[0])
            message = self.messages.get(self.selected, {}).get(uid)
            if message is None:
                return "OK", [None]
            message_id, flags = message
            flag_text = " ".join(sorted(flags))
            header = (
                b""
                if message_id is None
                else f"Message-ID: {message_id}\r\n\r\n".encode("ascii")
            )
            prefix = (
                f"1 (UID {uid} FLAGS ({flag_text}) "
                f"BODY[HEADER.FIELDS (MESSAGE-ID)] {{{len(header)}}}"
            ).encode("ascii")
            return "OK", [(prefix, header), b")"]
        if command == "SEARCH":
            requested = str(args[-1])
            matches = [
                str(uid).encode("ascii")
                for uid, (message_id, _flags) in self.messages.get(
                    self.selected, {}
                ).items()
                if message_id == requested
            ]
            return "OK", [b" ".join(matches)]
        if command == "STORE":
            uid = int(args[0])
            message_id, flags = self.messages[self.selected][uid]
            encoded_flags = str(args[2]).removeprefix("(").removesuffix(")")
            flags.update(encoded_flags.split())
            self.messages[self.selected][uid] = (message_id, flags)
            return "OK", [b"stored"]
        if command == "MOVE":
            uid = int(args[0])
            destination = self._mailbox_name(str(args[1]))
            message = self.messages[self.selected].pop(uid)
            destination_uid = max(self.messages.get(destination, {18: (None, set())})) + 1
            self.messages.setdefault(destination, {})[destination_uid] = message
            destination_uidvalidity = self.mailboxes[destination][1]
            self.selected = destination
            self.copyuid_response = (
                f"{destination_uidvalidity} {uid} {destination_uid}".encode("ascii")
                if self.include_copyuid
                else None
            )
            if self.timeout_after_move:
                self.timeout_after_move = False
                raise TimeoutError("provider timed out after accepting UID MOVE")
            return "OK", [None]
        raise AssertionError(f"unexpected UID command: {command}")

    def logout(self):
        self.calls.append(("logout",))
        self.logged_out = True
        return "BYE", [b"logout"]

    def shutdown(self):
        self.calls.append(("shutdown",))
        self.shutdown_called = True

    @staticmethod
    def _mailbox_name(value: str) -> str:
        if value.startswith('"') and value.endswith('"'):
            return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return value


def test_production_imap_refreshes_capabilities_after_login(monkeypatch) -> None:
    module = import_module("app.email_provider_actions")

    class LoginCapabilitySession:
        capabilities = (b"IMAP4rev1",)

        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def login(self, username: str, password: str):
            self.calls.append(("login", username, password))
            return "OK", [b"logged in"]

        def capability(self):
            self.calls.append(("capability",))
            return "OK", [b"IMAP4rev1 MOVE UIDPLUS"]

        def list(self, reference_name: str = "", pattern: str = "*"):
            self.calls.append(("list", reference_name, pattern))
            return "OK", [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\Archive \\HasNoChildren) "/" "Archive"',
            ]

        def logout(self):
            self.calls.append(("logout",))
            return "BYE", [b"logout"]

    session = LoginCapabilitySession()
    monkeypatch.setattr(module.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: session)

    provider = module.ImapDeterministicProvider.connect(
        "imap.example.com",
        "person@example.com",
        "imap-secret",
        account_id="account-1",
    )
    destination = provider.resolve_destination(
        _action(EmailAction.ARCHIVE, {}).locator,
        EmailAction.ARCHIVE,
        {},
    )
    provider.close()

    assert destination == "Archive"
    assert session.calls[:2] == [
        ("login", "person@example.com", "imap-secret"),
        ("capability",),
    ]


def test_production_imap_connect_verifies_certificate_and_hostname(monkeypatch) -> None:
    module = import_module("app.email_provider_actions")
    captured: dict[str, object] = {}

    class Session:
        def login(self, _username: str, _password: str):
            return "OK", [b"logged in"]

        def capability(self):
            return "OK", [b"IMAP4rev1 MOVE UIDPLUS"]

        def logout(self):
            return "BYE", [b"logout"]

    def connect(host, port, *, ssl_context, timeout):
        captured.update(
            host=host,
            port=port,
            ssl_context=ssl_context,
            timeout=timeout,
        )
        return Session()

    monkeypatch.setattr(module.imaplib, "IMAP4_SSL", connect)

    provider = module.ImapDeterministicProvider.connect(
        "imap.example.com",
        "person@example.com",
        "imap-secret",
        account_id="account-1",
    )
    provider.close()

    context = captured["ssl_context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


@pytest.mark.parametrize(
    ("action_type", "parameters", "expected_flags"),
    (
        (EmailAction.LABEL, {"labels": ("work", "priority")}, {"work", "priority"}),
        (EmailAction.MARK_READ, {}, {"\\Seen"}),
    ),
)
def test_production_imap_store_actions_use_uid_store_and_logout(
    action_type: EmailAction,
    parameters: dict[str, object],
    expected_flags: set[str],
) -> None:
    module = import_module("app.email_provider_actions")
    session = FakeWritableImapSession()
    provider = module.ImapDeterministicProvider(session, account_id="account-1")

    result = module.DeterministicEmailActionExecutor(provider).execute(
        _action(action_type, parameters)
    )

    assert result.status == "done"
    assert expected_flags.issubset(session.messages["INBOX"][7][1])
    assert [call[1] for call in session.calls if call[0] == "uid"].count("STORE") == 1
    assert session.logged_out is True
    assert session.calls[-1] == ("logout",)


@pytest.mark.parametrize(
    ("action_type", "parameters", "permanent_flags"),
    (
        (EmailAction.MARK_READ, {}, {"\\Answered", "\\*"}),
        (EmailAction.LABEL, {"labels": ("work",)}, {"\\Seen", "priority"}),
    ),
)
def test_production_imap_store_fails_before_write_when_flag_is_not_permanent(
    action_type: EmailAction,
    parameters: dict[str, object],
    permanent_flags: set[str],
) -> None:
    module = import_module("app.email_provider_actions")
    session = FakeWritableImapSession(permanent_flags=permanent_flags)

    result = module.DeterministicEmailActionExecutor(
        module.ImapDeterministicProvider(session, account_id="account-1")
    ).execute(_action(action_type, parameters))

    assert result.status == "failed"
    assert result.error == "provider_apply_failed:ImapPermanentFlagsUnsupported"
    assert result.retryable is False
    assert ("response", "PERMANENTFLAGS") in session.calls
    assert not any(call[:2] == ("uid", "STORE") for call in session.calls)


def test_production_imap_label_accepts_explicit_permanent_keyword() -> None:
    module = import_module("app.email_provider_actions")
    session = FakeWritableImapSession(permanent_flags={"\\Seen", "work"})

    result = module.DeterministicEmailActionExecutor(
        module.ImapDeterministicProvider(session, account_id="account-1")
    ).execute(_action(EmailAction.LABEL, {"labels": ("work",)}))

    assert result.status == "done"
    assert "work" in session.messages["INBOX"][7][1]


def test_production_imap_store_uses_new_connection_for_durable_readback() -> None:
    module = import_module("app.email_provider_actions")
    write_session = FakeWritableImapSession()
    readback_session = FakeWritableImapSession()

    def fresh_provider():
        assert write_session.logged_out is True
        return module.ImapDeterministicProvider(
            readback_session,
            account_id="account-1",
        )

    result = module.DeterministicEmailActionExecutor(
        module.ImapDeterministicProvider(write_session, account_id="account-1"),
        readback_provider_factory=fresh_provider,
    ).execute(_action(EmailAction.LABEL, {"labels": ("work",)}))

    assert result.status == "failed"
    assert result.error == "provider_readback_mismatch"
    assert "work" in write_session.messages["INBOX"][7][1]
    assert "work" not in readback_session.messages["INBOX"][7][1]
    assert write_session.logged_out is True
    assert readback_session.logged_out is True


@pytest.mark.parametrize(
    ("action_type", "parameters", "destination"),
    (
        (EmailAction.ARCHIVE, {}, "Archive"),
        (EmailAction.MOVE, {"target_folder": "Projects"}, "Projects"),
        (EmailAction.TRASH, {}, "Trash"),
    ),
)
def test_production_imap_destination_actions_use_uid_move_and_return_new_locator(
    action_type: EmailAction,
    parameters: dict[str, object],
    destination: str,
) -> None:
    module = import_module("app.email_provider_actions")
    session = FakeWritableImapSession()
    provider = module.ImapDeterministicProvider(session, account_id="account-1")
    action = _action(action_type, parameters)

    result = module.DeterministicEmailActionExecutor(provider).execute(action)

    assert result.status == "done"
    assert result.updated_locator is not None
    assert result.updated_locator.folder == destination
    assert result.updated_locator.uidvalidity == session.mailboxes[destination][1]
    assert result.updated_locator.uid == 19
    assert ("uid", "MOVE", "7", destination) in session.calls
    command_text = " ".join(str(call) for call in session.calls).upper()
    assert "EXPUNGE" not in command_text
    assert "\\DELETED" not in command_text
    assert "'COPY'" not in command_text
    assert session.logged_out is True


@pytest.mark.parametrize(
    ("capabilities", "mailboxes", "expected_error"),
    (
        (
            (b"IMAP4rev1", b"UIDPLUS"),
            None,
            "provider_destination_failed:ImapMoveUnsupported",
        ),
        (
            (b"IMAP4rev1", b"MOVE", b"UIDPLUS"),
            {"INBOX": (set(), 42)},
            "provider_destination_failed:ImapDestinationUnavailable",
        ),
    ),
)
def test_production_imap_move_fails_closed_before_write_when_destination_is_unsafe(
    capabilities: tuple[bytes, ...],
    mailboxes: Mapping[str, tuple[set[str], int]] | None,
    expected_error: str,
) -> None:
    module = import_module("app.email_provider_actions")
    session = FakeWritableImapSession(
        capabilities=capabilities,
        mailboxes=mailboxes,
    )
    provider = module.ImapDeterministicProvider(session, account_id="account-1")

    result = module.DeterministicEmailActionExecutor(provider).execute(
        _action(EmailAction.TRASH, {})
    )

    assert result.status == "failed"
    assert result.error == expected_error
    assert result.retryable is False
    assert not any(call[:2] == ("uid", "MOVE") for call in session.calls)
    assert session.logged_out is True


def test_production_imap_move_without_copyuid_or_message_id_fails_before_write() -> None:
    module = import_module("app.email_provider_actions")
    session = FakeWritableImapSession(
        capabilities=(b"IMAP4rev1", b"MOVE"),
        include_copyuid=False,
        messages={"INBOX": {7: (None, set())}},
    )
    action = _action(EmailAction.ARCHIVE, {})
    action = type(action)(
        **{
            **action.__dict__,
            "locator": type(action.locator)(
                **{
                    **action.locator.__dict__,
                    "rfc_message_id": None,
                    "stable_message_identity": "account-1:imap:INBOX:42:7",
                }
            ),
        }
    )

    result = module.DeterministicEmailActionExecutor(
        module.ImapDeterministicProvider(session, account_id="account-1")
    ).execute(action)

    assert result.status == "failed"
    assert result.error == "provider_destination_failed:ImapReadbackUnsupported"
    assert result.retryable is False
    assert not any(call[:2] == ("uid", "MOVE") for call in session.calls)


def test_production_imap_move_without_message_id_uses_uidplus_copyuid_response() -> None:
    module = import_module("app.email_provider_actions")
    session = FakeWritableImapSession(messages={"INBOX": {7: (None, set())}})
    readback_sessions = []

    def fresh_provider():
        readback_session = FakeWritableImapSession(messages=session.messages)
        readback_sessions.append(readback_session)
        return module.ImapDeterministicProvider(
            readback_session,
            account_id="account-1",
        )

    action = _action(EmailAction.ARCHIVE, {})
    action = type(action)(
        **{
            **action.__dict__,
            "locator": type(action.locator)(
                **{
                    **action.locator.__dict__,
                    "rfc_message_id": None,
                    "stable_message_identity": "account-1:imap:INBOX:42:7",
                }
            ),
        }
    )

    result = module.DeterministicEmailActionExecutor(
        module.ImapDeterministicProvider(session, account_id="account-1"),
        readback_provider_factory=fresh_provider,
    ).execute(action)

    assert result.status == "done"
    assert result.updated_locator is not None
    assert result.updated_locator.folder == "Archive"
    assert result.updated_locator.uidvalidity == 84
    assert result.updated_locator.uid == 19
    assert ("response", "COPYUID") in session.calls
    assert session.logged_out is True
    assert len(readback_sessions) == 1
    assert readback_sessions[0].logged_out is True


def test_production_imap_move_accepts_singleton_uid_ranges_in_copyuid() -> None:
    module = import_module("app.email_provider_actions")

    class SingletonRangeCopyuidSession(FakeWritableImapSession):
        def response(self, code: str):
            if code == "COPYUID":
                return "COPYUID", [b"84 7:7 19:19"]
            return super().response(code)

    session = SingletonRangeCopyuidSession(messages={"INBOX": {7: (None, set())}})
    action = _action(EmailAction.ARCHIVE, {})
    action = type(action)(
        **{
            **action.__dict__,
            "locator": type(action.locator)(
                **{
                    **action.locator.__dict__,
                    "rfc_message_id": None,
                    "stable_message_identity": "account-1:imap:INBOX:42:7",
                }
            ),
        }
    )
    result = module.DeterministicEmailActionExecutor(
        module.ImapDeterministicProvider(session, account_id="account-1"),
        readback_provider_factory=lambda: module.ImapDeterministicProvider(
            FakeWritableImapSession(messages=session.messages),
            account_id="account-1",
        ),
    ).execute(action)

    assert result.status == "done"
    assert result.updated_locator is not None
    assert result.updated_locator.uidvalidity == 84
    assert result.updated_locator.uid == 19


@pytest.mark.parametrize(
    ("copyuid_code", "copyuid_value"),
    (
        ("OK", b"84 7 19"),
        ("COPYUID", b"84 8 19"),
        ("COPYUID", b"0 7 19"),
        ("COPYUID", b"84 7 0"),
        ("COPYUID", b"84 7 19 extra"),
    ),
)
def test_production_imap_move_rejects_invalid_copyuid_without_message_id_fallback(
    copyuid_code: str,
    copyuid_value: bytes,
) -> None:
    module = import_module("app.email_provider_actions")

    class InvalidCopyuidSession(FakeWritableImapSession):
        def response(self, code: str):
            response = super().response(code)
            if code == "COPYUID":
                return copyuid_code, [copyuid_value]
            return response

    session = InvalidCopyuidSession()

    result = module.DeterministicEmailActionExecutor(
        module.ImapDeterministicProvider(session, account_id="account-1")
    ).execute(_action(EmailAction.MOVE, {"target_folder": "Projects"}))

    assert result.status == "failed"
    assert result.error == "provider_apply_failed:ImapReadbackUnsupported"
    assert result.retryable is False
    assert not any(call[:2] == ("uid", "SEARCH") for call in session.calls)


def test_production_imap_move_uses_message_id_readback_when_copyuid_is_absent() -> None:
    module = import_module("app.email_provider_actions")
    session = FakeWritableImapSession(
        capabilities=(b"IMAP4rev1", b"MOVE"),
        include_copyuid=False,
    )

    result = module.DeterministicEmailActionExecutor(
        module.ImapDeterministicProvider(session, account_id="account-1")
    ).execute(_action(EmailAction.MOVE, {"target_folder": "Projects"}))

    assert result.status == "done"
    assert result.updated_locator is not None
    assert result.updated_locator.folder == "Projects"
    assert result.updated_locator.uid == 19
    assert any(call[:2] == ("uid", "SEARCH") for call in session.calls)


def test_production_imap_move_timeout_retries_by_message_id_without_second_move() -> None:
    module = import_module("app.email_provider_actions")
    first_session = FakeWritableImapSession(timeout_after_move=True)
    action = _action(EmailAction.MOVE, {"target_folder": "Projects"})

    failed = module.DeterministicEmailActionExecutor(
        module.ImapDeterministicProvider(first_session, account_id="account-1")
    ).execute(action)
    retry_session = FakeWritableImapSession(messages=first_session.messages)
    recovered = module.DeterministicEmailActionExecutor(
        module.ImapDeterministicProvider(retry_session, account_id="account-1")
    ).execute(action)

    assert failed.status == "failed"
    assert failed.error == "provider_apply_failed:TimeoutError"
    assert recovered.status == "done"
    assert recovered.provider_operation == "readback_noop"
    assert recovered.updated_locator is not None
    assert recovered.updated_locator.folder == "Projects"
    all_calls = first_session.calls + retry_session.calls
    assert [call[1] for call in all_calls if call[0] == "uid"].count("MOVE") == 1
    assert any(call[:2] == ("uid", "SEARCH") for call in retry_session.calls)
    assert first_session.logged_out is True
    assert retry_session.logged_out is True


def test_production_imap_quotes_discovered_special_use_folder_for_uid_move() -> None:
    module = import_module("app.email_provider_actions")
    session = FakeWritableImapSession(
        mailboxes={
            "INBOX": (set(), 42),
            "All Mail": ({"\\All"}, 84),
        }
    )

    result = module.DeterministicEmailActionExecutor(
        module.ImapDeterministicProvider(session, account_id="account-1")
    ).execute(_action(EmailAction.ARCHIVE, {}))

    assert result.status == "done"
    assert result.updated_locator is not None
    assert result.updated_locator.folder == "All Mail"
    assert ("uid", "MOVE", "7", '"All Mail"') in session.calls


def test_production_imap_lists_folders_and_creates_exact_modified_utf7_name() -> None:
    module = import_module("app.email_provider_actions")
    session = FakeWritableImapSession(
        mailboxes={"INBOX": ({"\\Inbox"}, 42), "&U,BTFw-": (set(), 84)}
    )
    provider = module.ImapDeterministicProvider(session, account_id="account-1")

    assert provider.list_folders() == (
        ProviderFolder("INBOX", "INBOX", FolderRole.INBOX),
        ProviderFolder("台北", "台北", FolderRole.UNBOUND),
    )

    provider.create_folder_exact("项目")

    assert ("create", "&mHl27g-") in session.calls


def test_provider_action_folder_inventory_parses_literal_and_whitespace_names() -> None:
    module = import_module("app.email_provider_actions")

    class LiteralListSession(FakeWritableImapSession):
        def list(self, reference_name: str = "", pattern: str = "*"):
            self.calls.append(("list", reference_name, pattern))
            return "OK", [
                (b'(\\Trash \\Sent) "/" {9}', b" Deleted "),
                b" (STATUS (MESSAGES 0))",
                b'() "/" " Folder "',
            ]

    provider = module.ImapDeterministicProvider(
        LiteralListSession(), account_id="account-1"
    )

    assert provider.list_folders() == (
        ProviderFolder(" Deleted ", " Deleted ", FolderRole.TRASH),
        ProviderFolder(" Folder ", " Folder ", FolderRole.UNBOUND),
    )


@pytest.mark.parametrize(
    ("folder", "wire_name"),
    (
        ("R&D", "R&-D"),
        ("台北", "&U,BTFw-"),
        ("台北 Mail", '"&U,BTFw- Mail"'),
        ("📬", "&2D3c7A-"),
    ),
)
def test_production_imap_decodes_list_and_encodes_unicode_move_mailbox(
    folder: str,
    wire_name: str,
) -> None:
    module = import_module("app.email_provider_actions")
    unquoted_wire_name = wire_name.removeprefix('"').removesuffix('"')
    session = FakeWritableImapSession(
        mailboxes={
            "INBOX": (set(), 42),
            unquoted_wire_name: (set(), 84),
        }
    )

    result = module.DeterministicEmailActionExecutor(
        module.ImapDeterministicProvider(session, account_id="account-1")
    ).execute(_action(EmailAction.MOVE, {"target_folder": folder}))

    assert result.status == "done"
    assert result.updated_locator is not None
    assert result.updated_locator.folder == folder
    assert ("uid", "MOVE", "7", wire_name) in session.calls


@pytest.mark.parametrize(
    "wire_name",
    (
        "&U,BTFw",
        "&A-",
        "&2AA-",
    ),
)
def test_production_imap_rejects_malformed_modified_utf7_list_mailbox(
    wire_name: str,
) -> None:
    module = import_module("app.email_provider_actions")

    with pytest.raises(module.ImapDestinationUnavailable):
        module._parse_mailbox(f'() "/" "{wire_name}"'.encode("ascii"))


@pytest.mark.parametrize(
    "response",
    (
        b'() "/" bad]name',
        b'() "/" "bad' + bytes((0x5C,)) + b'x"',
    ),
)
def test_production_imap_rejects_malformed_list_mailbox_token(
    response: bytes,
) -> None:
    module = import_module("app.email_provider_actions")

    with pytest.raises(module.ImapDestinationUnavailable):
        module._parse_mailbox(response)


@pytest.mark.parametrize("folder", ("bad\nfolder", "bad\x00folder", "\ud800"))
def test_production_imap_rejects_unsafe_mailbox_before_select(folder: str) -> None:
    module = import_module("app.email_provider_actions")
    session = FakeWritableImapSession()
    provider = module.ImapDeterministicProvider(session, account_id="account-1")

    with pytest.raises(module.ImapDestinationUnavailable):
        provider._select(folder, readonly=True)

    assert not any(call[0] == "select" for call in session.calls)


@pytest.mark.parametrize(
    "provider_message_id",
    (
        "<LocalPart@EXAMPLE.COM>",
        "\r\n\t<LocalPart@Example.COM> \t",
    ),
)
def test_production_imap_read_uses_locator_message_id_canonicalization(
    provider_message_id: str,
) -> None:
    module = import_module("app.email_provider_actions")
    store_module = import_module("app.email_store")
    session = FakeWritableImapSession(
        messages={"INBOX": {7: (provider_message_id, set())}}
    )
    locator = store_module.StoredEmailLocator(
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=7,
        rfc_message_id="<LocalPart@example.com>",
        thread_id=None,
        stable_message_identity="account-1:message-id:<LocalPart@example.com>",
    )

    state = module.ImapDeterministicProvider(
        session,
        account_id="account-1",
    ).read_state(locator)

    assert state.locator is not None
    assert state.locator.rfc_message_id == "<LocalPart@example.com>"


def test_production_imap_rejects_non_atom_keyword_without_store() -> None:
    module = import_module("app.email_provider_actions")
    session = FakeWritableImapSession()

    result = module.DeterministicEmailActionExecutor(
        module.ImapDeterministicProvider(session, account_id="account-1")
    ).execute(_action(EmailAction.LABEL, {"labels": ("unsafe label",)}))

    assert result.status == "failed"
    assert result.error == "provider_apply_failed:ImapKeywordUnsupported"
    assert result.retryable is False
    assert not any(call[:2] == ("uid", "STORE") for call in session.calls)
