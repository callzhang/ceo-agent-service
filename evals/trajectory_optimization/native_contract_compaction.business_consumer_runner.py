"""Actual Consumer full/compact control over anonymous, task-bound readers."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.agent_context import MaterialReference
from app.agent_runtime_config import load_runtime_config
from app.agent_wire_contracts import ConsumerAgentWireResult
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.config import load_env_file
from app.consumer_agent import consumer_developer_sections, default_task_skill_catalog
from app.native_agent_output import native_developer_assembly, native_output_schema_path, native_task_prompt, parse_native_output
from app.prompt_composition import compose_consumer_task_assembly, load_prompt_configuration, render_prompt_sections
from app.wechat.codex_safety import ControlledCliConfig, make_role_agent_command

BASE = Path(__file__).resolve().parent


def task_context(case, work):
    spec = importlib.util.spec_from_file_location('business_inputs', BASE / 'native_contract_compaction.business_inputs.py')
    inputs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inputs)
    context = inputs.task_context(case)
    if case['domain'] == 'mail':
        # An explicit source locator, not PATH interception or a provider adapter.
        executable = work / 'dws'
        executable.write_text('#!/bin/sh\nexec ' + shlex.join([
            sys.executable, str(BASE / 'native_contract_compaction.business_mail_fixture.py'),
            str(work),
        ]) + ' "$@"\n')
        executable.chmod(0o755)
        mail = case['phases']['cold']['mail']
        command = shlex.join([
            str(executable), 'mail', '+thread', '--email', mail['emailAccounts'][0]['email'],
            '--id', mail['conversation']['id'], '--format', 'json',
        ])
        context = replace(context, materials=(MaterialReference(
            kind='email', reference=mail['conversation']['id'],
            source_message_id=context.trigger_message_id, read_commands=(command,),
        ),))
    return context


def control_stem(work, variant, phase):
    return work / (variant if phase == 'cold' else variant + '-resume')

def mail_cli_environment(work, env):
    return dict(env, PATH=str(work) + os.pathsep + env["PATH"])


def main():
    root = Path(sys.argv[1]).resolve()
    manifest_path = Path(sys.argv[2]).resolve()
    manifest = json.loads(manifest_path.read_text())
    case = next(c for c in manifest['cases'] if c['id'] == sys.argv[3])
    assert case['role'] == 'consumer'
    phase = sys.argv[4] if len(sys.argv) > 4 else 'cold'
    assert phase in case['phases']
    work = root / case['id']
    cold_records = json.loads((work / 'consumer-results.json').read_text()) if phase == 'resume' else []
    assert (work / 'fixture.sqlite3').is_file()
    load_env_file(Path('/Users/derek/Services/ceo-agent-service/.env'))
    config = load_runtime_config(os.environ)
    route = next(r for r in config.routes if r.name == manifest['configured_route'])
    assert route.model == manifest['model']
    context = task_context(case, work)
    configuration = load_prompt_configuration(create_missing=False, role='consumer')
    sections = consumer_developer_sections(runtime_context='', prompt_configuration=configuration)
    full = render_prompt_sections(sections, placement='developer')
    compact = native_developer_assembly(full, sections, 'consumer').text
    task = compose_consumer_task_assembly(
        configuration, task_context=context.render(current_time=manifest['fixed_runtime_time']),
        scheduled_prompt=context.consumer_prompt, skill_names=context.skill_names,
        frozen_skill_names=context.skill_names, skill_protocol_is_custom=False,
        skill_catalog=default_task_skill_catalog(context.skill_names),
    ).text
    prompt = native_task_prompt(task, 'consumer')
    (work / 'submitted-task.txt').write_text(prompt)
    adapter = CodexRuntimeAdapter(work, config)
    records = []
    for variant, developer in (('full', full), ('compact', compact)):
        stem = control_stem(work, variant, phase)
        workspace = work / (variant + '-workspace')
        if phase == 'cold':
            workspace.mkdir()
        else:
            assert workspace.is_dir()
        cold = next((r for r in cold_records if r['variant'] == variant), None)
        if phase == 'resume':
            assert cold['exit_code'] == 0 and cold['turn_completed'] and cold['session_id']
        session_id = cold['session_id'] if cold is not None else None
        stem.with_suffix('.developer.txt').write_text(developer)
        with patch('app.codex_runtime_adapter.service_mcp_config_options', lambda: []):
            argv = adapter.build_command(
                route=route, prompt=prompt, session_id=session_id, image_paths=None,
                output_schema_path=native_output_schema_path('consumer'), use_output_schema=True,
                approval_policy='never', developer_instructions=developer,
                use_approval_bypass=False, sandbox_mode='workspace-write',
                skip_git_repo_check=False, reasoning_effort=manifest['reasoning_effort'],
            )
        make_role_agent_command(argv, role='consumer', task_workspace=str(workspace), controlled_cli=ControlledCliConfig(
            command=sys.executable,
            args=(str(BASE / 'native_contract_compaction.business_fixture.py'), '--root', str(work),
                  '--manifest', str(manifest_path), '--case', case['id'], '--phase', phase, '--log-label', variant if phase == 'cold' else variant + '.resume'),
            cwd=str(work),
        ))
        env = adapter.build_env(route)
        if case['domain'] == 'mail':
            env = mail_cli_environment(work, env)
            argv[-1:-1] = ['-c', 'shell_environment_policy.set.PATH=' + json.dumps(env['PATH'])]
        argv[-1:-1] = ['-c', 'mcp_servers.agent_cli.enabled=true', '--output-last-message', str(stem.with_suffix('.output.json'))]
        with stem.with_suffix('.trajectory.jsonl').open('w') as stdout, stem.with_suffix('.stderr.txt').open('w') as stderr:
            completed = subprocess.run(argv, input=prompt, text=True, stdout=stdout, stderr=stderr,
                                       env=env, cwd=workspace, timeout=600)
        raw = stem.with_suffix('.trajectory.jsonl').read_text()
        wire = json.loads(parse_native_output(raw, 'consumer'))
        result = ConsumerAgentWireResult.model_validate(wire).to_result()
        events = [json.loads(line) for line in raw.splitlines() if line.startswith('{')]
        record = {
            'case': case['id'], 'variant': variant, 'phase': phase, 'model': route.model,
            'route': route.name, 'exit_code': completed.returncode,
            'session_id': next((e.get('thread_id') for e in events if e.get('type') == 'thread.started'), None),
            'turn_completed': any(e.get('type') == 'turn.completed' for e in events),
            'wire': wire, 'canonical': result.model_dump(mode='json'),
            'task_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
            'developer_sha256': hashlib.sha256(developer.encode()).hexdigest(),
            'developer_characters': len(developer),
            'native_schema_sha256': hashlib.sha256(native_output_schema_path('consumer').read_bytes()).hexdigest(),
        }
        records.append(record)
        (work / ('consumer-results.json' if phase == 'cold' else 'consumer-resume-results.json')).write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(json.dumps({k: record[k] for k in ('case', 'variant', 'exit_code', 'session_id')}), flush=True)


if __name__ == '__main__':
    main()
