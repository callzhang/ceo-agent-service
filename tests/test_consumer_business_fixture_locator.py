"""Ensure the native mail source locator executes anonymous original reads."""
import importlib.util
import json
import shlex
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / 'evals/trajectory_optimization'


def test_mail_locator_executes_fixture_without_path_interception(tmp_path):
    spec = importlib.util.spec_from_file_location('consumer_control', BASE / 'native_contract_compaction.business_consumer_runner.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = json.loads((BASE / 'native_contract_compaction.business_cases.v7.json').read_text())
    case = next(c for c in manifest['cases'] if c['domain'] == 'mail')
    mail = case['phases']['cold']['mail']
    (tmp_path / 'mail.json').write_text(json.dumps(mail))
    context = module.task_context(case, tmp_path)
    assert context.skill_names == ('ceo-mail-review',)
    assert context.trigger_raw_payload == case['trigger']['raw_payload']
    command = shlex.split(context.materials[0].read_commands[0])
    assert Path(command[0]) == tmp_path / 'dws'
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(completed.stdout)['data']['value'] == mail['conversation']
    assert len((tmp_path / 'mail.calls.jsonl').read_text().splitlines()) == 1
    failed = subprocess.run([command[0], 'mail', 'message', 'send'], capture_output=True, text=True)
    assert failed.returncode != 0
    assert len((tmp_path / 'mail.calls.jsonl').read_text().splitlines()) == 1


def test_mail_fixture_is_selected_by_native_login_shell(tmp_path):
    spec = importlib.util.spec_from_file_location('consumer_control', BASE / 'native_contract_compaction.business_consumer_runner.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = json.loads((BASE / 'native_contract_compaction.business_cases.v7.json').read_text())
    case = next(c for c in manifest['cases'] if c['domain'] == 'mail')
    (tmp_path / 'mail.json').write_text(json.dumps(case['phases']['cold']['mail']))
    module.task_context(case, tmp_path)
    env = module.mail_cli_environment(tmp_path, {'PATH': '/usr/bin:/bin:/opt/homebrew/bin'})
    completed = subprocess.run(['/bin/zsh', '-lc', 'command -v dws'], env=env, capture_output=True, text=True, check=True)
    assert Path(completed.stdout.strip()) == tmp_path / 'dws'


def test_resume_output_files_do_not_replace_cold_outputs(tmp_path):
    spec = importlib.util.spec_from_file_location('consumer_control', BASE / 'native_contract_compaction.business_consumer_runner.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cold = module.control_stem(tmp_path, 'full', 'cold')
    resume = module.control_stem(tmp_path, 'full', 'resume')
    for suffix in ('.trajectory.jsonl', '.output.json', '.developer.txt', '.stderr.txt'):
        assert cold.with_suffix(suffix) != resume.with_suffix(suffix)
