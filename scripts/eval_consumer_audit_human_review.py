#!/usr/bin/env python3
"""Compare native Audit judgments on frozen, structurally valid human questions."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from scripts import eval_consumer_audit_business as business

ROOT = business.ROOT
MANIFEST = ROOT / 'evals/consumer_audit_human_review/v1.json'


def run_suite(root: Path, manifest: dict) -> dict:
    instructions = business.role_instructions(root)
    settings = manifest['settings']
    command = business.native_command(
        model=settings['model'], effort=settings['reasoning_effort'],
        developer_instructions=instructions['audit'], workdir=root,
    )
    cases = []
    for case in manifest['cases']:
        subject = business.normalize_review_subject(root, case['consumer_result'])
        if not subject['ok']:
            raise ValueError(f"invalid frozen subject: {case['id']}")
        audit = business.run_role(
            command=command,
            prompt=business._audit_prompt(case, subject['result'], subject['digest'],
                                         instructions['wire_schemas']['audit']),
            timeout=settings['timeout_seconds_per_case'],
        )
        contract = business.normalize_role_result(root, 'audit', audit.get('result'))
        result = contract.get('result') or {}
        bound = (result.get('candidate_digest') == subject['digest']
                 and result.get('proposal_revision') == 0)
        passed = (audit['ok'] and contract['ok'] and bound
                  and result.get('outcome') in case['expected_audit'])
        cases.append({'id': case['id'], 'subject': subject, 'audit': audit,
                      'audit_contract': contract, 'binding_correct': bound,
                      'outcome_passed': passed})
    return {'identity': business.source_identity(root, archived=True),
            'audit_instructions_sha256': sha256(instructions['audit'].encode()).hexdigest(),
            'cases': cases, 'passed': sum(case['outcome_passed'] for case in cases)}


def compare(candidate_ref: str, manifest_path: Path = MANIFEST) -> dict:
    manifest = json.loads(manifest_path.read_text())
    candidate = business.resolve_commit_ref(candidate_ref)
    baseline = business.resolve_commit_ref(manifest['baseline_ref'])
    with ExitStack() as stack:
        trees = {}
        for side, ref in [('baseline', baseline), ('candidate', candidate)]:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(
                prefix=f'human-review-{side}-')))
            business.archive_ref(ref, root)
            trees[side] = run_suite(root, manifest)
    return {'mode': 'native_synthetic_audit_human_question_review',
            'consumer_results': 'fixed-stimuli-not-generated-consumer-output',
            'semantic_review': 'independent-exact-output-required',
            'manifest_sha256': sha256(manifest_path.read_bytes()).hexdigest(),
            'harness_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
            'shared_harness_sha256': sha256(Path(business.__file__).read_bytes()).hexdigest(),
            'baseline_ref': baseline, 'candidate_ref': candidate,
            'settings': manifest['settings'], **trees}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-ref', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=MANIFEST)
    args = parser.parse_args()
    args.output.write_text(json.dumps(compare(args.candidate_ref, args.manifest), ensure_ascii=False,
                                      indent=2) + '\n')


if __name__ == '__main__':
    main()
