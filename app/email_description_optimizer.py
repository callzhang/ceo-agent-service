"""Evidence-bound category-description proposals for staged evaluation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Literal

from app.email_classifier_contracts import validate_email_category_key
from app.email_embedding_classifier import CategoryDescription


ProposalStatus = Literal["proposed", "evaluated", "accepted", "rejected"]
MAX_AGENT_CONFLICT_GROUPS = 20
MAX_REDACTED_EXAMPLE_CHARACTERS = 1_000
OPTIMIZER_INVOCATION_LEASE_SECONDS = 300


def description_set_digest(
    descriptions: Mapping[str, CategoryDescription],
) -> str:
    if not descriptions:
        raise ValueError("description set must not be empty")
    payload = []
    for category, description in descriptions.items():
        category = validate_email_category_key(category)
        if type(description) is not CategoryDescription:
            raise TypeError("description set values must be CategoryDescription")
        payload.append(
            {
                "category": category,
                "core": description.core,
                "include": list(description.include),
                "exclude": list(description.exclude),
                "version": description.version,
            }
        )
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def category_description_digest(description: CategoryDescription) -> str:
    if type(description) is not CategoryDescription:
        raise TypeError("description must be CategoryDescription")
    return sha256(
        json.dumps(
            {
                "core": description.core,
                "include": list(description.include),
                "exclude": list(description.exclude),
                "version": description.version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class DescriptionSetOverlay:
    proposal_id: str
    category: str
    source_description_version: str
    source_description_digest: str
    source_snapshot_id: str
    source_snapshot_sha: str
    conflict_category_pair: tuple[str, str]
    conflict_description_digests: tuple[str, str]
    descriptions: Mapping[str, CategoryDescription]
    description_set_digest: str

    def __post_init__(self) -> None:
        _text(self.proposal_id, "proposal_id")
        category = validate_email_category_key(self.category)
        _text(self.source_description_version, "source_description_version")
        _digest(self.source_description_digest, "source_description_digest")
        _text(self.source_snapshot_id, "source_snapshot_id")
        _digest(self.source_snapshot_sha, "source_snapshot_sha")
        values = dict(self.descriptions)
        if category not in values:
            raise ValueError("overlay category is absent from description set")
        calculated = description_set_digest(values)
        if calculated != _digest(self.description_set_digest, "description_set_digest"):
            raise ValueError("description set digest does not match overlay")
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "descriptions", MappingProxyType(values))

    @property
    def description_set_version(self) -> str:
        return "description-set-sha256:" + self.description_set_digest

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "category": self.category,
            "source_description_version": self.source_description_version,
            "source_description_digest": self.source_description_digest,
            "source_snapshot_id": self.source_snapshot_id,
            "source_snapshot_sha": self.source_snapshot_sha,
            "conflict_category_pair": list(self.conflict_category_pair),
            "conflict_description_digests": list(self.conflict_description_digests),
            "description_set_digest": self.description_set_digest,
            "description_set_version": self.description_set_version,
            "descriptions": [
                {
                    "category": category,
                    "core": description.core,
                    "include": list(description.include),
                    "exclude": list(description.exclude),
                    "version": description.version,
                }
                for category, description in self.descriptions.items()
            ],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "DescriptionSetOverlay":
        raw_descriptions = value.get("descriptions")
        if isinstance(raw_descriptions, (str, bytes)) or not isinstance(
            raw_descriptions, Sequence
        ):
            raise ValueError("description overlay set is invalid")
        descriptions = {}
        for raw in raw_descriptions:
            if not isinstance(raw, Mapping):
                raise ValueError("description overlay entry is invalid")
            category = validate_email_category_key(raw.get("category"))
            if category in descriptions:
                raise ValueError("description overlay category is duplicated")
            descriptions[category] = CategoryDescription(
                core=_text(raw.get("core"), "core"),
                include=tuple(_text_list(raw.get("include"), "include")),
                exclude=tuple(_text_list(raw.get("exclude"), "exclude")),
                version=_text(raw.get("version"), "version"),
            )
        overlay = cls(
            proposal_id=_text(value.get("proposal_id"), "proposal_id"),
            category=validate_email_category_key(value.get("category")),
            source_description_version=_text(
                value.get("source_description_version"),
                "source_description_version",
            ),
            source_description_digest=_digest(
                value.get("source_description_digest"), "source_description_digest"
            ),
            source_snapshot_id=_text(
                value.get("source_snapshot_id"), "source_snapshot_id"
            ),
            source_snapshot_sha=_digest(
                value.get("source_snapshot_sha"), "source_snapshot_sha"
            ),
            conflict_category_pair=tuple(value.get("conflict_category_pair") or ()),
            conflict_description_digests=tuple(
                value.get("conflict_description_digests") or ()
            ),
            descriptions=descriptions,
            description_set_digest=_digest(
                value.get("description_set_digest"), "description_set_digest"
            ),
        )
        if value.get("description_set_version") != overlay.description_set_version:
            raise ValueError("description set version does not match overlay")
        return overlay


def build_description_set_overlay(
    proposal: "DescriptionProposal",
    active_descriptions: Mapping[str, CategoryDescription],
) -> DescriptionSetOverlay:
    descriptions = dict(active_descriptions)
    pair = proposal.conflict_category_pair
    pair_digests = proposal.conflict_description_digests
    if (
        len(pair) != 2
        or len(pair_digests) != 2
        or len(set(pair)) != 2
        or proposal.category != pair[1]
    ):
        raise ValueError("conflict category binding is invalid")
    try:
        validated_pair = tuple(validate_email_category_key(key) for key in pair)
    except (TypeError, ValueError) as exc:
        raise ValueError("conflict category binding is invalid") from exc
    if validated_pair != pair or any(key not in descriptions for key in pair):
        raise ValueError("conflict category binding is invalid")
    for category, bound_digest in zip(pair, pair_digests, strict=True):
        try:
            bound_digest = _digest(bound_digest, "conflict_description_digest")
        except (TypeError, ValueError) as exc:
            raise ValueError("conflict category binding is invalid") from exc
        if category_description_digest(descriptions[category]) != bound_digest:
            raise ValueError("conflict description is no longer active")
    current = descriptions.get(proposal.category)
    if current is None:
        raise ValueError("proposal category is not active")
    if current.version != proposal.source_description_version:
        raise ValueError("proposal source description version is no longer active")
    if category_description_digest(current) != proposal.source_description_digest:
        raise ValueError("proposal source description content is no longer active")
    descriptions[proposal.category] = proposal.proposed
    digest = description_set_digest(descriptions)
    return DescriptionSetOverlay(
        proposal_id=proposal.proposal_id,
        category=proposal.category,
        source_description_version=proposal.source_description_version,
        source_description_digest=proposal.source_description_digest,
        source_snapshot_id=proposal.source_snapshot_id,
        source_snapshot_sha=proposal.source_snapshot_sha,
        conflict_category_pair=proposal.conflict_category_pair,
        conflict_description_digests=proposal.conflict_description_digests,
        descriptions=descriptions,
        description_set_digest=digest,
    )


@dataclass(frozen=True)
class ConflictExample:
    sample_id: str
    group_key: str
    predicted_category: str
    confirmed_category: str
    redacted_text: str


@dataclass(frozen=True)
class DescriptionProposal:
    proposal_id: str
    category: str
    source_description_version: str
    source_description_digest: str
    source_snapshot_id: str
    source_snapshot_sha: str
    proposed: CategoryDescription
    cited_sample_ids: tuple[str, ...]
    reason: str
    conflict_category_pair: tuple[str, str] = ()
    conflict_description_digests: tuple[str, str] = ()
    status: ProposalStatus = "proposed"
    evaluated_model_id: str | None = None
    evaluation_passed: bool | None = None
    evaluated_description_set_digest: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "category": self.category,
            "source_description_version": self.source_description_version,
            "source_description_digest": self.source_description_digest,
            "source_snapshot_id": self.source_snapshot_id,
            "source_snapshot_sha": self.source_snapshot_sha,
            "proposed": {
                "core": self.proposed.core,
                "include": list(self.proposed.include),
                "exclude": list(self.proposed.exclude),
                "version": self.proposed.version,
            },
            "cited_sample_ids": list(self.cited_sample_ids),
            "reason": self.reason,
            "conflict_category_pair": list(self.conflict_category_pair),
            "conflict_description_digests": list(self.conflict_description_digests),
            "status": self.status,
            "evaluated_model_id": self.evaluated_model_id,
            "evaluation_passed": self.evaluation_passed,
            "evaluated_description_set_digest": (self.evaluated_description_set_digest),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "DescriptionProposal":
        proposed = value.get("proposed")
        if not isinstance(proposed, Mapping):
            raise ValueError("persisted description proposal is invalid")
        status = value.get("status")
        if status not in {"proposed", "evaluated", "accepted", "rejected"}:
            raise ValueError("persisted description proposal status is invalid")
        model_id = value.get("evaluated_model_id")
        if model_id is not None:
            model_id = _text(model_id, "evaluated_model_id")
        evaluation_passed = value.get("evaluation_passed")
        if evaluation_passed is not None and type(evaluation_passed) is not bool:
            raise ValueError("evaluation_passed must be boolean or null")
        evaluated_digest = value.get("evaluated_description_set_digest")
        if evaluated_digest is not None:
            evaluated_digest = _digest(
                evaluated_digest, "evaluated_description_set_digest"
            )
        return cls(
            proposal_id=_text(value.get("proposal_id"), "proposal_id"),
            category=validate_email_category_key(value.get("category")),
            source_description_version=_text(
                value.get("source_description_version"),
                "source_description_version",
            ),
            source_description_digest=_digest(
                value.get("source_description_digest"), "source_description_digest"
            ),
            source_snapshot_id=_text(
                value.get("source_snapshot_id"), "source_snapshot_id"
            ),
            source_snapshot_sha=_digest(
                value.get("source_snapshot_sha"), "source_snapshot_sha"
            ),
            proposed=CategoryDescription(
                core=_text(proposed.get("core"), "core"),
                include=tuple(_text_list(proposed.get("include"), "include")),
                exclude=tuple(_text_list(proposed.get("exclude"), "exclude")),
                version=_text(proposed.get("version"), "version"),
            ),
            cited_sample_ids=tuple(
                _text_list(value.get("cited_sample_ids"), "cited_sample_ids")
            ),
            reason=_text(value.get("reason"), "reason"),
            conflict_category_pair=tuple(value.get("conflict_category_pair") or ()),
            conflict_description_digests=tuple(
                value.get("conflict_description_digests") or ()
            ),
            status=status,
            evaluated_model_id=model_id,
            evaluation_passed=evaluation_passed,
            evaluated_description_set_digest=evaluated_digest,
        )


class DescriptionProposalRepository:
    """Small durable proposal ledger separate from active category configuration."""

    def __init__(self, registry_root: str | Path) -> None:
        self.root = Path(registry_root)
        self.proposals = self.root / "description-proposals"
        self.overlays = self.root / "description-overlays"
        self.optimization_requests = self.root / "description-optimization-requests"
        self.proposals.mkdir(parents=True, exist_ok=True)
        self.overlays.mkdir(parents=True, exist_ok=True)
        self.optimization_requests.mkdir(parents=True, exist_ok=True)

    def persist(self, proposal: DescriptionProposal) -> Path:
        path = self._proposal_path(proposal.proposal_id)
        if path.exists():
            raise ValueError("description proposal already exists")
        _write_json(path, proposal.to_dict(), immutable=True)
        return path

    def get(self, proposal_id: str) -> DescriptionProposal:
        try:
            value = json.loads(
                self._proposal_path(proposal_id).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("description proposal cannot be loaded") from exc
        if not isinstance(value, Mapping):
            raise ValueError("description proposal cannot be loaded")
        return DescriptionProposal.from_mapping(value)

    def list_by_status(self, status: ProposalStatus) -> tuple[DescriptionProposal, ...]:
        if status not in {"proposed", "evaluated", "accepted", "rejected"}:
            raise ValueError("invalid description proposal status")
        return tuple(
            proposal
            for path in sorted(self.proposals.glob("*.json"))
            for proposal in (self.get(path.stem),)
            if proposal.status == status
        )

    def persist_overlay(self, overlay: DescriptionSetOverlay) -> Path:
        if type(overlay) is not DescriptionSetOverlay:
            raise TypeError("overlay must be DescriptionSetOverlay")
        path = self._overlay_path(overlay.proposal_id, overlay.description_set_digest)
        payload = overlay.to_dict()
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != payload:
                raise ValueError("description overlay conflicts with persisted data")
            return path
        _write_json(path, payload, immutable=True)
        return path

    def get_overlay(
        self, proposal_id: str, description_set_digest: str
    ) -> DescriptionSetOverlay:
        path = self._overlay_path(proposal_id, description_set_digest)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("description overlay cannot be loaded") from exc
        if not isinstance(value, Mapping):
            raise ValueError("description overlay cannot be loaded")
        return DescriptionSetOverlay.from_mapping(value)

    def record_evaluation(
        self, proposal_id: str, *, model_id: str
    ) -> DescriptionProposal:
        current = self.get(proposal_id)
        if current.status not in {"proposed", "evaluated"}:
            raise ValueError("description proposal is already decided")
        model_id = _text(model_id, "model_id")
        evidence_path = self.root / "staged-evidence" / f"{_safe_name(model_id)}.json"
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            compatibility = evidence["compatibility"]
            readiness = evidence["whole_model_readiness"]
            binding = evidence["description_proposal"]
            passed = readiness["ready"]
            if type(passed) is not bool:
                raise ValueError("candidate readiness must be boolean")
            overlay = self.get_overlay(
                current.proposal_id,
                _digest(
                    binding["description_set_digest"],
                    "description_set_digest",
                ),
            )
            if (
                evidence["model_id"] != model_id
                or binding["proposal_id"] != current.proposal_id
                or binding["source_description_version"]
                != current.source_description_version
                or binding["source_description_digest"]
                != current.source_description_digest
                or binding["source_snapshot_id"] != current.source_snapshot_id
                or binding["source_snapshot_sha"] != current.source_snapshot_sha
                or tuple(binding["conflict_category_pair"])
                != current.conflict_category_pair
                or tuple(binding["conflict_description_digests"])
                != current.conflict_description_digests
                or compatibility["description_version"]
                != "description-set-sha256:" + binding["description_set_digest"]
                or evidence["hashes"]["snapshot_sha256"] != current.source_snapshot_sha
                or evidence["hashes"]["description_sha256"]
                != binding["description_set_digest"]
                or overlay.category != current.category
                or overlay.source_description_version
                != current.source_description_version
                or overlay.source_description_digest
                != current.source_description_digest
                or overlay.source_snapshot_id != current.source_snapshot_id
                or overlay.source_snapshot_sha != current.source_snapshot_sha
                or overlay.conflict_category_pair != current.conflict_category_pair
                or overlay.conflict_description_digests
                != current.conflict_description_digests
                or overlay.descriptions[current.category] != current.proposed
            ):
                raise ValueError(
                    "candidate description evidence does not match proposal"
                )
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "candidate description evidence cannot be verified"
            ) from exc
        updated = DescriptionProposal(
            **{
                **current.__dict__,
                "status": "evaluated",
                "evaluated_model_id": model_id,
                "evaluation_passed": passed,
                "evaluated_description_set_digest": binding["description_set_digest"],
            }
        )
        _write_json(
            self._proposal_path(proposal_id), updated.to_dict(), immutable=False
        )
        return updated

    def decide(self, proposal_id: str, *, accept: bool) -> DescriptionProposal:
        if type(accept) is not bool:
            raise TypeError("accept must be boolean")
        current = self.get(proposal_id)
        if current.status != "evaluated" or current.evaluation_passed is None:
            raise ValueError("description proposal has not been evaluated")
        if accept and not current.evaluation_passed:
            raise ValueError("failing model cannot accept a description proposal")
        if (
            accept
            and current.evaluated_model_id is not None
            and self.bound_description(current.evaluated_model_id) is not None
        ):
            raise ValueError("model already has a description binding")
        updated = DescriptionProposal(
            **{
                **current.__dict__,
                "status": "accepted" if accept else "rejected",
            }
        )
        # One atomic replacement binds accepted status, description version,
        # and the passing candidate identifier together.
        _write_json(
            self._proposal_path(proposal_id), updated.to_dict(), immutable=False
        )
        return updated

    def bound_description(self, model_id: str) -> dict[str, object] | None:
        model_id = _text(model_id, "model_id")
        found = [
            proposal
            for path in self.proposals.glob("*.json")
            for proposal in (self.get(path.stem),)
            if proposal.status == "accepted" and proposal.evaluated_model_id == model_id
        ]
        if len(found) > 1:
            raise ValueError("model has multiple description bindings")
        if not found:
            return None
        return {
            "description_version": (
                "description-set-sha256:"
                + str(found[0].evaluated_description_set_digest)
            ),
            "description_set_digest": found[0].evaluated_description_set_digest,
            "proposal_id": found[0].proposal_id,
        }

    def _proposal_path(self, proposal_id: str) -> Path:
        return self.proposals / f"{_safe_name(proposal_id)}.json"

    def _overlay_path(self, proposal_id: str, digest: str) -> Path:
        return (
            self.overlays
            / f"{_safe_name(proposal_id)}-{_digest(digest, 'digest')}.json"
        )

    def claim_optimization(
        self,
        request_key: str,
        evidence: Mapping[str, object],
        *,
        now: datetime | None = None,
    ) -> bool:
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        current_time = current_time.astimezone(timezone.utc)
        path = (
            self.optimization_requests / f"{_digest(request_key, 'request_key')}.json"
        )
        payload = dict(evidence)
        lease_expires_at = (
            current_time + timedelta(seconds=OPTIMIZER_INVOCATION_LEASE_SECONDS)
        ).isoformat()
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, Mapping):
                raise ValueError("optimization request is invalid")
            bound = {
                key: value
                for key, value in existing.items()
                if key
                not in {
                    "status",
                    "attempt",
                    "workload_key",
                    "lease_expires_at",
                    "proposal_id",
                }
            }
            if bound != payload:
                raise ValueError("optimization request evidence changed")
            if existing.get("status") != "invoking":
                return False
            lease = datetime.fromisoformat(str(existing.get("lease_expires_at")))
            if lease.tzinfo is None or lease.utcoffset() is None:
                raise ValueError("optimization invocation lease is invalid")
            if current_time < lease.astimezone(timezone.utc):
                return False
            attempt = int(existing.get("attempt", 0)) + 1
            _write_json(
                path,
                {
                    **payload,
                    "status": "invoking",
                    "attempt": attempt,
                    "workload_key": request_key,
                    "lease_expires_at": lease_expires_at,
                },
                immutable=False,
            )
            return True
        _write_json(
            path,
            {
                **payload,
                "status": "invoking",
                "attempt": 1,
                "workload_key": request_key,
                "lease_expires_at": lease_expires_at,
            },
            immutable=True,
        )
        return True

    def complete_optimization(
        self, request_key: str, *, status: str, proposal_id: str | None = None
    ) -> None:
        if status not in {"proposed", "failed", "no_proposal"}:
            raise ValueError("invalid optimization request status")
        path = (
            self.optimization_requests / f"{_digest(request_key, 'request_key')}.json"
        )
        current = json.loads(path.read_text(encoding="utf-8"))
        _write_json(
            path,
            {**current, "status": status, "proposal_id": proposal_id},
            immutable=False,
        )


class DescriptionOptimizationOrchestrator:
    """Turn durable frozen conflicts into at-most-once bounded Agent proposals."""

    def __init__(self, *, store: object, registry: object, agent: Callable):
        self.store = store
        self.registry = registry
        self.agent = agent
        self.repository = DescriptionProposalRepository(registry.root)

    def observe_candidate(self, model_id: str) -> tuple[DescriptionProposal, ...]:
        evidence = self.registry.get_staged_evidence(model_id)
        snapshot_id = _text(evidence.get("source_snapshot_id"), "source_snapshot_id")
        snapshot_sha = _digest(
            evidence.get("hashes", {}).get("snapshot_sha256"), "snapshot_sha256"
        )
        snapshot = self.store.get_training_snapshot(snapshot_id)
        if snapshot is None or snapshot.get("snapshot_digest") != snapshot_sha:
            raise ValueError("candidate source snapshot is unavailable or corrupt")
        text_by_id = {
            str(row["stable_message_identity"]): str(row["normalized_model_input"])
            for row in snapshot["observations"]
        }
        raw_conflicts = list(evidence.get("classification_conflicts", ()))
        raw_conflicts.extend(
            self.store.list_provider_folder_correction_conflicts(snapshot_id)
        )
        configs = {
            row["category_key"]: row
            for row in self.store.list_category_configs()
            if row["enabled"]
        }
        current_descriptions = {
            key: CategoryDescription(
                core=row["core_description"],
                include=tuple(row["include"]),
                exclude=tuple(row["exclude"]),
                version=row["description_version"],
            )
            for key, row in configs.items()
        }
        conflicts = tuple(
            ConflictExample(
                sample_id=_text(row.get("sample_id"), "sample_id"),
                group_key=_text(row.get("group_key"), "group_key"),
                predicted_category=validate_email_category_key(
                    row.get("predicted_category")
                ),
                confirmed_category=validate_email_category_key(
                    row.get("confirmed_category")
                ),
                redacted_text=_redact_optimizer_evidence(
                    text_by_id.get(str(row.get("sample_id")), "")
                    or str(row.get("redacted_text") or ""),
                    tuple(current_descriptions.values()),
                ),
            )
            for row in raw_conflicts
        )
        proposals = []
        for pair, cluster in cluster_description_conflicts(conflicts).items():
            if len(cluster) < 5:
                continue
            target = pair[1]
            config = configs.get(target)
            if config is None or any(key not in current_descriptions for key in pair):
                continue
            pair_descriptions = {key: current_descriptions[key] for key in pair}
            pair_digests = tuple(
                category_description_digest(pair_descriptions[key]) for key in pair
            )
            groups = tuple(sorted(item.group_key for item in cluster))
            request_key = deterministic_optimization_request_key(
                snapshot_id=snapshot_id,
                snapshot_sha=snapshot_sha,
                category_pair=pair,
                group_keys=groups,
                description_digests=pair_digests,
            )
            aliased_cluster, sample_aliases, group_aliases = _opaque_conflict_aliases(
                cluster, request_key=request_key
            )
            request_evidence = {
                "source_snapshot_id": snapshot_id,
                "source_snapshot_sha": snapshot_sha,
                "category_pair": list(pair),
                "group_keys": list(groups),
                "description_digests": list(pair_digests),
                "sample_aliases": sample_aliases,
                "group_aliases": group_aliases,
            }
            if not self.repository.claim_optimization(request_key, request_evidence):
                continue
            current = CategoryDescription(
                core=config["core_description"],
                include=tuple(config["include"]),
                exclude=tuple(config["exclude"]),
                version=config["description_version"],
            )
            try:
                proposal = propose_description_update(
                    category=target,
                    current=current,
                    current_pair=pair_descriptions,
                    source_snapshot_id=snapshot_id,
                    source_snapshot_sha=snapshot_sha,
                    conflicts=aliased_cluster,
                    agent=self.agent,
                )
                if proposal is None:
                    self.repository.complete_optimization(
                        request_key, status="no_proposal"
                    )
                    continue
                if not set(proposal.cited_sample_ids) <= set(sample_aliases):
                    raise ValueError(
                        "description proposal cites an unbound sample alias"
                    )
                self.repository.persist(proposal)
                self.repository.complete_optimization(
                    request_key, status="proposed", proposal_id=proposal.proposal_id
                )
                proposals.append(proposal)
            except Exception:
                self.repository.complete_optimization(request_key, status="failed")
                raise
        return tuple(proposals)


def _opaque_conflict_aliases(
    conflicts: Sequence[ConflictExample], *, request_key: str
) -> tuple[
    tuple[ConflictExample, ...],
    dict[str, str],
    dict[str, str],
]:
    aliased: list[ConflictExample] = []
    sample_aliases: dict[str, str] = {}
    group_aliases: dict[str, str] = {}
    for index, conflict in enumerate(conflicts[:MAX_AGENT_CONFLICT_GROUPS], start=1):
        sample_digest = sha256(
            f"{request_key}\0sample\0{conflict.sample_id}".encode("utf-8")
        ).hexdigest()
        group_digest = sha256(
            f"{request_key}\0group\0{conflict.group_key}".encode("utf-8")
        ).hexdigest()
        sample_alias = f"sample-{index:03d}-{sample_digest[:12]}"
        group_alias = f"group-{index:03d}-{group_digest[:12]}"
        sample_aliases[sample_alias] = conflict.sample_id
        group_aliases[group_alias] = conflict.group_key
        aliased.append(
            ConflictExample(
                sample_id=sample_alias,
                group_key=group_alias,
                predicted_category=conflict.predicted_category,
                confirmed_category=conflict.confirmed_category,
                redacted_text=conflict.redacted_text,
            )
        )
    return tuple(aliased), sample_aliases, group_aliases


def _redact_optimizer_evidence(
    value: str, descriptions: Sequence[CategoryDescription]
) -> str:
    """Emit only allowlisted class terms and non-identifying structure."""

    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        payload = {"body": value}
    if not isinstance(payload, Mapping):
        payload = {}
    allowed = {
        token
        for description in descriptions
        for text in (description.core, *description.include, *description.exclude)
        for token in _evidence_tokens(text)
        if len(token) >= 3
    }
    subject = str(payload.get("subject") or "")
    body = str(payload.get("body") or "")
    matched = sorted(
        (set(_evidence_tokens(subject)) | set(_evidence_tokens(body))) & allowed
    )
    raw_headers = payload.get("headers")
    raw_attachments = payload.get("attachments")
    redacted = {
        "matched_class_terms": matched,
        "subject_length_bucket": min(len(subject) // 40, 5),
        "body_length_bucket": min(len(body) // 500, 10),
        "header_names": (
            sorted(str(name) for name in raw_headers)
            if isinstance(raw_headers, Mapping)
            else []
        ),
        "attachment_count": (
            len(raw_attachments) if isinstance(raw_attachments, list) else 0
        ),
        "attachment_mime_types": sorted(
            {
                str(item.get("mime_type"))
                for item in raw_attachments or ()
                if isinstance(item, Mapping) and item.get("mime_type")
            }
        ),
    }
    return json.dumps(
        redacted,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _evidence_tokens(value: str) -> tuple[str, ...]:
    result: list[str] = []
    current: list[str] = []
    for character in value.casefold():
        if character.isalpha():
            current.append(character)
        elif current:
            result.append("".join(current))
            current = []
    if current:
        result.append("".join(current))
    return tuple(result)


def _redact_free_text(value: str, private_values: Sequence[str]) -> str:
    redacted = value
    for private in sorted(set(private_values), key=len, reverse=True):
        redacted = _replace_case_insensitive(redacted, private, "[redacted]")
    redacted = "".join(
        _redact_token(token) if not token.isspace() else token
        for token in _split_preserving_whitespace(redacted)
    )
    return _redact_long_digit_runs(redacted)


def _replace_case_insensitive(value: str, target: str, replacement: str) -> str:
    if not target:
        return value
    result: list[str] = []
    folded_value = value.casefold()
    folded_target = target.casefold()
    start = 0
    while (index := folded_value.find(folded_target, start)) >= 0:
        result.extend((value[start:index], replacement))
        start = index + len(target)
    result.append(value[start:])
    return "".join(result)


def _split_preserving_whitespace(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    result: list[str] = []
    start = 0
    whitespace = value[0].isspace()
    for index, character in enumerate(value[1:], start=1):
        if character.isspace() != whitespace:
            result.append(value[start:index])
            start = index
            whitespace = character.isspace()
    result.append(value[start:])
    return tuple(result)


def _redact_token(token: str) -> str:
    trimmed = token.strip("<>()[]{}\"'.,;:")
    if "@" in trimmed or "://" in trimmed:
        return token.replace(trimmed, "[redacted]")
    return token


def _redact_long_digit_runs(value: str) -> str:
    separators = frozenset(" +-()./")
    result: list[str] = []
    index = 0
    while index < len(value):
        if not value[index].isdigit() and not (
            value[index] == "+"
            and index + 1 < len(value)
            and value[index + 1].isdigit()
        ):
            result.append(value[index])
            index += 1
            continue
        end = index
        digit_count = 0
        while end < len(value) and (value[end].isdigit() or value[end] in separators):
            digit_count += value[end].isdigit()
            end += 1
        if digit_count >= 6:
            result.append("[number]")
        else:
            result.append(value[index:end])
        index = end
    return "".join(result)


def deterministic_optimization_request_key(
    *,
    snapshot_id: str,
    snapshot_sha: str,
    category_pair: tuple[str, str],
    group_keys: Sequence[str],
    description_digests: Sequence[str] = (),
) -> str:
    return sha256(
        json.dumps(
            {
                "source_snapshot_id": _text(snapshot_id, "snapshot_id"),
                "source_snapshot_sha": _digest(snapshot_sha, "snapshot_sha"),
                "category_pair": list(category_pair),
                "group_keys": sorted({_text(item, "group_key") for item in group_keys}),
                "description_digests": [
                    _digest(item, "description_digest") for item in description_digests
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class RoutedDescriptionOptimizerAgent:
    """Production read-only bounded Agent adapter for description proposals."""

    def __init__(self, routed_execution: object):
        self.routed_execution = routed_execution

    def __call__(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        from app.agent_runtime_router import (
            ApprovedCodexCommandFactory,
            RoutedResultCodec,
        )

        request_key = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        prompt = (
            "Improve exactly one email category description from the bounded JSON "
            "evidence below. Return JSON only with complete core, include, exclude, "
            "cited_sample_ids, and reason fields.\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        result = self.routed_execution.execute(
            workload_kind="email_classification",
            workload_key="description-optimization:" + request_key,
            prompt=prompt,
            command_factory=ApprovedCodexCommandFactory.read_only_without_tools(
                developer_instructions=(
                    "You optimize one email category definition from supplied bounded "
                    "evidence. Perform no actions and return exactly one JSON object."
                ),
                use_output_schema=False,
            ),
            parser=lambda raw: raw.strip(),
            result_codec=RoutedResultCodec.text(
                schema_id="email.description-optimization-result.v1"
            ),
            conversation_id=None,
            required_capabilities=frozenset({"structured_output"}),
        )
        parsed = json.loads(result.value)
        if not isinstance(parsed, Mapping):
            raise ValueError("description optimizer Agent result must be an object")
        return parsed


def propose_description_update(
    *,
    category: str,
    current: CategoryDescription,
    current_pair: Mapping[str, CategoryDescription] | None = None,
    source_snapshot_id: str,
    source_snapshot_sha: str,
    conflicts: Sequence[ConflictExample],
    agent: Callable[[Mapping[str, object]], object],
) -> DescriptionProposal | None:
    """Ask once only when five independent conflict groups support the request."""

    category = validate_email_category_key(category)
    source_snapshot_id = _text(source_snapshot_id, "source_snapshot_id")
    source_snapshot_sha = _digest(source_snapshot_sha, "source_snapshot_sha")
    clusters = cluster_description_conflicts(conflicts)
    eligible = [
        (pair, examples)
        for pair, examples in clusters.items()
        if category in pair and len(examples) >= 5
    ]
    if not eligible:
        return None
    pair, selected_cluster = sorted(
        eligible, key=lambda item: (-len(item[1]), item[0])
    )[0]
    pair_descriptions = (
        dict(current_pair)
        if current_pair is not None
        else {key: current for key in pair}
    )
    if tuple(pair_descriptions) != pair or any(
        type(pair_descriptions[key]) is not CategoryDescription for key in pair
    ):
        raise ValueError("current_pair must contain the ordered conflict categories")
    pair_payload = {
        key: {
            "core": pair_descriptions[key].core,
            "include": list(pair_descriptions[key].include),
            "exclude": list(pair_descriptions[key].exclude),
            "version": pair_descriptions[key].version,
        }
        for key in pair
    }
    pair_digests = tuple(
        category_description_digest(pair_descriptions[key]) for key in pair
    )
    relevant = selected_cluster[:MAX_AGENT_CONFLICT_GROUPS]
    payload = {
        "category": category,
        "category_pair": list(pair),
        "current_pair": pair_payload,
        "current": {
            "core": current.core,
            "include": list(current.include),
            "exclude": list(current.exclude),
            "version": current.version,
        },
        "examples": [
            {
                "sample_id": item.sample_id,
                "group_key": item.group_key,
                "predicted_category": item.predicted_category,
                "confirmed_category": item.confirmed_category,
                "text": item.redacted_text[:MAX_REDACTED_EXAMPLE_CHARACTERS],
            }
            for item in relevant
        ],
        "required_output": (
            "complete core/include/exclude, cited_sample_ids, and reason"
        ),
    }
    raw = agent(payload)
    if not isinstance(raw, Mapping):
        raise ValueError("description optimizer result must be an object")
    required = {"core", "include", "exclude", "cited_sample_ids", "reason"}
    if set(raw) != required:
        raise ValueError("description optimizer result has an invalid schema")
    cited = tuple(_text_list(raw["cited_sample_ids"], "cited_sample_ids"))
    available = {item.sample_id for item in relevant}
    if not cited or not set(cited) <= available:
        raise ValueError("description proposal cites unknown samples")
    material = json.dumps(
        raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = sha256(
        (
            source_snapshot_id
            + source_snapshot_sha
            + json.dumps(pair_payload, sort_keys=True, separators=(",", ":"))
            + material
        ).encode("utf-8")
    ).hexdigest()
    proposed = CategoryDescription(
        core=_text(raw["core"], "core"),
        include=tuple(_text_list(raw["include"], "include")),
        exclude=tuple(_text_list(raw["exclude"], "exclude")),
        version=f"{current.version}+proposal-{digest[:12]}",
    )
    return DescriptionProposal(
        proposal_id=f"description-proposal-{digest[:20]}",
        category=category,
        source_description_version=current.version,
        source_description_digest=category_description_digest(current),
        source_snapshot_id=source_snapshot_id,
        source_snapshot_sha=source_snapshot_sha,
        proposed=proposed,
        cited_sample_ids=cited,
        reason=_text(raw["reason"], "reason"),
        conflict_category_pair=pair,
        conflict_description_digests=pair_digests,
    )


def cluster_description_conflicts(
    conflicts: Sequence[ConflictExample],
) -> dict[tuple[str, str], tuple[ConflictExample, ...]]:
    """Group false decisions by ordered category pair and independent matter."""

    grouped: dict[tuple[str, str], dict[str, ConflictExample]] = {}
    for item in conflicts:
        predicted = validate_email_category_key(item.predicted_category)
        confirmed = validate_email_category_key(item.confirmed_category)
        if predicted == confirmed:
            continue
        if not item.sample_id.strip() or not item.group_key.strip():
            raise ValueError("conflict identity and group must be non-empty")
        pair = (predicted, confirmed)
        grouped.setdefault(pair, {}).setdefault(item.group_key, item)
    return {
        pair: tuple(by_group[key] for key in sorted(by_group))
        for pair, by_group in sorted(grouped.items())
    }


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()


def _text_list(value: object, field: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a list")
    result = [_text(item, field) for item in value]
    if not result or len(result) != len(set(result)):
        raise ValueError(f"{field} must contain unique non-empty text")
    return result


def _safe_name(value: object) -> str:
    result = _text(value, "identifier")
    if Path(result).name != result:
        raise ValueError("identifier is not a safe path component")
    return result


def _digest(value: object, field: str) -> str:
    result = _text(value, field)
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise ValueError(f"{field} must be lowercase SHA-256")
    return result


def _write_json(path: Path, value: Mapping[str, object], *, immutable: bool) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return
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
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
