"""Raw cells of the existing SQLite JSONB Task arrays.

SQLite permits nonminimal headers and its jsonb_set retains them on some
shrinks. JSON text extraction/re-encoding cannot preserve those bytes.
This module only splits/joins existing cells; SQLite still validates, decodes,
and updates every value. Format: https://www.sqlite.org/jsonb.html
"""
from app import task20_definitions as source
from app.task_lifecycle_definitions import SOURCE_FIELDS


def _cell(blob, offset):
    tag = blob[offset]
    size = tag >> 4
    width = 1 if size < 12 else 1 + (1 << (size - 12))
    if width > 1:
        size = int.from_bytes(blob[offset + 1:offset + width], "big")
    end = offset + width + size
    if end > len(blob) or offset + width > len(blob):
        raise ValueError("truncated Task JSONB cell")
    return width, end


def _array(blob):
    width, end = _cell(blob, 0)
    if blob[0] & 15 != 11 or end != len(blob):
        raise ValueError("Task JSONB must be one array")
    cells, offset = [], width
    while offset < end:
        _, next_offset = _cell(blob, offset)
        cells.append(blob[offset:next_offset])
        offset = next_offset
    return width, cells


def _framed(cells, width):
    body = b"".join(cells)
    size = len(body)
    minimum = 1 if size < 12 else 2 if size < 256 else 3 if size < 65536 else 5 if size < 2**32 else 9
    width = max(width, minimum)
    header = (bytes([(size << 4) | 11]) if width == 1 else
              bytes([((12 + (width - 1).bit_length() - 1) << 4) | 11])
              + size.to_bytes(width - 1, "big"))
    return header + body


def cell(blob, index):
    """Return one encoded cell including its original, possibly padded header."""
    width, end = _cell(blob, 0)
    offset = width
    for current in range(index + 1):
        if offset >= end:
            return None
        _, next_offset = _cell(blob, offset)
        if current == index:
            return blob[offset:next_offset]
        offset = next_offset


def domain(kind, blob):
    width, cells = _array(blob)
    fields = source.TASK20_PAYLOAD_FIELDS[kind]
    if len(cells) != len(fields):
        raise ValueError("Task JSONB field count changed")
    owners = SOURCE_FIELDS[kind]
    return _framed((value for field, value in zip(fields, cells, strict=True) if field not in owners), width)


def restore(kind, blob, *owned):
    width, cells = _array(blob)
    owners = dict(zip(SOURCE_FIELDS[kind], owned, strict=True))
    fields = source.TASK20_PAYLOAD_FIELDS[kind]
    if len(cells) + len(owners) != len(fields):
        raise ValueError("Task domain JSONB field count changed")
    remaining = iter(cells)
    return _framed(
        ((owners[field] if owners[field] is not None else b"\x00")
         if field in owners else next(remaining) for field in fields), width)


def restore_sql(kind, payload="payload_json", alias=""):
    prefix = alias + "." if alias else ""
    return "ceo_task_restore(" + repr(kind) + "," + payload + "," + ",".join(
        prefix + '"' + target + '"' for target in SOURCE_FIELDS[kind].values()
    ) + ")"


def register(db):
    db.create_function("ceo_task_cell", 2, cell, deterministic=True)
    db.create_function("ceo_task_domain", 2, domain, deterministic=True)
    db.create_function("ceo_task_restore", -1, restore, deterministic=True)
