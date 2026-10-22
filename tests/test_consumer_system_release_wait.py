from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.consumer_system_release as release_module
from app.consumer_system_release import ConsumerSystemPublication, MANAGED_NAMES


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _Store:
    def __init__(self, *, config_reads, receipt_reads, digests):
        self.config_reads = list(config_reads)
        self.receipt_reads = list(receipt_reads)
        self.digests = digests
        self.bindings = [
            SimpleNamespace(skill_id=index, revision_id=index, enabled=True)
            for index, _name in enumerate(MANAGED_NAMES, start=1)
        ]
        self.skills = {
            name: SimpleNamespace(id=index)
            for index, name in enumerate(MANAGED_NAMES, start=1)
        }

    def get_active_runtime_skill_config(self):
        if len(self.config_reads) > 1:
            return self.config_reads.pop(0)
        return self.config_reads[0]

    def list_runtime_skill_bindings(self, _config_id):
        return self.bindings

    def get_managed_skill_revision(self, revision_id):
        return SimpleNamespace(sha256=self.digests[str(revision_id)])

    def get_managed_skill_by_name(self, name):
        return self.skills[name]

    def list_runtime_skill_load_receipts(self, _config_id):
        if len(self.receipt_reads) > 1:
            return self.receipt_reads.pop(0)
        return self.receipt_reads[0]


def _publication(tmp_path: Path, *, active=True, receipt=True, wrong_digest=False):
    expected = {}
    digests = {}
    plans = []
    for index, name in enumerate(MANAGED_NAMES, start=1):
        path = tmp_path / f"{name}.md"
        path.write_text(f"skill {name}\n", encoding="utf-8")
        digest = release_module._sha256(path.read_bytes())
        expected[str(index)] = digest
        digests[str(index)] = "f" * 64 if wrong_digest and index == 1 else digest
        plans.append(SimpleNamespace(
            source=f"ci/shared-skills/{name}/SKILL.md",
            new_sha256=digest,
            target=path,
        ))
    config = SimpleNamespace(id=10, parent_id=None) if active else None
    loaded_json = json.dumps(expected)
    receipt_row = SimpleNamespace(pid=os.getpid(), error=None, loaded_json=loaded_json)
    store = _Store(
        config_reads=[config if active else None],
        receipt_reads=[[receipt_row] if receipt else []],
        digests=digests,
    )
    publication = ConsumerSystemPublication(
        store=store,
        plans=tuple(plans),
        receipt_dir=tmp_path / "receipt",
        old_config_id=1,
        original_task_refs={},
        original_task_versions={},
        bindings=(),
    )
    publication.new_config_id = 10
    return publication, store, expected


def test_verify_loaded_waits_for_activation_and_worker_receipt(tmp_path, monkeypatch):
    publication, store, expected = _publication(tmp_path, active=True, receipt=True)
    active = store.config_reads[0]
    store.config_reads = [None, active]
    receipt = SimpleNamespace(pid=os.getpid(), error=None, loaded_json=json.dumps(expected))
    store.receipt_reads = [[], [receipt]]
    clock = _Clock()
    monkeypatch.setattr(release_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(release_module.time, "sleep", clock.sleep)

    publication.verify_loaded()

    assert clock.sleeps == [1, 1]


def test_verify_loaded_times_out_after_fifteen_minutes_waiting_for_activation(
    tmp_path, monkeypatch,
):
    publication, store, _expected = _publication(tmp_path, active=False, receipt=False)
    clock = _Clock()
    monkeypatch.setattr(release_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(release_module.time, "sleep", clock.sleep)

    with pytest.raises(ValueError, match="did not activate within 15 minutes"):
        publication.verify_loaded()

    assert clock.now == 15 * 60


def test_verify_loaded_times_out_after_fifteen_minutes_waiting_for_worker_receipt(
    tmp_path, monkeypatch,
):
    publication, _store, _expected = _publication(tmp_path, active=True, receipt=False)
    clock = _Clock()
    monkeypatch.setattr(release_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(release_module.time, "sleep", clock.sleep)

    with pytest.raises(ValueError, match="load receipt did not appear within 15 minutes"):
        publication.verify_loaded()

    assert clock.now == 15 * 60


def test_verify_loaded_keeps_digest_mismatch_immediate(tmp_path, monkeypatch):
    publication, _store, _expected = _publication(
        tmp_path, active=True, receipt=True, wrong_digest=True,
    )
    clock = _Clock()
    monkeypatch.setattr(release_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(release_module.time, "sleep", clock.sleep)

    with pytest.raises(ValueError, match="active Skill revision mismatch"):
        publication.verify_loaded()

    assert clock.sleeps == []


def test_verify_loaded_keeps_worker_receipt_digest_mismatch_immediate(
    tmp_path, monkeypatch,
):
    publication, store, _expected = _publication(tmp_path, active=True, receipt=True)
    store.receipt_reads = [[SimpleNamespace(
        pid=os.getpid(), error=None, loaded_json=json.dumps({"wrong": "digest"}),
    )]]
    clock = _Clock()
    monkeypatch.setattr(release_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(release_module.time, "sleep", clock.sleep)

    with pytest.raises(ValueError, match="load receipt digest mismatch"):
        publication.verify_loaded()

    assert clock.sleeps == []
