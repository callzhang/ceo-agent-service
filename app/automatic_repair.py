"""Automatic code repair on the existing native Workbench lifecycle."""

from collections.abc import Callable
import fcntl
import json
from pathlib import Path
import subprocess
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.automatic_repair_store import (
    TERMINAL, ensure_conversation, get_repair, list_repairs, save_repair,
)
from app.external_retry import DEFAULT_MAX_RETRY_DELAY_SECONDS, MAX_AUTOMATIC_ATTEMPTS, run_external


class RepairDiagnosis(BaseModel):
    model_config = ConfigDict(extra='forbid')
    outcome: Literal['pr_created', 'no_code_change', 'blocked']
    summary: str = Field(min_length=1)


def repair_presentation(store, source: str, source_id: int) -> dict | None:
    entry = get_repair(store, source, source_id)
    if entry is None:
        return None
    return {
        'status': entry['status'], 'summary': entry['summary'],
        'refresh_url': f'/api/console/repairs/{source}/{source_id}',
        'conversation_url': f"/?task={entry['task_id']}" if entry['task_id'] else '',
        'pr_url': entry['pr_url'], 'turn_count': len(entry['turn_ids']),
    }


class AutomaticRepairCoordinator:
    def __init__(
        self, store, *, repository: Path,
        pr_reader: Callable[[str], list[dict]] | None = None,
        interval_seconds: float = 10,
    ):
        self.store = store
        self.repository = Path(repository)
        self.pr_reader = pr_reader or self._read_prs
        self.interval_seconds = interval_seconds
        self._next_poll = 0.0

    def _read_prs(self, branch: str) -> list[dict]:
        def read():
            result = subprocess.run(
                ['gh', 'pr', 'list', '--head', branch, '--state', 'all',
                 '--json', 'url,headRefOid,state'],
                cwd=self.repository, capture_output=True, text=True, timeout=30, check=True,
            )
            return json.loads(result.stdout)
        return run_external('repair_pr_readback', read)

    def run_once(self) -> None:
        if time.monotonic() < self._next_poll:
            return
        self._next_poll = time.monotonic() + self.interval_seconds
        path = self.store.path.with_name(f'.{self.store.path.name}.automatic-repair.lock')
        with path.open('a+') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            # One repair at a time; ordinary user Workbench turns retain the
            # executor's existing independent capacity and leases.
            entries = [entry for entry in list_repairs(self.store)
                       if entry['status'] not in TERMINAL
                       and entry.get('next_readback_at', 0) <= time.time()]
            entries.sort(key=lambda entry: (entry['status'] != 'running',
                                            not bool(entry['task_id']), entry['source_id']))
            if entries:
                self._advance(entries[0])

    def _advance(self, entry: dict) -> None:
        if entry.get('next_readback_at', 0) > time.time():
            return
        entry = ensure_conversation(self.store, entry)
        branch = f"codex/repair-{entry['source']}-{entry['source_id']}"
        turn_ids = entry['turn_ids']
        if turn_ids:
            turn = self.store.get_turn(turn_ids[-1])
            if turn is None:
                raise ValueError('automatic repair turn is missing')
            if turn.status in {'queued', 'running'}:
                return
            if turn.status == 'stopped':
                entry.update(status='stopped', summary='修复对话已停止。')
                save_repair(self.store, entry)
                return
            try:
                prs = self.pr_reader(branch)
            except Exception as exc:
                entry.update(status='awaiting_pr', summary=f'等待 PR 读回：{exc}',
                             next_readback_at=time.time() + DEFAULT_MAX_RETRY_DELAY_SECONDS)
                save_repair(self.store, entry)
                return
            if prs:
                pr = prs[0]
                entry.update(status='pr_created', pr_url=pr['url'],
                             pr_head=pr['headRefOid'], pr_state=pr['state'],
                             summary='远端 PR 已创建，尚不代表合并、部署或原业务问题已解决。')
                save_repair(self.store, entry)
                return
            try:
                diagnosis = RepairDiagnosis.model_validate_json(turn.final_text)
            except ValidationError:
                diagnosis = None
            if diagnosis is not None and diagnosis.outcome != 'pr_created':
                entry.update(status=diagnosis.outcome, summary=diagnosis.summary)
                save_repair(self.store, entry)
                return
            entry['summary'] = turn.error_detail or '上一轮未交付可读回的 PR；继续原修复对话。'
            if len(turn_ids) >= MAX_AUTOMATIC_ATTEMPTS:
                entry.update(status='failed', summary='修复续接次数已耗尽，尚未交付 PR。')
                save_repair(self.store, entry)
                return
        source = entry['source']
        identity = entry['source_id']
        source_url = f'/attempts/{identity}' if source == 'reply_attempts' else f'/history/errors/{identity}'
        prompt = (
            f"Derek has authorized automatic code repair for this service error. "
            f"Read {Path(__file__).resolve().parents[1]}/docs/automatic-error-repair.md and follow its workflow. "
            f"Source: {source} #{identity}, URL http://127.0.0.1:8765{source_url}, "
            f"database {self.store.path}. Development repository {self.repository}. "
            f"Use branch {branch} and worktree {self.repository}/.worktrees/repair-{source}-{identity}. "
            f"This is turn {len(turn_ids) + 1}/{MAX_AUTOMATIC_ATTEMPTS} in the SAME repair conversation. "
            f"Previous progress: {entry['summary'] or 'No previous repair turn.'} "
            "Read the original trajectory, fix the mechanism, run regression tests and open a PR. "
            "Continue existing work instead of restarting diagnosis. Do not merge, deploy or replay business actions. "
            'End with ONLY JSON: {"outcome":"pr_created","summary":"concrete result and evidence"}. '
            'For a proven non-code cause use outcome no_code_change; for an external blocker use blocked. '
            "A plan or an intention to continue is not completion."
        )
        turn = self.store.create_turn(
            entry['task_id'], user_text=prompt,
            client_request_id=f'automatic-repair:{source}:{identity}:{len(turn_ids) + 1}',
        )
        entry.update(status='running', turn_ids=[*turn_ids, turn.id])
        save_repair(self.store, entry)
