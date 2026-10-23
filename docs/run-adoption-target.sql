CREATE TABLE run_adopted_results (
 consumer_run_id INTEGER PRIMARY KEY REFERENCES runs(id),
 stage_index INTEGER NOT NULL,
 predecessor_review_id INTEGER REFERENCES run_adopted_reviews(id),
 candidate_digest TEXT NOT NULL, candidate_json TEXT NOT NULL,
 invalidated_at TEXT NOT NULL, invalidation_reason TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(consumer_run_id,candidate_digest));

CREATE TABLE run_adopted_reviews (
 id INTEGER PRIMARY KEY,
 audit_run_id INTEGER NOT NULL UNIQUE REFERENCES runs(id),
 consumer_run_id INTEGER NOT NULL,
 candidate_digest TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('approve','return','reject')),
 result_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(consumer_run_id,audit_run_id),
 FOREIGN KEY(consumer_run_id,candidate_digest)
  REFERENCES run_adopted_results(consumer_run_id,candidate_digest));

CREATE TABLE run_adopted_selections (
 id INTEGER PRIMARY KEY,
 consumer_run_id INTEGER NOT NULL REFERENCES run_adopted_results(consumer_run_id),
 audit_run_id INTEGER NOT NULL, option_key TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(consumer_run_id), UNIQUE(consumer_run_id,audit_run_id),
 FOREIGN KEY(consumer_run_id,audit_run_id)
  REFERENCES run_adopted_reviews(consumer_run_id,audit_run_id));

CREATE TABLE run_adopted_supplements (
 id INTEGER PRIMARY KEY,
 consumer_run_id INTEGER NOT NULL REFERENCES run_adopted_results(consumer_run_id),
 instruction TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE run_execution_progress (
 consumer_run_id INTEGER PRIMARY KEY REFERENCES run_adopted_results(consumer_run_id),
 approved_audit_run_id INTEGER NOT NULL,
 selected_audit_run_id INTEGER,
 status TEXT NOT NULL CHECK(status IN ('running','done','skipped','failed','retry','uncertain')),
 lease_owner TEXT NOT NULL, lease_expires_at TEXT NOT NULL,
 result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(consumer_run_id,approved_audit_run_id)
  REFERENCES run_adopted_reviews(consumer_run_id,audit_run_id),
 FOREIGN KEY(consumer_run_id,selected_audit_run_id)
  REFERENCES run_adopted_selections(consumer_run_id,audit_run_id));

CREATE TABLE run_adoption_identity_map (
 source_kind TEXT NOT NULL, source_physical_id INTEGER NOT NULL,
 source_public_id TEXT, source_public_id_type TEXT NOT NULL,
 target_run_id INTEGER NOT NULL REFERENCES runs(id), target_child_id INTEGER,
 PRIMARY KEY(source_kind,source_physical_id),
 CHECK(source_public_id_type IN ('null','integer','text','blob')));

create table action_receipts (id integer primary key, action_id integer not null references actions(id), operation text, target text, provider_result_id text, provider_result_json text, result_digest text, status text check(status is null or status in ('done','skipped','failed','dispatched','verified','confirmed_no_effect','uncertain')), attempt_number integer, error_text text, agent_run_id integer, started_at text, finished_at text, execution_run_id integer references run_execution_progress(consumer_run_id), action_index integer, outcome_json text, created_at text, updated_at text, observed_at text, dispatched_at text, provider_receipt_id integer references action_receipts(id));

create table action_observers (action_id integer not null references actions(id), agent_run_id integer not null, task_id integer not null, created_at text not null, primary key(action_id,agent_run_id));

CREATE INDEX idx_run_adopted_reviews_consumer ON run_adopted_reviews(consumer_run_id,audit_run_id);

CREATE INDEX idx_run_adopted_supplements_consumer ON run_adopted_supplements(consumer_run_id,id);

create index idx_action_receipts_action on action_receipts(action_id,id);

create unique index idx_action_receipts_execution_run_order on action_receipts(execution_run_id,action_index) where execution_run_id is not null;

CREATE TRIGGER trg_run_adopted_results_consumer_insert
 BEFORE INSERT ON run_adopted_results
 WHEN NOT EXISTS(SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
  WHERE r.id=NEW.consumer_run_id AND r.role='consumer' AND t.kind='reply')
 BEGIN SELECT RAISE(ABORT,'adopted result owner must be Consumer Run for reply Task'); END;

CREATE TRIGGER trg_run_adopted_results_consumer_update
 BEFORE UPDATE OF consumer_run_id ON run_adopted_results
 WHEN NOT EXISTS(SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
  WHERE r.id=NEW.consumer_run_id AND r.role='consumer' AND t.kind='reply')
 BEGIN SELECT RAISE(ABORT,'adopted result owner must be Consumer Run for reply Task'); END;

CREATE TRIGGER trg_run_adopted_reviews_binding_insert
 BEFORE INSERT ON run_adopted_reviews
 WHEN NOT EXISTS(SELECT 1 FROM runs audit JOIN runs consumer ON consumer.id=NEW.consumer_run_id
  WHERE audit.id=NEW.audit_run_id AND audit.role='audit' AND audit.parent_id=consumer.id
   AND audit.task_id=consumer.task_id AND audit.execution_generation=consumer.execution_generation
   AND audit.proposal_revision=consumer.proposal_revision)
 BEGIN SELECT RAISE(ABORT,'review must bind the matching Audit Run'); END;

CREATE TRIGGER trg_run_adopted_reviews_binding_update
 BEFORE UPDATE OF audit_run_id,consumer_run_id ON run_adopted_reviews
 WHEN NOT EXISTS(SELECT 1 FROM runs audit JOIN runs consumer ON consumer.id=NEW.consumer_run_id
  WHERE audit.id=NEW.audit_run_id AND audit.role='audit' AND audit.parent_id=consumer.id
   AND audit.task_id=consumer.task_id AND audit.execution_generation=consumer.execution_generation
   AND audit.proposal_revision=consumer.proposal_revision)
 BEGIN SELECT RAISE(ABORT,'review must bind the matching Audit Run'); END;

CREATE TRIGGER trg_run_adopted_predecessor_review_insert
 BEFORE INSERT ON run_adopted_results
 WHEN NEW.predecessor_review_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM run_adopted_reviews review JOIN runs audit ON audit.id=review.audit_run_id
  WHERE review.id=NEW.predecessor_review_id AND audit.role='audit')
 BEGIN SELECT RAISE(ABORT,'predecessor must be adopted Audit review'); END;

CREATE TRIGGER trg_run_adopted_predecessor_review_update
 BEFORE UPDATE OF predecessor_review_id ON run_adopted_results
 WHEN NEW.predecessor_review_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM run_adopted_reviews review JOIN runs audit ON audit.id=review.audit_run_id
  WHERE review.id=NEW.predecessor_review_id AND audit.role='audit')
 BEGIN SELECT RAISE(ABORT,'predecessor must be adopted Audit review'); END;

create trigger trg_action_receipt_owner_insert before insert on action_receipts when not exists(select 1 from actions where id=new.action_id and record_type='action') begin select raise(abort,'receipt owner must be Action'); end;

create trigger trg_action_observer_owner_insert before insert on action_observers when not exists(select 1 from actions where id=new.action_id and record_type='action') begin select raise(abort,'observer owner must be Action'); end;

create trigger trg_action_receipt_reference_owner_insert before insert on action_receipts when new.provider_receipt_id is not null and not exists(select 1 from action_receipts r where r.id=new.provider_receipt_id and r.action_id=new.action_id and r.provider_result_json is not null) begin select raise(abort,'provider receipt reference must resolve within Action'); end;

create trigger trg_action_receipt_reference_owner_update before update of provider_receipt_id on action_receipts when new.provider_receipt_id is not null and old.provider_receipt_id is not new.provider_receipt_id and not exists(select 1 from action_receipts r where r.id=new.provider_receipt_id and r.action_id=new.action_id and r.provider_result_json is not null) begin select raise(abort,'provider receipt reference must resolve within Action'); end;

create trigger trg_action_delivery_receipt_owner_delete before delete on action_receipts when exists(select 1 from actions a where a.record_type='action' and a.action_delivery_receipt_id=old.id) begin select raise(abort,'Sent receipt is still referenced by Action'); end;

create trigger trg_action_receipt_identity_immutable before update of id,action_id on action_receipts when old.id is not new.id or old.action_id is not new.action_id begin select raise(abort,'receipt identity and owner are immutable'); end;

create trigger trg_action_receipt_lineage_immutable before update of provider_result_json,result_digest,provider_receipt_id on action_receipts when old.provider_result_json is not new.provider_result_json or (old.result_digest is not new.result_digest and not (old.result_digest is null and exists(select 1 from actions a where a.record_type='action' and a.id=old.action_id and a.action_delivery_receipt_id=old.id))) or (old.provider_receipt_id is not new.provider_receipt_id and not (((exists(select 1 from actions a where a.record_type='action' and a.id=old.action_id and a.action_delivery_receipt_id=old.id) or (old.provider_result_json is null and old.execution_run_id is null and old.action_index is null) or (old.execution_run_id is not null and old.action_index is not null)) and exists(select 1 from action_receipts next where next.id=new.provider_receipt_id and next.action_id=old.action_id) and ((old.execution_run_id is not null and old.action_index is not null) or new.observed_at is old.observed_at)))) begin select raise(abort,'provider receipt versions and exact references are immutable'); end;
