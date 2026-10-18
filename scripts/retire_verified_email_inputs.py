"""Explicitly publish proven input references from an audited final-schema copy.

The source must have completed original-source verification. Only reference
metadata crosses databases; target business facts never come from the copy.
This tool performs no original-source reads and creates no original archive.
"""
from contextlib import closing
import argparse
import json
from pathlib import Path
import sqlite3

from app.action_email_schema_release import (
    TARGET_EMAIL_VERSION, TARGET_STORE_VERSION, require_current_email_pair,
)
from app.email_body_source import verify_original
from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_store import EmailStore


def _validate_reference(reference, *, model):
    kind = reference['kind']
    if kind == 'native':
        native = reference['native_provenance']
        if (native['decoded_utf8_sha256'] != reference['sha256']
                or native['decoded_utf8_bytes'] != reference['utf8_bytes']):
            raise ValueError('native input proof differs from original digest')
    elif kind == 'provider':
        representations = (CpuTfidfLogisticClassifier.FEATURE_VERSION,) if model else (
            'provider_body', 'scan_normalized_message')
        if reference['representation'] not in representations:
            raise ValueError('input proof uses the wrong representation')
        locator = reference['locator']
        if not locator['folder'] or locator['uidvalidity'] <= 0 or locator['uid'] <= 0:
            raise ValueError('input proof has no exact provider locator')
    else:
        raise ValueError('unknown input proof kind')


def _matches(value, reference):
    if not isinstance(value, str) or not value:
        return False
    try:
        verify_original(value, reference)
    except ValueError:
        return False  # Current live input differs; it must remain available.
    return True


def retire_verified_email_inputs(source: Path, target: Path, *, apply=False):
    source, target = Path(source).resolve(), Path(target).resolve()
    for path in (source, target):
        if not path.is_file():
            raise FileNotFoundError(path)
        require_current_email_pair(path, store_version=TARGET_STORE_VERSION,
                                   email_version=TARGET_EMAIL_VERSION)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        proofs = []
        for row in db.execute('select classification_id,account_id,stable_message_identity,'
                              'message_source_reference_json from emails '
                              'where classification_id is not null and message_source_reference_json is not null '
                              'order by classification_id'):
            envelope = json.loads(row['message_source_reference_json'])
            body = {k:v for k,v in envelope.items() if k != 'model_input'} if 'kind' in envelope else None
            model = envelope.get('model_input')
            if body is not None:
                _validate_reference(body, model=False)
            if model is not None:
                _validate_reference(model, model=True)
            if body is not None or model is not None:
                proofs.append((dict(row), body, model))
    report = dict(dry_run=not apply, source_rows=len(proofs), missing_or_ineligible=0,
                  body_matches=0, model_matches=0, body_retired=0,
                  model_inputs_retired=0, changed_rows=0)
    mode = 'rw' if apply else 'ro'
    with closing(sqlite3.connect(target.as_uri() + '?mode=' + mode, uri=True)) as db:
        db.row_factory = sqlite3.Row
        for source_row, body, model in proofs:
            with db:
                if apply:
                    db.execute('begin immediate')
                row = db.execute('select * from emails where classification_id=? and account_id=? '
                    'and stable_message_identity=? and ' + EmailStore._completed_email_input_sql(),
                    (source_row['classification_id'], source_row['account_id'],
                     source_row['stable_message_identity'])).fetchone()
                if row is None:
                    report['missing_or_ineligible'] += 1
                    continue
                current = json.loads(row['message_source_reference_json'] or '{}')
                retire_body = body is not None and 'kind' not in current and _matches(row['message_normalized_text'], body)
                retire_model = model is not None and 'model_input' not in current and _matches(row['class_model_text'], model)
                report['body_matches'] += retire_body
                report['model_matches'] += retire_model
                if not apply or not (retire_body or retire_model):
                    continue
                values = {}
                if retire_body:
                    values['message_normalized_text'] = ''
                    current.update(body)
                if retire_model:
                    values['class_model_text'] = ''
                    current['model_input'] = model
                values['message_source_reference_json'] = json.dumps(current, ensure_ascii=False,
                    sort_keys=True, separators=(',', ':'))
                db.execute('update emails set ' + ','.join(name+'=?' for name in values) + ' where id=?',
                           (*values.values(), row['id']))
                if retire_model:
                    db.execute('update emails set class_included_in_model_id=? where id=?',
                               (row['class_included_in_model_id'], row['id']))
                report['body_retired'] += retire_body
                report['model_inputs_retired'] += retire_model
                report['changed_rows'] += 1
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='apply matching references; default is read-only preview')
    args = parser.parse_args()
    print(json.dumps(retire_verified_email_inputs(args.source, args.target, apply=args.apply), sort_keys=True))


if __name__ == '__main__':
    main()
