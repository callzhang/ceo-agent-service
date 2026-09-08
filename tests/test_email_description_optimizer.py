import json
from datetime import datetime, timedelta, timezone

import pytest

from app.email_description_optimizer import (
    ConflictExample,
    DescriptionProposal,
    DescriptionSetOverlay,
    build_description_set_overlay,
    category_description_digest,
    description_set_digest,
    propose_description_update,
    DescriptionProposalRepository,
    DescriptionOptimizationOrchestrator,
)
from app.email_embedding_classifier import CategoryDescription
from app.email_classifier_learning import EmailClassifierLearningService
from app.email_classifier_retrain import (
    RetrainState,
    SnapshotTrainingSignal,
    TrainingSubprocessRun,
    load_retrain_state,
    save_retrain_state,
)
from app.email_model_registry import EmailModelRegistry


def test_optimizer_invocation_lease_recovers_hard_crash_idempotently(tmp_path):
    repository = DescriptionProposalRepository(tmp_path / "registry")
    request_key = "a" * 64
    evidence = {"source_snapshot_id": "snapshot-1", "category_pair": ["work", "legal"]}
    now = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)

    assert repository.claim_optimization(request_key, evidence, now=now) is True
    assert (
        repository.claim_optimization(
            request_key, evidence, now=now + timedelta(seconds=299)
        )
        is False
    )
    assert (
        repository.claim_optimization(
            request_key, evidence, now=now + timedelta(seconds=301)
        )
        is True
    )
    assert (
        repository.claim_optimization(
            request_key, evidence, now=now + timedelta(seconds=302)
        )
        is False
    )

    request = json.loads(
        next(repository.optimization_requests.glob("*.json")).read_text()
    )
    assert request["status"] == "invoking"
    assert request["attempt"] == 2
    assert request["workload_key"] == request_key


CURRENT = CategoryDescription(
    core="External legal rights and obligations.",
    include=("Contracts sent by external parties.",),
    exclude=("Ordinary internal project coordination.",),
    version="legal-v1",
)
ACTIVE_DESCRIPTIONS = {
    "work": CategoryDescription(
        core="Routine business work.",
        include=("Projects and operations.",),
        exclude=("External legal rights and obligations.",),
        version="work-v1",
    ),
    "legal": CURRENT,
}


def _examples(count: int):
    return tuple(
        ConflictExample(
            sample_id=f"sample-{index}",
            group_key=f"matter-{index}",
            predicted_category="work",
            confirmed_category="legal",
            redacted_text=f"redacted example {index}",
        )
        for index in range(count)
    )


def test_description_agent_requires_five_independent_conflicting_groups() -> None:
    calls = []
    result = propose_description_update(
        category="legal",
        current=CURRENT,
        source_snapshot_id="snapshot-1",
        source_snapshot_sha="a" * 64,
        conflicts=_examples(4),
        agent=lambda payload: calls.append(payload),
    )
    assert result is None
    assert calls == []


def test_description_proposal_is_complete_cited_and_does_not_mutate_active() -> None:
    seen = []

    def agent(payload):
        seen.append(payload)
        return {
            "core": "External legal rights, obligations, disputes, and compliance.",
            "include": ["External contracts and regulatory notices."],
            "exclude": ["Routine delivery and internal project coordination."],
            "cited_sample_ids": [f"sample-{index}" for index in range(5)],
            "reason": "Five independent matters were confused with work.",
        }

    proposal = propose_description_update(
        category="legal",
        current=CURRENT,
        current_pair=ACTIVE_DESCRIPTIONS,
        source_snapshot_id="snapshot-1",
        source_snapshot_sha="a" * 64,
        conflicts=_examples(5),
        agent=agent,
    )

    assert isinstance(proposal, DescriptionProposal)
    assert proposal.status == "proposed"
    assert proposal.source_description_version == "legal-v1"
    assert proposal.proposed.version != CURRENT.version
    assert set(proposal.cited_sample_ids) == {f"sample-{index}" for index in range(5)}
    assert CURRENT.core == "External legal rights and obligations."
    assert seen[0]["examples"][0]["text"] == "redacted example 0"


def test_proposal_builds_and_persists_full_immutable_description_set_overlay(
    tmp_path,
) -> None:
    proposal = propose_description_update(
        category="legal",
        current=CURRENT,
        current_pair=ACTIVE_DESCRIPTIONS,
        source_snapshot_id="snapshot-1",
        source_snapshot_sha="a" * 64,
        conflicts=_examples(5),
        agent=lambda _payload: {
            "core": "External contracts, disputes, rights, and obligations.",
            "include": ["Contracts and regulatory notices."],
            "exclude": ["Routine project delivery."],
            "cited_sample_ids": [f"sample-{index}" for index in range(5)],
            "reason": "Repeated independent work/legal confusion.",
        },
    )
    assert proposal is not None

    overlay = build_description_set_overlay(proposal, ACTIVE_DESCRIPTIONS)
    repository = DescriptionProposalRepository(tmp_path / "registry")
    repository.persist(proposal)
    repository.persist_overlay(overlay)
    loaded = repository.get_overlay(
        proposal.proposal_id, overlay.description_set_digest
    )

    assert isinstance(loaded, DescriptionSetOverlay)
    assert tuple(loaded.descriptions) == ("work", "legal")
    assert loaded.descriptions["work"] == ACTIVE_DESCRIPTIONS["work"]
    assert loaded.descriptions["legal"] == proposal.proposed
    assert loaded.description_set_digest == description_set_digest(loaded.descriptions)
    assert loaded.description_set_version == (
        "description-set-sha256:" + loaded.description_set_digest
    )
    assert ACTIVE_DESCRIPTIONS["legal"] == CURRENT
    changed_same_version = {
        **ACTIVE_DESCRIPTIONS,
        "legal": CategoryDescription(
            core="Changed without a version bump.",
            include=CURRENT.include,
            exclude=CURRENT.exclude,
            version=CURRENT.version,
        ),
    }
    with pytest.raises(ValueError, match="no longer active"):
        build_description_set_overlay(proposal, changed_same_version)

    changed_peer = {
        **ACTIVE_DESCRIPTIONS,
        "work": CategoryDescription(
            core="Changed peer definition.",
            include=ACTIVE_DESCRIPTIONS["work"].include,
            exclude=ACTIVE_DESCRIPTIONS["work"].exclude,
            version=ACTIVE_DESCRIPTIONS["work"].version,
        ),
    }
    with pytest.raises(ValueError, match="conflict description.*no longer active"):
        build_description_set_overlay(proposal, changed_peer)


@pytest.mark.parametrize(
    ("pair", "digests"),
    [
        (("work",), ("a" * 64,)),
        (("work", "work"), ("a" * 64, "a" * 64)),
        (("work", "legal"), ("a" * 64,)),
        (("legal", "work"), ("a" * 64, "b" * 64)),
        (("work", "personal"), ("a" * 64, "b" * 64)),
    ],
)
def test_description_overlay_rejects_invalid_conflict_bindings(pair, digests) -> None:
    proposal = propose_description_update(
        category="legal",
        current=CURRENT,
        current_pair=ACTIVE_DESCRIPTIONS,
        source_snapshot_id="snapshot-1",
        source_snapshot_sha="a" * 64,
        conflicts=_examples(5),
        agent=lambda _payload: {
            "core": "External contracts and disputes.",
            "include": ["Contracts."],
            "exclude": ["Routine delivery."],
            "cited_sample_ids": [f"sample-{index}" for index in range(5)],
            "reason": "Repeated independent conflicts.",
        },
    )
    assert proposal is not None
    invalid = DescriptionProposal(
        **{
            **proposal.__dict__,
            "conflict_category_pair": pair,
            "conflict_description_digests": digests,
        }
    )

    with pytest.raises(ValueError, match="conflict category binding"):
        build_description_set_overlay(invalid, ACTIVE_DESCRIPTIONS)


def test_record_evaluation_requires_aggregate_set_and_proposal_bindings(
    tmp_path,
) -> None:
    proposal = propose_description_update(
        category="legal",
        current=CURRENT,
        current_pair=ACTIVE_DESCRIPTIONS,
        source_snapshot_id="snapshot-1",
        source_snapshot_sha="a" * 64,
        conflicts=_examples(5),
        agent=lambda _payload: {
            "core": "External legal obligations and disputes.",
            "include": ["Contracts and regulatory notices."],
            "exclude": ["Routine project delivery."],
            "cited_sample_ids": [f"sample-{index}" for index in range(5)],
            "reason": "Repeated independent work/legal confusion.",
        },
    )
    assert proposal is not None
    repository = DescriptionProposalRepository(tmp_path / "registry")
    registry = EmailModelRegistry(tmp_path / "registry")
    repository.persist(proposal)
    overlay = build_description_set_overlay(proposal, ACTIVE_DESCRIPTIONS)
    repository.persist_overlay(overlay)
    registry.persist_staged_evidence(
        "email-embedding-mlp-candidate-1",
        {
            "model_id": "email-embedding-mlp-candidate-1",
            "compatibility": {"description_version": overlay.description_set_version},
            "whole_model_readiness": {"ready": False},
            "hashes": {
                "snapshot_sha256": "a" * 64,
                "description_sha256": overlay.description_set_digest,
            },
            "description_proposal": {
                "proposal_id": proposal.proposal_id,
                "source_description_version": proposal.source_description_version,
                "source_description_digest": proposal.source_description_digest,
                "source_snapshot_id": proposal.source_snapshot_id,
                "description_set_digest": overlay.description_set_digest,
                "source_snapshot_sha": proposal.source_snapshot_sha,
                "conflict_category_pair": list(proposal.conflict_category_pair),
                "conflict_description_digests": list(
                    proposal.conflict_description_digests
                ),
            },
        },
    )
    evaluated = repository.record_evaluation(
        proposal.proposal_id, model_id="email-embedding-mlp-candidate-1"
    )
    assert evaluated.status == "evaluated"
    rejected = repository.decide(proposal.proposal_id, accept=False)
    assert rejected.status == "rejected"

    second = DescriptionProposal(**{**proposal.__dict__, "proposal_id": "proposal-2"})
    repository.persist(second)
    second_overlay = build_description_set_overlay(second, ACTIVE_DESCRIPTIONS)
    repository.persist_overlay(second_overlay)
    registry.persist_staged_evidence(
        "email-embedding-mlp-candidate-2",
        {
            "model_id": "email-embedding-mlp-candidate-2",
            "compatibility": {
                "description_version": second_overlay.description_set_version
            },
            "whole_model_readiness": {"ready": True},
            "hashes": {
                "snapshot_sha256": "a" * 64,
                "description_sha256": second_overlay.description_set_digest,
            },
            "description_proposal": {
                "proposal_id": second.proposal_id,
                "source_description_version": second.source_description_version,
                "source_description_digest": second.source_description_digest,
                "source_snapshot_id": second.source_snapshot_id,
                "description_set_digest": second_overlay.description_set_digest,
                "source_snapshot_sha": second.source_snapshot_sha,
                "conflict_category_pair": list(second.conflict_category_pair),
                "conflict_description_digests": list(
                    second.conflict_description_digests
                ),
            },
        },
    )
    evaluated = repository.record_evaluation(
        second.proposal_id, model_id="email-embedding-mlp-candidate-2"
    )
    assert evaluated.status == "evaluated"
    accepted = repository.decide(second.proposal_id, accept=True)
    assert accepted.status == "accepted"
    assert accepted.evaluated_model_id == "email-embedding-mlp-candidate-2"
    assert repository.bound_description("email-embedding-mlp-candidate-2") == {
        "description_version": second_overlay.description_set_version,
        "description_set_digest": second_overlay.description_set_digest,
        "proposal_id": second.proposal_id,
    }


def test_record_evaluation_rejects_evidence_without_persisted_full_overlay(
    tmp_path,
) -> None:
    proposal = DescriptionProposal(
        proposal_id="proposal-without-overlay",
        category="legal",
        source_description_version="legal-v1",
        source_description_digest=category_description_digest(CURRENT),
        source_snapshot_id="snapshot-1",
        source_snapshot_sha="a" * 64,
        proposed=CategoryDescription(
            core="External contracts and disputes.",
            include=("Regulatory notices.",),
            exclude=("Routine project delivery.",),
            version="legal-v2",
        ),
        cited_sample_ids=("s1", "s2", "s3", "s4", "s5"),
        reason="Five independent conflicts.",
        conflict_category_pair=("work", "legal"),
        conflict_description_digests=tuple(
            category_description_digest(ACTIVE_DESCRIPTIONS[key])
            for key in ("work", "legal")
        ),
    )
    overlay = build_description_set_overlay(proposal, ACTIVE_DESCRIPTIONS)
    repository = DescriptionProposalRepository(tmp_path / "registry")
    registry = EmailModelRegistry(tmp_path / "registry")
    repository.persist(proposal)
    registry.persist_staged_evidence(
        "email-embedding-mlp-unfrozen-overlay",
        {
            "model_id": "email-embedding-mlp-unfrozen-overlay",
            "compatibility": {"description_version": overlay.description_set_version},
            "whole_model_readiness": {"ready": True},
            "hashes": {
                "snapshot_sha256": proposal.source_snapshot_sha,
                "description_sha256": overlay.description_set_digest,
            },
            "description_proposal": {
                "proposal_id": proposal.proposal_id,
                "source_description_version": proposal.source_description_version,
                "source_description_digest": proposal.source_description_digest,
                "source_snapshot_id": proposal.source_snapshot_id,
                "description_set_digest": overlay.description_set_digest,
                "source_snapshot_sha": proposal.source_snapshot_sha,
            },
        },
    )

    with pytest.raises(ValueError, match="overlay cannot be loaded"):
        repository.record_evaluation(
            proposal.proposal_id,
            model_id="email-embedding-mlp-unfrozen-overlay",
        )


def test_production_proposal_evaluation_starts_bound_overlay_without_config_mutation(
    tmp_path,
) -> None:
    proposal = propose_description_update(
        category="legal",
        current=CURRENT,
        current_pair=ACTIVE_DESCRIPTIONS,
        source_snapshot_id="snapshot-1",
        source_snapshot_sha="a" * 64,
        conflicts=_examples(5),
        agent=lambda _payload: {
            "core": "External contracts, disputes, rights, and obligations.",
            "include": ["Contracts and regulatory notices."],
            "exclude": ["Routine project delivery."],
            "cited_sample_ids": [f"sample-{index}" for index in range(5)],
            "reason": "Repeated independent work/legal confusion.",
        },
    )
    assert proposal is not None
    registry = EmailModelRegistry(tmp_path / "registry")
    repository = DescriptionProposalRepository(registry.root)
    repository.persist(proposal)
    config_rows = [
        {
            "category_key": category,
            "core_description": description.core,
            "include": list(description.include),
            "exclude": list(description.exclude),
            "description_version": description.version,
            "enabled": True,
        }
        for category, description in ACTIVE_DESCRIPTIONS.items()
    ]

    class Store:
        def list_category_configs(self):
            return config_rows

        def get_training_snapshot(self, snapshot_id):
            assert snapshot_id == "snapshot-1"
            return {
                "snapshot_id": "snapshot-1",
                "snapshot_digest": "a" * 64,
                "description_version": "unused-active-version",
                "observations": [],
            }

        def latest_training_snapshot_state(self):
            return {
                "snapshot_id": "newer-snapshot",
                "snapshot_sha": "b" * 64,
                "folder_label_watermark": 10,
                "important_label_watermark": 5,
            }

    class Controller:
        def __init__(self):
            self.overlay = None

        def start(self, *, now, signal, snapshot_id, description_overlay=None):
            self.overlay = description_overlay
            return TrainingSubprocessRun(
                run_id="proposal-run",
                status="running",
                pid=7,
                started_at=now.isoformat(),
                updated_at=now.isoformat(),
                snapshot_id=snapshot_id,
                snapshot_sha=signal.snapshot_sha,
                description_version=signal.description_version,
                folder_label_watermark=signal.folder_label_watermark,
                important_label_watermark=signal.important_label_watermark,
                description_proposal_id=description_overlay.proposal_id,
                source_description_version=(
                    description_overlay.source_description_version
                ),
                description_set_digest=description_overlay.description_set_digest,
            )

    controller = Controller()
    service = EmailClassifierLearningService(
        Store(),
        registry=registry,
        retrain_state_path=registry.root / "retrain-state.json",
        controller=controller,
    )
    before = tuple(config_rows)

    result = service.request_description_proposal_evaluation(proposal.proposal_id)

    assert result.training_run is not None
    assert result.training_run.description_proposal_id == proposal.proposal_id
    assert result.training_run.snapshot_sha == proposal.source_snapshot_sha
    assert result.training_run.description_set_digest == (
        controller.overlay.description_set_digest
    )
    assert (
        repository.get_overlay(
            proposal.proposal_id, result.training_run.description_set_digest
        ).descriptions["legal"]
        == proposal.proposed
    )
    assert tuple(config_rows) == before


def test_production_optimizer_combines_frozen_errors_and_folder_corrections_once(
    tmp_path,
) -> None:
    registry = EmailModelRegistry(tmp_path / "registry")
    frozen_conflicts = [
        {
            "sample_id": f"frozen-{index}",
            "group_key": f"group-{index}",
            "predicted_category": "work",
            "confirmed_category": "legal",
        }
        for index in range(3)
    ]
    registry.persist_staged_evidence(
        "email-embedding-mlp-conflicts",
        {
            "model_id": "email-embedding-mlp-conflicts",
            "source_snapshot_id": "snapshot-conflicts",
            "hashes": {"snapshot_sha256": "a" * 64},
            "classification_conflicts": frozen_conflicts,
        },
    )

    class Store:
        def get_training_snapshot(self, snapshot_id):
            assert snapshot_id == "snapshot-conflicts"
            return {
                "snapshot_id": snapshot_id,
                "snapshot_digest": "a" * 64,
                "observations": [
                    {
                        "stable_message_identity": f"frozen-{index}",
                        "normalized_model_input": f"frozen text {index}",
                    }
                    for index in range(3)
                ],
            }

        def list_provider_folder_correction_conflicts(self, snapshot_id):
            assert snapshot_id == "snapshot-conflicts"
            return [
                {
                    "sample_id": f"folder-{index}",
                    "group_key": f"group-{index + 3}",
                    "predicted_category": "work",
                    "confirmed_category": "legal",
                    "redacted_text": f"folder correction {index}",
                }
                for index in range(2)
            ]

        def list_category_configs(self):
            return [
                {
                    "category_key": category,
                    "core_description": description.core,
                    "include": list(description.include),
                    "exclude": list(description.exclude),
                    "description_version": description.version,
                    "enabled": True,
                }
                for category, description in ACTIVE_DESCRIPTIONS.items()
            ]

    calls = []

    def agent(payload):
        calls.append(payload)
        return {
            "core": "External legal rights, contracts, and disputes.",
            "include": ["External contracts and regulatory notices."],
            "exclude": ["Routine internal project delivery."],
            "cited_sample_ids": [item["sample_id"] for item in payload["examples"]],
            "reason": "Five independent folder-truth conflicts.",
        }

    orchestrator = DescriptionOptimizationOrchestrator(
        store=Store(), registry=registry, agent=agent
    )

    first = orchestrator.observe_candidate("email-embedding-mlp-conflicts")
    second = orchestrator.observe_candidate("email-embedding-mlp-conflicts")

    assert len(first) == 1
    assert second == ()
    assert len(calls) == 1
    assert first[0].source_snapshot_id == "snapshot-conflicts"
    aliases = {item["sample_id"] for item in calls[0]["examples"]}
    assert len(aliases) == 5
    assert all(alias.startswith("sample-") for alias in aliases)
    encoded_prompt = json.dumps(calls[0], sort_keys=True)
    assert "frozen-" not in encoded_prompt
    assert "folder-" not in encoded_prompt
    request = json.loads(
        next(
            DescriptionProposalRepository(registry.root).optimization_requests.glob(
                "*.json"
            )
        ).read_text()
    )
    assert set(request["sample_aliases"].values()) == {
        "frozen-0",
        "frozen-1",
        "frozen-2",
        "folder-0",
        "folder-1",
    }


def test_optimizer_redacts_private_values_and_preserves_class_evidence(tmp_path):
    registry = EmailModelRegistry(tmp_path / "registry")
    registry.persist_staged_evidence(
        "email-embedding-mlp-private-conflicts",
        {
            "model_id": "email-embedding-mlp-private-conflicts",
            "source_snapshot_id": "snapshot-private",
            "hashes": {"snapshot_sha256": "c" * 64},
            "classification_conflicts": [
                {
                    "sample_id": (
                        f"account-private:message-id:<owner-{index}@example.test>"
                    ),
                    "group_key": f"account-private:thread-{index}",
                    "predicted_category": "work",
                    "confirmed_category": "legal",
                }
                for index in range(5)
            ],
        },
    )
    private_input = json.dumps(
        {
            "sender": {"name": "Derek Zen", "email": "derek@stardust.ai"},
            "to_recipients": [{"name": "Alice Private", "email": "alice@example.test"}],
            "cc_recipients": [],
            "subject": "Contract dispute for Derek Zen account 99887766",
            "body": (
                "Legal contract evidence for Derek Zen, derek@stardust.ai, "
                "+1 415 555 0198, customer 99887766, Bob Unknown at "
                "742 Evergreen Terrace, reference A-1234."
            ),
            "headers": {
                "message-id": "<private-message@example.test>",
                "references": "<private-thread@example.test>",
            },
            "attachments": [
                {
                    "filename": "Derek-Zen-private-contract.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": 123,
                    "content_id": "private-content-id",
                }
            ],
            "attachment_count": 1,
        },
        sort_keys=True,
    )

    class Store:
        def get_training_snapshot(self, _snapshot_id):
            return {
                "snapshot_digest": "c" * 64,
                "observations": [
                    {
                        "stable_message_identity": (
                            f"account-private:message-id:<owner-{index}@example.test>"
                        ),
                        "normalized_model_input": private_input,
                    }
                    for index in range(5)
                ],
            }

        def list_provider_folder_correction_conflicts(self, _snapshot_id):
            return []

        def list_category_configs(self):
            return [
                {
                    "category_key": category,
                    "core_description": description.core,
                    "include": list(description.include),
                    "exclude": list(description.exclude),
                    "description_version": description.version,
                    "enabled": True,
                }
                for category, description in ACTIVE_DESCRIPTIONS.items()
            ]

    calls = []
    orchestrator = DescriptionOptimizationOrchestrator(
        store=Store(),
        registry=registry,
        agent=lambda payload: (
            calls.append(payload)
            or {
                "core": "External legal rights and contracts.",
                "include": ["Contract disputes."],
                "exclude": ["Routine project delivery."],
                "cited_sample_ids": [item["sample_id"] for item in payload["examples"]],
                "reason": "Repeated legal conflicts.",
            }
        ),
    )

    orchestrator.observe_candidate("email-embedding-mlp-private-conflicts")

    encoded = json.dumps(calls, ensure_ascii=False)
    for private_value in (
        "Derek Zen",
        "derek@stardust.ai",
        "Alice Private",
        "alice@example.test",
        "415 555 0198",
        "99887766",
        "private-message",
        "private-thread",
        "Derek-Zen-private-contract.pdf",
        "private-content-id",
        "Bob Unknown",
        "742 Evergreen Terrace",
        "A-1234",
        "account-private",
        "owner-0@example.test",
        "thread-0",
    ):
        assert private_value not in encoded
    assert "contract" in encoded.casefold()


def test_optimizer_payload_binds_both_current_category_descriptions() -> None:
    seen = []
    proposal = propose_description_update(
        category="legal",
        current=CURRENT,
        current_pair=ACTIVE_DESCRIPTIONS,
        source_snapshot_id="snapshot-pair",
        source_snapshot_sha="e" * 64,
        conflicts=_examples(5),
        agent=lambda payload: (
            seen.append(payload)
            or {
                "core": "External legal rights and contracts.",
                "include": ["Contract disputes."],
                "exclude": ["Routine project delivery."],
                "cited_sample_ids": [item["sample_id"] for item in payload["examples"]],
                "reason": "Repeated work/legal conflicts.",
            }
        ),
    )

    assert proposal is not None
    assert seen[0]["category_pair"] == ["work", "legal"]
    assert seen[0]["current_pair"] == {
        key: {
            "core": value.core,
            "include": list(value.include),
            "exclude": list(value.exclude),
            "version": value.version,
        }
        for key, value in ACTIVE_DESCRIPTIONS.items()
    }
    assert proposal.conflict_category_pair == ("work", "legal")
    assert proposal.conflict_description_digests == tuple(
        category_description_digest(ACTIVE_DESCRIPTIONS[key])
        for key in ("work", "legal")
    )


def test_idle_poll_recovers_proposed_and_evaluated_continuations_after_crash(
    tmp_path,
) -> None:
    registry = EmailModelRegistry(tmp_path / "registry")
    repository = DescriptionProposalRepository(registry.root)
    proposal = propose_description_update(
        category="legal",
        current=CURRENT,
        current_pair=ACTIVE_DESCRIPTIONS,
        source_snapshot_id="snapshot-source",
        source_snapshot_sha="d" * 64,
        conflicts=_examples(5),
        agent=lambda payload: {
            "core": "External legal rights, contracts, and disputes.",
            "include": ["External contracts and regulatory notices."],
            "exclude": ["Routine internal project delivery."],
            "cited_sample_ids": [item["sample_id"] for item in payload["examples"]],
            "reason": "Five independent legal conflicts.",
        },
    )
    assert proposal is not None

    class Store:
        def list_category_configs(self):
            return [
                {
                    "category_key": category,
                    "core_description": description.core,
                    "include": list(description.include),
                    "exclude": list(description.exclude),
                    "description_version": description.version,
                    "enabled": True,
                }
                for category, description in ACTIVE_DESCRIPTIONS.items()
            ]

        def get_training_snapshot(self, snapshot_id):
            assert snapshot_id == "snapshot-source"
            return {"snapshot_id": snapshot_id, "snapshot_digest": "d" * 64}

        def latest_training_snapshot_state(self):
            return {
                "snapshot_id": "snapshot-source",
                "snapshot_sha": "d" * 64,
                "folder_label_watermark": 50,
                "important_label_watermark": 50,
            }

    class Optimizer:
        def __init__(self):
            self.called = False

        def observe_candidate(self, model_id):
            assert model_id == "email-embedding-mlp-source"
            if self.called:
                return ()
            self.called = True
            repository.persist(proposal)
            return (proposal,)

    class Controller:
        def __init__(self):
            self.starts = []
            self.start_attempts = 0

        def start(self, *, now, signal, snapshot_id, description_overlay=None):
            self.start_attempts += 1
            if self.start_attempts in {1, 3}:
                raise RuntimeError("crash before proposal run registration")
            self.starts.append(description_overlay)
            index = len(self.starts)
            return TrainingSubprocessRun(
                run_id=f"proposal-run-{index}",
                status="running",
                pid=index,
                started_at=now.isoformat(),
                snapshot_id=snapshot_id,
                snapshot_sha=signal.snapshot_sha,
                description_version=signal.description_version,
                folder_label_watermark=signal.folder_label_watermark,
                important_label_watermark=signal.important_label_watermark,
                description_proposal_id=description_overlay.proposal_id,
                source_description_version=description_overlay.source_description_version,
                description_set_digest=description_overlay.description_set_digest,
            )

        def poll(self, run_id, *, now):
            if run_id == "normal-run":
                return TrainingSubprocessRun(
                    run_id=run_id,
                    status="succeeded",
                    pid=1,
                    started_at=now.isoformat(),
                    model_id="email-embedding-mlp-source",
                    snapshot_id="snapshot-source",
                    snapshot_sha="d" * 64,
                    description_version="active-descriptions",
                    folder_label_watermark=50,
                    important_label_watermark=50,
                )
            index = int(run_id.rsplit("-", 1)[1])
            overlay = self.starts[index - 1]
            model_id = f"email-embedding-mlp-proposal-{index}"
            registry.persist_staged_evidence(
                model_id,
                {
                    "model_id": model_id,
                    "compatibility": {
                        "description_version": overlay.description_set_version
                    },
                    "whole_model_readiness": {"ready": index == 2},
                    "unresolved_historical_systematic_error": False,
                    "hashes": {
                        "snapshot_sha256": overlay.source_snapshot_sha,
                        "description_sha256": overlay.description_set_digest,
                    },
                    "description_proposal": {
                        "proposal_id": overlay.proposal_id,
                        "source_description_version": overlay.source_description_version,
                        "source_description_digest": overlay.source_description_digest,
                        "source_snapshot_id": overlay.source_snapshot_id,
                        "source_snapshot_sha": overlay.source_snapshot_sha,
                        "description_set_digest": overlay.description_set_digest,
                        "conflict_category_pair": list(overlay.conflict_category_pair),
                        "conflict_description_digests": list(
                            overlay.conflict_description_digests
                        ),
                    },
                },
            )
            return TrainingSubprocessRun(
                run_id=run_id,
                status="succeeded",
                pid=index,
                started_at=now.isoformat(),
                model_id=model_id,
                snapshot_id=overlay.source_snapshot_id,
                snapshot_sha=overlay.source_snapshot_sha,
                description_version=overlay.description_set_version,
                folder_label_watermark=50,
                important_label_watermark=50,
                description_proposal_id=overlay.proposal_id,
                source_description_version=overlay.source_description_version,
                description_set_digest=overlay.description_set_digest,
            )

    state_path = registry.root / "retrain-state.json"
    non_due_pending = SnapshotTrainingSignal(
        snapshot_sha="e" * 64,
        description_version="active-descriptions",
        folder_label_watermark=50,
        important_label_watermark=50,
        minimum_ready=True,
    )
    save_retrain_state(
        state_path,
        RetrainState()
        .with_active_run("normal-run")
        .with_pending_snapshot(non_due_pending, snapshot_id="pending-after-normal"),
    )
    controller = Controller()
    optimizer = Optimizer()
    service = EmailClassifierLearningService(
        Store(),
        registry=registry,
        retrain_state_path=state_path,
        controller=controller,
        description_optimizer=optimizer,
    )

    with pytest.raises(RuntimeError, match="crash before proposal run registration"):
        service.poll_retrain()
    crashed = load_retrain_state(state_path)
    assert crashed.active_run_id is None
    assert crashed.pending_snapshot_id is None
    assert repository.get(proposal.proposal_id).status == "proposed"

    restarted = EmailClassifierLearningService(
        Store(),
        registry=registry,
        retrain_state_path=state_path,
        controller=controller,
        description_optimizer=optimizer,
    )
    first = restarted.poll_retrain()
    assert first.training_run.run_id == "proposal-run-1"
    assert first.state.pending_snapshot_id is None
    assert repository.get(proposal.proposal_id).status == "proposed"
    save_retrain_state(
        state_path,
        load_retrain_state(state_path).with_pending_snapshot(
            SnapshotTrainingSignal(
                snapshot_sha="f" * 64,
                description_version="active-descriptions",
                folder_label_watermark=50,
                important_label_watermark=50,
                minimum_ready=True,
            ),
            snapshot_id="pending-after-first-proposal",
        ),
    )
    with pytest.raises(RuntimeError, match="crash before proposal run registration"):
        restarted.poll_retrain()
    crashed = load_retrain_state(state_path)
    assert crashed.active_run_id is None
    assert crashed.pending_snapshot_id is None
    assert repository.get(proposal.proposal_id).status == "evaluated"

    restarted = EmailClassifierLearningService(
        Store(),
        registry=registry,
        retrain_state_path=state_path,
        controller=controller,
        description_optimizer=optimizer,
    )
    second = restarted.poll_retrain()
    assert second.training_run.run_id == "proposal-run-2"
    assert second.state.pending_snapshot_id is None
    assert repository.get(proposal.proposal_id).status == "evaluated"
    third = restarted.poll_retrain()

    accepted = repository.get(proposal.proposal_id)
    assert third.state.active_run_id is None
    assert accepted.status == "accepted"
    assert accepted.evaluated_model_id == "email-embedding-mlp-proposal-2"
    assert len(controller.starts) == 2
    assert controller.start_attempts == 4
    idle = restarted.poll_retrain()
    assert idle.training_run is None
    assert controller.start_attempts == 4
    assert ACTIVE_DESCRIPTIONS["legal"] == CURRENT


@pytest.mark.parametrize(
    ("evaluation_passed", "expected_status"),
    [(True, "accepted"), (False, "rejected")],
)
def test_idle_recovery_decides_completed_second_candidate_without_third_run(
    tmp_path, evaluation_passed, expected_status
) -> None:
    registry = EmailModelRegistry(tmp_path / "registry")
    repository = DescriptionProposalRepository(registry.root)
    proposal = propose_description_update(
        category="legal",
        current=CURRENT,
        current_pair=ACTIVE_DESCRIPTIONS,
        source_snapshot_id="snapshot-source",
        source_snapshot_sha="d" * 64,
        conflicts=_examples(5),
        agent=lambda payload: {
            "core": "External legal rights, contracts, and disputes.",
            "include": ["External contracts and regulatory notices."],
            "exclude": ["Routine internal project delivery."],
            "cited_sample_ids": [item["sample_id"] for item in payload["examples"]],
            "reason": "Five independent legal conflicts.",
        },
    )
    assert proposal is not None
    overlay = build_description_set_overlay(proposal, ACTIVE_DESCRIPTIONS)
    repository.persist(proposal)
    repository.persist_overlay(overlay)

    def evidence(model_id, *, ready):
        return {
            "model_id": model_id,
            "compatibility": {"description_version": overlay.description_set_version},
            "whole_model_readiness": {"ready": ready},
            "unresolved_historical_systematic_error": False,
            "hashes": {
                "snapshot_sha256": overlay.source_snapshot_sha,
                "description_sha256": overlay.description_set_digest,
            },
            "description_proposal": {
                "proposal_id": overlay.proposal_id,
                "source_description_version": overlay.source_description_version,
                "source_description_digest": overlay.source_description_digest,
                "source_snapshot_id": overlay.source_snapshot_id,
                "source_snapshot_sha": overlay.source_snapshot_sha,
                "description_set_digest": overlay.description_set_digest,
                "conflict_category_pair": list(overlay.conflict_category_pair),
                "conflict_description_digests": list(
                    overlay.conflict_description_digests
                ),
            },
        }

    first_model_id = "email-embedding-mlp-proposal-candidate-1"
    second_model_id = "email-embedding-mlp-proposal-candidate-2"
    registry.persist_staged_evidence(
        first_model_id, evidence(first_model_id, ready=False)
    )
    registry.persist_staged_evidence(
        second_model_id,
        evidence(second_model_id, ready=evaluation_passed),
    )
    repository.record_evaluation(proposal.proposal_id, model_id=second_model_id)

    class Controller:
        starts = 0

        def start(self, **_kwargs):
            self.starts += 1
            raise AssertionError("completed evaluation must not start a third run")

    controller = Controller()
    service = EmailClassifierLearningService(
        object(),
        registry=registry,
        retrain_state_path=registry.root / "retrain-state.json",
        controller=controller,
    )

    recovered = service.poll_retrain()
    replay = service.poll_retrain()

    assert recovered.training_run is None
    assert replay.training_run is None
    assert repository.get(proposal.proposal_id).status == expected_status
    assert controller.starts == 0


def test_idle_recovery_fails_closed_when_evaluated_evidence_is_missing(
    tmp_path,
) -> None:
    registry = EmailModelRegistry(tmp_path / "registry")
    repository = DescriptionProposalRepository(registry.root)
    proposal = DescriptionProposal(
        proposal_id="proposal-invalid-recovery",
        category="legal",
        source_description_version="legal-v1",
        source_description_digest=category_description_digest(CURRENT),
        source_snapshot_id="snapshot-source",
        source_snapshot_sha="d" * 64,
        proposed=CURRENT,
        cited_sample_ids=("s1", "s2", "s3", "s4", "s5"),
        reason="Persisted evaluated proposal with missing evidence.",
        conflict_category_pair=("work", "legal"),
        conflict_description_digests=tuple(
            category_description_digest(ACTIVE_DESCRIPTIONS[key])
            for key in ("work", "legal")
        ),
        status="evaluated",
        evaluated_model_id="email-embedding-mlp-missing-candidate",
        evaluation_passed=False,
        evaluated_description_set_digest="a" * 64,
    )
    repository.persist(proposal)

    class Controller:
        starts = 0

        def start(self, **_kwargs):
            self.starts += 1
            raise AssertionError("invalid evidence must fail closed")

    controller = Controller()
    service = EmailClassifierLearningService(
        object(),
        registry=registry,
        retrain_state_path=registry.root / "retrain-state.json",
        controller=controller,
    )

    first = service.poll_retrain()
    second = service.poll_retrain()

    assert first.decision.reason == "description_proposal_evidence_invalid"
    assert second.decision.reason == "description_proposal_evidence_invalid"
    assert repository.get(proposal.proposal_id).status == "evaluated"
    assert controller.starts == 0
