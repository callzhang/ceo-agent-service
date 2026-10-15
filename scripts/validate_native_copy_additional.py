"""Opt-in native scheduled, TaskAgent and Workbench acceptance on an owned copy."""
from __future__ import annotations

import argparse
from dataclasses import fields
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validate_native_copy_lifecycle import (  # noqa: E402
    InMemoryProvider, NativeExecutor, _backup_database, _file_sha256,
    _publish_email_pair, _reserve_fixture_task_ids, original_rows_unchanged,
)


def prepare(root):
    root = root.resolve()
    assert root.parent == Path('/private/tmp') and root.name.startswith('ceo-native-copy.')
    metadata = json.loads((root / 'source-meta.json').read_text())
    source = root / 'source.sqlite3'
    assert _file_sha256(source) == metadata['sha256']
    db = root / 'candidate.sqlite3'
    assert not db.exists()
    shutil.copyfile(source, db)
    published_pair = _publish_email_pair(ROOT, db)
    target_baseline = root / 'pre-native-target.sqlite3'
    _backup_database(db, target_baseline)
    target_baseline_sha256 = _file_sha256(target_baseline)
    task_id_reservation = _reserve_fixture_task_ids(db)
    assert _file_sha256(source) == metadata['sha256']
    workspace = root / 'workspace'
    workspace.mkdir()
    manifest = root / 'service-mcp.json'
    manifest.write_text('{"servers":{}}')
    os.environ.update(CEO_ENV_FILE=str(root / 'missing.env'), CEO_WORKSPACE=str(workspace),
        CEO_WORKER_DB=str(db), CEO_SERVICE_MCP_CONFIG_PATH=str(manifest),
        CEO_AGENT_RUNTIME_ROUTES='codex_oauth', CEO_CODEX_MODEL='gpt-5.6-luna',
        CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL='', CEO_CORPUS_DIR=str(root / 'no-corpus'),
        PYTHONDONTWRITEBYTECODE='1')
    from app.store import AutoReplyStore
    from app.agent_runtime_config import load_runtime_config
    preparation = {'published_pair': published_pair,
                   'target_baseline_sha256': target_baseline_sha256,
                   'task_id_reservation': task_id_reservation}
    return AutoReplyStore(db), workspace, load_runtime_config(os.environ), metadata, preparation


class LocalTextExecutor:
    """Keep native model/session execution, with no external tools in this test."""
    def __init__(self, workspace):
        self.workspace = workspace
        self.calls = []

    def __call__(self, command, **kwargs):
        from app.process_runner import run_process_with_idle_timeout
        from app.wechat.codex_safety import inherited_native_server_names
        args = list(command)
        assert not any('dangerously-bypass' in x for x in args)
        names = set(inherited_native_server_names())
        for value in args:
            if value.startswith('mcp_servers.'):
                names.add(value.split('.', 2)[1])
        options = []
        for name in sorted(names):
            options.extend(['-c', f'mcp_servers.{name}.enabled=false'])
        options.extend(['-c', 'features.shell_tool=false', '-c', 'features.unified_exec=false',
                        '-c', 'project_doc_max_bytes=0', '-c', 'sandbox_mode="read-only"'])
        index = len(args) - 2 if 'resume' in args else len(args) - 1
        if '--skip-git-repo-check' not in args:
            options.insert(0, '--skip-git-repo-check')
        args[index:index] = options
        print('native_start domain', flush=True)
        result = run_process_with_idle_timeout(args, **kwargs)
        self.calls.append({'returncode': result.returncode, 'timed_out': result.timed_out,
                           'prompt_sha256': hashlib.sha256(kwargs['prompt'].encode()).hexdigest()})
        print('native_end domain', json.dumps(self.calls[-1]), flush=True)
        return result


def routed(store, workspace, config, executor):
    from app.agent_runtime_router import AgentRuntimeRouter, RoutedCodexExecution
    from app.codex_runtime_adapter import CodexRuntimeAdapter
    # Existing native OAuth bootstrap path; actual CLI/model/auth, not a fake probe.
    return RoutedCodexExecution(store=store, config=config,
        router=AgentRuntimeRouter(routes=config.routes, store=store, snapshots={}),
        adapter=CodexRuntimeAdapter(workspace, config), executor=executor,
        total_timeout_seconds=300, idle_timeout_seconds=120, allow_legacy_oauth_bootstrap=True)


def scheduled(store, workspace, config):
    from app.agent_cli import _task_file_root
    from app.agent_cron.consumer import ScheduledTaskTriggerConsumer, ScheduledAgentConsumer
    from app.agent_orchestrator import AgentOrchestrator
    from app.audit_agent import AuditAgentRunner
    from app.consumer_agent import ConsumerAgentRunner
    from app.dispatcher.adapters import ScheduledTaskQueueAdapter, ScheduledExecutionQueueAdapter
    from app.dispatcher.models import ClaimGuard
    from app.system_action_handlers import native_dingtalk_message_handlers
    from app.system_executor import SystemExecutor
    clock = datetime(1990, 1, 1, tzinfo=UTC)
    target = 'native-copy-acceptance:scheduled:' + uuid4().hex
    prompt = ('这是数据库副本的测试任务。用 read_task_artifact(name="case-input.json") 读取材料，'
              '统计记录数量与 units 总和，读取 dingtalk-chat/send_group_message 契约。'
              f'材料中的 send_target.conversation_id 是本任务已提供的精确测试会话 ID；请直接作为 action.target.conversation_id 使用，值为 {target}。不要把 scheduled-task-run 标识用作发送会话，不需要按名称搜索。正文包含 verification_tag、条目数和总和。'
              '这是当前任务明确指定的测试目标。请生成完整发送方案，授权审核通过后由本验收的 SystemExecutor 通过内存替身执行。Agent 本轮不能直接投递；禁止读取真实消息。本验收的 provider 不具有外部投递能力。')
    route = config.routes[0]
    options = SimpleNamespace(resolve_runtime_route=lambda *a, **kw: route)
    scheduled_task = store.create_scheduled_task(name=target, prompt=prompt,
        cron_expression='0 * * * *', timezone_name='UTC', runtime_id=route.name,
        runtime_options={'model': route.model, 'reasoning_effort': 'medium'},
        working_directory=str(workspace), enabled=True, skill_refs=(), now=clock)
    run = store.create_scheduled_task_run(scheduled_task.id, trigger_kind='manual', scheduled_for=clock, now=clock)
    def claim(adapter, expected, owner):
        envelope = adapter.claim(clock, owner=owner, owner_pid=os.getpid(), lease=timedelta(minutes=10))
        assert envelope and envelope.source_id == str(expected)
        return envelope, ClaimGuard(adapter=adapter, envelope=envelope, owner=owner)
    adapter = ScheduledTaskQueueAdapter(store, owner_alive=lambda pid: True)
    envelope, guard = claim(adapter, run.id, 'native-copy-trigger')
    ScheduledTaskTriggerConsumer(store=store, option_service=options, commands={}, now=lambda: clock)(envelope, guard)
    run = store.get_scheduled_task_run(run.id)
    assert run.dispatch_status == 'dispatched'
    task = store.get_reply_task(int(run.execution_id))
    material = _task_file_root(store.path, task.id, task.execution_generation, create=True) / 'case-input.json'
    fixture = {'verification_tag': 'SCHEDULED_' + uuid4().hex[:12], 'send_target': {'conversation_id':target, 'source':'exact test destination supplied by scheduled task'}, 'records': [{'units':23},{'units':27},{'units':50}]}
    material.write_text(json.dumps(fixture))
    native = NativeExecutor(workspace, store.path)
    provider = InMemoryProvider(store, target)
    drivers = []
    def factory(built):
        driver = AgentOrchestrator(store=store,
            consumer=ConsumerAgentRunner(store=store, workspace=workspace, runtime_config=config,
                executor=native, skill_protocol_override=built.skill_protocol),
            audit=AuditAgentRunner(store=store, workspace=workspace, runtime_config=config,
                executor=native, skill_protocol_override=built.skill_protocol),
            system_executor=SystemExecutor(store, native_dingtalk_message_handlers(store, provider)))
        drivers.append(driver)
        return driver
    adapter = ScheduledExecutionQueueAdapter(store, owner_alive=lambda pid: True)
    envelope, guard = claim(adapter, task.id, 'native-copy-scheduled')
    ScheduledAgentConsumer(store=store, option_service=options, orchestrator_factory=factory, now=lambda: clock)(envelope, guard)
    guard.complete(clock)
    current = store.get_reply_task(task.id)
    assert current.status == 'done' and current.input_compacted
    assert len(provider.calls) == 1 and fixture['verification_tag'] in provider.calls[0]['text']
    assert '100' in provider.calls[0]['text'] and '3' in provider.calls[0]['text']
    assert store.get_scheduled_task_run(run.id).execution_id == str(task.id)
    assert drivers[0].persisted_terminal_result(current).status == 'executed'
    return {'case':'scheduled','task_id':task.id,'scheduled_id':scheduled_task.id,'source_run_id':run.id,
            'native_invocations':native.calls,'provider_calls':len(provider.calls),'status':current.status}


def work_summary(store, workspace, config):
    from app.task_agent import TaskAgentRunner, TaskAgentCodexRunner, process_work_item
    from app.task_models import WorkItem
    from app.task_retrieval import TaskSemanticContext
    key = 'native-copy-acceptance:work:' + uuid4().hex
    summary = f'建议保留一个候选事项“{key}”：补齐验收来源链接。责任人、截止时间和项目尚未确定。这是测试资料，不要创建正式指派、不要对外通知。'
    item = WorkItem.model_validate({'source':{'type':'local_file','ref':key,'title':key,'created_at':'2026-10-12T00:00:00Z'},
        'summary':summary,'context':{'source_conversation_kind':'file'}})
    input_id = store.enqueue_work_summary_input(item.source.type.value,key,item.model_dump_json())
    claimed = store.claim_work_summary_inputs(limit=1)
    assert len(claimed)==1 and claimed[0].id==input_id
    native = LocalTextExecutor(workspace)
    runner = TaskAgentRunner(TaskAgentCodexRunner(routed_execution=routed(store,workspace,config,native)))
    empty = TaskSemanticContext(**{f.name:() for f in fields(TaskSemanticContext)})
    with patch('app.task_agent.retrieve_task_semantic_context',return_value=empty), \
         patch('app.task_agent.TASK_AGENT_SESSION_SCOPE_ID',key), \
         patch('app.task_agent.memory_connector_config_issue',return_value='No external connector in copy acceptance'):
        process_work_item(store,runner,claimed[0])
    current=store.get_work_summary_input(input_id)
    assert current.status.value=='done', current
    assert store.enqueue_work_summary_input(item.source.type.value,key,item.model_dump_json())==input_id
    with store._connect() as db:
        projection_row=db.execute("select session_id,projection_json from runs where record_type='task_agent_runs' and summary_input_id=?",(input_id,)).fetchone()
        projection=json.loads(projection_row[1])
        assert len(projection['task_decisions'])==1
        applied=projection['task_decisions'][0]
        business=db.execute('select id,title from business_tasks where id=?',(applied['task_id'],)).fetchall()
        assert db.execute('select source_ref from business_task_signals where id=?',(applied['signal_id'],)).fetchone()[0]==key
        state=db.execute("select body_compacted,body_sha256,body_bytes,payload_json from tasks where record_type='work_summary_inputs' and legacy_key=?",(str(input_id),)).fetchone()
        assert len(business)==1 and state[0]==1 and state[1] and state[2]>0 and state[3]=='{}'
    assert runner.codex.last_session_id and runner.codex.last_transcript_end_line>runner.codex.last_transcript_start_line
    return {'case':'work_summary','input_id':input_id,'source_ref':key,'business_id':business[0][0],
            'native_invocations':native.calls,'provider_calls':0,'status':current.status.value,
            'session_id':runner.codex.last_session_id,'start':runner.codex.last_transcript_start_line,'end':runner.codex.last_transcript_end_line}


def workbench(store, workspace, config):
    from app.workbench.store import WorkbenchStore
    from app.workbench.executor import WorkbenchExecutor
    from app.workbench.service_runtime import ServiceWorkbenchRuntime
    from app.workbench.runtime import RuntimeRegistry
    from app.workbench.models import TurnStatus
    bench=WorkbenchStore(store.path)
    with store._connect() as db:
        assert db.execute("select count(*) from tasks where record_type='workbench_turns' and status in ('queued','running','waiting_confirmation')").fetchone()[0]==0
    key='native-copy-acceptance:workbench:'+uuid4().hex
    task=bench.create_task(title=key,runtime_kind='codex')
    text=f'这是本地副本的纯文本验收。只计算23+27+50，回答总和并原样包含校验标记 {key}；不需要任何工具或外部操作。'
    turn=bench.create_turn(task.id,user_text=text,client_request_id=key)
    assert bench.create_turn(task.id,user_text=text,client_request_id=key).id==turn.id
    native=LocalTextExecutor(workspace)
    runtime=ServiceWorkbenchRuntime(workspace=workspace,store=store,
        routed_execution_factory=lambda **kwargs:routed(store,workspace,config,native))
    executor=WorkbenchExecutor(bench,RuntimeRegistry([runtime]),workspace=workspace)
    try:
        assert executor.run_once(max_turns=1)==[turn.id]
    finally:
        executor.close()
    current=bench.get_turn(turn.id)
    assert current.status is TurnStatus.COMPLETED, current
    assert key in current.final_text and '100' in current.final_text
    events=bench.events_after(turn.id)
    assert any(x.event_type=='turn_completed' for x in events)
    reopened=WorkbenchStore(store.path)
    assert reopened.get_turn(turn.id).final_text==current.final_text
    assert reopened.get_task(task.id).provider_session_ref
    return {'case':'workbench','task_id':task.id,'turn_id':turn.id,'native_invocations':native.calls,
            'provider_calls':0,'status':current.status.value,'event_count':len(events),
            'session_id':reopened.get_task(task.id).provider_session_ref}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owned-temp-root',type=Path,required=True)
    parser.add_argument('--case',choices=('scheduled','work_summary','workbench'),required=True)
    args=parser.parse_args()
    store,workspace,config,metadata,preparation=prepare(args.owned_temp_root)
    report=globals()[args.case](store,workspace,config)
    target_baseline = args.owned_temp_root/'pre-native-target.sqlite3'
    report['original_tables_unchanged']=original_rows_unchanged(target_baseline,store.path)
    with store._connect() as db:
        assert db.execute('pragma quick_check').fetchone()[0]=='ok'
    assert _file_sha256(target_baseline)==preparation['target_baseline_sha256']
    assert _file_sha256(args.owned_temp_root/'source.sqlite3')==metadata['sha256']
    report.update(source=metadata,**preparation,passed=True)
    (args.owned_temp_root/'native-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('acceptance_pass',json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
