"""Pinned, unactivated adopted-result and receipt Run-reference contract."""
from __future__ import annotations

from app.action_entity_definitions import RECEIPT_SCHEMA_DDL
from app.action_storage import SOURCE_COLUMNS
from app.schema_sql import serialize_schema_sql


ADOPTED_SOURCE_KINDS = (
    "review_candidates", "candidate_reviews", "candidate_selections",
    "candidate_supplements", "candidate_executions", "candidate_execution_alias",
)

TABLES = {
    "run_adopted_results": """CREATE TABLE run_adopted_results (
 consumer_run_id INTEGER PRIMARY KEY REFERENCES runs(id),
 stage_index INTEGER NOT NULL,
 predecessor_review_id INTEGER REFERENCES run_adopted_reviews(id),
 candidate_digest TEXT NOT NULL, candidate_json TEXT NOT NULL,
 invalidated_at TEXT NOT NULL, invalidation_reason TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(consumer_run_id,candidate_digest))""",
    "run_adopted_reviews": """CREATE TABLE run_adopted_reviews (
 id INTEGER PRIMARY KEY,
 audit_run_id INTEGER NOT NULL UNIQUE REFERENCES runs(id),
 consumer_run_id INTEGER NOT NULL,
 candidate_digest TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('approve','return','reject')),
 result_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(consumer_run_id,audit_run_id),
 FOREIGN KEY(consumer_run_id,candidate_digest)
  REFERENCES run_adopted_results(consumer_run_id,candidate_digest))""",
    "run_adopted_selections": """CREATE TABLE run_adopted_selections (
 id INTEGER PRIMARY KEY,
 consumer_run_id INTEGER NOT NULL REFERENCES run_adopted_results(consumer_run_id),
 audit_run_id INTEGER NOT NULL, option_key TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(consumer_run_id), UNIQUE(consumer_run_id,audit_run_id),
 FOREIGN KEY(consumer_run_id,audit_run_id)
  REFERENCES run_adopted_reviews(consumer_run_id,audit_run_id))""",
    "run_adopted_supplements": """CREATE TABLE run_adopted_supplements (
 id INTEGER PRIMARY KEY,
 consumer_run_id INTEGER NOT NULL REFERENCES run_adopted_results(consumer_run_id),
 instruction TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "run_execution_progress": """CREATE TABLE run_execution_progress (
 consumer_run_id INTEGER PRIMARY KEY REFERENCES run_adopted_results(consumer_run_id),
 approved_audit_run_id INTEGER NOT NULL,
 selected_audit_run_id INTEGER,
 status TEXT NOT NULL CHECK(status IN ('running','done','skipped','failed','retry','uncertain')),
 lease_owner TEXT NOT NULL, lease_expires_at TEXT NOT NULL,
 result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(consumer_run_id,approved_audit_run_id)
  REFERENCES run_adopted_reviews(consumer_run_id,audit_run_id),
 FOREIGN KEY(consumer_run_id,selected_audit_run_id)
  REFERENCES run_adopted_selections(consumer_run_id,audit_run_id))""",
    # Sparse, content-free IDs needed for exact current-fact inverse. Public IDs
    # are typed text so integer/text distinctions survive independently of rowid.
    "run_adoption_identity_map": """CREATE TABLE run_adoption_identity_map (
 source_kind TEXT NOT NULL, source_physical_id INTEGER NOT NULL,
 source_public_id TEXT, source_public_id_type TEXT NOT NULL,
 target_run_id INTEGER NOT NULL REFERENCES runs(id), target_child_id INTEGER,
 PRIMARY KEY(source_kind,source_physical_id),
 CHECK(source_public_id_type IN ('null','integer','text','blob')))""",
}

INDEXES = {
    "idx_run_adopted_reviews_consumer":
        "CREATE INDEX idx_run_adopted_reviews_consumer ON run_adopted_reviews(consumer_run_id,audit_run_id)",
    "idx_run_adopted_supplements_consumer":
        "CREATE INDEX idx_run_adopted_supplements_consumer ON run_adopted_supplements(consumer_run_id,id)",
}

TRIGGERS = {
    "trg_run_adopted_results_consumer_insert": """CREATE TRIGGER trg_run_adopted_results_consumer_insert
 BEFORE INSERT ON run_adopted_results
 WHEN NOT EXISTS(SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
  WHERE r.id=NEW.consumer_run_id AND r.role='consumer' AND t.kind='reply')
 BEGIN SELECT RAISE(ABORT,'adopted result owner must be Consumer Run for reply Task'); END""",
    "trg_run_adopted_results_consumer_update": """CREATE TRIGGER trg_run_adopted_results_consumer_update
 BEFORE UPDATE OF consumer_run_id ON run_adopted_results
 WHEN NOT EXISTS(SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
  WHERE r.id=NEW.consumer_run_id AND r.role='consumer' AND t.kind='reply')
 BEGIN SELECT RAISE(ABORT,'adopted result owner must be Consumer Run for reply Task'); END""",
    "trg_run_adopted_reviews_binding_insert": """CREATE TRIGGER trg_run_adopted_reviews_binding_insert
 BEFORE INSERT ON run_adopted_reviews
 WHEN NOT EXISTS(SELECT 1 FROM runs audit JOIN runs consumer ON consumer.id=NEW.consumer_run_id
  WHERE audit.id=NEW.audit_run_id AND audit.role='audit' AND audit.parent_id=consumer.id
   AND audit.task_id=consumer.task_id AND audit.execution_generation=consumer.execution_generation
   AND audit.proposal_revision=consumer.proposal_revision)
 BEGIN SELECT RAISE(ABORT,'review must bind the matching Audit Run'); END""",
    "trg_run_adopted_reviews_binding_update": """CREATE TRIGGER trg_run_adopted_reviews_binding_update
 BEFORE UPDATE OF audit_run_id,consumer_run_id ON run_adopted_reviews
 WHEN NOT EXISTS(SELECT 1 FROM runs audit JOIN runs consumer ON consumer.id=NEW.consumer_run_id
  WHERE audit.id=NEW.audit_run_id AND audit.role='audit' AND audit.parent_id=consumer.id
   AND audit.task_id=consumer.task_id AND audit.execution_generation=consumer.execution_generation
   AND audit.proposal_revision=consumer.proposal_revision)
 BEGIN SELECT RAISE(ABORT,'review must bind the matching Audit Run'); END""",
    "trg_run_adopted_predecessor_review_insert": """CREATE TRIGGER trg_run_adopted_predecessor_review_insert
 BEFORE INSERT ON run_adopted_results
 WHEN NEW.predecessor_review_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM run_adopted_reviews review JOIN runs audit ON audit.id=review.audit_run_id
  WHERE review.id=NEW.predecessor_review_id AND audit.role='audit')
 BEGIN SELECT RAISE(ABORT,'predecessor must be adopted Audit review'); END""",
    "trg_run_adopted_predecessor_review_update": """CREATE TRIGGER trg_run_adopted_predecessor_review_update
 BEFORE UPDATE OF predecessor_review_id ON run_adopted_results
 WHEN NEW.predecessor_review_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM run_adopted_reviews review JOIN runs audit ON audit.id=review.audit_run_id
  WHERE review.id=NEW.predecessor_review_id AND audit.role='audit')
 BEGIN SELECT RAISE(ABORT,'predecessor must be adopted Audit review'); END""",
}


def receipt_schema_patch():
    """Return the current receipt declaration with a Consumer progress-owner FK.

    All existing receipt columns, provider lineage constraints, observer
    ownership, and immutability triggers are retained verbatim where possible.
    """
    declarations = []
    for sql in RECEIPT_SCHEMA_DDL:
        if sql.startswith("create table action_receipts"):
            sql = sql.replace(
                "execution_id integer, action_index integer",
                "execution_run_id integer references run_execution_progress(consumer_run_id), action_index integer",
            )
        elif sql.startswith("create unique index idx_action_receipts_execution_order"):
            sql = sql.replace(
                "idx_action_receipts_execution_order on action_receipts(execution_id,action_index) ",
                "idx_action_receipts_execution_run_order on action_receipts(execution_run_id,action_index) ",
            ).replace("where execution_id is not null", "where execution_run_id is not null")
        if "execution_id" in sql:
            sql = sql.replace("execution_id", "execution_run_id")
        declarations.append(sql)
    return tuple(declarations)


def receipt_schema_objects():
    names = (
        "action_receipts", "idx_action_receipts_action",
        "idx_action_receipts_execution_run_order", "action_observers",
        "trg_action_receipt_owner_insert", "trg_action_observer_owner_insert",
        "trg_action_receipt_reference_owner_insert", "trg_action_receipt_reference_owner_update",
        "trg_action_delivery_receipt_owner_delete", "trg_action_receipt_identity_immutable",
        "trg_action_receipt_lineage_immutable",
    )
    declarations = receipt_schema_patch()
    if len(names) != len(declarations):
        raise ValueError("Action receipt declaration names are incomplete")
    return dict(zip(names, declarations, strict=True))


def adoption_source_fields():
    """All declared fields for adopted source families, with typed disposition."""
    fields = {kind: SOURCE_COLUMNS[kind] for kind in ADOPTED_SOURCE_KINDS[:-1]}
    fields["candidate_execution_alias"] = ("id", "candidate_id", "legacy_key")
    return fields


def field_map():
    """Deterministic source-field to Run-owned contract mapping."""
    mapping = {
        "review_candidates": {
            "id": ("run_adoption_identity_map", "source_public_id"),
            "task_id": ("runs", "task_id (through consumer_run_id)"),
            "execution_generation": ("runs", "execution_generation (through consumer_run_id)"),
            "consumer_run_id": ("run_adopted_results", "consumer_run_id"),
            "stage_index": ("run_adopted_results", "stage_index"),
            "proposal_revision": ("runs", "proposal_revision (through consumer_run_id)"),
            "predecessor_review_id": ("run_adopted_results", "predecessor_review_id"),
            "candidate_digest": ("run_adopted_results", "candidate_digest"),
            "candidate_json": ("run_adopted_results", "candidate_json"),
            "invalidated_at": ("run_adopted_results", "invalidated_at"),
            "invalidation_reason": ("run_adopted_results", "invalidation_reason"),
            "created_at": ("run_adopted_results", "created_at"),
        },
        "candidate_reviews": {
            "id": ("run_adopted_reviews", "id and run_adoption_identity_map.target_child_id"),
            "candidate_id": ("run_adopted_reviews", "consumer_run_id (through review row)"),
            "audit_run_id": ("run_adopted_reviews", "audit_run_id"),
            "decision": ("run_adopted_reviews", "decision"),
            "candidate_digest": ("run_adopted_reviews", "candidate_digest"),
            "result_json": ("run_adopted_reviews", "result_json"),
            "created_at": ("run_adopted_reviews", "created_at"),
        },
        "candidate_selections": {
            "id": ("run_adoption_identity_map", "source_public_id"),
            "candidate_id": ("run_adopted_selections", "consumer_run_id"),
            "review_id": ("run_adopted_selections", "audit_run_id (via Review owner)"),
            "option_key": ("run_adopted_selections", "option_key"),
            "branch_json": (None, "derived from bound candidate option_key; omitted from stored target"),
            "status": ("run_adopted_selections", "status"),
            "created_at": ("run_adopted_selections", "created_at"),
        },
        "candidate_supplements": {
            "id": ("run_adopted_supplements", "id and run_adoption_identity_map.target_child_id"),
            "candidate_id": ("run_adopted_supplements", "consumer_run_id"),
            "instruction": ("run_adopted_supplements", "instruction"),
            "created_at": ("run_adopted_supplements", "created_at"),
        },
        "candidate_executions": {
            "id": ("run_adoption_identity_map", "source_public_id"),
            "candidate_id": ("run_execution_progress", "consumer_run_id"),
            "review_id": ("run_execution_progress", "approved_audit_run_id"),
            "selection_id": ("run_execution_progress", "selected_audit_run_id"),
            "status": ("run_execution_progress", "status"),
            "lease_owner": ("run_execution_progress", "lease_owner"),
            "lease_expires_at": ("run_execution_progress", "lease_expires_at"),
            "result_json": ("run_execution_progress", "result_json"),
            "created_at": ("run_execution_progress", "created_at"),
            "updated_at": ("run_execution_progress", "updated_at"),
        },
        "candidate_execution_alias": {
            "id": ("run_adoption_identity_map", "source physical identity"),
            "candidate_id": ("run_execution_progress", "consumer_run_id"),
            "legacy_key": ("run_adoption_identity_map", "source_public_id"),
        },
    }
    return mapping


def validate_field_map(mapping=None):
    """Reject unknown or missing declared source fields before artifact use."""
    actual = field_map() if mapping is None else mapping
    expected = adoption_source_fields()
    if set(actual) != set(expected):
        raise ValueError("adoption source kinds differ from declarations")
    for kind, fields in expected.items():
        if set(actual[kind]) != set(fields):
            raise ValueError(f"adoption fields differ from {kind} declarations")
        for field, destination in actual[kind].items():
            if (not isinstance(destination, tuple) or len(destination) != 2
                    or destination[0] is None and not (kind == "candidate_selections" and field == "branch_json")):
                raise ValueError(f"invalid adoption mapping for {kind}.{field}")
    return True


def schema_sql():
    objects = {**TABLES, **INDEXES, **TRIGGERS, **receipt_schema_objects()}
    return serialize_schema_sql(objects)
