"""Native paired evaluation of meeting ordinary/private delivery decisions.

Only synthetic inline evidence is sent to the model. Tools and MCP are disabled.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BUILD = '''
import json,sys
from pathlib import Path
from app.meeting_alignment_agent import MeetingAlignmentAgent,build_meeting_alignment_prompt
from app.meeting_alignment_models import MeetingSource,MeetingAlignmentDecision,MeetingAlignmentAuditResult
x=json.load(sys.stdin)
s=MeetingSource.model_validate(x['source'])
skill=Path('ci/shared-skills/ceo-meeting-work/SKILL.md').read_text()
class Capture:
 def audit(self,*,prompt,run_id):return prompt
class Candidate:
 def decide(self,**kwargs):return MeetingAlignmentDecision.model_validate(x['result'])
if x['role']=='planner':
 print(build_meeting_alignment_prompt(s,work_profile='',work_profile_source='synthetic',group_candidates=x['groups'],skill_protocol=skill))
elif x['role']=='audit':
 print(MeetingAlignmentAgent(Capture()).audit(s,MeetingAlignmentDecision.model_validate(x['candidate']),run_id=1,group_candidates=x['groups'],skill_protocol=skill))
elif x['role']=='validate_planner':
 print(MeetingAlignmentAgent(Candidate()).decide(s).model_dump_json())
else:
 print(MeetingAlignmentAuditResult.model_validate(x['result']).model_dump_json())
'''


def planner_matches(result, expected):
    private = result.get('sensitive_private_message') or {}
    body = result.get('final_message', '')
    private_body = private.get('message', '')
    return (
        result.get('action') == 'send'
        and ((result.get('target') or {}).get('conversation_id') == expected['group'])
        and (private.get('target') or {}).get('direct_user_id') == expected['private_user']
        and all(token in body for token in expected['ordinary_tokens'])
        and all(token in private_body for token in expected['sensitive_tokens'])
        and all(token not in body + result.get('message_title', '') for token in expected['sensitive_tokens'])
        and (bool(private.get('recipient_evidence')) if expected['private_user'] else not private)
    )


def main():
    from scripts.eval_meeting_group_scope import run_native
    from scripts.eval_meeting_audience import run_local
    from scripts.eval_consumer_audit_business import archive_ref, resolve_commit_ref

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-ref', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = (ROOT/'evals/meeting_sensitive_split/v1.json').read_bytes()
    manifest = json.loads(raw)
    report = dict(manifest_sha256=sha256(raw).hexdigest(), harness_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), settings=manifest['settings'], external_effects='none; synthetic facts; tools and MCP disabled')
    for label, ref in [('baseline', manifest['baseline_ref']), ('candidate', args.candidate_ref)]:
        ref = resolve_commit_ref(ref)
        rows = []
        with tempfile.TemporaryDirectory(prefix='meeting-sensitive-eval-') as temporary:
            root = Path(temporary)
            archive_ref(ref, root)
            for case in manifest['cases']:
                row = dict(id=case['id'], expected=case['expected'])
                for role in ('planner', 'audit'):
                    payload = dict(role=role, source=case['source'], groups=manifest['groups'], candidate=case['candidate'])
                    built = run_local(root, BUILD, payload)
                    if built.returncode:
                        raise RuntimeError(built.stderr[-1500:])
                    prompt = 'Synthetic read-only evaluation. All inline facts and identities are verified. No tools or external actions are available. Return only your role JSON.\n' + built.stdout
                    native = run_native(root, role, prompt, manifest['settings'])
                    result = native.get('result') or {}
                    validated = run_local(root, BUILD, {**payload, 'role': 'validate_'+role, 'result': result}) if native['ok'] else None
                    contract_ok = validated is not None and validated.returncode == 0
                    passed = contract_ok and (planner_matches(result, case['expected']) if role == 'planner' else result.get('outcome') == case['expected']['audit'])
                    row[role] = dict(native=native, contract_ok=contract_ok, passed=passed, prompt_sha256=sha256(prompt.encode()).hexdigest())
                    print(f"{label} {case['id']} {role}: {passed}", flush=True)
                rows.append(row)
        report[label] = dict(ref=ref, cases=rows, passed=sum(int(row[role]['passed']) for row in rows for role in ('planner', 'audit')), total=2*len(rows))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return 0 if report['candidate']['passed'] == report['candidate']['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
