"""Current Attempt payload ownership, preserving the original JSONB cells."""
from app.task_lifecycle_jsonb import _array, _framed

_NULL = b'\x00'
_TRIPLET = (22, 52, 53)


def _owner(db, blob):
    row = db.execute(
        "select jsonb_array(session_id,transcript_start,transcript_end) from runs "
        "where id=json_extract(?,'$[55]') and status in ('completed','failed') "
        "and typeof(session_id)='text' and typeof(transcript_start)='integer' "
        "and typeof(transcript_end)='integer'", (blob,),
    ).fetchone()
    return _array(row[0])[1] if row else None


def _cells(blob):
    width, cells = _array(blob)
    if len(cells) != 57:
        raise ValueError('Attempt JSONB field count changed')
    nulls = [cells[i] == _NULL for i in _TRIPLET]
    if any(nulls) and not all(nulls):
        raise ValueError('mixed null Attempt provenance')
    return width, cells


def normalize_payload(db, blob):
    width, cells = _cells(blob)
    if cells[7] == cells[21]:
        cells[7] = _NULL
    owner = _owner(db, blob)
    if all(cells[i] == _NULL for i in _TRIPLET) and owner is None:
        raise ValueError('invalid Attempt Run owner')
    if owner is not None and [cells[i] for i in _TRIPLET] == owner:
        for i in _TRIPLET:
            cells[i] = _NULL
    return _framed(cells, width)


def materialize_payload(db, blob):
    width, cells = _cells(blob)
    if cells[7] == _NULL:
        cells[7] = cells[21]
    if all(cells[i] == _NULL for i in _TRIPLET):
        owner = _owner(db, blob)
        if owner is None:
            raise ValueError('invalid Attempt Run owner')
        for i, value in zip(_TRIPLET, owner, strict=True):
            cells[i] = value
    return _framed(cells, width)
