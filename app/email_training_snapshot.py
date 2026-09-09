"""Build immutable, provider-folder-derived email training snapshots."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unicodedata
from urllib.parse import urlsplit

from app.email_experiment_snapshot import deterministic_payload_digest
from app.email_classifier_retrain import retrain_state_reservation
from app.email_folder_truth import (
    EmailFolderTruthState,
    resolve_email_folder_truth,
)
from app.email_important import ImportantSignals, important_effective
from app.email_provider_folders import FolderRole, ProviderFolder


MODEL_INPUT_SCHEMA_VERSION = "email-folder-model-input-v2"
TRAINING_SNAPSHOT_VERSION = "email-folder-training-snapshot-v1"
MAX_BODY_CHARACTERS = 32_000
_BODY_HEAD_CHARACTERS = 24_000
_BODY_TAIL_CHARACTERS = MAX_BODY_CHARACTERS - _BODY_HEAD_CHARACTERS
_BODY_TRUNCATION_MARKER = "\n[...bounded-body-truncation...]\n"
_APPROVED_HEADERS = frozenset(
    {
        "message-id",
        "in-reply-to",
        "references",
        "auto-submitted",
    }
)
_SPLITS = frozenset({"train", "validation", "test"})
_SOURCES = frozenset({"natural", "targeted"})


class FolderTrainingSnapshotError(ValueError):
    """A provider observation cannot form a trustworthy frozen snapshot."""


@dataclass(frozen=True)
class TrainingSnapshotObservation:
    snapshot_id: str
    account_id: str
    stable_message_identity: str
    provider_folder_id: str
    provider_folder_name: str
    category_key: str | None
    important: bool
    normalized_model_input: str
    normalized_model_input_hash: str
    input_schema_version: str
    provider_thread_id: str | None
    normalized_body_digest: str
    sender_template_signature: str | None
    explicit_matter_group: str | None
    group_key: str
    observed_at: str
    source: str
    split: str
    selected_for_training: bool
    ordered_record_digest: str

    def to_dict(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


@dataclass(frozen=True)
class FolderTrainingSnapshot:
    snapshot_id: str
    snapshot_version: str
    description_version: str
    input_schema_version: str
    seed: int
    observed_at: str
    observations: tuple[TrainingSnapshotObservation, ...]
    snapshot_digest: str
    _manifest_json: str

    @property
    def manifest(self) -> dict[str, object]:
        return json.loads(self._manifest_json)

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "snapshot_version": self.snapshot_version,
            "description_version": self.description_version,
            "input_schema_version": self.input_schema_version,
            "seed": self.seed,
            "observed_at": self.observed_at,
            "observations": [row.to_dict() for row in self.observations],
            "snapshot_digest": self.snapshot_digest,
            "manifest": self.manifest,
        }


@dataclass(frozen=True)
class SnapshotObservationEventResult:
    observation_digest: str
    deduplicated: bool
    publication: object | None


@dataclass(frozen=True)
class EmailTrainingSnapshotPublicationJob:
    """Production Task 5 builder that publishes only through the learning service."""

    store: object
    learning_service: object
    observation_loader: Callable[[], Sequence[Mapping[str, object]]]
    description_version_loader: Callable[[], str]
    seed: int = 20260905

    def run_once(self, *, now: datetime | None = None):
        result = self.publish_observations(self.observation_loader(), now=now)
        return result.publication

    def publish_observations(
        self,
        observations: Sequence[Mapping[str, object]],
        *,
        now: datetime | None = None,
    ) -> SnapshotObservationEventResult:
        """Publish one actual provider-change observation, once per durable digest."""

        observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        observations = tuple(observations)
        identity_digest = deterministic_payload_digest(
            sorted(_identity(item) for item in observations)
        )
        snapshot_id = (
            "email-folder-snapshot-"
            + observed_at.strftime("%Y%m%dT%H%M%S.%fZ-")
            + identity_digest[:12]
        )
        snapshot = build_folder_training_snapshot(
            observations,
            snapshot_id=snapshot_id,
            description_version=self.description_version_loader(),
            observed_at=observed_at,
            seed=self.seed,
        )
        observation_digest = _snapshot_observation_digest(snapshot)
        state_path = Path(self.learning_service.registry.root) / (
            "training-observation-event.json"
        )
        with retrain_state_reservation(state_path):
            event = _load_observation_event(state_path)
            if (
                event is not None
                and event["observation_digest"] == observation_digest
                and event["status"] in {"pending-trigger", "published"}
            ):
                return SnapshotObservationEventResult(
                    observation_digest=observation_digest,
                    deduplicated=True,
                    publication=None,
                )
            if event is not None and event["observation_digest"] == observation_digest:
                observed_at = datetime.fromisoformat(str(event["observed_at"]))
                snapshot = build_folder_training_snapshot(
                    observations,
                    snapshot_id=str(event["snapshot_id"]),
                    description_version=str(event["description_version"]),
                    observed_at=observed_at,
                    seed=self.seed,
                )
            else:
                event = {
                    "observation_digest": observation_digest,
                    "status": "claimed",
                    "snapshot_id": snapshot.snapshot_id,
                    "snapshot_sha": snapshot.snapshot_digest,
                    "observed_at": snapshot.observed_at,
                    "description_version": snapshot.description_version,
                }
                _save_observation_event(state_path, event)
            stored = self.store.get_training_snapshot(snapshot.snapshot_id)
            if stored is None:
                publication = self.learning_service.publish_training_snapshot(
                    snapshot, now=observed_at
                )
            else:
                if stored["snapshot_digest"] != snapshot.snapshot_digest:
                    raise FolderTrainingSnapshotError(
                        "claimed training snapshot content changed"
                    )
                publication = self.learning_service.observe_snapshot_and_maybe_retrain(
                    now=observed_at
                )
            status = (
                "pending-trigger"
                if getattr(publication, "pending_trigger", False)
                else "published"
            )
            _save_observation_event(state_path, {**event, "status": status})
        return SnapshotObservationEventResult(
            observation_digest=observation_digest,
            deduplicated=False,
            publication=publication,
        )


def _snapshot_observation_digest(snapshot: FolderTrainingSnapshot) -> str:
    excluded = {"snapshot_id", "observed_at", "ordered_record_digest"}
    return deterministic_payload_digest(
        {
            "snapshot_version": snapshot.snapshot_version,
            "description_version": snapshot.description_version,
            "input_schema_version": snapshot.input_schema_version,
            "seed": snapshot.seed,
            "observations": [
                {
                    key: value
                    for key, value in row.to_dict().items()
                    if key not in excluded
                }
                for row in snapshot.observations
            ],
        }
    )


def _load_observation_event(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise FolderTrainingSnapshotError(
            "training observation event state is corrupt"
        ) from exc
    if not isinstance(value, Mapping):
        raise FolderTrainingSnapshotError("training observation event is invalid")
    digest = value.get("observation_digest")
    if not isinstance(digest, str) or len(digest) != 64:
        raise FolderTrainingSnapshotError(
            "training observation event digest is invalid"
        )
    if value.get("status") not in {"claimed", "pending-trigger", "published"}:
        raise FolderTrainingSnapshotError(
            "training observation event status is invalid"
        )
    if not isinstance(value.get("snapshot_id"), str) or not value["snapshot_id"]:
        raise FolderTrainingSnapshotError("training observation snapshot id is invalid")
    _digest_text = value.get("snapshot_sha")
    if not isinstance(_digest_text, str) or len(_digest_text) != 64:
        raise FolderTrainingSnapshotError(
            "training observation snapshot SHA is invalid"
        )
    _validate_timestamp_text(str(value.get("observed_at") or ""), "observed_at")
    if (
        not isinstance(value.get("description_version"), str)
        or not value["description_version"]
    ):
        raise FolderTrainingSnapshotError(
            "training observation description version is invalid"
        )
    return dict(value)


def mark_observation_trigger_handled(
    registry_root: str | Path, snapshot_sha: str
) -> None:
    """Mark the matching durable pending trigger handled after terminal polling."""

    state_path = Path(registry_root) / "training-observation-event.json"
    with retrain_state_reservation(state_path):
        event = _load_observation_event(state_path)
        if (
            event is None
            or event["status"] != "pending-trigger"
            or event["snapshot_sha"] != snapshot_sha
        ):
            return
        _save_observation_event(state_path, {**event, "status": "published"})


def _save_observation_event(path: Path, value: Mapping[str, object]) -> None:
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
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class _Candidate:
    account_id: str
    stable_message_identity: str
    provider_folder_id: str
    provider_folder_name: str
    category_key: str | None
    important: bool
    normalized_model_input: str
    normalized_model_input_hash: str
    provider_thread_id: str | None
    normalized_body_digest: str
    sender_template_signature: str | None
    explicit_matter_group: str | None
    observed_at: str
    source: str


def _merge_provider_folder_observations(
    observations: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise FolderTrainingSnapshotError("observation must be an object")
        grouped[_identity(observation)].append(observation)
    merged: list[Mapping[str, object]] = []
    for identity in sorted(grouped):
        copies = grouped[identity]
        highest = max(_folder_authority(item) for item in copies)
        authoritative = [item for item in copies if _folder_authority(item) == highest]
        truths = {_folder_truth_key(item) for item in authoritative}
        if len(truths) != 1:
            raise FolderTrainingSnapshotError(
                f"conflicting folder truth for stable identity {identity}"
            )
        winner = min(
            authoritative,
            key=lambda item: (
                _required_text(item.get("provider_folder_id"), "provider_folder_id"),
                _required_text(
                    item.get("provider_folder_name"), "provider_folder_name"
                ),
            ),
        )
        signals = [item.get("important_signals") for item in copies]
        if any(type(item) is not ImportantSignals for item in signals):
            raise FolderTrainingSnapshotError(
                "important_signals must be an ImportantSignals observation"
            )
        raw_names = tuple(
            sorted({name for item in signals for name in item.raw_signal_names})
        )
        merged.append(
            {
                **winner,
                "important_signals": ImportantSignals(
                    raw_names,
                    any(item.provider_important for item in signals),
                ),
            }
        )
    return tuple(merged)


def _folder_authority(value: Mapping[str, object]) -> int:
    role = _folder_role(value.get("folder_role"))
    if role in {FolderRole.JUNK, FolderRole.TRASH}:
        return 4
    if role is FolderRole.CATEGORY:
        return 3
    if role is FolderRole.INBOX:
        return 2
    return 1


def _folder_truth_key(value: Mapping[str, object]) -> str | None:
    role = _folder_role(value.get("folder_role"))
    if role in {FolderRole.JUNK, FolderRole.TRASH}:
        return "junk"
    if role is FolderRole.CATEGORY:
        return _optional_text(value.get("bound_category_key"), "bound_category_key")
    return None


def build_folder_training_snapshot(
    observations: Sequence[Mapping[str, object]],
    *,
    snapshot_id: str,
    description_version: str,
    observed_at: datetime,
    seed: int,
    proposed_splits: Mapping[str, str] | None = None,
) -> FolderTrainingSnapshot:
    """Freeze current provider state without consulting classification history."""

    snapshot_identity = _required_text(snapshot_id, "snapshot_id")
    description = _required_text(description_version, "description_version")
    observed_timestamp = _timestamp(observed_at, "observed_at")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise FolderTrainingSnapshotError("seed must be a non-negative integer")
    merged_observations = _merge_provider_folder_observations(observations)
    candidates = tuple(
        candidate
        for item in merged_observations
        if (candidate := _candidate(item, observed_at=observed_timestamp)) is not None
    )
    candidate_identities = {item.stable_message_identity for item in candidates}
    proposed = _validate_proposed_splits(proposed_splits, candidate_identities)
    group_keys = _group_keys(candidates)
    splits = _split_groups(group_keys, proposed=proposed, seed=seed)
    initially_frozen = tuple(
        TrainingSnapshotObservation(
            snapshot_id=snapshot_identity,
            account_id=item.account_id,
            stable_message_identity=item.stable_message_identity,
            provider_folder_id=item.provider_folder_id,
            provider_folder_name=item.provider_folder_name,
            category_key=item.category_key,
            important=item.important,
            normalized_model_input=item.normalized_model_input,
            normalized_model_input_hash=item.normalized_model_input_hash,
            input_schema_version=MODEL_INPUT_SCHEMA_VERSION,
            provider_thread_id=item.provider_thread_id,
            normalized_body_digest=item.normalized_body_digest,
            sender_template_signature=item.sender_template_signature,
            explicit_matter_group=item.explicit_matter_group,
            group_key=group_keys[item.stable_message_identity],
            observed_at=item.observed_at,
            source=item.source,
            split=splits[item.stable_message_identity],
            selected_for_training=False,
            ordered_record_digest="",
        )
        for item in sorted(candidates, key=lambda row: row.stable_message_identity)
    )
    selected, conflicts = _balanced_training_selection(initially_frozen, seed=seed)
    frozen = tuple(
        replace(
            row,
            selected_for_training=row.stable_message_identity in selected,
            ordered_record_digest=_record_digest(
                row,
                selected_for_training=row.stable_message_identity in selected,
            ),
        )
        for row in initially_frozen
    )
    manifest_without_digest = _manifest(
        frozen,
        description_version=description,
        seed=seed,
        observed_at=observed_timestamp,
        conflicts=conflicts,
    )
    digest = deterministic_payload_digest(manifest_without_digest)
    manifest = {**manifest_without_digest, "overall_sha256": digest}
    snapshot = FolderTrainingSnapshot(
        snapshot_id=snapshot_identity,
        snapshot_version=TRAINING_SNAPSHOT_VERSION,
        description_version=description,
        input_schema_version=MODEL_INPUT_SCHEMA_VERSION,
        seed=seed,
        observed_at=observed_timestamp,
        observations=frozen,
        snapshot_digest=digest,
        _manifest_json=_canonical_json(manifest),
    )
    validate_folder_training_snapshot(snapshot)
    return snapshot


def validate_folder_training_snapshot(
    snapshot: object, *, allow_legacy_manifest: bool = False
) -> None:
    """Reject any in-memory mutation or inconsistent frozen evidence."""

    if type(snapshot) is not FolderTrainingSnapshot:
        raise TypeError("snapshot must be a FolderTrainingSnapshot")
    if snapshot.snapshot_version != TRAINING_SNAPSHOT_VERSION:
        raise FolderTrainingSnapshotError("unsupported training snapshot version")
    if snapshot.input_schema_version != MODEL_INPUT_SCHEMA_VERSION:
        raise FolderTrainingSnapshotError("unsupported model input schema version")
    _validate_timestamp_text(snapshot.observed_at, "observed_at")
    actual_manifest = snapshot.manifest
    legacy_unsigned_time = (
        allow_legacy_manifest and "observed_at" not in actual_manifest
    )
    identities = [row.stable_message_identity for row in snapshot.observations]
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise FolderTrainingSnapshotError(
            "snapshot observations are not uniquely ordered"
        )
    splits_by_group: dict[str, set[str]] = defaultdict(set)
    for row in snapshot.observations:
        if row.snapshot_id != snapshot.snapshot_id:
            raise FolderTrainingSnapshotError("snapshot observation identity mismatch")
        if row.input_schema_version != snapshot.input_schema_version:
            raise FolderTrainingSnapshotError("snapshot observation schema mismatch")
        if row.observed_at != snapshot.observed_at:
            raise FolderTrainingSnapshotError("snapshot observation time mismatch")
        if row.source not in _SOURCES or row.split not in _SPLITS:
            raise FolderTrainingSnapshotError("snapshot observation enum mismatch")
        if row.selected_for_training and (
            row.split != "train" or row.category_key is None
        ):
            raise FolderTrainingSnapshotError("invalid training selection")
        if (
            sha256(row.normalized_model_input.encode("utf-8")).hexdigest()
            != row.normalized_model_input_hash
        ):
            raise FolderTrainingSnapshotError("model input hash mismatch")
        expected_record_digest = _record_digest(
            row,
            selected_for_training=row.selected_for_training,
            include_observed_at=not legacy_unsigned_time,
        )
        if row.ordered_record_digest != expected_record_digest:
            raise FolderTrainingSnapshotError("observation digest mismatch")
        splits_by_group[row.group_key].add(row.split)
    if any(len(splits) != 1 for splits in splits_by_group.values()):
        raise FolderTrainingSnapshotError("group leakage in frozen snapshot")
    if legacy_unsigned_time:
        expected_manifest = _legacy_manifest(
            snapshot.observations,
            description_version=snapshot.description_version,
            seed=snapshot.seed,
            observed_at=snapshot.observed_at,
            include_counts="observation_count" in actual_manifest,
        )
        expected_digest = deterministic_payload_digest(expected_manifest)
        if actual_manifest != {
            **expected_manifest,
            "overall_sha256": expected_digest,
        }:
            raise FolderTrainingSnapshotError("legacy training manifest mismatch")
        if snapshot.snapshot_digest != expected_digest:
            raise FolderTrainingSnapshotError("legacy training digest mismatch")
        return
    expected_selected, conflicts = _balanced_training_selection(
        snapshot.observations,
        seed=snapshot.seed,
    )
    actual_selected = {
        row.stable_message_identity
        for row in snapshot.observations
        if row.selected_for_training
    }
    if actual_selected != expected_selected:
        raise FolderTrainingSnapshotError("training selection is not balanced")
    expected_manifest = _manifest(
        snapshot.observations,
        description_version=snapshot.description_version,
        seed=snapshot.seed,
        observed_at=snapshot.observed_at,
        conflicts=conflicts,
    )
    expected_digest = deterministic_payload_digest(expected_manifest)
    if actual_manifest != {
        **expected_manifest,
        "overall_sha256": expected_digest,
    }:
        raise FolderTrainingSnapshotError("training snapshot manifest mismatch")
    if snapshot.snapshot_digest != expected_digest:
        raise FolderTrainingSnapshotError("training snapshot digest mismatch")


def _legacy_manifest(
    observations: Sequence[TrainingSnapshotObservation],
    *,
    description_version: str,
    seed: int,
    observed_at: str,
    include_counts: bool,
) -> dict[str, object]:
    current = _manifest(
        observations,
        description_version=description_version,
        seed=seed,
        observed_at=observed_at,
        conflicts=_training_conflicts(observations),
    )
    current.pop("observed_at")
    if not include_counts:
        for field in (
            "observation_count",
            "selected_group_count",
            "selected_category_counts",
            "conflicted_group_count",
            "conflicted_groups",
        ):
            current.pop(field)
    return current


def _candidate(value: Mapping[str, object], *, observed_at: str) -> _Candidate | None:
    if not isinstance(value, Mapping):
        raise FolderTrainingSnapshotError("observation must be an object")
    role = _folder_role(value.get("folder_role"))
    account_id = _required_text(value.get("account_id"), "account_id")
    provider_folder_id = _required_text(
        value.get("provider_folder_id"), "provider_folder_id"
    )
    provider_folder_name = _required_text(
        value.get("provider_folder_name"), "provider_folder_name"
    )
    binding_category = _optional_text(
        value.get("bound_category_key"), "bound_category_key"
    )
    bindings: tuple[dict[str, object], ...] = ()
    if role is FolderRole.CATEGORY:
        binding_status = _required_text(
            value.get("folder_binding_status"), "folder_binding_status"
        )
        if binding_category is not None:
            bindings = (
                {
                    "account_id": account_id,
                    "provider_folder_id": provider_folder_id,
                    "category_key": binding_category,
                    "binding_status": binding_status,
                },
            )
    truth = resolve_email_folder_truth(
        account_id=account_id,
        current_provider_folder_id=provider_folder_id,
        provider_folders=(
            ProviderFolder(provider_folder_id, provider_folder_name, role),
        ),
        bindings=bindings,
    )
    if truth.state is EmailFolderTruthState.EXCLUDED:
        return None
    category: str | None
    if truth.state is EmailFolderTruthState.JUNK:
        category = "junk"
    elif truth.state is EmailFolderTruthState.CATEGORIZED:
        # Ordinary labels only cover mail this service processed; all-date junk is
        # the deliberate exception because the Agent processes unread mail only.
        if value.get("processed_by_email_service") is not True:
            return None
        category = truth.category_key
    else:
        category = None
    model_input, body_digest, template_signature = _model_input(value)
    signals = value.get("important_signals")
    if type(signals) is not ImportantSignals:
        raise FolderTrainingSnapshotError(
            "important_signals must be an ImportantSignals observation"
        )
    source = _required_text(value.get("source"), "source")
    if source not in _SOURCES:
        raise FolderTrainingSnapshotError("source must be natural or targeted")
    identity = _identity(value)
    return _Candidate(
        account_id=account_id,
        stable_message_identity=identity,
        provider_folder_id=provider_folder_id,
        provider_folder_name=provider_folder_name,
        category_key=category,
        important=important_effective(
            category=category or "unclassified",
            provider_signals=signals,
            model_important=False,
        ),
        normalized_model_input=model_input,
        normalized_model_input_hash=sha256(model_input.encode("utf-8")).hexdigest(),
        provider_thread_id=_optional_text(
            value.get("provider_thread_id"), "provider_thread_id"
        ),
        normalized_body_digest=body_digest,
        sender_template_signature=template_signature,
        explicit_matter_group=_optional_text(
            value.get("explicit_matter_group"), "explicit_matter_group"
        ),
        observed_at=observed_at,
        source=source,
    )


def provider_model_input_fields(message: Mapping[str, object]) -> dict[str, object]:
    """Map an already-read provider message to the training input fields.

    Shared by provider observations and online inference. Folder/label facts,
    store access and mailbox actions are deliberately outside this pure adapter.
    Preserve the established textBody and metadata-only attachment contract.
    """
    unsubscribe_headers = {
        "list-unsubscribe": message.get("listUnsubscribe", ""),
        "list-unsubscribe-post": message.get("listUnsubscribePost", ""),
    }
    return {
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
    }


def canonical_model_input(value: Mapping[str, object]) -> str:
    """Serialize model fields using the exact existing snapshot normalization."""
    return _model_input(value)[0]


def _model_input(value: Mapping[str, object]) -> tuple[str, str, str | None]:
    sender = _address(value.get("sender"), "sender")
    to_recipients = _addresses(value.get("to_recipients"), "to_recipients")
    cc_recipients = _addresses(value.get("cc_recipients"), "cc_recipients")
    subject = _normalized_text(value.get("subject"), "subject")
    body = _bounded_body(value.get("body"))
    headers = _approved_headers(value.get("headers"))
    unsubscribe = (
        _validated_unsubscribe_features(value.get("unsubscribe_features"))
        if value.get("unsubscribe_features") is not None
        else unsubscribe_training_features(value.get("headers"))
    )
    attachments = _attachments(value.get("attachments"))
    meaningful = (
        sender["name"]
        or sender["email"]
        or to_recipients
        or cc_recipients
        or subject
        or body
        or headers
        or unsubscribe["has_unsubscribe"]
        or attachments
    )
    if not meaningful:
        raise FolderTrainingSnapshotError("model input is empty")
    payload = {
        "input_schema_version": MODEL_INPUT_SCHEMA_VERSION,
        "sender": sender,
        "to_recipients": to_recipients,
        "cc_recipients": cc_recipients,
        "subject": subject,
        "body": body,
        "headers": headers,
        "unsubscribe": unsubscribe,
        "attachment_count": len(attachments),
        "attachments": attachments,
    }
    canonical_body = " ".join(body.split()).casefold()
    body_digest = sha256(canonical_body.encode("utf-8")).hexdigest()
    sender_key = sender["email"].casefold() or sender["name"].casefold()
    subject_template = _subject_template(subject)
    signature = None
    if sender_key and subject_template:
        signature = deterministic_payload_digest([sender_key, subject_template])
    return _canonical_json(payload), body_digest, signature


def _address(value: object, field: str) -> dict[str, str]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise FolderTrainingSnapshotError(f"{field} must be an address object")
    return {
        "name": _normalized_text(value.get("name"), f"{field}.name"),
        "email": _normalized_text(value.get("email"), f"{field}.email").casefold(),
    }


def _addresses(value: object, field: str) -> list[dict[str, str]]:
    if value is None:
        value = ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise FolderTrainingSnapshotError(f"{field} must be a list")
    result = [_address(item, field) for item in value]
    return sorted(result, key=lambda item: (item["email"], item["name"]))


def _approved_headers(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise FolderTrainingSnapshotError("headers must be an object")
    result: dict[str, str] = {}
    for name, raw in value.items():
        if not isinstance(name, str):
            raise FolderTrainingSnapshotError("header names must be text")
        normalized_name = name.strip().casefold()
        if normalized_name in _APPROVED_HEADERS:
            result[normalized_name] = _normalized_text(raw, normalized_name)
    return dict(sorted(result.items()))


def unsubscribe_training_features(value: object) -> dict[str, object]:
    """Reduce private unsubscribe headers to non-reversible model features."""

    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise FolderTrainingSnapshotError("headers must be an object")
    normalized = {
        str(name).strip().casefold(): _normalized_text(raw, str(name))
        for name, raw in value.items()
        if isinstance(name, str)
    }
    unsubscribe = normalized.get("list-unsubscribe", "")
    post = normalized.get("list-unsubscribe-post", "")
    schemes: set[str] = set()
    hosts: set[str] = set()
    for candidate in _unsubscribe_candidates(unsubscribe):
        parsed = urlsplit(candidate)
        scheme = parsed.scheme.casefold()
        if scheme not in {"http", "https", "mailto"}:
            continue
        schemes.add(scheme)
        if scheme == "mailto":
            address = parsed.path.rsplit("@", 1)
            if len(address) == 2 and address[1]:
                hosts.add(address[1].casefold())
        elif parsed.hostname:
            hosts.add(parsed.hostname.casefold())
    raw_digest = sha256(f"{unsubscribe}\n{post}".encode("utf-8")).hexdigest()
    return {
        "has_unsubscribe": bool(unsubscribe or post),
        "hosts": sorted(hosts),
        "one_click": post.casefold() == "list-unsubscribe=one-click",
        "schemes": sorted(schemes),
        "value_sha256": raw_digest if unsubscribe or post else None,
    }


def _validated_unsubscribe_features(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise FolderTrainingSnapshotError("unsubscribe_features must be an object")
    if set(value) != {
        "has_unsubscribe",
        "hosts",
        "one_click",
        "schemes",
        "value_sha256",
    }:
        raise FolderTrainingSnapshotError("unsubscribe_features shape is invalid")
    has_unsubscribe = value["has_unsubscribe"]
    one_click = value["one_click"]
    if type(has_unsubscribe) is not bool or type(one_click) is not bool:
        raise FolderTrainingSnapshotError("unsubscribe feature flags must be boolean")
    schemes = value["schemes"]
    hosts = value["hosts"]
    if not isinstance(schemes, list) or any(
        scheme not in {"http", "https", "mailto"} for scheme in schemes
    ):
        raise FolderTrainingSnapshotError("unsubscribe schemes are invalid")
    if not isinstance(hosts, list) or any(
        not isinstance(host, str) or not host or urlsplit(f"//{host}").hostname != host
        for host in hosts
    ):
        raise FolderTrainingSnapshotError("unsubscribe hosts are invalid")
    digest = value["value_sha256"]
    if digest is not None and (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise FolderTrainingSnapshotError("unsubscribe digest is invalid")
    if has_unsubscribe is False and digest is not None:
        raise FolderTrainingSnapshotError("unsubscribe digest has no source value")
    return {
        "has_unsubscribe": has_unsubscribe,
        "hosts": sorted(set(hosts)),
        "one_click": one_click,
        "schemes": sorted(set(schemes)),
        "value_sha256": digest,
    }


def _unsubscribe_candidates(value: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for segment in value.split(","):
        candidate = segment.strip()
        if candidate.startswith("<") and candidate.endswith(">"):
            candidate = candidate[1:-1].strip()
        if candidate:
            candidates.append(candidate)
    return tuple(candidates)


def _attachments(value: object) -> list[dict[str, object]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise FolderTrainingSnapshotError("attachments must be a list")
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise FolderTrainingSnapshotError("attachment must be an object")
        size = item.get("size_bytes", 0)
        inline = item.get("inline", False)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise FolderTrainingSnapshotError(
                "attachment size_bytes must be non-negative"
            )
        if type(inline) is not bool:
            raise FolderTrainingSnapshotError("attachment inline must be boolean")
        result.append(
            {
                "filename": _normalized_text(item.get("filename"), "filename"),
                "mime_type": _normalized_text(item.get("mime_type"), "mime_type"),
                "size_bytes": size,
                "inline": inline,
                "content_id": _normalized_text(item.get("content_id"), "content_id"),
                "disposition": _normalized_text(
                    item.get("disposition"), "disposition"
                ).casefold(),
            }
        )
    return sorted(result, key=_canonical_json)


def _bounded_body(value: object) -> str:
    body = _normalized_text(value, "body", preserve_lines=True)
    if len(body) <= MAX_BODY_CHARACTERS:
        return body
    return (
        body[:_BODY_HEAD_CHARACTERS]
        + _BODY_TRUNCATION_MARKER
        + body[-_BODY_TAIL_CHARACTERS:]
    )


def _subject_template(value: str) -> str:
    result: list[str] = []
    previous_numeric = False
    for character in value.casefold():
        category = unicodedata.category(character)
        numeric = category.startswith("N")
        if numeric:
            if not previous_numeric:
                result.append("#")
        elif category.startswith(("L", "M")):
            result.append(character)
        else:
            result.append(" ")
        previous_numeric = numeric
    return " ".join("".join(result).split())


def _group_keys(candidates: Sequence[_Candidate]) -> dict[str, str]:
    parent = {
        item.stable_message_identity: item.stable_message_identity
        for item in candidates
    }

    def find(identity: str) -> str:
        while parent[identity] != identity:
            parent[identity] = parent[parent[identity]]
            identity = parent[identity]
        return identity

    def union(first: str, second: str) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parent[max(first_root, second_root)] = min(first_root, second_root)

    relationships: dict[tuple[str, str], str] = {}
    for item in candidates:
        values = (
            ("thread", item.provider_thread_id),
            ("body", item.normalized_body_digest if _body_has_content(item) else None),
            ("template", item.sender_template_signature),
            ("matter", item.explicit_matter_group),
        )
        for kind, value in values:
            if not value:
                continue
            relationship = (kind, value)
            previous = relationships.setdefault(
                relationship, item.stable_message_identity
            )
            union(previous, item.stable_message_identity)
    components: dict[str, list[str]] = defaultdict(list)
    for identity in parent:
        components[find(identity)].append(identity)
    result: dict[str, str] = {}
    for identities in components.values():
        group_key = deterministic_payload_digest(sorted(identities))
        for identity in identities:
            result[identity] = group_key
    return result


def _body_has_content(item: _Candidate) -> bool:
    payload = json.loads(item.normalized_model_input)
    return bool(payload["body"])


def _validate_proposed_splits(
    proposed: Mapping[str, str] | None, identities: set[str]
) -> dict[str, str]:
    if proposed is None:
        return {}
    if not isinstance(proposed, Mapping):
        raise FolderTrainingSnapshotError("proposed_splits must be an object")
    unknown = set(proposed) - identities
    if unknown:
        raise FolderTrainingSnapshotError("proposed split contains unknown identity")
    result: dict[str, str] = {}
    for identity, split in proposed.items():
        if split not in _SPLITS:
            raise FolderTrainingSnapshotError("unsupported split")
        result[identity] = split
    return result


def _split_groups(
    group_keys: Mapping[str, str],
    *,
    proposed: Mapping[str, str],
    seed: int,
) -> dict[str, str]:
    identities_by_group: dict[str, list[str]] = defaultdict(list)
    for identity, group in group_keys.items():
        identities_by_group[group].append(identity)
    split_by_group: dict[str, str] = {}
    for group, identities in sorted(identities_by_group.items()):
        explicit = {proposed[item] for item in identities if item in proposed}
        if len(explicit) > 1:
            raise FolderTrainingSnapshotError("group leakage across proposed splits")
        if explicit:
            split = explicit.pop()
        else:
            bucket = int(deterministic_payload_digest([seed, group])[:8], 16) % 100
            split = "train" if bucket < 80 else "validation" if bucket < 90 else "test"
        split_by_group[group] = split
    return {identity: split_by_group[group] for identity, group in group_keys.items()}


def _balanced_training_selection(
    observations: Sequence[TrainingSnapshotObservation], *, seed: int
) -> tuple[set[str], tuple[dict[str, object], ...]]:
    rows_by_group: dict[str, list[TrainingSnapshotObservation]] = defaultdict(list)
    for row in observations:
        if row.category_key is not None:
            rows_by_group[row.group_key].append(row)
    by_category: dict[str, list[TrainingSnapshotObservation]] = defaultdict(list)
    conflicts = list(_training_conflicts(observations))
    conflicted_groups = {str(conflict["group_key"]) for conflict in conflicts}
    eligible_categories: set[str] = set()
    for group_key, rows in sorted(rows_by_group.items()):
        categories = sorted({str(row.category_key) for row in rows})
        if group_key in conflicted_groups:
            continue
        category = categories[0]
        eligible_categories.add(category)
        if rows[0].split != "train":
            continue
        representative = min(
            rows,
            key=lambda item: (
                deterministic_payload_digest(
                    [
                        seed,
                        category,
                        group_key,
                        item.source,
                        item.stable_message_identity,
                    ]
                ),
                item.stable_message_identity,
            ),
        )
        by_category[category].append(representative)
    missing_categories = sorted(eligible_categories - set(by_category))
    if missing_categories:
        raise FolderTrainingSnapshotError(
            "snapshot is untrainable: categories without train groups: "
            + ", ".join(missing_categories)
        )
    category_cap = min(
        (len(rows) for rows in by_category.values() if rows),
        default=0,
    )
    selected: set[str] = set()
    for category, rows in by_category.items():
        ordered = sorted(
            rows,
            key=lambda item: (
                deterministic_payload_digest(
                    [seed, category, item.group_key, item.stable_message_identity]
                ),
                item.stable_message_identity,
            ),
        )
        selected.update(row.stable_message_identity for row in ordered[:category_cap])
    return selected, tuple(conflicts)


def _training_conflicts(
    observations: Sequence[TrainingSnapshotObservation],
) -> tuple[dict[str, object], ...]:
    categories_by_group: dict[str, set[str]] = defaultdict(set)
    for row in observations:
        if row.category_key is not None:
            categories_by_group[row.group_key].add(row.category_key)
    return tuple(
        {
            "group_key": group_key,
            "categories": sorted(categories),
            "reason": "multiple_category_labels",
        }
        for group_key, categories in sorted(categories_by_group.items())
        if len(categories) > 1
    )


def _record_digest(
    row: TrainingSnapshotObservation,
    *,
    selected_for_training: bool,
    include_observed_at: bool = True,
) -> str:
    payload = {
        "account_id": row.account_id,
        "stable_message_identity": row.stable_message_identity,
        "provider_folder_id": row.provider_folder_id,
        "provider_folder_name": row.provider_folder_name,
        "category_key": row.category_key,
        "important": row.important,
        "normalized_model_input_hash": row.normalized_model_input_hash,
        "input_schema_version": row.input_schema_version,
        "provider_thread_id": row.provider_thread_id,
        "normalized_body_digest": row.normalized_body_digest,
        "sender_template_signature": row.sender_template_signature,
        "explicit_matter_group": row.explicit_matter_group,
        "group_key": row.group_key,
        "source": row.source,
        "split": row.split,
        "selected_for_training": selected_for_training,
    }
    if include_observed_at:
        payload["observed_at"] = row.observed_at
    return deterministic_payload_digest(payload)


def _manifest(
    observations: Sequence[TrainingSnapshotObservation],
    *,
    description_version: str,
    seed: int,
    observed_at: str,
    conflicts: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    source_counts = {source: 0 for source in sorted(_SOURCES)}
    training_source_counts = {source: 0 for source in sorted(_SOURCES)}
    category_counts: dict[str, int] = defaultdict(int)
    selected_category_counts: dict[str, int] = defaultdict(int)
    group_counts: dict[str, int] = defaultdict(int)
    for row in observations:
        source_counts[row.source] += 1
        if row.selected_for_training:
            training_source_counts[row.source] += 1
            assert row.category_key is not None
            selected_category_counts[row.category_key] += 1
        if row.category_key is not None:
            category_counts[row.category_key] += 1
        group_counts[row.group_key] += 1
    return {
        "snapshot_version": TRAINING_SNAPSHOT_VERSION,
        "description_version": description_version,
        "input_schema_version": MODEL_INPUT_SCHEMA_VERSION,
        "seed": seed,
        "observed_at": observed_at,
        "observation_count": len(observations),
        "ordered_stable_ids": [row.stable_message_identity for row in observations],
        "input_hashes": {
            row.stable_message_identity: row.normalized_model_input_hash
            for row in observations
        },
        "ordered_record_digests": [row.ordered_record_digest for row in observations],
        "split_assignment": {
            row.stable_message_identity: row.split for row in observations
        },
        "training_selection": [
            row.stable_message_identity
            for row in observations
            if row.selected_for_training
        ],
        "selected_group_count": sum(row.selected_for_training for row in observations),
        "source_distribution": source_counts,
        "training_source_distribution": training_source_counts,
        "category_counts": dict(sorted(category_counts.items())),
        "selected_category_counts": {
            category: selected_category_counts[category]
            for category in sorted(category_counts)
        },
        "group_counts": dict(sorted(group_counts.items())),
        "conflicted_group_count": len(conflicts),
        "conflicted_groups": [dict(conflict) for conflict in conflicts],
    }


def _identity(value: Mapping[str, object]) -> str:
    if not isinstance(value, Mapping):
        raise FolderTrainingSnapshotError("observation must be an object")
    return _required_text(
        value.get("stable_message_identity"), "stable_message_identity"
    )


def _folder_role(value: object) -> FolderRole:
    try:
        return value if type(value) is FolderRole else FolderRole(value)
    except (TypeError, ValueError) as exc:
        raise FolderTrainingSnapshotError("unsupported folder role") from exc


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FolderTrainingSnapshotError(f"{field} must be non-empty text")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FolderTrainingSnapshotError(f"{field} must be text or null")
    return value.strip() or None


def _normalized_text(value: object, field: str, *, preserve_lines: bool = False) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise FolderTrainingSnapshotError(f"{field} must be text")
    normalized = (
        unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    )
    if preserve_lines:
        return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()
    return " ".join(normalized.split())


def _timestamp(value: object, field: str) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise FolderTrainingSnapshotError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _validate_timestamp_text(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise FolderTrainingSnapshotError(f"{field} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FolderTrainingSnapshotError(
            f"{field} must be a canonical UTC timestamp"
        ) from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or _timestamp(parsed, field) != value
    ):
        raise FolderTrainingSnapshotError(f"{field} must be a canonical UTC timestamp")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
