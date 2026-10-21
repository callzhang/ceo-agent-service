"""Read-only inventory and validation for the first unified Action contract."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONTRACT_PATH = ROOT / 'docs/action-entity-field-map.json'
DDL_PATH = ROOT / 'docs/action-entity-target.sql'
SOURCE_TABLES = ('email_actions', 'email_action_attempts', 'candidate_action_attempts', 'external_action_results', 'outbound_postfixes', 'sent_replies', 'sent_reply_observers')

def _declared_source_fields():
    # Pure declaration imports only: inspection never initializes a Store.
    from app.action_email_definitions import EMAIL_FIELDS
    from app.action_storage import SOURCE_COLUMNS
    from app.prepared_action_definitions import COLUMNS as PREPARED_COLUMNS, SLOTS
    prepared = tuple(column for fields in SLOTS.values() for column in fields.values())
    if prepared != PREPARED_COLUMNS:
        raise ValueError('prepared action declaration columns do not match slots')
    return {'email_actions': EMAIL_FIELDS['email_actions'], 'email_action_attempts': EMAIL_FIELDS['email_action_attempts'], **{name: SOURCE_COLUMNS[name] for name in SOURCE_TABLES[2:] if name != 'outbound_postfixes'}, 'outbound_postfixes': (*SOURCE_COLUMNS['outbound_postfixes'], *prepared)}

def _declared_status_values():
    from app.action_email_definitions import EMAIL_CHECKS
    from app.action_storage import ENUMS

    def email_statuses(kind):
        for check in EMAIL_CHECKS[kind]:
            match = re.search(r"email_status\s+in\s*\(([^)]*)\)", check)
            if match:
                return re.findall(r"'([^']+)'", match.group(1))
        raise ValueError(f'{kind}: declared status enum missing')

    return {
        'action_status': email_statuses('email_actions'),
        'email_receipt_status': email_statuses('email_action_attempts'),
        'candidate_observation_status': list(ENUMS['candidate_action_attempts'][1]),
    }

ACTION_KIND = 70
ACTION_COLUMNS = {
    "operation": "TEXT", "parameters_json": "TEXT",
    "status": "TEXT", "action_plan_id": "TEXT",
    "classification_id": "INTEGER", "account_id": "TEXT",
    "external_action_key": "TEXT", "next_attempt_at": "TEXT",
    "started_at": "TEXT", "finished_at": "TEXT", "created_at": "TEXT", "updated_at": "TEXT",
    "prepared_body": "TEXT", "prepared_at": "TEXT", "delivery_channel": "TEXT",
    "delivery_key": "TEXT", "feedback_token": "TEXT", "postfix_version": "TEXT",
    "conversation_id": "TEXT", "trigger_message_id": "TEXT", "recall_key": "TEXT",
    "recall_status": "TEXT", "recall_error": "TEXT", "recalled_at": "TEXT",
    "agent_run_id": "INTEGER", "business_object_key": "TEXT", "action_identity": "TEXT",
    "current_provider_operation": "TEXT", "current_provider_target": "TEXT",
    "current_provider_result_id": "TEXT", "current_error_text": "TEXT",
}


def build_target_ddl() -> str:
    """Rebuild the current Actions declaration with one shared Action kind."""
    from app.current_action_schema import current_action_objects

    objects = current_action_objects()
    table = next(item["sql"] for item in objects if item["type"] == "table")
    table = table.replace("_kind in (", f"_kind in ({ACTION_KIND},", 1)
    table = table.replace("case _kind when ", f"case _kind when {ACTION_KIND} then 'action' when ", 1)
    table = table.replace("legacy_key text not null check(case when", "legacy_key text check(record_type='action' and legacy_key is null or (case when", 1)
    table = table.replace("end),task_id integer references", "end)),task_id integer references", 1)
    table = table.replace("payload_json blob not null,", "action_data blob,payload_json blob not null,", 1)
    generated = "," + ",".join(
        f"action_{name} {type_name.lower()} generated always as "
        f"(case when _kind={ACTION_KIND} then json_extract(action_data,'$.{name}') end) virtual"
        for name, type_name in ACTION_COLUMNS.items()
    )
    marker = ",check(record_type<>'review_candidates'"
    if marker not in table:
        raise ValueError("current Actions declaration changed; target rebuild marker missing")
    table = table.replace(marker, generated + marker, 1)
    table = table[:-1] + ",check(record_type<>'action' or (legacy_key is null and typeof(action_data)='blob' and json_valid(action_data,8) and json_type(action_data)='object')),check(record_type='action' or action_data is null),check(record_type<>'action' or action_status is null or action_status in ('pending','processing','done','skipped','failed')))"
    rendered = ["-- Full current Actions declaration rebuilt with one common Action kind (70).",
                "-- Existing kinds retain their original columns, checks, indexes and triggers.",
                "-- Action fields use one fixed JSONB object; generated columns are virtual.", table]
    for item in objects:
        if item["type"] == "table":
            continue
        sql = item["sql"]
        if item["type"] == "index" and item["name"] == "idx_actions_public_id":
            sql = sql.replace("where record_type<>", "where record_type<>'action' and record_type<>", 1)
        rendered.append(sql)
    rendered.extend((
        "create unique index idx_actions_action_external_key on actions(action_external_action_key) where record_type='action' and action_external_action_key is not null",
        "create unique index idx_actions_action_delivery_key on actions(action_delivery_channel,action_delivery_key) where record_type='action' and action_delivery_channel is not null and action_delivery_key is not null",
        "create table action_receipts (id integer primary key, action_id integer not null references actions(id), operation text, target text, provider_result_id text, provider_result_json text, result_digest text, status text check(status is null or status in ('done','skipped','failed','dispatched','verified','confirmed_no_effect','uncertain')), attempt_number integer, error_text text, agent_run_id integer, started_at text, finished_at text, execution_id integer, action_index integer, outcome_json text, created_at text, updated_at text, observed_at text, dispatched_at text)",
        "create index idx_action_receipts_action on action_receipts(action_id,id)",
        "create index idx_action_receipts_execution_order on action_receipts(execution_id,action_index,action_id) where execution_id is not null",
        "create table action_observers (action_id integer not null references actions(id), agent_run_id integer not null, task_id integer not null, created_at text not null, primary key(action_id,agent_run_id))",
        "create trigger trg_action_receipt_owner_insert before insert on action_receipts when not exists(select 1 from actions where id=new.action_id and record_type='action') begin select raise(abort,'receipt owner must be Action'); end",
        "create trigger trg_action_observer_owner_insert before insert on action_observers when not exists(select 1 from actions where id=new.action_id and record_type='action') begin select raise(abort,'observer owner must be Action'); end",
    ))
    return ";\n".join(rendered) + ";\n"


def validate_action_entity_contract(contract: dict) -> None:
    expected = _declared_source_fields()
    sources = contract.get('sources')
    if not isinstance(sources, list):
        raise ValueError('sources must enumerate every declared source table')
    by_table = {item.get('table'): item for item in sources if isinstance(item, dict)}
    if len(by_table) != len(sources):
        raise ValueError('duplicate source table or malformed source declaration')
    if set(by_table) != set(expected):
        raise ValueError('source table coverage does not match declarations')
    for table, names in expected.items():
        fields = by_table[table].get('fields')
        if not isinstance(fields, list):
            raise ValueError(f'{table}: fields must be a list')
        by_name = {field.get('name'): field for field in fields if isinstance(field, dict)}
        if len(by_name) != len(fields):
            raise ValueError(f'{table}: duplicate field declaration')
        if set(by_name) != set(names):
            raise ValueError(f'{table}: field coverage differs from declarations')
        for name, field in by_name.items():
            for key in ('owner', 'disposition', 'evidence'):
                if not field.get(key):
                    raise ValueError(f'{table}.{name}: missing {key}')
            if field['disposition'] not in {'retain', 'reference', 'retire'}:
                raise ValueError(f'{table}.{name}: unsupported disposition')
            if field['disposition'] == 'retire' and not field.get('inverse_rule'):
                raise ValueError(f'{table}.{name}: retirement requires inverse_rule')
    target = contract.get('target', {})
    if target.get('layout_selector') is not None:
        raise ValueError('target layout cannot branch on source or origin')
    if target.get('action_storage_type') != 'action':
        raise ValueError('all first-release records use action storage type')
    mappings = contract.get('state_mappings', {})
    declared_states = _declared_status_values()
    if mappings.get('action_status', {}).get('declared_values') != declared_states['action_status']:
        raise ValueError('Action status mapping differs from EMAIL_CHECKS')
    receipt_values = set(mappings.get('receipt_status', {}).get('declared_values', []))
    if receipt_values != set(declared_states['email_receipt_status']) | set(declared_states['candidate_observation_status']):
        raise ValueError('receipt status mapping differs from source enums')
    columns = target.get('business_columns')
    if not isinstance(columns, list) or not columns:
        raise ValueError('target business_columns must be enumerated')
    if len({column.get('name') for column in columns}) != len(columns):
        raise ValueError('duplicate target business field owner')
    if {column.get('name') for column in columns} != set(ACTION_COLUMNS):
        raise ValueError('target Action columns differ from the fixed common payload declaration')
    source_fields = {f'{table}.{name}' for table, names in expected.items() for name in names}
    for collection in (columns, target.get('receipt_columns', [])):
        for column in collection:
            if not column.get('sources'):
                raise ValueError(f"target column {column.get('name')} has no source")
            for source in column['sources']:
                if source not in source_fields:
                    raise ValueError(f"target column {column.get('name')} has unknown source {source}")
    ddl = DDL_PATH.read_text()
    if ddl != build_target_ddl():
        raise ValueError('static target DDL differs from the rebuilt current declaration')
    with sqlite3.connect(':memory:') as db:
        db.create_function('unhex', 1, lambda value: bytes.fromhex(value) if value is not None else None, deterministic=True)
        db.create_function('pow', 2, lambda x, y: x ** y, deterministic=True)
        db.executescript(ddl)
        action_columns = {row[1] for row in db.execute('pragma table_xinfo(actions)')}
        receipt_columns = {row[1] for row in db.execute('pragma table_info(action_receipts)')}
        observer_columns = {row[1] for row in db.execute('pragma table_info(action_observers)')}
    if 'action_data' not in action_columns or 'action_operation' not in action_columns:
        raise ValueError('target lacks shared generated Action columns')
    for column in columns:
        if ('id' if column['name'] == 'id' else 'action_' + column['name']) not in action_columns:
            raise ValueError(f"target DDL is missing Action column {column['name']}")
    for column in target.get('receipt_columns', []):
        if column['name'] not in receipt_columns:
            raise ValueError(f"target DDL is missing receipt column {column['name']}")
    expected_receipt_columns = {column['name'] for column in target.get('receipt_columns', [])}
    if receipt_columns - {'id'} != expected_receipt_columns:
        raise ValueError('target receipt DDL contains unsourced or missing fact columns')
    if not {'action_id', 'agent_run_id', 'task_id', 'created_at'} <= observer_columns:
        raise ValueError('target DDL is missing observer relationship fields')
    if contract.get('identity', {}).get('public_id_is_identity') is not False:
        raise ValueError('public source IDs cannot define unified identity')
    if contract.get('receipts', {}).get('preserve_distinct_values') is not True:
        raise ValueError('distinct receipt facts must be retained')

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

def inspect_action_entity_source(database: Path) -> dict:
    """Inventory current source rows and stable-identity buckets read-only."""
    database = Path(database).resolve(strict=True)
    before = _sha256(database)
    db = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('pragma query_only=on')
        tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
        if 'actions' not in tables:
            raise ValueError('source database has no current actions inventory')
        declared = _declared_source_fields()
        kinds = tuple(declared)
        marks = ','.join('?' for _ in kinds)
        rows = db.execute(f'select record_type,legacy_key,count(*) from actions where record_type in ({marks}) group by record_type,legacy_key', kinds).fetchall()
        counts = {kind: 0 for kind in kinds}
        public_ids = {}
        for kind, key, count in rows:
            counts[kind] += count
            public_ids.setdefault(str(key), {})[kind] = count
        observer_groups = db.execute("select count(*),count(distinct sent_reply_id),count(distinct sent_reply_id || ':' || agent_run_id) from actions where record_type='sent_reply_observers'").fetchone()
        prepared_clocks = db.execute("select sum(postfix_dispatch_created_at is not null),sum(postfix_receipt_created_at is not null) from actions where record_type='outbound_postfixes'").fetchone()
        duplicate_candidate_keys = db.execute("select count(*) from (select external_action_key from actions where record_type='candidate_action_attempts' and external_action_key is not null and trim(external_action_key)<>'' group by external_action_key having count(*)>1)").fetchone()[0]
        duplicate_candidate_facts = db.execute("""
            select count(*),sum(c.status is not null),sum(c.result_json is null),
                   count(distinct c.execution_id),count(distinct e.operation),
                   count(distinct e.target_identifiers_json),count(distinct e.provider_result_json)
            from actions c join actions e on e.record_type='external_action_results'
                and e.external_action_key=c.external_action_key
            where c.record_type='candidate_action_attempts' and c.external_action_key in (
                select external_action_key from actions where record_type='candidate_action_attempts'
                and external_action_key is not null and trim(external_action_key)<>''
                group by external_action_key having count(*)>1
            )
        """).fetchone()
        total_rows = db.execute("select count(*) from actions").fetchone()[0]
        unchanged_rows = total_rows - sum(counts.values())
        sent_result_distinctions = db.execute("select sum(send_result_json is null),sum(external_action_key is not null and trim(external_action_key)<>'') from actions where record_type='sent_replies'").fetchone()
        index_names = [row[0] for row in db.execute("select name from sqlite_master where type='index' and tbl_name='actions' and sql is not null order by name")]
        index_disposition = [
            {'name': name, 'disposition': 'retained excluding common Action rows' if name == 'idx_actions_public_id' else 'retained for unchanged record kinds'}
            for name in index_names
        ]
        from app.current_action_schema import current_action_objects
        target_indexes = [item['name'] for item in current_action_objects() if item['type'] == 'index']
        new_indexes = ['idx_actions_action_external_key', 'idx_actions_action_delivery_key', 'idx_action_receipts_action', 'idx_action_receipts_execution_order']
        index_disposition.extend({'name': name, 'disposition': 'added for common Action identity/order/owned receipt access'} for name in new_indexes)
        return {
            'database_sha256': before,
            'source_rows': sum(counts.values()),
            'source_rows_by_table': counts,
            'equal_public_id_groups': sum(len(kinds_for_id) > 1 for kinds_for_id in public_ids.values()),
            'observer_groups': {'rows': observer_groups[0], 'sent_replies': observer_groups[1], 'unique_sent_reply_run_pairs': observer_groups[2]},
            'prepared_clock_counts': {'dispatch_created_at_present': prepared_clocks[0], 'receipt_created_at_present': prepared_clocks[1]},
            'sent_reply_distinctions': {'null_provider_result_json': sent_result_distinctions[0], 'nonblank_external_action_key': sent_result_distinctions[1]},
            'duplicate_candidate_external_action_key_groups': duplicate_candidate_keys,
            'duplicate_candidate_observations': {'rows': duplicate_candidate_facts[0], 'statuses_present': duplicate_candidate_facts[1], 'null_outcomes': duplicate_candidate_facts[2], 'distinct_execution_ids': duplicate_candidate_facts[3], 'distinct_canonical_operations': duplicate_candidate_facts[4], 'distinct_canonical_targets': duplicate_candidate_facts[5], 'distinct_canonical_provider_results': duplicate_candidate_facts[6]},
            'actions_total_rows': total_rows,
            'unchanged_action_rows': unchanged_rows,
            'existing_action_index_disposition': index_disposition,
            'target_action_index_names': target_indexes + new_indexes[:2],
            'declaration_fields_by_table': {kind: len(fields) for kind, fields in declared.items()},
            'declaration_locations': {
                'email_actions': 'app/action_email_definitions.py:EMAIL_FIELDS',
                'email_action_attempts': 'app/action_email_definitions.py:EMAIL_FIELDS',
                **{kind: ('app/action_storage.py:SOURCE_COLUMNS + app/prepared_action_definitions.py:COLUMNS,SLOTS' if kind == 'outbound_postfixes' else 'app/action_storage.py:SOURCE_COLUMNS') for kind in SOURCE_TABLES[2:]},
            },
        }
    finally:
        db.close()
        if _sha256(database) != before:
            raise RuntimeError('read-only source database changed during inspection')

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    contract = json.loads(CONTRACT_PATH.read_text())
    validate_action_entity_contract(contract)
    report = inspect_action_entity_source(args.database)
    report['contract_sha256'] = _sha256(CONTRACT_PATH)
    report['target_ddl_sha256'] = _sha256(DDL_PATH)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
