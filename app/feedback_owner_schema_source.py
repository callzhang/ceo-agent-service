"""Frozen Store28.2 / Email54 catalog for the explicit Feedback release."""
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path


@lru_cache(maxsize=1)
def feedback_source_objects():
    raw = (Path(__file__).parent / 'schema_fixtures' / 'store28_2_email54_catalog.json').read_bytes()
    decoded = json.loads(raw)
    canonical = json.dumps(decoded, ensure_ascii=False, separators=(',', ':')).encode()
    if sha256(canonical).hexdigest() != '8fe0b0d99c39c9bcab5b6637ba188677038cd620a72c78e7daa720a8d0d36b2a':
        raise ValueError('Store28.2 source catalog digest mismatch')
    return tuple(tuple(row) for row in decoded)
