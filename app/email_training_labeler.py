"""Label targeted historical mail for classifier training without touching the mailbox.

Some categories have too little labelled mail to split into train, validation and
test. Plenty of mail sits unclassified in INBOX, but the realtime pipeline cannot be
used to label it: an Agent classification there always builds an ActionPlan that
moves the message. This job reuses the same read-only provider re-read, task input,
Agent and persistence, and persists a certain result with an ActionPlan that has no
actions, so the label becomes a training record and nothing in the mailbox changes.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

LABEL_ONLY_MODEL_ID = "email-classifier-agent:v1"

# Signals in the subject or sender that make a message worth spending an Agent
# call on for a thin category. They only choose candidates; the Agent decides.
TARGETING_PATTERNS: Mapping[str, str] = {
    "external_billing": (
        r"invoice|receipt|billing|\bbill\b|payment|charge|subscription|renew|"
        r"账单|发票|收据|扣款|付款|订单|续费"
    ),
    "finance": (
        r"财务|报销|对账|银行|\bbank\b|statement|\btax\b|税|finance|expense|"
        r"工资|薪资|薪酬|社保|公积金|审计|预算|汇款|转账|付款申请|费用|结算|"
        r"会计|账户|资金|payroll|salary|audit|budget|accounting|remittance"
    ),
    "personal": (
        r"生日|家人|朋友|个人|personal|family|wedding|婚礼|"
        r"医院|体检|挂号|预约|学校|孩子|家长|房租|物业|快递|机票|酒店|行程|"
        r"签证|护照|保险|健身|会员|旅行|度假|"
        r"flight|hotel|booking|itinerary|visa|passport|insurance|school|doctor|appointment"
    ),
}


@dataclass(frozen=True)
class LabelCandidate:
    account_id: str
    folder: str
    uidvalidity: int
    uid: int
    stable_message_identity: str
    subject: str
    sender: str
    targeted_category: str

    @property
    def provider_message(self) -> dict[str, object]:
        return {
            "accountId": self.account_id,
            "folder": self.folder,
            "uidValidity": self.uidvalidity,
            "uid": self.uid,
            "stableMessageIdentity": self.stable_message_identity,
        }


def select_candidates(
    observation_cache: Mapping[str, object],
    *,
    classified_identities: frozenset[str],
    categories: Sequence[str],
    folder: str = "INBOX",
    limit_per_category: int,
) -> list[LabelCandidate]:
    """Pick unclassified messages in one folder whose subject or sender targets a category."""

    patterns = {
        category: re.compile(TARGETING_PATTERNS[category], re.IGNORECASE)
        for category in categories
    }
    chosen: dict[str, LabelCandidate] = {}
    per_category: dict[str, int] = {category: 0 for category in categories}
    accounts = observation_cache.get("accounts") or {}
    for account_id, account in sorted(accounts.items()):
        for folder_state in (account.get("folders") or {}).values():
            uidvalidity = int(folder_state.get("uidvalidity") or 0)
            for entry in (folder_state.get("observations") or {}).values():
                observation = entry.get("observation") or {}
                if str(observation.get("provider_folder_name") or "") != folder:
                    continue
                identity = str(observation.get("stable_message_identity") or "")
                if not identity or identity in classified_identities or identity in chosen:
                    continue
                subject = str(observation.get("subject") or "")
                sender = str(observation.get("sender") or "")
                text = f"{subject} {sender}"
                for category, pattern in patterns.items():
                    if per_category[category] >= limit_per_category:
                        continue
                    if pattern.search(text):
                        chosen[identity] = LabelCandidate(
                            account_id=str(account_id),
                            folder=folder,
                            uidvalidity=uidvalidity,
                            uid=int(entry.get("uid") or 0),
                            stable_message_identity=identity,
                            subject=subject,
                            sender=sender,
                            targeted_category=category,
                        )
                        per_category[category] += 1
                        break
    return [
        candidate
        for candidate in chosen.values()
        if candidate.uidvalidity > 0 and candidate.uid > 0
    ]


def search_candidates(
    source: object,
    *,
    account_id: str,
    query: str,
    targeted_category: str,
    classified_identities: frozenset[str],
    limit: int,
    folder: str = "INBOX",
) -> list[tuple[LabelCandidate, Mapping[str, object]]]:
    """Find candidates with Gmail's own search, newest first, over a readonly select.

    Gmail already sorts mail into purchases, social and so on; `X-GM-RAW` asks
    for that directly instead of guessing from subjects. Each hit is fetched once
    through the readonly adapter, and that fetched record is what the Agent reads.
    """

    from app.email_classifier_scan import _provider_locator, _stable_message_identity
    from app.email_imap_mailbox import encode_imap_mailbox_argument
    from app.email_imap_readonly import _require_ok, _search_uids, _uidvalidity

    session = source.session
    status, _ = session.select(encode_imap_mailbox_argument(folder), readonly=True)
    _require_ok(status, "IMAP readonly select failed")
    uidvalidity = _uidvalidity(session.response("UIDVALIDITY"))
    quoted = '"' + query.replace("\\", "\\\\").replace('"', '\\"') + '"'
    status, data = session.uid("SEARCH", "X-GM-RAW", quoted)
    _require_ok(status, "IMAP X-GM-RAW search failed")
    uids = sorted((int(uid) for uid in _search_uids(data)), reverse=True)
    chosen: list[tuple[LabelCandidate, Mapping[str, object]]] = []
    for uid in uids:
        if len(chosen) >= limit:
            break
        batch = source.fetch_uid_batch(
            folder, cursor_uidvalidity=uidvalidity, last_seen_uid=uid - 1, limit=1, unread_only=False
        )
        message = next((item for item in batch.messages if int(item.get("uid") or 0) == uid), None)
        if int(batch.uidvalidity) != uidvalidity or message is None:
            continue
        locator = _provider_locator(message)
        identity = _stable_message_identity(message, locator)
        if identity in classified_identities:
            continue
        sender_value = message.get("from") or {}
        chosen.append(
            (
                LabelCandidate(
                    account_id=account_id,
                    folder=folder,
                    uidvalidity=uidvalidity,
                    uid=uid,
                    stable_message_identity=identity,
                    subject=str(message.get("subject") or ""),
                    sender=str(sender_value.get("email") or "")
                    if isinstance(sender_value, Mapping)
                    else str(sender_value),
                    targeted_category=targeted_category,
                ),
                message,
            )
        )
    return chosen


def label_only_action_plan(*, classification_id: int, account_id: str, result: object, config_version: str, created_at: datetime):
    """An ActionPlan with no actions: the label is recorded and nothing executes."""

    from app.email_classifier_contracts import build_email_action_plan

    return build_email_action_plan(
        classification_id=classification_id,
        account_id=account_id,
        category=getattr(result, "category"),
        classification_source="agent",
        confidence=float(getattr(result, "confidence")),
        model_id=LABEL_ONLY_MODEL_ID,
        config_version=config_version,
        actions=(),
        action_parameters={},
        created_at=created_at,
    )


def label_candidate(
    candidate: LabelCandidate,
    *,
    email_store: object,
    agent: object,
    read_current_message: Callable[[LabelCandidate], Mapping[str, object]],
    context: object,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, object]:
    """Classify one message with the Agent and persist a certain result as a label only."""

    from app.email_classifier_agent import durable_agent_classification_result
    from app.email_classifier_contracts import (
        EmailAttachmentMetadata,
        EmailClassification,
        EmailClassificationStatus,
        EmailProviderLocator,
    )
    from app.email_classifier_model import email_message_to_text
    from app.email_imap_readonly import (
        ephemeral_body_html,
        ephemeral_unsubscribe_authentication,
    )
    from app.email_task_adapter import EmailClassificationTaskInput
    from app.email_unsubscribe import (
        browser_unsubscribe_entries,
        extract_unsubscribe_entries,
    )

    if email_store.get_classification_by_stable_identity(candidate.stable_message_identity):
        return {"identity": candidate.stable_message_identity, "outcome": "already_classified"}
    current_message = read_current_message(candidate)
    body_text = str(
        current_message.get("markdownBody") or current_message.get("textBody") or ""
    )
    entries = browser_unsubscribe_entries(
        extract_unsubscribe_entries(
            list_unsubscribe=str(current_message.get("listUnsubscribe") or ""),
            list_unsubscribe_post=str(current_message.get("listUnsubscribePost") or ""),
            body_text=body_text,
            body_html=ephemeral_body_html(current_message),
            authentication_evidence=ephemeral_unsubscribe_authentication(current_message),
        ),
        normalize_indexes=True,
    )
    task_input = EmailClassificationTaskInput.from_message(
        current_message,
        allowed_category_keys=context.allowed_category_keys,
        category_descriptions=context.category_descriptions,
        folder_targets=context.folder_targets,
        config_version=context.config_version,
        unsubscribe_candidates=entries,
    )
    payload = task_input.payload()
    task = SimpleNamespace(
        # The runtime router only accepts classification-shaped workload keys.
        # The digest is namespaced so it never equals a realtime task's key.
        task_id="email-classification:"
        + sha256(
            ("training-label:" + task_input.stable_message_identity).encode("utf-8")
        ).hexdigest(),
        stable_message_identity=task_input.stable_message_identity,
        input_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    prompt_message = dict(payload.get("message") or {})
    prompt_message["text"] = body_text
    prompt_message["subject"] = str(current_message.get("subject") or "")
    result = agent.classify(
        task,
        current_message=prompt_message,
        unsubscribe_candidates=tuple(entry.private_url for entry in entries),
        unsubscribe_candidate_metadata=tuple(
            {
                "index": entry.index,
                "source": entry.source.value,
                "scheme": entry.scheme,
                "host": entry.host,
                "context": entry.context,
                "reference": entry.reference,
            }
            for entry in entries
        ),
    )
    if result.certainty != "certain" or not result.category:
        # An uncertain result would land in the owner's 待确认 queue. This job
        # only exists to add training labels, so it keeps nothing it is unsure of.
        return {
            "identity": task.stable_message_identity,
            "outcome": "not_certain",
            "category": result.category,
        }
    durable_result = durable_agent_classification_result(result, entries)
    locator = EmailProviderLocator.model_validate(payload["provider_locator"])
    classification_id = (
        int.from_bytes(sha256(task.stable_message_identity.encode("utf-8")).digest()[:8], "big")
        & ((1 << 63) - 1)
        or 1
    )
    plan = label_only_action_plan(
        classification_id=classification_id,
        account_id=locator.account_id,
        result=result,
        config_version=context.config_version,
        created_at=now(),
    )
    message = payload["message"]
    sender_value = message.get("sender") or {}
    sender = (
        str(sender_value.get("email") or sender_value.get("name") or "")
        if isinstance(sender_value, Mapping)
        else str(sender_value)
    )
    classification = EmailClassification(
        classification_id=classification_id,
        stable_message_identity=task.stable_message_identity,
        provider_locator=locator,
        category=result.category,
        confidence=result.confidence,
        margin=0.0,
        probabilities={result.category: result.confidence},
        model_id=LABEL_ONLY_MODEL_ID,
        config_version=context.config_version,
        status=EmailClassificationStatus.PROCESSED,
        classification_source="agent",
        action_plan=plan,
    )
    email_store.persist_scan_result(
        classification,
        agent_result=durable_result,
        sender=sender,
        recipients=tuple(
            str(item.get("email") or item.get("name") or "")
            if isinstance(item, Mapping)
            else str(item)
            for item in (
                *(message.get("to_recipients") or ()),
                *(message.get("cc_recipients") or ()),
            )
        ),
        subject=str(message.get("subject") or ""),
        normalized_text=body_text,
        attachment_metadata=tuple(
            EmailAttachmentMetadata.model_validate(item)
            for item in message.get("attachments") or ()
        ),
        received_at=str(message.get("date") or ""),
        model_text=email_message_to_text(current_message) or "__empty__",
    )
    return {
        "identity": task.stable_message_identity,
        "outcome": "labelled",
        "category": result.category,
        "targeted_category": candidate.targeted_category,
    }


def _scan_context(email_store: object, account_id: str) -> object:
    from app.email_classifier_scan import AgentScanContext

    configs = tuple(item for item in email_store.list_category_configs() if item["enabled"])
    bindings = tuple(
        item
        for item in email_store.list_account_folder_bindings()
        if item["account_id"] == account_id and item["binding_status"] == "active"
    )
    return AgentScanContext(
        allowed_category_keys=tuple(item["category_key"] for item in configs),
        category_descriptions={
            item["category_key"]: {
                "core": item["core_description"],
                "include": item["include"],
                "exclude": item["exclude"],
            }
            for item in configs
        },
        folder_targets={
            item["category_key"]: item["provider_folder_name"]
            for item in bindings
            if item["category_key"] != "junk"
        },
        config_version="|".join(sorted({str(item["config_version"]) for item in configs})),
    )


def main(argv: Iterable[str] | None = None) -> int:
    from app.agent_runtime_config import load_runtime_config
    from app.config import load_env_file, worker_db_path, workspace_path
    from app.agent_runtime_production import build_production_routed_codex_execution
    from app.email_agent_api import EmailClassifierApiBackend, EmailClassifierFallbackBackend
    from app.email_classifier_agent import EmailClassifierAgent, EmailClassifierRoutedBackend
    from app.email_store import EmailStore
    from app.email_worker import _build_email_source_factory, _reread_historical_candidate_message
    from app.managed_skills import runtime_skill_snapshot_for_process
    from app.store import AutoReplyStore

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--categories", nargs="+", default=list(TARGETING_PATTERNS))
    parser.add_argument("--limit-per-category", type=int, default=20)
    parser.add_argument("--preview", action="store_true", help="list candidates only; no Agent calls, no writes")
    parser.add_argument("--service-pid", type=int, required=False, help="pid of the running service, to reuse its loaded classifier Skill")
    parser.add_argument("--account", help="with --gmail-query: the account to search; it may be disabled")
    parser.add_argument("--gmail-query", help="Gmail search (X-GM-RAW), e.g. category:purchases")
    parser.add_argument("--target", default="", help="with --gmail-query: the category this search aims at, for the report")
    args = parser.parse_args(list(argv) if argv is not None else None)

    load_env_file()
    db_path = Path(os.environ.get("CEO_WORKER_DB") or worker_db_path())
    email_store = EmailStore(db_path)
    cache_path = db_path.parent / "email-models" / "provider-training-observations.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    classified = frozenset(
        str(row["stable_message_identity"])
        for row in email_store.list_selected_training_records()
    ) | frozenset(
        identity
        for identity in _all_classified_identities(email_store)
    )
    source_factory = _build_email_source_factory(SimpleNamespace())
    fetched: dict[str, Mapping[str, object]] = {}
    search_source = None
    if args.gmail_query:
        account = email_store.get_account(args.account or "")
        if account is None:
            parser.error("--gmail-query needs --account naming a configured account")
        search_source = source_factory(account)
        found = search_candidates(
            search_source,
            account_id=str(account["account_id"]),
            query=args.gmail_query,
            targeted_category=args.target,
            classified_identities=classified,
            limit=args.limit_per_category,
        )
        candidates = [candidate for candidate, _message in found]
        fetched = {candidate.stable_message_identity: message for candidate, message in found}
    else:
        candidates = select_candidates(
            cache,
            classified_identities=classified,
            categories=args.categories,
            limit_per_category=args.limit_per_category,
        )
    if args.preview:
        for candidate in candidates:
            print(json.dumps({
                "targeted_category": candidate.targeted_category,
                "uid": candidate.uid,
                "subject": candidate.subject[:80],
                "sender": candidate.sender,
            }, ensure_ascii=False))
        print(json.dumps({"candidates": len(candidates)}))
        if search_source is not None:
            search_source.logout()
        return 0

    if not args.service_pid:
        parser.error("--service-pid is required to reuse the running classifier Skill")
    task_store = AutoReplyStore(db_path)
    snapshot = runtime_skill_snapshot_for_process(task_store, pid=args.service_pid)
    if snapshot is None:
        raise SystemExit("no classifier Skill snapshot is loaded by that service process")
    runtime_config = load_runtime_config(os.environ)
    route = next((item for item in runtime_config.routes if item.name == "codex_api"), None)
    secret = runtime_config.secret_for("codex_api")
    if route is None or secret is None:
        raise SystemExit("codex_api runtime route and key are required")
    agent = EmailClassifierAgent(
        EmailClassifierFallbackBackend(
            EmailClassifierApiBackend(
                base_url=runtime_config.codex_api_base_url,
                model=route.model,
                api_key=secret.get_secret_value(),
            ),
            EmailClassifierRoutedBackend(
                build_production_routed_codex_execution(
                    store=task_store,
                    workspace=workspace_path(),
                    total_timeout_seconds=300.0,
                    idle_timeout_seconds=120.0,
                )
            ),
            recorder=lambda event: print(json.dumps(event, ensure_ascii=False)),
        ),
        runtime_skill_snapshot=snapshot,
        skill_name="ceo-email-classifier",
    )
    contexts: dict[str, object] = {}
    counts: dict[str, int] = {}
    for candidate in candidates:
        account = email_store.get_account(candidate.account_id)
        context = contexts.setdefault(candidate.account_id, _scan_context(email_store, candidate.account_id))

        def read_current_message(item: LabelCandidate, account=account) -> Mapping[str, object]:
            if item.stable_message_identity in fetched:
                return fetched[item.stable_message_identity]
            return _reread_historical_candidate_message(source_factory, account, item)

        try:
            outcome = label_candidate(
                candidate,
                email_store=email_store,
                agent=agent,
                read_current_message=read_current_message,
                context=context,
            )
        except Exception as exc:  # one unreadable message must not stop the batch
            outcome = {"identity": candidate.stable_message_identity, "outcome": "error", "error": f"{type(exc).__name__}:{exc}"[:200]}
        key = f"{outcome['outcome']}:{outcome.get('category') or ''}"
        counts[key] = counts.get(key, 0) + 1
        print(json.dumps(outcome, ensure_ascii=False))
    if search_source is not None:
        search_source.logout()
    print(json.dumps({"summary": counts}, ensure_ascii=False))
    return 0


def _all_classified_identities(email_store: object) -> frozenset[str]:
    with email_store._connect() as db:
        return frozenset(
            str(row["stable_message_identity"])
            for row in db.execute("select stable_message_identity from email_classifications")
        )


if __name__ == "__main__":
    raise SystemExit(main())
