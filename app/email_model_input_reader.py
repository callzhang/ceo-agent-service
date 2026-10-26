"""Request-local exact historical model features for existing owner examples."""

from collections import defaultdict
import json

from app.email_body_source import read_referenced_original, verify_original, _provider_original


def read_owner_model_inputs(store, rows, *, source_reader=None):
    texts = {}
    provider_rows = defaultdict(list)
    for row in rows:
        identity = (row['account_id'], row['stable_message_identity'])
        text = str(row['model_text'])
        source = json.loads(row['message_source_reference_json'] or '{}')
        reference = source.get('model_input')
        if text or reference is None:
            texts[identity] = text
        elif reference['kind'] == 'native':
            texts[identity] = read_referenced_original(reference,
                read_message=lambda: None)  # Native coordinates are sufficient.
        elif reference['kind'] == 'provider':
            provider_rows[row['account_id']].append((row, reference))
        else:
            raise ValueError('unknown Email model input source kind')
    if provider_rows and source_reader is None:
        from app.email_source_reader import build_email_source_reader
        from app.config import effective_env_values

        source_reader = build_email_source_reader(store, environment_factory=effective_env_values)
    for account, values in provider_rows.items():
        for start in range(0, len(values), 50):
            batch = values[start:start + 50]
            try:
                messages = source_reader.read_many(
                    account, [row['stable_message_identity'] for row, _ in batch]
                )
            except LookupError:
                # These historical examples are supplemental. A stale provider
                # source must not fail classification of the current message.
                continue
            for row, reference in batch:
                identity = row['stable_message_identity']
                texts[(account, identity)] = verify_original(
                    _provider_original(messages[identity], reference['representation']), reference)
    return texts
