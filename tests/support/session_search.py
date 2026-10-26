"""Seed retained search metadata for reader tests; no runtime writer remains."""


def seed_session_search_metadata(store, *, session_id, source_type, source_id, title):
    with store._connect() as db:
        db.execute(
            "insert into codex_session_search_index "
            "(session_id,source_type,source_id,title,created_at,updated_at) "
            "values(?,?,?,?,current_timestamp,current_timestamp)",
            (session_id, source_type, source_id, title),
        )
