"""Automatic code repair on the existing native Workbench lifecycle."""

from collections.abc import Callable
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import time

from pydantic import ValidationError

from app.automatic_repair_contracts import (
    PRRepairResult, RepairDiagnosis, REPAIR_DIAGNOSIS_CONTRACT, REPAIR_PR_CONTRACT,
    RepairMergeResult, REPAIR_MERGE_CONTRACT,
)
from app.automatic_repair_store import (
    TERMINAL, ensure_conversation, get_repair, list_repairs, save_repair,
)
from app.repair_pull_request import PullRequestReader, assess_feedback, review_fingerprint
from app.external_retry import DEFAULT_MAX_RETRY_DELAY_SECONDS, MAX_AUTOMATIC_ATTEMPTS, run_external


def repair_presentation(store, source: str, source_id: int) -> dict | None:
    entry = get_repair(store, source, source_id)
    if entry is None:
        return None
    return {
        'status': entry['status'], 'summary': entry['summary'],
        'refresh_url': f'/api/console/repairs/{source}/{source_id}',
        'conversation_url': f"/?task={entry['task_id']}" if entry['task_id'] else '',
        'pr_url': entry['pr_url'], 'turn_count': len(entry['turn_ids']),
        'merge_commit': entry.get('merge_commit', ''), 'merged_at': entry.get('merged_at', ''),
    }


class AutomaticRepairCoordinator:
    def __init__(
        self, store, *, repository: Path,
        pr_reader: Callable[[str], list[dict]] | None = None,
        interval_seconds: float = 10,
        feedback_reader: Callable[[str], dict] | None = None,
        pr_poll_seconds: float = 60,
    ):
        self.store = store
        self.repository = Path(repository)
        self.pr_reader = pr_reader or self._read_prs
        self.interval_seconds = interval_seconds
        self.feedback_reader = feedback_reader or PullRequestReader(self.repository)
        self.pr_poll_seconds = pr_poll_seconds
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
            candidates = []
            now = time.time()
            for entry in list_repairs(self.store):
                if entry['status'] in TERMINAL:
                    continue
                summary = self.store.get_task_summary(entry['task_id']) if entry['task_id'] else None
                active = summary is not None and summary[1] in {'queued', 'running'}
                if active or entry.get('next_readback_at', 0) <= now:
                    candidates.append((entry, active))
            candidates.sort(key=lambda item: (not item[1],
                                              not bool(item[0]['task_id']), int(item[0]['task_id'] or 0),
                                              item[0]['source_id']))
            if candidates:
                self._advance(candidates[0][0])

    def _advance(self, entry: dict) -> None:
        if entry.get('next_readback_at', 0) > time.time():
            return
        entry = ensure_conversation(self.store, entry)
        if entry.get('pending_initial_turn'):
            self._submit_initial_turn(entry)
            return
        if entry['pr_url']:
            self._advance_pr(entry)
            return
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
            "Continue existing work instead of restarting diagnosis. The same conversation will merge "
            "a proven deterministic fix after CI/review pass. Do not deploy or replay business actions. "
            'End with ONLY JSON: {"outcome":"pr_created","summary":"concrete result and evidence"}. '
            'For a proven non-code cause use outcome no_code_change; for an external blocker use blocked. '
            "A plan or an intention to continue is not completion."
        )
        entry['pending_initial_turn'] = {
            'prompt': prompt, 'result_contract': REPAIR_DIAGNOSIS_CONTRACT,
            'request_id': f'automatic-repair:{source}:{identity}:{len(turn_ids) + 1}',
        }
        save_repair(self.store, entry)
        self._submit_initial_turn(entry)

    def _submit_initial_turn(self, entry: dict) -> None:
        pending = entry['pending_initial_turn']
        turn = self.store.create_turn(entry['task_id'], user_text=pending['prompt'],
                                      client_request_id=pending['request_id'],
                                      result_contract=pending['result_contract'])
        entry.update(status='running', turn_ids=[*entry['turn_ids'], turn.id])
        del entry['pending_initial_turn']
        save_repair(self.store, entry)

    def _advance_pr(self, entry: dict) -> None:
        if entry.get('pending_feedback_turn'):
            self._submit_feedback_turn(entry)
            return
        turn = self.store.get_turn(entry['turn_ids'][-1]) if entry['turn_ids'] else None
        if turn is not None and turn.status in {'queued', 'running'}:
            return
        contract = self.store.get_turn_result_contract(turn.id) if turn is not None else ''
        stopped = turn is not None and turn.status == 'stopped'
        if stopped and contract != REPAIR_MERGE_CONTRACT:
            entry.update(status='stopped', summary='修复对话已停止。')
            save_repair(self.store, entry)
            return
        if turn is not None and not stopped and contract == REPAIR_MERGE_CONTRACT:
            try:
                merge_result = RepairMergeResult.model_validate_json(turn.final_text)
            except ValidationError as exc:
                entry['last_merge_result'] = {
                    'error': 'invalid_result', 'details': json.loads(exc.json(include_input=False)),
                }
            else:
                entry['last_merge_result'] = merge_result.model_dump()
        elif turn is not None and not stopped and entry.get('active_feedback'):
            try:
                result = PRRepairResult.model_validate_json(turn.final_text)
            except ValidationError as exc:
                result = None
                entry['last_feedback_result'] = {
                    'error': 'invalid_result', 'details': json.loads(exc.json(include_input=False)),
                }
            if result is not None:
                entry['last_feedback_result'] = result.model_dump()
                entry['last_feedback_summary'] = result.summary
                handled = set(entry.get('handled_reviews', []))
                handled.update(review_fingerprint(review) for review in entry['active_feedback']['commented_reviews']
                               if review['id'] in result.addressed_review_ids)
                entry['handled_reviews'] = sorted(handled)
        try:
            pr = self.feedback_reader(entry['pr_url'])
        except Exception as exc:
            entry.update(status='awaiting_pr', summary=f'等待 PR 检查与 review 读回：{exc}',
                         next_readback_at=time.time() + DEFAULT_MAX_RETRY_DELAY_SECONDS)
            save_repair(self.store, entry)
            return
        entry.update(pr_head=pr['headRefOid'], pr_state=pr['state'],
                     next_readback_at=time.time() + self.pr_poll_seconds)
        if pr['state'] == 'MERGED':
            commit = (pr.get('mergeCommit') or {}).get('oid', '')
            if pr.get('baseRefName') and pr['baseRefName'] != 'main':
                entry.update(status='pr_closed', summary=f'PR 已合并到 {pr["baseRefName"]}，未合并到 main。')
            elif commit and pr.get('mergedAt') and pr.get('baseRefName') == 'main':
                entry.update(status='merged', merge_commit=commit, merged_at=pr['mergedAt'],
                             summary=f'PR 已合并到 main，合并提交 {commit}；原业务状态保持原记录。')
            else:
                entry.update(status='awaiting_pr', summary='GitHub 已显示 MERGED，等待合并提交与时间读回。')
            save_repair(self.store, entry)
            return
        if pr['state'] != 'OPEN':
            entry.update(status='pr_closed', summary=f"PR 状态为 {pr['state']}；自动续接已结束。")
            save_repair(self.store, entry)
            return
        if stopped:
            entry.update(status='stopped', summary='合并对话已停止，GitHub 尚未返回合并结果；不自动重新执行。')
            save_repair(self.store, entry)
            return
        feedback, identity, actionable, passed, pending = assess_feedback(pr, entry.get('handled_reviews', []))
        schema = PRRepairResult.model_json_schema()
        identity = hashlib.sha256(json.dumps(
            {'feedback_id': identity, 'result_schema': schema}, sort_keys=True,
        ).encode()).hexdigest()
        if not actionable:
            if passed:
                self._continue_merge(entry, pr, feedback)
                return
            entry.update(status='awaiting_checks',
                         summary=f'等待当前提交的检查结果：{pending} 项进行中；尚未判定通过。')
        else:
            attempts = entry.get('feedback_attempt_counts', {}).get(identity, 0)
            if attempts >= MAX_AUTOMATIC_ATTEMPTS:
                entry.update(status='feedback_blocked',
                             summary='相同 PR 反馈的修复次数已耗尽；保留原对话，等待新提交或新反馈。'
                                     + entry.get('last_feedback_summary', ''))
            else:
                prompt = (
                    f"Goal: bring ALL current checks and review findings on PR {pr['url']} to passing status. "
                    "The scope is the PR's entire failing check set, including failures that predate this PR "
                    "or also reproduce on main. Derek has authorized repairing that code/tests in this PR. "
                    f"Continue the SAME repair conversation and branch {pr['headRefName']}; "
                    f"development repository {self.repository}. "
                    f"Read {Path(__file__).resolve().parents[1]}/docs/automatic-error-repair.md. "
                    f"Attempt {attempts + 1}/{MAX_AUTOMATIC_ATTEMPTS} for this feedback/result contract. "
                    "Use the previous result and current remote facts below to finish remaining work. "
                    "Read full job logs/discussions, verify root causes, add/run focused regressions, "
                    "commit and push the same PR. Resolve verified review corrections; report actual external blockers. "
                    "Do not weaken checks/test limits, deploy, or replay business actions. "
                    "After checks/review pass, this same conversation will merge a proven deterministic fix. "
                    "Return the declared native result JSON, including concrete remaining_work until the goal is met. "
                    "The coordinator independently observes the new head and remote results.\n"
                    + json.dumps({'previous_result': entry.get('last_feedback_result'),
                                  'current_feedback': feedback},
                                 ensure_ascii=False, sort_keys=True)
                )
                entry['pending_feedback_turn'] = {
                    'prompt': prompt, 'identity': identity, 'attempt': attempts + 1,
                    'feedback': feedback, 'result_contract': REPAIR_PR_CONTRACT,
                    'request_id': f"automatic-repair:{entry['source']}:{entry['source_id']}:pr:{identity}:{attempts + 1}",
                }
                # Save intent before creating a native turn. A crash and changed GitHub
                # evidence must still recover exactly this turn before reading again.
                save_repair(self.store, entry)
                self._submit_feedback_turn(entry)
                return
        save_repair(self.store, entry)

    def _continue_merge(self, entry: dict, pr: dict, feedback: dict) -> None:
        result = entry.get('last_merge_result', {})
        if result.get('head_sha') == pr['headRefOid'] and result.get('outcome') in {'review_required', 'blocked'}:
            entry.update(status='review_required' if result['outcome'] == 'review_required' else 'merge_blocked',
                         summary=result['summary'])
            save_repair(self.store, entry)
            return
        identity = hashlib.sha256(json.dumps({
            'merge_head': pr['headRefOid'], 'result_schema': RepairMergeResult.model_json_schema(),
        }, sort_keys=True).encode()).hexdigest()
        attempts = entry.get('feedback_attempt_counts', {}).get(identity, 0)
        if attempts >= MAX_AUTOMATIC_ATTEMPTS:
            entry.update(status='merge_blocked', summary='当前提交的合并执行次数已耗尽，尚无 GitHub 合并回执；保留原对话并继续观察新提交。')
            save_repair(self.store, entry)
            return
        prompt = (
            f"Derek authorizes automatic merge to main for proven deterministic fixes. "
            f"Continue this SAME repair conversation, worktree and PR {pr['url']}, branch {pr['headRefName']}. "
            f"Development repository: {self.repository}. Locate and reuse the existing worktree for this "
            "PR branch with git worktree list; preserve its progress and other authors' commits. "
            f"Read {Path(__file__).resolve().parents[1]}/docs/automatic-error-repair.md. "
            f"Current-head CI and review are passing at {pr['headRefOid']}. "
            "Inspect the complete final diff, root cause and red/green regression evidence, including CI repairs. "
            "A deterministic fix has a confirmed failing mechanism and passing regression without competing product objectives. "
            "For a deterministic fix, verify the PR still targets main and current checks/review still pass, "
            f"then run gh pr merge {pr['url']} --merge --match-head-commit {pr['headRefOid']}. "
            "Use native GitHub requirements; do not bypass them. Read back state, mergeCommit and mergedAt. "
            "For a product/algorithm tradeoff needing evaluation, return review_required and explain the decision/eval needed. "
            "If the head moved, conflicts or new feedback require corrections, update this same PR and return pr_updated. "
            "For a concrete external blocker return blocked. Do not deploy or replay business actions. "
            "Return the declared native result JSON with head_sha, deterministic_fix, regression_evidence and concrete summary.\n"
            + json.dumps({'previous_merge_result': result, 'current_pr': pr}, ensure_ascii=False, sort_keys=True)
        )
        entry['pending_feedback_turn'] = {
            'prompt': prompt, 'identity': identity, 'attempt': attempts + 1, 'feedback': feedback,
            'result_contract': REPAIR_MERGE_CONTRACT,
            'request_id': f"automatic-repair:{entry['source']}:{entry['source_id']}:merge:{identity}:{attempts + 1}",
        }
        save_repair(self.store, entry)
        self._submit_feedback_turn(entry)

    def _submit_feedback_turn(self, entry: dict) -> None:
        pending = entry['pending_feedback_turn']
        turn = self.store.create_turn(entry['task_id'], user_text=pending['prompt'],
                                      client_request_id=pending['request_id'],
                                      result_contract=pending.get('result_contract', ''))
        counts = entry.get('feedback_attempt_counts', {})
        counts[pending['identity']] = pending['attempt']
        feedback = pending['feedback']
        entry.update(status='running', turn_ids=[*entry['turn_ids'], turn.id],
                     feedback_attempt_counts=counts, active_feedback=feedback,
                     next_readback_at=0,
                     summary=f"当前提交有 {len(feedback['failed_checks'])} 项 CI 失败、"
                             f"{len(feedback['unresolved_threads'])} 个未解决 review、"
                             f"{len(feedback['commented_reviews'])} 条 review 正文；已自动续接原对话。")
        if self.store.get_turn_result_contract(turn.id) == REPAIR_MERGE_CONTRACT:
            entry.update(status='merging', summary='CI / review 已通过；同一 Agent 正在核对确定性修复证据并合并到 main。')
        del entry['pending_feedback_turn']
        save_repair(self.store, entry)
