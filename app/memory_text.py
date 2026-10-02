"""Text written to Memory carries what was said, not what the service stamps on every message.

memory-connector embeds each episode and clusters by similarity (Derek
2026-10-02). Lines the service adds identically to every outbound message --
the 👍/👎 feedback callbacks, the assistant signature, a fixed ID tag -- made
unrelated episodes score as near-duplicates: two different meetings' tags at
0.992, a feedback link at 0.81 against unrelated content. Identifiers belong in
``source_metadata``, never in the text.
"""
from __future__ import annotations

from app.config import assistant_signature
from app.feedback_spike import FEEDBACK_CALLBACK_PATH


def memory_body(text: str) -> str:
    """Return ``text`` without the service's feedback callbacks and signature."""
    paragraphs = text.strip().split("\n\n")
    while paragraphs and FEEDBACK_CALLBACK_PATH in paragraphs[-1]:
        paragraphs.pop()
    body = "\n\n".join(paragraphs).rstrip()
    signature = assistant_signature().strip()
    if signature and body.endswith(signature):
        body = body[: -len(signature)].rstrip()
    return body
