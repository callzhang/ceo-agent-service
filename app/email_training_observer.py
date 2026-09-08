"""Bounded, durable provider observations for folder-derived training snapshots."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any

from app.email_important import ImportantSignals
from app.email_imap_readonly import ProviderFolderFingerprint
from app.email_provider_folders import FolderRole
from app.email_training_snapshot import unsubscribe_training_features


_STATE_VERSION = 2
_PROCESS_LOCK = threading.RLock()


def provider_training_folder_is_relevant(
    folder: object, binding: Mapping[str, object] | None
) -> bool:
    """Limit provider-body reads to folders that can supply training labels."""

    return binding is not None or getattr(folder, "role", None) in {
        FolderRole.JUNK,
        FolderRole.TRASH,
    }


@dataclass(frozen=True)
class ProviderTrainingObservationResult:
    observations: tuple[dict[str, object], ...]
    more_available: bool
    unavailable_folders: tuple[str, ...] = ()


class ProviderTrainingObservationJob:
    """Advance each provider folder by at most one bounded UID batch."""

    def __init__(
        self,
        *,
        state_path: str | Path,
        source_factory: Callable[[Mapping[str, object]], object],
        email_store: object,
        batch_size: int = 200,
        reconciliation_batch_size: int = 50,
        include_folder: Callable[[object, Mapping[str, object] | None], bool]
        | None = None,
    ) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise TypeError("batch_size must be an integer")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if isinstance(reconciliation_batch_size, bool) or not isinstance(
            reconciliation_batch_size, int
        ):
            raise TypeError("reconciliation_batch_size must be an integer")
        if reconciliation_batch_size <= 0:
            raise ValueError("reconciliation_batch_size must be positive")
        self.state_path = Path(state_path)
        self.source_factory = source_factory
        self.email_store = email_store
        self.batch_size = batch_size
        self.reconciliation_batch_size = reconciliation_batch_size
        self.include_folder = include_folder or (lambda _folder, _binding: True)

    def run_once(
        self, accounts: Sequence[Mapping[str, object]]
    ) -> ProviderTrainingObservationResult:
        with _state_lock(self.state_path):
            state = _load_observation_state(self.state_path)
            updated = json.loads(json.dumps(state))
            bindings = tuple(self.email_store.list_account_folder_bindings())
            more_available = False
            unavailable: list[str] = []
            authoritative_folders: set[str] = set()
            active_account_ids: set[str] = set()
            for account in accounts:
                account_id = _required_text(account.get("account_id"), "account_id")
                active_account_ids.add(account_id)
                source = self.source_factory(account)
                try:
                    inventory = tuple(source.list_folders())
                    inventory = tuple(
                        folder
                        for folder in inventory
                        if self.include_folder(
                            folder,
                            _active_binding(
                                bindings,
                                account_id,
                                _required_text(
                                    folder.provider_folder_id,
                                    "provider_folder_id",
                                ),
                            ),
                        )
                    )
                    account_state = updated["accounts"].setdefault(
                        account_id, {"folders": {}}
                    )
                    folder_states = account_state["folders"]
                    current_ids = {
                        _required_text(folder.provider_folder_id, "provider_folder_id")
                        for folder in inventory
                    }
                    for deleted in set(folder_states) - current_ids:
                        authoritative_folders.add(f"{account_id}:{deleted}")
                        del folder_states[deleted]
                    for folder in inventory:
                        folder_id = _required_text(
                            folder.provider_folder_id, "provider_folder_id"
                        )
                        binding = _active_binding(bindings, account_id, folder_id)
                        role = (
                            FolderRole.CATEGORY if binding is not None else folder.role
                        )
                        folder_state = folder_states.setdefault(
                            folder_id,
                            _new_folder_state(),
                        )
                        _refresh_folder_truth(
                            folder_state,
                            account_id=account_id,
                            folder=folder,
                            role=role,
                            binding=binding,
                            email_store=self.email_store,
                        )
                        key = f"{account_id}:{folder_id}"
                        if folder_state["status"] in {
                            "uidvalidity_unavailable",
                            "membership_unavailable",
                        }:
                            unavailable.append(key)
                            continue
                        cursor_uidvalidity = folder_state["uidvalidity"]
                        highest_uid = int(folder_state["highest_uid"])
                        if (
                            cursor_uidvalidity is not None
                            and folder_state["observations"]
                        ):
                            membership_reader = getattr(
                                source, "fetch_uid_membership", None
                            )
                            if not callable(membership_reader):
                                folder_state["status"] = "membership_unavailable"
                                unavailable.append(key)
                                continue
                            selected_uids = _reconciliation_uids(
                                folder_state,
                                limit=self.reconciliation_batch_size,
                            )
                            membership = membership_reader(
                                folder.display_name,
                                cursor_uidvalidity=cursor_uidvalidity,
                                uids=selected_uids,
                            )
                            membership_uidvalidity = getattr(
                                membership, "uidvalidity", None
                            )
                            if (
                                isinstance(membership_uidvalidity, bool)
                                or not isinstance(membership_uidvalidity, int)
                                or membership_uidvalidity <= 0
                            ):
                                folder_state["status"] = "uidvalidity_unavailable"
                                unavailable.append(key)
                                continue
                            if membership_uidvalidity != cursor_uidvalidity:
                                folder_state["observations"] = {}
                                folder_state["highest_uid"] = 0
                                folder_state["reconcile_after_uid"] = 0
                                cursor_uidvalidity = membership_uidvalidity
                                highest_uid = 0
                            else:
                                existing_uids = frozenset(
                                    getattr(membership, "existing_uids", ())
                                )
                                if not existing_uids <= frozenset(selected_uids):
                                    raise ValueError(
                                        "provider membership returned unexpected UIDs"
                                    )
                                _apply_membership(
                                    folder_state,
                                    selected=selected_uids,
                                    existing=existing_uids,
                                    important_signals_by_uid=getattr(
                                        membership, "important_signals_by_uid", None
                                    ),
                                )
                                more_available = (
                                    more_available
                                    or int(folder_state["reconcile_after_uid"]) > 0
                                )
                        batch = source.fetch_uid_batch(
                            folder.display_name,
                            cursor_uidvalidity=cursor_uidvalidity,
                            last_seen_uid=highest_uid,
                            limit=self.batch_size,
                        )
                        uidvalidity = getattr(batch, "uidvalidity", None)
                        if (
                            isinstance(uidvalidity, bool)
                            or not isinstance(uidvalidity, int)
                            or uidvalidity <= 0
                        ):
                            folder_state["status"] = "uidvalidity_unavailable"
                            unavailable.append(key)
                            continue
                        if (
                            cursor_uidvalidity is not None
                            and uidvalidity != cursor_uidvalidity
                        ):
                            folder_state["observations"] = {}
                            folder_state["reconcile_after_uid"] = 0
                            highest_uid = 0
                        messages = tuple(batch.messages)
                        for message in messages:
                            uid = message.get("uid")
                            if (
                                isinstance(uid, bool)
                                or not isinstance(uid, int)
                                or uid <= 0
                            ):
                                raise ValueError(
                                    "provider message UID must be positive"
                                )
                            observation = _provider_observation(
                                account_id=account_id,
                                folder=folder,
                                role=role,
                                binding=binding,
                                message=message,
                                email_store=self.email_store,
                            )
                            folder_state["observations"][
                                observation["stable_message_identity"]
                            ] = {
                                "uid": uid,
                                "observation": _encode_observation(observation),
                            }
                            highest_uid = max(highest_uid, uid)
                        folder_state["uidvalidity"] = uidvalidity
                        folder_state["highest_uid"] = highest_uid
                        folder_state["status"] = "ready"
                        if (
                            int(folder_state["reconcile_after_uid"]) == 0
                            and len(messages) < self.batch_size
                        ):
                            authoritative_folders.add(key)
                        more_available = (
                            more_available or len(messages) == self.batch_size
                        )
                finally:
                    _close_source(source)
            for removed_account in set(updated["accounts"]) - active_account_ids:
                del updated["accounts"][removed_account]
            if not more_available:
                for account_state in updated["accounts"].values():
                    for folder_state in account_state["folders"].values():
                        if folder_state.get("fingerprint_migration") == "pending":
                            folder_state["fingerprint_migration"] = "complete"
            _write_state_atomic(self.state_path, updated)
            observations = _state_observations(updated)
            recorder = getattr(
                self.email_store, "record_current_provider_observations", None
            )
            if callable(recorder):
                recorder(
                    observations,
                    unavailable_folders=tuple(sorted(unavailable)),
                    authoritative_folders=tuple(sorted(authoritative_folders)),
                    active_account_ids=tuple(sorted(active_account_ids)),
                    observed_at=datetime.now(timezone.utc).isoformat(),
                )
            return ProviderTrainingObservationResult(
                observations=observations,
                more_available=more_available,
                unavailable_folders=tuple(sorted(unavailable)),
            )

    def cached_observations(self) -> tuple[dict[str, object], ...]:
        with _state_lock(self.state_path):
            return _state_observations(_load_observation_state(self.state_path))

    def begin_reconciliation_generation(self, generation: int) -> None:
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise TypeError("reconciliation generation must be an integer")
        if generation <= 0:
            raise ValueError("reconciliation generation must be positive")
        with _state_lock(self.state_path):
            state = _load_observation_state(self.state_path)
            if state.get("reconciliation_generation") == generation:
                return
            for account_state in state["accounts"].values():
                for folder_state in account_state["folders"].values():
                    folder_state["reconcile_after_uid"] = 0
            state["reconciliation_generation"] = generation
            _write_state_atomic(self.state_path, state)

    def initialized(self) -> bool:
        return self.state_path.exists()

    def detect_changes_once(
        self,
        accounts: Sequence[Mapping[str, object]],
        *,
        on_change: Callable[[str], object] | None = None,
    ) -> bool:
        """Persist lightweight folder state and report only observed changes."""

        with _state_lock(self.state_path):
            existed = self.state_path.exists()
            state = _load_observation_state(self.state_path)
            updated = json.loads(json.dumps(state))
            changed = False
            change_evidence: list[object] = []
            active_account_ids: set[str] = set()
            for account in accounts:
                account_id = _required_text(account.get("account_id"), "account_id")
                active_account_ids.add(account_id)
                source = self.source_factory(account)
                try:
                    inventory = tuple(
                        folder
                        for folder in source.list_folders()
                        if folder.role not in {FolderRole.SENT, FolderRole.DRAFT}
                    )
                    account_preexisted = account_id in updated["accounts"]
                    account_state = updated["accounts"].setdefault(
                        account_id, {"folders": {}}
                    )
                    folder_states = account_state["folders"]
                    current_ids = {
                        _required_text(folder.provider_folder_id, "provider_folder_id")
                        for folder in inventory
                    }
                    if account_preexisted and set(folder_states) != current_ids:
                        changed = True
                        change_evidence.append(
                            ("folder-inventory", account_id, sorted(current_ids))
                        )
                    for folder in inventory:
                        folder_id = _required_text(
                            folder.provider_folder_id, "provider_folder_id"
                        )
                        folder_state = folder_states.setdefault(
                            folder_id, _new_folder_state()
                        )
                        fingerprint = source.fetch_folder_fingerprint(
                            folder.display_name
                        )
                        if type(fingerprint) is not ProviderFolderFingerprint:
                            raise TypeError("provider folder fingerprint is invalid")
                        encoded = {
                            "uidvalidity": fingerprint.uidvalidity,
                            "uidnext": fingerprint.uidnext,
                            "exists": fingerprint.exists,
                            "highest_modseq": fingerprint.highest_modseq,
                        }
                        previous = folder_state.get("fingerprint")
                        migration = folder_state.get("fingerprint_migration")
                        if migration is None:
                            if previous is None and folder_state["observations"]:
                                folder_state["fingerprint_migration"] = "pending"
                                changed = True
                                change_evidence.append(
                                    ("fingerprint-migration", account_id, folder_id)
                                )
                            else:
                                folder_state["fingerprint_migration"] = "complete"
                        elif migration == "pending":
                            changed = True
                            change_evidence.append(
                                ("fingerprint-migration", account_id, folder_id)
                            )
                        if previous is not None and previous != encoded:
                            changed = True
                            change_evidence.append(
                                ("fingerprint", account_id, folder_id, encoded)
                            )
                        folder_state["fingerprint"] = encoded
                        if (
                            fingerprint.highest_modseq is None
                            and folder_state["observations"]
                        ):
                            selected_uids = _change_probe_uids(
                                folder_state,
                                limit=self.reconciliation_batch_size,
                            )
                            membership = source.fetch_uid_membership(
                                folder.display_name,
                                cursor_uidvalidity=fingerprint.uidvalidity,
                                uids=selected_uids,
                            )
                            if _membership_differs(
                                folder_state,
                                selected=selected_uids,
                                membership=membership,
                            ):
                                changed = True
                                change_evidence.append(
                                    (
                                        "membership",
                                        account_id,
                                        folder_id,
                                        selected_uids,
                                        sorted(membership.existing_uids),
                                        {
                                            str(uid): {
                                                "raw": list(signals.raw_signal_names),
                                                "important": signals.provider_important,
                                            }
                                            for uid, signals in sorted(
                                                membership.important_signals_by_uid.items()
                                            )
                                        },
                                    )
                                )
                finally:
                    _close_source(source)
            if existed and set(updated["accounts"]) != active_account_ids:
                changed = True
                change_evidence.append(
                    ("account-inventory", sorted(active_account_ids))
                )
            if changed and on_change is not None:
                on_change(_change_request_key(change_evidence))
            _write_state_atomic(self.state_path, updated)
            return changed


class ProviderTrainingChangeDetector:
    """Run lightweight provider detection outside the realtime scan loop."""

    def __init__(
        self,
        *,
        job: ProviderTrainingObservationJob,
        accounts_loader: Callable[[], Sequence[Mapping[str, object]]],
        request: Callable[[str | None], object],
    ) -> None:
        self.job = job
        self.accounts_loader = accounts_loader
        self.request = request

    def tick(self) -> bool:
        return self.job.detect_changes_once(
            tuple(self.accounts_loader()), on_change=self.request
        )


class TrainingObservationCoordinator:
    """Durably request observation work without provider I/O on the scan thread."""

    def __init__(
        self,
        *,
        request_state_path: str | Path,
        job: object,
        accounts_loader: Callable[[], Sequence[Mapping[str, object]]],
        publish: Callable[[Sequence[Mapping[str, object]]], object],
        description_version_loader: Callable[[], str] | None = None,
    ) -> None:
        self.request_state_path = Path(request_state_path)
        self.job = job
        self.accounts_loader = accounts_loader
        self.publish = publish
        self.description_version_loader = description_version_loader

    def request_initialization(self) -> None:
        if not self.job.initialized():
            self.request()

    def request(self, dedupe_key: str | None = None) -> bool:
        if dedupe_key is not None and (
            not isinstance(dedupe_key, str) or not dedupe_key.strip()
        ):
            raise ValueError("observation request dedupe key must be non-empty")
        with _state_lock(self.request_state_path):
            state = _load_request_state(self.request_state_path)
            if (
                dedupe_key is not None
                and state["requested_generation"] > state["handled_generation"]
                and state["last_request_key"] == dedupe_key
            ):
                return False
            state["requested_generation"] += 1
            state["last_request_key"] = dedupe_key
            _write_state_atomic(self.request_state_path, state)
            return True

    def tick(self) -> object | None:
        with _state_lock(self.request_state_path):
            state = _load_request_state(self.request_state_path)
            requested = state["requested_generation"]
            processing = state["processing_generation"]
            if processing is None and requested > state["handled_generation"]:
                processing = requested
                state["processing_generation"] = processing
                _write_state_atomic(self.request_state_path, state)
            description_version = (
                self.description_version_loader()
                if self.description_version_loader is not None
                else None
            )
            description_changed = (
                description_version is not None
                and description_version != state["published_description_version"]
            )
            provider_requested = processing is not None
            if not provider_requested and not description_changed:
                return None
        if provider_requested:
            self.job.begin_reconciliation_generation(processing)
            result = self.job.run_once(tuple(self.accounts_loader()))
        else:
            result = ProviderTrainingObservationResult(
                observations=self.job.cached_observations(), more_available=False
            )
        if result.more_available:
            return None
        publication = self.publish(result.observations)
        with _state_lock(self.request_state_path):
            state = _load_request_state(self.request_state_path)
            if provider_requested and not result.more_available:
                if state["processing_generation"] != processing:
                    raise ValueError("observation processing generation changed")
                state["handled_generation"] = max(
                    state["handled_generation"], processing
                )
                state["processing_generation"] = None
            if description_version is not None:
                state["published_description_version"] = description_version
            _write_state_atomic(self.request_state_path, state)
        return publication


def _provider_observation(
    *,
    account_id: str,
    folder: object,
    role: FolderRole,
    binding: Mapping[str, object] | None,
    message: Mapping[str, object],
    email_store: object,
) -> dict[str, object]:
    stable_identity = _required_text(
        message.get("stableMessageIdentity"), "stableMessageIdentity"
    )
    unsubscribe_headers = {
        "list-unsubscribe": message.get("listUnsubscribe", ""),
        "list-unsubscribe-post": message.get("listUnsubscribePost", ""),
    }
    return {
        "account_id": account_id,
        "stable_message_identity": stable_identity,
        "provider_folder_id": folder.provider_folder_id,
        "provider_folder_name": folder.display_name,
        "folder_role": role,
        "bound_category_key": None if binding is None else binding["category_key"],
        "folder_binding_status": (
            "unbound" if binding is None else binding["binding_status"]
        ),
        "processed_by_email_service": email_store.has_stable_classification(
            stable_identity
        ),
        "important_signals": message["importantSignals"],
        "sender": message.get("from", {}),
        "to_recipients": message.get("toRecipients", ()),
        "cc_recipients": message.get("ccRecipients", ()),
        "subject": message.get("subject", ""),
        "body": message.get("textBody", ""),
        "headers": {
            "message-id": message.get("messageId", ""),
            "in-reply-to": message.get("inReplyTo", ""),
            "references": " ".join(message.get("references", ())),
            "auto-submitted": message.get("autoSubmitted", ""),
        },
        "unsubscribe_features": unsubscribe_training_features(unsubscribe_headers),
        "attachments": message.get("attachments", ()),
        "provider_thread_id": message.get("threadId"),
        "explicit_matter_group": None,
        "source": "natural",
        "received_at": message.get("date", ""),
    }


def _refresh_folder_truth(
    folder_state: dict[str, object],
    *,
    account_id: str,
    folder: object,
    role: FolderRole,
    binding: Mapping[str, object] | None,
    email_store: object,
) -> None:
    for cached in folder_state["observations"].values():
        encoded = cached["observation"]
        encoded["provider_folder_id"] = folder.provider_folder_id
        encoded["provider_folder_name"] = folder.display_name
        encoded["folder_role"] = role.value
        encoded["bound_category_key"] = (
            None if binding is None else binding["category_key"]
        )
        encoded["folder_binding_status"] = (
            "unbound" if binding is None else binding["binding_status"]
        )
        encoded["processed_by_email_service"] = email_store.has_stable_classification(
            encoded["stable_message_identity"]
        )


def _active_binding(
    bindings: Sequence[Mapping[str, object]], account_id: str, folder_id: str
) -> Mapping[str, object] | None:
    matches = tuple(
        row
        for row in bindings
        if row["account_id"] == account_id
        and row["provider_folder_id"] == folder_id
        and row["binding_status"] == "active"
    )
    if len(matches) > 1:
        raise ValueError("provider folder has multiple active category bindings")
    return matches[0] if matches else None


def _encode_observation(value: Mapping[str, object]) -> dict[str, object]:
    result = dict(value)
    signals = result["important_signals"]
    if type(signals) is not ImportantSignals:
        raise TypeError("important_signals must be ImportantSignals")
    result["important_signals"] = {
        "raw_signal_names": list(signals.raw_signal_names),
        "provider_important": signals.provider_important,
    }
    role = result["folder_role"]
    result["folder_role"] = role.value if type(role) is FolderRole else role
    return result


def _decode_observation(value: Mapping[str, object]) -> dict[str, object]:
    result = dict(value)
    signals = result.get("important_signals")
    if not isinstance(signals, Mapping):
        raise ValueError("cached important signals are invalid")
    result["important_signals"] = ImportantSignals(
        tuple(signals.get("raw_signal_names", ())),
        signals.get("provider_important"),
    )
    result["folder_role"] = FolderRole(result["folder_role"])
    return result


def _state_observations(state: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    rows = [
        _decode_observation(observation)
        for account in state["accounts"].values()
        for folder in account["folders"].values()
        for cached in folder["observations"].values()
        for observation in (cached["observation"],)
    ]
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                str(row["account_id"]),
                str(row["stable_message_identity"]),
                str(row["provider_folder_id"]),
            ),
        )
    )


def _reconciliation_uids(
    folder_state: dict[str, object], *, limit: int
) -> tuple[int, ...]:
    after_uid = int(folder_state["reconcile_after_uid"])
    all_uids = sorted(
        int(cached["uid"]) for cached in folder_state["observations"].values()
    )
    selected = [uid for uid in all_uids if uid > after_uid][:limit]
    if not selected:
        folder_state["reconcile_after_uid"] = 0
        selected = all_uids[:limit]
    return tuple(selected)


def _apply_membership(
    folder_state: dict[str, object],
    *,
    selected: tuple[int, ...],
    existing: frozenset[int],
    important_signals_by_uid: object,
) -> None:
    if not isinstance(important_signals_by_uid, Mapping):
        raise ValueError("provider membership important signals are missing")
    if frozenset(important_signals_by_uid) != existing:
        raise ValueError("provider membership important signals are incomplete")
    if any(
        type(signals) is not ImportantSignals
        for signals in important_signals_by_uid.values()
    ):
        raise TypeError("provider membership important signals are invalid")
    selected_set = frozenset(selected)
    stale = [
        identity
        for identity, cached in folder_state["observations"].items()
        if int(cached["uid"]) in selected_set and int(cached["uid"]) not in existing
    ]
    for identity in stale:
        del folder_state["observations"][identity]
    for cached in folder_state["observations"].values():
        uid = int(cached["uid"])
        if uid in existing:
            cached["observation"]["important_signals"] = {
                "raw_signal_names": list(
                    important_signals_by_uid[uid].raw_signal_names
                ),
                "provider_important": important_signals_by_uid[uid].provider_important,
            }
    last_checked = max(selected)
    has_later = any(
        int(cached["uid"]) > last_checked
        for cached in folder_state["observations"].values()
    )
    folder_state["reconcile_after_uid"] = last_checked if has_later else 0


def _new_folder_state() -> dict[str, object]:
    return {
        "uidvalidity": None,
        "highest_uid": 0,
        "reconcile_after_uid": 0,
        "change_probe_after_uid": 0,
        "fingerprint": None,
        "fingerprint_migration": "complete",
        "status": "ready",
        "observations": {},
    }


def _change_probe_uids(
    folder_state: dict[str, object], *, limit: int
) -> tuple[int, ...]:
    after_uid = int(folder_state.get("change_probe_after_uid", 0))
    all_uids = sorted(
        int(cached["uid"]) for cached in folder_state["observations"].values()
    )
    selected = [uid for uid in all_uids if uid > after_uid][:limit]
    if not selected:
        selected = all_uids[:limit]
    last_checked = max(selected)
    folder_state["change_probe_after_uid"] = (
        last_checked if any(uid > last_checked for uid in all_uids) else 0
    )
    return tuple(selected)


def _membership_differs(
    folder_state: Mapping[str, object],
    *,
    selected: tuple[int, ...],
    membership: object,
) -> bool:
    uidvalidity = getattr(membership, "uidvalidity", None)
    if uidvalidity != folder_state["uidvalidity"]:
        return True
    existing = frozenset(getattr(membership, "existing_uids", ()))
    signals_by_uid = getattr(membership, "important_signals_by_uid", None)
    if not isinstance(signals_by_uid, Mapping):
        raise ValueError("provider membership important signals are missing")
    if existing != frozenset(selected) or frozenset(signals_by_uid) != existing:
        return True
    cached_by_uid = {
        int(cached["uid"]): _decode_observation(cached["observation"])[
            "important_signals"
        ]
        for cached in folder_state["observations"].values()
        if int(cached["uid"]) in existing
    }
    return any(cached_by_uid.get(uid) != signals_by_uid[uid] for uid in existing)


def _load_observation_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": _STATE_VERSION, "accounts": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("version") != _STATE_VERSION
        or not isinstance(value.get("accounts"), dict)
    ):
        raise ValueError("training observation state is invalid")
    return value


def _load_request_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "requested_generation": 0,
            "handled_generation": 0,
            "processing_generation": None,
            "published_description_version": None,
            "last_request_key": None,
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("training observation request state is invalid")
    requested = value.get("requested_generation")
    handled = value.get("handled_generation")
    processing = value.get("processing_generation")
    if (
        isinstance(requested, bool)
        or not isinstance(requested, int)
        or isinstance(handled, bool)
        or not isinstance(handled, int)
        or requested < handled
        or handled < 0
    ):
        raise ValueError("training observation request state is invalid")
    if processing is not None and (
        isinstance(processing, bool)
        or not isinstance(processing, int)
        or processing <= handled
        or processing > requested
    ):
        raise ValueError("training observation request state is invalid")
    description = value.get("published_description_version")
    if description is not None and (
        not isinstance(description, str) or not description.strip()
    ):
        raise ValueError("training observation request state is invalid")
    request_key = value.get("last_request_key")
    if request_key is not None and (
        not isinstance(request_key, str) or not request_key.strip()
    ):
        raise ValueError("training observation request state is invalid")
    return {
        "requested_generation": requested,
        "handled_generation": handled,
        "processing_generation": processing,
        "published_description_version": description,
        "last_request_key": request_key,
    }


def _change_request_key(evidence: Sequence[object]) -> str:
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"provider-change:{sha256(payload).hexdigest()}"


def _write_state_atomic(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        json.loads(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _state_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with _PROCESS_LOCK:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _close_source(source: object) -> None:
    close = getattr(source, "logout", None) or getattr(source, "close", None)
    if callable(close):
        close()


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()
