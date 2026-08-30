from __future__ import annotations

from importlib import import_module

from app.email_classifier_contracts import EmailAction


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
        (EmailAction.MARK_READ, {}, "STORE \\Seen"),
        (EmailAction.ARCHIVE, {}, "MOVE ARCHIVE"),
        (EmailAction.MOVE, {"target_folder": "Projects"}, "MOVE"),
        (EmailAction.TRASH, {}, "MOVE TRASH"),
    )

    for action_type, parameters, operation in cases:
        provider = StatefulFakeImapProvider()
        action = _action(action_type, parameters)

        result = module.DeterministicEmailActionExecutor(provider).execute(action)

        assert result.status == "done"
        assert result.provider_operation == operation
        assert result.provider_result_id == "revision-1"
        assert provider.command_log == ["READ", operation, "READ"]


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

    result = module.DeterministicEmailActionExecutor(provider).execute(
        _action(EmailAction.TRASH, {})
    )

    assert result.status == "done"
    command_text = " ".join(provider.command_log).upper()
    assert "EXPUNGE" not in command_text
    assert "PERMANENT" not in command_text
    assert provider.trashed is True
    assert provider.folder == "Trash"
