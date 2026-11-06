"""Dispatch the exact task file and destination approved by Audit."""

import hashlib
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, uuid5

from app.agent_contracts import ProposedAction
from app.system_executor import ActionOutcome
from app.task_artifacts import task_artifact_directory


class DingTalkFileHandler:
    def __init__(self, store: object, dws: object) -> None:
        self.store = store
        self.dws = dws

    def _verified(self, action: ProposedAction, evidence: dict) -> ActionOutcome:
        verification = self.dws.verify_message_send_result(evidence['provider_result'])
        result = {**evidence, 'verification': verification}
        if verification['state'] == 'failed':
            return ActionOutcome('failed', result)
        status = verification.get('status_result', {}).get('result', {})
        if (verification['state'] != 'sent' or not status.get('openMessageId')
                or not status.get('openConversationId')
                or (action.target.get('conversation_id')
                    and status['openConversationId'] != action.target['conversation_id'])):
            return ActionOutcome('uncertain', result)
        return ActionOutcome('verified', result)

    def dispatch(self, action: ProposedAction, *, action_key: str, candidate: dict) -> ActionOutcome:
        task = self.store.get_reply_task_for_execution(
            candidate['task_id'], candidate['execution_generation'],
        )
        if task is None:
            return ActionOutcome('confirmed_no_effect', {'reason': 'task_generation_mismatch'})
        root = task_artifact_directory(task.id, task.execution_generation, create=False)
        source = root / action.payload['name']
        if source.is_symlink() or not source.is_file():
            return ActionOutcome('confirmed_no_effect', {'reason': 'reviewed_file_unavailable'})
        # Dispatch a private snapshot. Hash that snapshot so later Consumer
        # edits cannot change the bytes actually handed to the provider.
        with TemporaryDirectory(prefix='.file-delivery-', dir=root.parent) as directory:
            staged = Path(directory) / source.name
            shutil.copyfile(source, staged)
            staged.chmod(0o600)
            size = staged.stat().st_size
            with staged.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if not size or digest != action.payload['sha256']:
                return ActionOutcome('confirmed_no_effect', {'reason': 'reviewed_file_digest_mismatch'})
            receipt = self.dws.send_file(
                staged, **action.target,
                idempotency_uuid=str(uuid5(NAMESPACE_URL, action_key)),
            )
        evidence = {'file': {**action.payload, 'size_bytes': size},
                    'target': action.target, 'provider_result': receipt}
        attempt = candidate['action_attempt']
        # Persist the native send task before querying delivery. Query failure
        # or restart resumes readback against this receipt, never upload again.
        self.store.record_candidate_action_outcome(
            attempt['execution_id'], candidate['execution_owner'], attempt['action_index'],
            'uncertain', evidence,
        )
        return self._verified(action, evidence)

    def reconcile(self, action: ProposedAction, *, action_key: str, candidate: dict) -> ActionOutcome:
        evidence = json.loads(candidate['action_attempt'].get('result_json') or '{}')
        if (evidence.get('target') != action.target
                or evidence.get('file', {}).get('name') != action.payload['name']
                or evidence.get('file', {}).get('sha256') != action.payload['sha256']
                or not isinstance(evidence.get('provider_result'), dict)):
            return ActionOutcome('uncertain', {**evidence, 'reason': 'file_send_readback_unavailable'})
        return self._verified(action, evidence)
