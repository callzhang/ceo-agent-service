from math import ceil


DEFAULT_DINGTALK_TEXT_CHUNK_CHARS = 2800


def split_dingtalk_text(
    text: str,
    *,
    limit: int = DEFAULT_DINGTALK_TEXT_CHUNK_CHARS,
) -> list[str]:
    if limit <= 0:
        raise ValueError("message split limit must be positive")
    stripped = text.strip()
    if not stripped:
        return []
    if len(stripped) <= limit:
        return [stripped]
    body_limit = limit - len("【1/2】\n")
    if body_limit <= 0:
        raise ValueError("message split limit is too small for chunk prefix")
    estimated_total = max(2, ceil(len(stripped) / body_limit))
    while True:
        chunks = _split_text_body(
            stripped,
            limit=_chunk_body_limit(limit, estimated_total),
        )
        actual_total = len(chunks)
        if actual_total <= estimated_total:
            break
        estimated_total = actual_total
    total = len(chunks)
    return [f"【{index}/{total}】\n{chunk}" for index, chunk in enumerate(chunks, start=1)]


def _chunk_body_limit(limit: int, total: int) -> int:
    chunk_limit = limit - _chunk_prefix_length(total)
    if chunk_limit <= 0:
        raise ValueError("message split limit is too small for chunk prefix")
    return chunk_limit


def _chunk_prefix_length(total: int) -> int:
    return len(f"【{total}/{total}】\n")


def _split_text_body(text: str, *, limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in _paragraph_blocks(text):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(block) <= limit:
            current = block
            continue
        chunks.extend(_split_oversized_block(block, limit=limit))
    if current:
        chunks.append(current)
    return chunks


def _paragraph_blocks(text: str) -> list[str]:
    return [block.strip() for block in text.split("\n\n") if block.strip()]


def _split_oversized_block(block: str, *, limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(line) <= limit:
            current = line
            continue
        chunks.extend(line[start : start + limit] for start in range(0, len(line), limit))
    if current:
        chunks.append(current)
    return chunks
