"""Paired native planner/Audit evaluation of ordinary meeting group ownership.

Synthetic identities, complete inline evidence, no tools or external actions.
Both refs receive the same source, groups and frozen Audit candidate.
"""
import argparse
from hashlib import sha256
import json
import subprocess
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BUILD = '''
import json,sys
from app.meeting_alignment_agent import MeetingAlignmentAgent,build_meeting_alignment_prompt
from app.meeting_alignment_models import MeetingSource,MeetingAlignmentDecision,MeetingAlignmentAuditResult
x=json.load(sys.stdin)
class Capture:
 def audit(self,*,prompt,run_id):return prompt
if x['role']=='planner':
 s=MeetingSource.model_validate(x['source'])
 print(build_meeting_alignment_prompt(s,work_profile='',work_profile_source='synthetic',group_candidates=x['groups']))
elif x['role']=='audit':
 s=MeetingSource.model_validate(x['source'])
 print(MeetingAlignmentAgent(Capture()).audit(s,MeetingAlignmentDecision.model_validate(x['candidate']),run_id=1,group_candidates=x['groups']))
else:
 model=MeetingAlignmentAuditResult if x['role']=='validate_audit' else MeetingAlignmentDecision
 print(model.model_validate(x['result']).model_dump_json())
'''



def run_native(root, role, prompt, settings):
    schema_name = 'decision' if role == 'planner' else 'audit'
    schema = json.loads((root/f'app/schemas/meeting_alignment_{schema_name}.schema.json').read_text())
    schema.pop('$schema', None)
    command = ['claude', '-p', '--setting-sources', '', '--settings', '{}',
               '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
               '--input-format', 'text', '--output-format', 'stream-json', '--verbose',
               '--model', settings['model'], '--effort', settings['reasoning_effort'],
               '--tools', '', '--max-turns', '2', '--json-schema', json.dumps(schema)]
    completed = subprocess.run(command, input=prompt, text=True, capture_output=True,
                               cwd=root, timeout=settings['timeout_seconds_per_call'], check=False)
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    terminal = next((e for e in reversed(events) if e.get('type') == 'result'), {})
    tools = [b.get('name') for e in events for b in e.get('message', {}).get('content', [])
             if isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') != 'StructuredOutput']
    result = terminal.get('structured_output')
    return dict(ok=completed.returncode == 0 and isinstance(result, dict) and not tools,
                exit_code=completed.returncode, result=result, tool_item_types=tools,
                session_id=terminal.get('session_id'), error=terminal.get('subtype', 'no_result'))


def main():
    from scripts.eval_consumer_audit_business import archive_ref, resolve_commit_ref
    from scripts.eval_meeting_audience import run_local

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-ref', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=ROOT/'evals/meeting_group_scope/v1.json')
    args = parser.parse_args()
    raw = args.manifest.read_bytes()
    manifest = json.loads(raw)
    settings = manifest['settings']
    report = dict(manifest_sha256=sha256(raw).hexdigest(),harness_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),settings=settings,external_effects='none; synthetic facts; tools disabled')
    for label, ref in [('baseline',manifest['baseline_ref']),('candidate',args.candidate_ref)]:
        ref = resolve_commit_ref(ref)
        rows = []
        with tempfile.TemporaryDirectory(prefix='meeting-group-scope-') as tmp:
            root = Path(tmp)
            archive_ref(ref,root)
            for case in manifest['cases']:
                source = dict(meeting_id=case['id'],title=case['title'],status='ended',started_at='2026-11-01T10:00:00Z',ended_at='2026-11-01T11:00:00Z',participants=manifest['participants'],attendee_evidence=case['attendee_evidence'],attendee_roster_complete=case['attendee_roster_complete'],current_user_id='principal',summary=case['summary'],transcript=[dict(speaker_name='Morgan',speaker_user_id='manager',text=case['summary'])])
                selected = next(g for g in case['groups'] if g['conversation_id']==case['candidate_group'])
                target = dict(kind='group',conversation_id=selected['conversation_id'],direct_user_id='',title=selected['title'],candidates=[dict(conversation_id=selected['conversation_id'],title=selected['title'],evidence=['The complete roster is readable and recent discussion covers the related work line.'])])
                candidate = dict(action='send',audience_scope='business',trigger_reasons=['meeting_summary'],topics=[],derek_viewpoint=None,key_questions=[],mention_names=[],target=target,message_title='Work review',final_message=case['summary'],sensitive_private_message=None,audit_summary=('Ordinary business content only. The calendar attendee roster is complete. The selected group discusses related department work.' if case['attendee_roster_complete'] else 'Ordinary business content only. Attendee coverage is incomplete, recorded as a gap. The selected group discusses related department work.'),risk='low',confidence=.85,rule_coverage=1,information_completeness=.85)
                payload = dict(source=source,groups=case['groups'],candidate=candidate)
                row = dict(id=case['id'],expected_audit=case['expected_audit'],expected_planner_group=case['expected_planner_group'])
                for role in ('planner','audit'):
                    built = run_local(root,BUILD,{**payload,'role':role})
                    if built.returncode:
                        raise RuntimeError(built.stderr[-2000:])
                    prompt = 'Synthetic read-only evaluation. All inline facts are verified; no tools or external effects are available. Return only your role JSON.\n'+built.stdout
                    native = run_native(root, role, prompt, settings)
                    result = native.get('result') or {}
                    valid = run_local(root,BUILD,dict(role='validate_'+role,result=result)) if native['ok'] else None
                    contract_ok = valid is not None and valid.returncode==0
                    passed = contract_ok and (result.get('outcome')==case['expected_audit'] if role=='audit' else (result.get('target') or {}).get('conversation_id')==case['expected_planner_group'])
                    row[role] = dict(native=native,prompt_sha256=sha256(prompt.encode()).hexdigest(),contract_ok=contract_ok,passed=passed,content_review='pending manual review of all conclusions and actionable Audit feedback')
                    print(label,case['id'],role,passed,flush=True)
                rows.append(row)
                report[label] = dict(ref=ref,cases=rows,passed=sum(r[role]['passed'] for r in rows for role in ('planner','audit')))
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return 0 if report['candidate']['passed']==2*len(manifest['cases']) else 1


if __name__=='__main__':
    raise SystemExit(main())
