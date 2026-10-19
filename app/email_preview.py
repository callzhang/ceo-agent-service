"""One bounded plain-text preview; never a substitute for an exact full input."""

from email.parser import Parser

from app.email_html_text import html_to_text, visible_email_text

PREVIEW_CHARACTERS = 1000


def email_preview(normalized_text: str) -> str:
    message = Parser().parsestr(normalized_text)
    payload = message.get_payload()
    if not isinstance(payload, str):
        return ""
    text = html_to_text(payload) if message.get_content_type() == "text/html" else visible_email_text(payload)
    return text[:PREVIEW_CHARACTERS]


def populate_email_previews(db) -> int:
    """Explicit publication backfill before originals retire; caller owns transaction."""
    rows = db.execute(
        "select id,classification_id,message_normalized_text,class_preview,message_preview "
        "from emails"
    ).fetchall()
    changed = 0
    for row_id, classification_id, original, class_preview, message_preview in rows:
        # Retired originals remain retired. Their existing short preview is an
        # adopted display fact; do not fetch or reconstruct an original here.
        previous = class_preview if classification_id is not None else message_preview
        preview = email_preview(original) if original else (previous or "")[:PREVIEW_CHARACTERS]
        if classification_id is not None:
            if class_preview != preview or message_preview:
                db.execute("update emails set class_preview=?,message_preview='' where id=?", (preview, row_id))
                changed += 1
        elif message_preview != preview:
            db.execute("update emails set message_preview=? where id=?", (preview, row_id))
            changed += 1
    return changed
