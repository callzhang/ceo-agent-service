from app.email_classifier_contracts import EmailAction
from app.email_dws_actions import DwsVerifiedMoveActionExecutor
from app.email_store import StoredEmailAction, StoredEmailLocator
from app import email_worker
from hashlib import sha256
import json


CONTEXT = {
    "move_action_id": "111699",
    "message_id": "DzzzzzzNJ0u",
    "folder_id": 167,
    "receipt_digest": "",
    "account_id": "dingtalk_primary",
    "stable_message_identity": "dingtalk_primary:message-id:<junhe@example.com>",
    "sender": "xup@junhe.com",
    "subject": "回复: Quotation for On-Premise Labeling Platform with Customer Cloud_225.pdf",
    "received_at": "2026-05-07T08:56:27+00:00",
}


def message(**overrides):
    value = {
        "id": "DzzzzzzNJ0u",
        "conversationId": "DzzzzyK-vA1---.hRXZG19",
        "from": {"email": "xup@junhe.com"},
        "subject": CONTEXT["subject"],
        "receivedDateTime": "2026-05-07T08:56:33Z",
    }
    value.update(overrides)
    return value


def proof(candidate=None):
    candidate = candidate or message()
    return sha256(json.dumps({
        "account": "derek@stardust.ai",
        "sender": CONTEXT["sender"].casefold(),
        "subject": CONTEXT["subject"],
        "conversation": candidate["conversationId"],
        "received_at": candidate["receivedDateTime"],
        "dws_message_id": CONTEXT["message_id"],
        "folder_id": "167",
        "action_id": CONTEXT["move_action_id"],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


CONTEXT["receipt_digest"] = proof()


def action(kind=EmailAction.FLAG_IMPORTANT):
    return StoredEmailAction(
        action_id="126955",
        action_plan_id="plan-v3",
        classification_id=949082524487675729,
        account_id="dingtalk_primary",
        action_type=kind,
        parameters={},
        config_version="email-test-v1",
        locator=StoredEmailLocator(
            account_id="dingtalk_primary",
            folder="INBOX",
            uidvalidity=42,
            uid=123,
            rfc_message_id="<junhe@example.com>",
            thread_id="DzzzzyK-vA1---.hRXZG19",
            stable_message_identity=CONTEXT["stable_message_identity"],
        ),
        attempt_number=1,
        claim_started_at="2026-10-29T11:02:00+00:00",
    )


class FakeDws:
    dws_bin = "dws"

    def __init__(self, *, important=False, read=True, bad_message=None):
        self.important = important
        self.read = read
        self.bad_message = bad_message
        self.commands = []

    def run_json(self, command):
        self.commands.append(command)
        if command[1:4] == ["mail", "message", "search"]:
            query = command[command.index("--query") + 1]
            candidate = message() if self.bad_message is None else self.bad_message
            if "tag:important" in query:
                return {"total": 1 if self.important else 0,
                        "messages": [candidate] if self.important else []}
            if "isRead:true" in query:
                return {"total": 1 if self.read else 0,
                        "messages": [candidate] if self.read else []}
            return {"total": 1, "messages": [candidate]}
        if command[1:4] == ["mail", "tag", "list"]:
            return {"tags": [{"id": "107", "name": "重要"}]}
        if command[1:4] == ["mail", "message", "batch-update"]:
            if "addTags" in command:
                self.important = True
            if "markRead" in command:
                self.read = True
            return {"success": True}
        raise AssertionError(command)


def test_flag_uses_exact_dws_move_receipt_and_reads_back_tag():
    dws = FakeDws(important=False)
    result = DwsVerifiedMoveActionExecutor(
        dws, mailbox="derek@stardust.ai", context=CONTEXT
    ).execute(action())

    assert result.status == "done"
    assert result.provider_operation == "dws_verified_readback"
    updates = [c for c in dws.commands if c[1:4] == ["mail", "message", "batch-update"]]
    assert len(updates) == 1
    assert updates[0][updates[0].index("--ids") + 1] == "DzzzzzzNJ0u"
    assert updates[0][updates[0].index("--tags") + 1] == "107"
    assert result.provider_result_id.startswith("dws-mail:")


def test_read_action_is_noop_when_exact_message_is_already_read():
    dws = FakeDws(read=True)
    result = DwsVerifiedMoveActionExecutor(
        dws, mailbox="derek@stardust.ai", context=CONTEXT
    ).execute(action(EmailAction.MARK_READ))

    assert result.status == "done"
    assert result.provider_operation == "dws_readback_noop"
    assert not any(c[1:4] == ["mail", "message", "batch-update"] for c in dws.commands)


def test_dws_action_fails_closed_when_time_identity_is_outside_approved_window():
    dws = FakeDws(bad_message=message(receivedDateTime="2026-05-07T08:56:38Z"))
    result = DwsVerifiedMoveActionExecutor(
        dws, mailbox="derek@stardust.ai", context=CONTEXT
    ).execute(action())

    assert result.status == "failed"
    assert not any(c[1:4] == ["mail", "message", "batch-update"] for c in dws.commands)


def test_dws_action_fails_closed_when_conversation_is_wrong():
    dws = FakeDws(bad_message=message(conversationId="different-thread"))
    result = DwsVerifiedMoveActionExecutor(
        dws, mailbox="derek@stardust.ai", context=CONTEXT
    ).execute(action())

    assert result.status == "failed"
    assert not any(c[1:4] == ["mail", "message", "batch-update"] for c in dws.commands)


def test_email_worker_selects_dws_only_for_persisted_verified_move(monkeypatch):
    dws = FakeDws(important=False)

    class Store:
        completed = None

        def recover_stale_processing_actions(self, **_kwargs):
            return None

        def claim_next_direct_action(self, **_kwargs):
            return action()

        def verified_dws_move_context_for_action(self, claimed):
            assert claimed.action_id == "126955"
            return CONTEXT

        def get_account(self, account_id):
            return {"enabled": True, "account_id": account_id, "email_address": "derek@stardust.ai"}

        def complete_direct_action_attempt(self, claimed, **kwargs):
            self.completed = (claimed, kwargs)

    store = Store()
    monkeypatch.setattr("app.dws_client.DwsClient", lambda: dws)
    result = email_worker._run_next_direct_action(
        store,
        lambda _account_id: (_ for _ in ()).throw(AssertionError("IMAP path selected")),
    )

    assert result.status == "done"
    assert store.completed is not None
    assert store.completed[1]["provider_operation"] == "dws_verified_readback"
