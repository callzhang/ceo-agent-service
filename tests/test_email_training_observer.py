from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from app.email_important import ImportantSignals
from app.email_provider_folders import FolderRole


def _message(uid: int, identity: str) -> dict[str, object]:
    return {
        "uid": uid,
        "stableMessageIdentity": identity,
        "importantSignals": ImportantSignals((), False),
        "from": {"name": "Sender", "email": "sender@example.test"},
        "toRecipients": (),
        "ccRecipients": (),
        "subject": identity,
        "textBody": "body",
        "messageId": f"<{identity}@example.test>",
        "references": (),
        "attachments": (),
        "date": "2026-09-07T00:00:00+00:00",
    }


def _folder(folder_id="inbox", *, role=FolderRole.INBOX):
    return SimpleNamespace(
        provider_folder_id=folder_id,
        display_name=folder_id,
        role=role,
    )


class _Store:
    def __init__(self, bindings=()):
        self.bindings = bindings
        self.current_observation_calls = []

    def list_account_folder_bindings(self):
        return self.bindings

    def has_stable_classification(self, _identity):
        return False

    def record_current_provider_observations(
        self,
        observations,
        *,
        unavailable_folders,
        authoritative_folders,
        active_account_ids,
        observed_at,
    ):
        self.current_observation_calls.append(
            (
                tuple(observations),
                tuple(unavailable_folders),
                tuple(authoritative_folders),
                tuple(active_account_ids),
                observed_at,
            )
        )


def test_observer_uses_bounded_uid_watermark_and_resumes_after_restart(tmp_path):
    from app.email_training_observer import ProviderTrainingObservationJob

    calls = []
    batches = [
        SimpleNamespace(
            uidvalidity=10,
            messages=(_message(1, "one"), _message(2, "two")),
        ),
        SimpleNamespace(uidvalidity=10, messages=(_message(3, "three"),)),
    ]

    class Source:
        def list_folders(self):
            return (_folder(),)

        def fetch_uid_membership(self, _folder, *, cursor_uidvalidity, uids):
            return SimpleNamespace(
                uidvalidity=cursor_uidvalidity,
                existing_uids=frozenset(uids),
                important_signals_by_uid={
                    uid: ImportantSignals((), False) for uid in uids
                },
            )

        def fetch_uid_batch(self, folder, *, cursor_uidvalidity, last_seen_uid, limit):
            calls.append((folder, cursor_uidvalidity, last_seen_uid, limit))
            return batches.pop(0)

        def logout(self):
            return None

    store = _Store()
    arguments = dict(
        state_path=tmp_path / "observer.json",
        source_factory=lambda _account: Source(),
        email_store=store,
        batch_size=2,
    )
    first = ProviderTrainingObservationJob(**arguments).run_once(
        ({"account_id": "account-1"},)
    )
    second = ProviderTrainingObservationJob(**arguments).run_once(
        ({"account_id": "account-1"},)
    )

    assert calls == [("inbox", None, 0, 2), ("inbox", 10, 2, 2)]
    assert first.more_available is True
    assert second.more_available is False
    assert [row["stable_message_identity"] for row in second.observations] == [
        "one",
        "three",
        "two",
    ]
    assert len(store.current_observation_calls) == 2
    assert store.current_observation_calls[-1][0] == second.observations
    assert store.current_observation_calls[-1][1] == ()


def test_uidvalidity_reset_is_bounded_and_replaces_old_folder_cache(tmp_path):
    from app.email_training_observer import ProviderTrainingObservationJob

    batches = [
        SimpleNamespace(uidvalidity=10, messages=(_message(8, "old"),)),
        SimpleNamespace(uidvalidity=20, messages=(_message(1, "reset"),)),
    ]

    class Source:
        def list_folders(self):
            return (_folder(),)

        def fetch_uid_membership(self, _folder, **_kwargs):
            return SimpleNamespace(
                uidvalidity=20,
                existing_uids=frozenset(),
                important_signals_by_uid={},
            )

        def fetch_uid_batch(self, *_args, **_kwargs):
            return batches.pop(0)

        def logout(self):
            return None

    job = ProviderTrainingObservationJob(
        state_path=tmp_path / "observer.json",
        source_factory=lambda _account: Source(),
        email_store=_Store(),
        batch_size=1,
    )
    job.run_once(({"account_id": "account-1"},))
    reset = job.run_once(({"account_id": "account-1"},))

    assert [row["stable_message_identity"] for row in reset.observations] == ["reset"]
    assert reset.more_available is True


def test_failed_cache_publication_does_not_advance_watermark(tmp_path, monkeypatch):
    import app.email_training_observer as observer_module
    from app.email_training_observer import ProviderTrainingObservationJob

    calls = []

    class Source:
        def list_folders(self):
            return (_folder(),)

        def fetch_uid_batch(self, folder, *, cursor_uidvalidity, last_seen_uid, limit):
            calls.append((cursor_uidvalidity, last_seen_uid))
            return SimpleNamespace(uidvalidity=10, messages=(_message(1, "one"),))

        def logout(self):
            return None

    job = ProviderTrainingObservationJob(
        state_path=tmp_path / "observer.json",
        source_factory=lambda _account: Source(),
        email_store=_Store(),
        batch_size=10,
    )
    real_write = observer_module._write_state_atomic
    monkeypatch.setattr(
        observer_module,
        "_write_state_atomic",
        lambda *_args: (_ for _ in ()).throw(OSError("crash")),
    )
    with pytest.raises(OSError, match="crash"):
        job.run_once(({"account_id": "account-1"},))
    monkeypatch.setattr(observer_module, "_write_state_atomic", real_write)
    result = job.run_once(({"account_id": "account-1"},))

    assert calls == [(None, 0), (None, 0)]
    assert len(result.observations) == 1


def test_missing_uidvalidity_is_durable_unavailable_and_never_falls_back(tmp_path):
    from app.email_training_observer import ProviderTrainingObservationJob

    calls = 0

    class Source:
        def list_folders(self):
            return (_folder(),)

        def fetch_uid_batch(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(uidvalidity=None, messages=())

        def logout(self):
            return None

    job = ProviderTrainingObservationJob(
        state_path=tmp_path / "observer.json",
        source_factory=lambda _account: Source(),
        email_store=_Store(),
        batch_size=10,
    )
    first = job.run_once(({"account_id": "account-1"},))
    second = job.run_once(({"account_id": "account-1"},))

    assert calls == 1
    assert first.unavailable_folders == ("account-1:inbox",)
    assert second.unavailable_folders == ("account-1:inbox",)


def test_folder_deletion_and_binding_change_rebuild_cached_folder_truth(tmp_path):
    from app.email_training_observer import ProviderTrainingObservationJob

    inventories = [
        (_folder("inbox"), _folder("work")),
        (_folder("work"),),
    ]
    batches = [
        SimpleNamespace(uidvalidity=1, messages=(_message(1, "inbox-mail"),)),
        SimpleNamespace(uidvalidity=2, messages=(_message(1, "work-mail"),)),
        SimpleNamespace(uidvalidity=2, messages=()),
    ]

    class Source:
        def list_folders(self):
            return inventories.pop(0)

        def fetch_uid_batch(self, *_args, **_kwargs):
            return batches.pop(0)

        def logout(self):
            return None

    store = _Store()
    job = ProviderTrainingObservationJob(
        state_path=tmp_path / "observer.json",
        source_factory=lambda _account: Source(),
        email_store=store,
        batch_size=10,
    )
    job.run_once(({"account_id": "account-1"},))
    store.bindings = (
        {
            "account_id": "account-1",
            "provider_folder_id": "work",
            "category_key": "work",
            "binding_status": "active",
        },
    )
    updated = job.run_once(({"account_id": "account-1"},))

    assert [row["stable_message_identity"] for row in updated.observations] == [
        "work-mail"
    ]
    assert updated.observations[0]["folder_role"] == FolderRole.CATEGORY
    assert updated.observations[0]["bound_category_key"] == "work"


def test_observation_coordinator_request_never_runs_provider_inline(tmp_path):
    from app.email_training_observer import TrainingObservationCoordinator

    events = []

    class Job:
        def begin_reconciliation_generation(self, _generation):
            return None

        def run_once(self, accounts):
            events.append(("provider", tuple(accounts)))
            return SimpleNamespace(observations=({"id": 1},), more_available=False)

    coordinator = TrainingObservationCoordinator(
        request_state_path=tmp_path / "requests.json",
        job=Job(),
        accounts_loader=lambda: ({"account_id": "account-1"},),
        publish=lambda observations: events.append(("publish", observations)),
    )

    coordinator.request()
    assert events == []
    restarted = TrainingObservationCoordinator(
        request_state_path=tmp_path / "requests.json",
        job=coordinator.job,
        accounts_loader=coordinator.accounts_loader,
        publish=coordinator.publish,
    )
    restarted.tick()
    assert events == [
        ("provider", ({"account_id": "account-1"},)),
        ("publish", ({"id": 1},)),
    ]


def test_coordinator_replays_request_when_publish_crashes(tmp_path):
    from app.email_training_observer import TrainingObservationCoordinator

    publications = 0

    class Job:
        def begin_reconciliation_generation(self, _generation):
            return None

        def run_once(self, _accounts):
            return SimpleNamespace(observations=({"id": 1},), more_available=False)

    def publish(_observations):
        nonlocal publications
        publications += 1
        if publications == 1:
            raise OSError("publication crash")
        return "published"

    coordinator = TrainingObservationCoordinator(
        request_state_path=tmp_path / "requests.json",
        job=Job(),
        accounts_loader=lambda: (),
        publish=publish,
    )
    coordinator.request()
    with pytest.raises(OSError, match="publication crash"):
        coordinator.tick()

    restarted = TrainingObservationCoordinator(
        request_state_path=tmp_path / "requests.json",
        job=Job(),
        accounts_loader=lambda: (),
        publish=publish,
    )
    assert restarted.tick() == "published"
    assert restarted.tick() is None
    assert publications == 2


def test_description_change_publishes_cache_without_provider_read(tmp_path):
    from app.email_training_observer import TrainingObservationCoordinator

    versions = ["descriptions-v1"]
    events = []

    class Job:
        def begin_reconciliation_generation(self, _generation):
            return None

        def run_once(self, _accounts):
            events.append("provider")
            return SimpleNamespace(observations=({"id": 1},), more_available=False)

        def cached_observations(self):
            return ({"id": 1},)

    coordinator = TrainingObservationCoordinator(
        request_state_path=tmp_path / "requests.json",
        job=Job(),
        accounts_loader=lambda: (),
        publish=lambda observations: events.append(("publish", observations)),
        description_version_loader=lambda: versions[0],
    )
    coordinator.request()
    coordinator.tick()
    coordinator.tick()
    versions[0] = "descriptions-v2"
    coordinator.tick()

    assert events == [
        "provider",
        ("publish", ({"id": 1},)),
        ("publish", ({"id": 1},)),
    ]


def test_observation_cache_never_persists_private_unsubscribe_token(tmp_path):
    from app.email_training_observer import ProviderTrainingObservationJob

    private_token = "private-observer-token-A-1234"
    message = _message(1, "private")
    message["listUnsubscribe"] = (
        f"<https://unsubscribe.example.test/remove?token={private_token}>"
    )

    class Source:
        def list_folders(self):
            return (_folder(),)

        def fetch_uid_batch(self, *_args, **_kwargs):
            return SimpleNamespace(uidvalidity=10, messages=(message,))

        def logout(self):
            return None

    state_path = tmp_path / "observer.json"
    result = ProviderTrainingObservationJob(
        state_path=state_path,
        source_factory=lambda _account: Source(),
        email_store=_Store(),
        batch_size=10,
    ).run_once(({"account_id": "account-1"},))

    assert private_token.encode() not in state_path.read_bytes()
    assert result.observations[0]["unsubscribe_features"]["has_unsubscribe"] is True


def test_cached_inbox_and_trash_copies_freeze_as_provider_junk_truth(tmp_path):
    from app.email_training_observer import ProviderTrainingObservationJob
    from app.email_training_snapshot import build_folder_training_snapshot

    class Source:
        def list_folders(self):
            return (_folder("inbox"), _folder("trash", role=FolderRole.TRASH))

        def fetch_uid_batch(self, folder, **_kwargs):
            return SimpleNamespace(
                uidvalidity=10 if folder == "inbox" else 20,
                messages=(_message(1, "same-message"),),
            )

        def logout(self):
            return None

    observations = (
        ProviderTrainingObservationJob(
            state_path=tmp_path / "observer.json",
            source_factory=lambda _account: Source(),
            email_store=_Store(),
            batch_size=10,
        )
        .run_once(({"account_id": "account-1"},))
        .observations
    )
    snapshot = build_folder_training_snapshot(
        observations,
        snapshot_id="snapshot-junk",
        description_version="descriptions-v1",
        observed_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        seed=20260905,
        proposed_splits={"same-message": "train"},
    )

    assert len(snapshot.observations) == 1
    assert snapshot.observations[0].provider_folder_id == "trash"
    assert snapshot.observations[0].category_key == "junk"


def test_bounded_membership_tombstone_removes_source_after_move_and_resumes(
    tmp_path,
) -> None:
    from app.email_training_observer import ProviderTrainingObservationJob

    phase = [0]
    membership_calls = []

    class Source:
        def list_folders(self):
            if phase[0] == 0:
                return (_folder("work"),)
            return (_folder("work"), _folder("legal"))

        def fetch_uid_membership(self, folder, *, cursor_uidvalidity, uids):
            membership_calls.append((folder, cursor_uidvalidity, uids))
            existing = (
                frozenset() if folder == "work" and phase[0] > 0 else frozenset(uids)
            )
            return SimpleNamespace(
                uidvalidity=10 if folder == "work" else 20,
                existing_uids=existing,
                important_signals_by_uid={
                    uid: ImportantSignals((), False) for uid in existing
                },
            )

        def fetch_uid_batch(self, folder, *, cursor_uidvalidity, last_seen_uid, limit):
            if phase[0] == 0:
                return SimpleNamespace(
                    uidvalidity=10, messages=(_message(5, "moved-message"),)
                )
            if folder == "work":
                return SimpleNamespace(uidvalidity=10, messages=())
            return SimpleNamespace(
                uidvalidity=20, messages=(_message(1, "moved-message"),)
            )

        def logout(self):
            return None

    store = _Store(
        (
            {
                "account_id": "account-1",
                "provider_folder_id": "work",
                "category_key": "work",
                "binding_status": "active",
            },
            {
                "account_id": "account-1",
                "provider_folder_id": "legal",
                "category_key": "legal",
                "binding_status": "active",
            },
        )
    )
    arguments = dict(
        state_path=tmp_path / "observer.json",
        source_factory=lambda _account: Source(),
        email_store=store,
        batch_size=10,
        reconciliation_batch_size=1,
    )
    ProviderTrainingObservationJob(**arguments).run_once(({"account_id": "account-1"},))
    phase[0] = 1
    moved = ProviderTrainingObservationJob(**arguments).run_once(
        ({"account_id": "account-1"},)
    )

    assert membership_calls == [("work", 10, (5,))]
    assert len(moved.observations) == 1
    assert moved.observations[0]["provider_folder_id"] == "legal"
    assert moved.observations[0]["bound_category_key"] == "legal"
    assert store.current_observation_calls[-1][2] == (
        "account-1:legal",
        "account-1:work",
    )


def test_observer_publishes_excluded_folders_and_removed_accounts(tmp_path) -> None:
    from app.email_training_observer import ProviderTrainingObservationJob

    phase = ["inbox"]

    class Source:
        def list_folders(self):
            if phase[0] == "sent":
                return (_folder("sent", role=FolderRole.SENT),)
            return (_folder("inbox"),)

        def fetch_uid_membership(self, _folder, *, cursor_uidvalidity, uids):
            return SimpleNamespace(
                uidvalidity=cursor_uidvalidity,
                existing_uids=frozenset(),
                important_signals_by_uid={},
            )

        def fetch_uid_batch(self, folder, **_kwargs):
            if folder == phase[0]:
                return SimpleNamespace(
                    uidvalidity=10, messages=(_message(1, "message-1"),)
                )
            return SimpleNamespace(uidvalidity=10, messages=())

        def logout(self):
            return None

    store = _Store()
    job = ProviderTrainingObservationJob(
        state_path=tmp_path / "observer.json",
        source_factory=lambda _account: Source(),
        email_store=store,
        batch_size=10,
    )
    job.run_once(({"account_id": "account-1"},))
    phase[0] = "sent"
    result = job.run_once(({"account_id": "account-1"},))

    assert result.observations[0]["folder_role"] == FolderRole.SENT
    assert store.current_observation_calls[-1][2] == (
        "account-1:inbox",
        "account-1:sent",
    )

    job.run_once(())
    assert store.current_observation_calls[-1][3] == ()


def test_membership_reconciliation_cursor_is_bounded_and_resumes_after_restart(
    tmp_path,
) -> None:
    from app.email_training_observer import ProviderTrainingObservationJob

    membership_calls = []
    first_fetch = [True]

    class Source:
        def list_folders(self):
            return (_folder(),)

        def fetch_uid_membership(self, _folder, *, cursor_uidvalidity, uids):
            membership_calls.append(uids)
            return SimpleNamespace(
                uidvalidity=cursor_uidvalidity,
                existing_uids=frozenset(uids),
                important_signals_by_uid={
                    uid: ImportantSignals((), False) for uid in uids
                },
            )

        def fetch_uid_batch(self, *_args, **_kwargs):
            if first_fetch[0]:
                first_fetch[0] = False
                return SimpleNamespace(
                    uidvalidity=10,
                    messages=tuple(
                        _message(uid, f"message-{uid}") for uid in (1, 2, 3)
                    ),
                )
            return SimpleNamespace(uidvalidity=10, messages=())

        def logout(self):
            return None

    arguments = dict(
        state_path=tmp_path / "observer.json",
        source_factory=lambda _account: Source(),
        email_store=_Store(),
        batch_size=10,
        reconciliation_batch_size=1,
    )
    ProviderTrainingObservationJob(**arguments).run_once(({"account_id": "account-1"},))
    ProviderTrainingObservationJob(**arguments).run_once(({"account_id": "account-1"},))
    ProviderTrainingObservationJob(**arguments).run_once(({"account_id": "account-1"},))

    assert membership_calls == [(1,), (2,)]


def test_pending_generation_waits_for_all_membership_pages_and_restart(tmp_path):
    from app.email_training_observer import (
        ProviderTrainingObservationJob,
        TrainingObservationCoordinator,
    )

    state_path = tmp_path / "observer.json"
    request_path = tmp_path / "requests.json"
    seeding = [True]
    membership_calls = []

    class Source:
        def list_folders(self):
            return (_folder(),)

        def fetch_uid_membership(self, _folder, *, cursor_uidvalidity, uids):
            membership_calls.append(uids)
            return SimpleNamespace(
                uidvalidity=cursor_uidvalidity,
                existing_uids=frozenset(uid for uid in uids if uid != 51),
                important_signals_by_uid={
                    uid: ImportantSignals((), False) for uid in uids if uid != 51
                },
            )

        def fetch_uid_batch(self, *_args, **_kwargs):
            if seeding[0]:
                seeding[0] = False
                return SimpleNamespace(
                    uidvalidity=10,
                    messages=tuple(
                        _message(uid, f"message-{uid}") for uid in range(1, 52)
                    ),
                )
            return SimpleNamespace(uidvalidity=10, messages=())

        def logout(self):
            return None

    def build_coordinator():
        job = ProviderTrainingObservationJob(
            state_path=state_path,
            source_factory=lambda _account: Source(),
            email_store=_Store(),
            batch_size=100,
            reconciliation_batch_size=50,
        )
        return job, TrainingObservationCoordinator(
            request_state_path=request_path,
            job=job,
            accounts_loader=lambda: ({"account_id": "account-1"},),
            publish=lambda observations: tuple(observations),
        )

    seed_job, _coordinator = build_coordinator()
    seed_job.run_once(({"account_id": "account-1"},))
    _job, coordinator = build_coordinator()
    coordinator.request()
    first = coordinator.tick()

    request_state = json.loads(request_path.read_text())
    assert request_state["requested_generation"] == 1
    assert request_state["handled_generation"] == 0
    assert first is None

    restarted_job, restarted = build_coordinator()
    second = restarted.tick()

    request_state = json.loads(request_path.read_text())
    assert membership_calls == [tuple(range(1, 51)), (51,)]
    assert request_state["handled_generation"] == 1
    assert len(second) == 50
    assert all(
        row["stable_message_identity"] != "message-51"
        for row in restarted_job.cached_observations()
    )


def test_new_generation_during_reconciliation_starts_a_fresh_bounded_round(
    tmp_path,
):
    from app.email_training_observer import (
        ProviderTrainingObservationJob,
        TrainingObservationCoordinator,
    )

    state_path = tmp_path / "observer.json"
    request_path = tmp_path / "requests.json"
    seeding = [True]
    removed_uids = set()
    membership_calls = []
    publications = []

    class Source:
        def list_folders(self):
            return (_folder(),)

        def fetch_uid_membership(self, _folder, *, cursor_uidvalidity, uids):
            membership_calls.append(uids)
            existing = frozenset(uid for uid in uids if uid not in removed_uids)
            return SimpleNamespace(
                uidvalidity=cursor_uidvalidity,
                existing_uids=existing,
                important_signals_by_uid={
                    uid: ImportantSignals((), False) for uid in existing
                },
            )

        def fetch_uid_batch(self, *_args, **_kwargs):
            if seeding[0]:
                seeding[0] = False
                return SimpleNamespace(
                    uidvalidity=10,
                    messages=tuple(
                        _message(uid, f"message-{uid}") for uid in range(1, 52)
                    ),
                )
            return SimpleNamespace(uidvalidity=10, messages=())

        def logout(self):
            return None

    job = ProviderTrainingObservationJob(
        state_path=state_path,
        source_factory=lambda _account: Source(),
        email_store=_Store(),
        batch_size=100,
        reconciliation_batch_size=50,
    )
    accounts = ({"account_id": "account-1"},)
    job.run_once(accounts)

    def build_coordinator():
        return TrainingObservationCoordinator(
            request_state_path=request_path,
            job=job,
            accounts_loader=lambda: accounts,
            publish=lambda observations: publications.append(tuple(observations)),
        )

    coordinator = build_coordinator()

    coordinator.request("generation-1")
    assert coordinator.tick() is None
    assert membership_calls == [tuple(range(1, 51))]
    assert publications == []
    assert json.loads(request_path.read_text())["processing_generation"] == 1

    removed_uids.add(1)
    coordinator = build_coordinator()
    coordinator.request("generation-2")
    coordinator.tick()

    after_generation_1 = json.loads(request_path.read_text())
    assert after_generation_1["handled_generation"] == 1
    assert after_generation_1["processing_generation"] is None
    assert len(publications) == 1
    assert any(row["stable_message_identity"] == "message-1" for row in publications[0])

    coordinator = build_coordinator()
    assert coordinator.tick() is None
    during_generation_2 = json.loads(request_path.read_text())
    assert during_generation_2["handled_generation"] == 1
    assert during_generation_2["processing_generation"] == 2
    assert membership_calls == [
        tuple(range(1, 51)),
        (51,),
        tuple(range(1, 51)),
    ]
    assert len(publications) == 1

    coordinator = build_coordinator()
    coordinator.tick()

    completed = json.loads(request_path.read_text())
    assert completed["handled_generation"] == 2
    assert completed["processing_generation"] is None
    assert len(publications) == 2
    assert all(row["stable_message_identity"] != "message-1" for row in publications[1])


def test_change_detector_persists_folder_fingerprint_and_only_requests_on_change(
    tmp_path,
):
    from app.email_imap_readonly import ProviderFolderFingerprint
    from app.email_training_observer import (
        ProviderTrainingChangeDetector,
        ProviderTrainingObservationJob,
    )

    fingerprints = [
        ProviderFolderFingerprint(10, 2, 1, 100),
        ProviderFolderFingerprint(10, 2, 1, 101),
        ProviderFolderFingerprint(10, 2, 1, 101),
    ]
    requests = []

    class Source:
        def list_folders(self):
            return (_folder(),)

        def fetch_folder_fingerprint(self, _folder):
            return fingerprints.pop(0)

        def logout(self):
            return None

    def build_detector():
        job = ProviderTrainingObservationJob(
            state_path=tmp_path / "observer.json",
            source_factory=lambda _account: Source(),
            email_store=_Store(),
            batch_size=10,
        )
        return ProviderTrainingChangeDetector(
            job=job,
            accounts_loader=lambda: ({"account_id": "account-1"},),
            request=lambda _key=None: requests.append("requested"),
        )

    assert build_detector().tick() is False
    assert build_detector().tick() is True
    assert build_detector().tick() is False
    assert requests == ["requested"]


def test_legacy_cache_without_fingerprint_requests_reconciliation_before_baseline(
    tmp_path,
):
    from app.email_imap_readonly import ProviderFolderFingerprint
    from app.email_training_observer import (
        ProviderTrainingChangeDetector,
        ProviderTrainingObservationJob,
        TrainingObservationCoordinator,
    )

    state_path = tmp_path / "observer.json"
    request_path = tmp_path / "requests.json"
    phase = ["seed"]

    class Source:
        def list_folders(self):
            return (_folder(),)

        def fetch_folder_fingerprint(self, _folder):
            return ProviderFolderFingerprint(10, 2, 1, 101)

        def fetch_uid_membership(self, _folder, *, cursor_uidvalidity, uids):
            return SimpleNamespace(
                uidvalidity=cursor_uidvalidity,
                existing_uids=frozenset(uids),
                important_signals_by_uid={
                    uid: ImportantSignals(("\\Flagged",), True) for uid in uids
                },
            )

        def fetch_uid_batch(self, *_args, **_kwargs):
            if phase[0] == "seed":
                phase[0] = "changed"
                return SimpleNamespace(
                    uidvalidity=10, messages=(_message(1, "message-1"),)
                )
            return SimpleNamespace(uidvalidity=10, messages=())

        def logout(self):
            return None

    job = ProviderTrainingObservationJob(
        state_path=state_path,
        source_factory=lambda _account: Source(),
        email_store=_Store(),
        batch_size=10,
    )
    job.run_once(({"account_id": "account-1"},))
    legacy = json.loads(state_path.read_text())
    folder_state = legacy["accounts"]["account-1"]["folders"]["inbox"]
    folder_state.pop("fingerprint", None)
    folder_state.pop("fingerprint_migration", None)
    state_path.write_text(json.dumps(legacy))

    publications = []
    coordinator = TrainingObservationCoordinator(
        request_state_path=request_path,
        job=job,
        accounts_loader=lambda: ({"account_id": "account-1"},),
        publish=lambda observations: publications.append(tuple(observations)),
    )
    detector = ProviderTrainingChangeDetector(
        job=job,
        accounts_loader=lambda: ({"account_id": "account-1"},),
        request=coordinator.request,
    )

    assert detector.tick() is True
    pending = json.loads(state_path.read_text())["accounts"]["account-1"]["folders"][
        "inbox"
    ]
    assert pending["fingerprint_migration"] == "pending"
    coordinator.tick()

    assert len(publications) == 1
    assert publications[0][0]["important_signals"].provider_important is True
    completed = json.loads(state_path.read_text())["accounts"]["account-1"]["folders"][
        "inbox"
    ]
    assert completed["fingerprint_migration"] == "complete"


def test_legacy_fingerprint_migration_request_replay_is_idempotent(
    tmp_path, monkeypatch
):
    import app.email_training_observer as observer_module
    from app.email_imap_readonly import ProviderFolderFingerprint
    from app.email_training_observer import (
        ProviderTrainingChangeDetector,
        ProviderTrainingObservationJob,
        TrainingObservationCoordinator,
    )

    state_path = tmp_path / "observer.json"
    request_path = tmp_path / "requests.json"

    class Source:
        def list_folders(self):
            return (_folder(),)

        def fetch_folder_fingerprint(self, _folder):
            return ProviderFolderFingerprint(10, 2, 1, 101)

        def logout(self):
            return None

    state_path.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": {
                    "account-1": {
                        "folders": {
                            "inbox": {
                                "uidvalidity": 10,
                                "highest_uid": 1,
                                "reconcile_after_uid": 0,
                                "status": "ready",
                                "observations": {
                                    "message-1": {
                                        "uid": 1,
                                        "observation": {
                                            "important_signals": {
                                                "raw_signal_names": [],
                                                "provider_important": False,
                                            },
                                            "folder_role": "inbox",
                                        },
                                    }
                                },
                            }
                        }
                    }
                },
            }
        )
    )
    job = ProviderTrainingObservationJob(
        state_path=state_path,
        source_factory=lambda _account: Source(),
        email_store=_Store(),
    )
    coordinator = TrainingObservationCoordinator(
        request_state_path=request_path,
        job=job,
        accounts_loader=lambda: (),
        publish=lambda _observations: None,
    )
    detector = ProviderTrainingChangeDetector(
        job=job,
        accounts_loader=lambda: ({"account_id": "account-1"},),
        request=coordinator.request,
    )

    real_write = observer_module._write_state_atomic
    crashed = [False]

    def crash_after_request(path, value):
        if path == state_path and not crashed[0]:
            crashed[0] = True
            raise SystemExit("crash-after-durable-request")
        real_write(path, value)

    monkeypatch.setattr(observer_module, "_write_state_atomic", crash_after_request)
    with pytest.raises(SystemExit, match="crash-after-durable-request"):
        detector.tick()
    monkeypatch.setattr(observer_module, "_write_state_atomic", real_write)
    assert detector.tick() is True

    requests = json.loads(request_path.read_text())
    assert requests["requested_generation"] == 1


def test_change_detector_without_modseq_probes_cached_flags_in_bounded_pages(
    tmp_path,
):
    from app.email_imap_readonly import ProviderFolderFingerprint
    from app.email_training_observer import (
        ProviderTrainingChangeDetector,
        ProviderTrainingObservationJob,
    )

    seeding = [True]
    probes = []
    requests = []

    class Source:
        def list_folders(self):
            return (_folder(),)

        def fetch_folder_fingerprint(self, _folder):
            return ProviderFolderFingerprint(10, 52, 51, None)

        def fetch_uid_membership(self, _folder, *, cursor_uidvalidity, uids):
            probes.append(uids)
            return SimpleNamespace(
                uidvalidity=cursor_uidvalidity,
                existing_uids=frozenset(uids),
                important_signals_by_uid={
                    uid: (
                        ImportantSignals(("\\Flagged",), True)
                        if uid == 51
                        else ImportantSignals((), False)
                    )
                    for uid in uids
                },
            )

        def fetch_uid_batch(self, *_args, **_kwargs):
            if seeding[0]:
                seeding[0] = False
                return SimpleNamespace(
                    uidvalidity=10,
                    messages=tuple(
                        _message(uid, f"message-{uid}") for uid in range(1, 52)
                    ),
                )
            return SimpleNamespace(uidvalidity=10, messages=())

        def logout(self):
            return None

    state_path = tmp_path / "observer.json"
    ProviderTrainingObservationJob(
        state_path=state_path,
        source_factory=lambda _account: Source(),
        email_store=_Store(),
        batch_size=100,
        reconciliation_batch_size=50,
    ).run_once(({"account_id": "account-1"},))

    def detector():
        job = ProviderTrainingObservationJob(
            state_path=state_path,
            source_factory=lambda _account: Source(),
            email_store=_Store(),
            batch_size=100,
            reconciliation_batch_size=50,
        )
        return ProviderTrainingChangeDetector(
            job=job,
            accounts_loader=lambda: ({"account_id": "account-1"},),
            request=lambda _key=None: requests.append("requested"),
        )

    assert detector().tick() is False
    assert detector().tick() is True
    assert probes == [tuple(range(1, 51)), (51,)]
    assert requests == ["requested"]


def test_membership_reconciliation_refreshes_existing_uid_important_flags(tmp_path):
    from app.email_training_observer import ProviderTrainingObservationJob
    from app.email_training_snapshot import build_folder_training_snapshot

    phase = [0]

    class Source:
        def list_folders(self):
            return (_folder(),)

        def fetch_uid_membership(self, _folder, *, cursor_uidvalidity, uids):
            signals = (
                ImportantSignals(("\\Flagged",), True)
                if phase[0] == 1
                else ImportantSignals((), False)
            )
            return SimpleNamespace(
                uidvalidity=cursor_uidvalidity,
                existing_uids=frozenset(uids),
                important_signals_by_uid={uid: signals for uid in uids},
            )

        def fetch_uid_batch(self, *_args, **_kwargs):
            if phase[0] == 0:
                return SimpleNamespace(
                    uidvalidity=10, messages=(_message(1, "message-1"),)
                )
            return SimpleNamespace(uidvalidity=10, messages=())

        def logout(self):
            return None

    job = ProviderTrainingObservationJob(
        state_path=tmp_path / "observer.json",
        source_factory=lambda _account: Source(),
        email_store=_Store(),
        batch_size=10,
        reconciliation_batch_size=1,
    )
    job.run_once(({"account_id": "account-1"},))
    phase[0] = 1
    starred = job.run_once(({"account_id": "account-1"},))
    phase[0] = 2
    unstarred = job.run_once(({"account_id": "account-1"},))

    assert starred.observations[0]["important_signals"].provider_important is True
    assert unstarred.observations[0]["important_signals"].provider_important is False
    starred_snapshot = build_folder_training_snapshot(
        starred.observations,
        snapshot_id="snapshot-starred",
        description_version="descriptions-v1",
        observed_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        seed=20260905,
        proposed_splits={"message-1": "train"},
    )
    unstarred_snapshot = build_folder_training_snapshot(
        unstarred.observations,
        snapshot_id="snapshot-unstarred",
        description_version="descriptions-v1",
        observed_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        seed=20260905,
        proposed_splits={"message-1": "train"},
    )
    assert starred_snapshot.observations[0].important is True
    assert unstarred_snapshot.observations[0].important is False


def test_coordinator_publishes_only_after_all_observation_pages_complete(tmp_path):
    from app.email_training_observer import TrainingObservationCoordinator

    publications = []
    results = [
        SimpleNamespace(observations=({"page": 1},), more_available=True),
        SimpleNamespace(observations=({"page": 2},), more_available=False),
    ]

    class Job:
        def begin_reconciliation_generation(self, _generation):
            return None

        def run_once(self, _accounts):
            return results.pop(0)

    coordinator = TrainingObservationCoordinator(
        request_state_path=tmp_path / "requests.json",
        job=Job(),
        accounts_loader=lambda: (),
        publish=lambda observations: publications.append(tuple(observations)),
    )
    coordinator.request()

    assert coordinator.tick() is None
    assert publications == []
    assert (
        json.loads((tmp_path / "requests.json").read_text())["handled_generation"] == 0
    )

    coordinator.tick()

    assert publications == [({"page": 2},)]
    assert (
        json.loads((tmp_path / "requests.json").read_text())["handled_generation"] == 1
    )
