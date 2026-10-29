# D1c legacy Todo monitor retirement — 2026-10-20

Derek approved keeping 540 recorded-active legacy DingTalk Todo links only as source-qualified history because no current Task identity can be verified. D1c retires their operational monitoring after the separately committed D1b provider writer and dispatcher retirement (`f6df7cf34`). This candidate does not promote a legacy Todo to Task, cancel or complete an external Todo, replay a provider action, change old status values, alter physical schemas, or introduce an Audit or safety policy.

Scope is exact: `app/quality_gate.py` removes `work_todo_dingtalk_links` from required sources, `task_todo_sync_outbox` from optional sources, both physical-source entries, both external delivery checks and the link-only deduplication branch. The remaining WeChat, Memory and meeting Memory query/count/severity logic and scan order remain. `app/audit_web.py` removes the `DingTalk Todos` queue spec, its physical mapping, and the now-unused link-specific retryable helper. Other generic queue rows retain `retryable=0`; dedicated queue readers retain their own calculations. Existing History, legacy-project and sent-Todo readers continue to read the original typed facts and receipts. Email physical-table behavior is outside this batch.

Fail-first command, run before source edits:

```sh
/Users/derek/miniforge3/bin/python -m pytest -q \
  tests/test_quality_gate.py::test_quality_gate_preserves_historical_todo_links_without_monitoring_them \
  tests/test_quality_gate.py::test_quality_gate_keeps_historical_todo_outbox_out_of_coverage \
  tests/test_quality_gate.py::test_quality_gate_keeps_failed_historical_outbox_as_facts_only \
  tests/test_quality_gate.py::test_quality_gate_preserves_historical_retry_status_without_attention \
  tests/test_audit_web.py::test_status_keeps_historical_todo_link_out_of_queue_summary
```

RED: 4 failed with old sources in `checked_sources` or the old Status queue; 1 passed. GREEN: 5 passed. Raw logs in the task-owned temporary directory were SHA-256 `92234ab07eef15f6b2613d147e08d1c070a6e7cd88f033ce3c34c9f7de58150a` (RED) and `aa3f3d68cff493b05f4a7df2198de91dc96854cf22a17e7eccebf0157a8e03f1` (GREEN). Their expected failures establish that the tests caught the retired behavior before implementation. Historical link/outbox readers still return the original statuses and error.

Expanded focused run covered the five D1c cases plus current failed Work/Meeting/Email queues, rejected WeChat delivery, required-source failure, feedback and OKR projections, coverage state, and current BusinessTask dispatcher presence: **12 passed, 1 unrelated baseline failure**. The failure is `test_attention_excludes_an_email_action_still_inside_its_retry_window`: its unchanged Email fixture calls `EmailStore._update_direct_action`, absent from `HEAD:app/email_store.py` (test fixture line 12063; unchanged file Git blob `8a4398caccba18273eb2559322c5b838e058728c` equals HEAD). D1c does not edit Email. Raw log SHA-256: `9ff6c69a3f6755c7fc05077a80309d09c25c28c2e56e5586e0db46064d484eb4`. A separate 12-case selection excluding only that unchanged Email fixture passed; its raw log SHA-256 is `23da01f95077a10bb24c1edfc4cf88d0b18e3ae478f0c0b3277361fc0592a001`. Ruff on the two app and two test files and `git diff --check` passed.

For source boundary reproduction from base `f6df7cf34`, compare AST bodies of every other top-level function in the two app files:

```python
import ast
import subprocess
from pathlib import Path

excluded = {
    'app/quality_gate.py': {'scan_hourly_quality', '_check_external_delivery_queues'},
    'app/audit_web.py': {'_queue_status_snapshots', '_queue_retryable_count'},
}
for path, changed in excluded.items():
    before = subprocess.check_output(['git', 'show', f'HEAD:{path}'], text=True)
    after = Path(path).read_text()
    def bodies(source):
        return {node.name: ast.dump(node, include_attributes=False)
                for node in ast.parse(source).body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name not in changed}
    assert bodies(before) == bodies(after), path
```

Root still owns independent SPEC then QUALITY review, final combined-copy nonlegacy statistic comparison and all typed-fact preservation, root-copy rollback gate, stage/commit/push/deploy, and post-deploy readback. This local source/test result is not a live service result or full unit D completion.
