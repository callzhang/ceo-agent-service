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
        'action_status': list(dict.fromkeys(email_statuses('email_actions') + list(ENUMS['candidate_action_attempts'][1]))),
        'email_receipt_status': email_statuses('email_action_attempts'),
        'candidate_observation_status': list(ENUMS['candidate_action_attempts'][1]),
    }

ACTION_KIND = 70
ACTION_COLUMNS = {
    "operation": "TEXT", "parameters_json": "TEXT",
    "status": "TEXT", "action_plan_id": "TEXT",
    "classification_id": "INTEGER", "account_id": "TEXT",
    "external_action_key": "TEXT", "next_attempt_at": "TEXT",
    "started_at": "TEXT", "created_at": "TEXT", "updated_at": "TEXT",
    "prepared_body": "TEXT", "prepared_at": "TEXT", "delivery_channel": "TEXT",
    "delivery_key": "TEXT", "feedback_token": "TEXT", "postfix_version": "TEXT",
    "delivery_receipt_id": "INTEGER", "prepared_receipt_id": "INTEGER",
    "conversation_id": "TEXT", "trigger_message_id": "TEXT", "recall_key": "TEXT",
    "recall_status": "TEXT", "recall_error": "TEXT", "recalled_at": "TEXT",
    "agent_run_id": "INTEGER", "business_object_key": "TEXT", "action_identity": "TEXT",
}


def source_identity(source_table: str, source_id: object) -> tuple[str, str, str]:
    """Return a migration-only identity preserving source namespace and SQLite type."""
    if source_table not in SOURCE_TABLES:
        raise ValueError(f'unknown source identity namespace: {source_table}')
    return source_table, type(source_id).__name__, str(source_id)


def build_target_ddl() -> str:
    """Render the original Action field contract using its frozen source owner."""
    from app.current_action_schema import signal_source_action_objects, _action_entity_objects

    rendered = [
        "-- Original unified Actions field-contract declaration and its fixed receipt relationships.",
        "-- Frozen Store25.2 Actions declaration for explicit field-contract tooling; not the current runtime schema.",
    ]
    action_objects = signal_source_action_objects()
    entity_objects = _action_entity_objects()
    rendered.extend(item["sql"] for item in sorted(
        action_objects, key=lambda item: item["type"] != "table"))
    rendered.extend(item["sql"] for item in sorted(
        entity_objects, key=lambda item: item["type"] != "table"))
    return ";\n".join(rendered) + ";\n"


def build_current_release_target_ddl() -> str:
    """Render the exact current Store/Email target schema manifest."""
    from app.current_schema_manifest import current_objects

    rendered = [
        "-- Exact current Store/Email release target schema manifest.",
        "-- Generated object SQL is kept in manifest order; this is the populated Action release target.",
    ]
    rendered.extend(
        f"-- {kind} {name}\n{sql};"
        for kind, name, _table, sql in current_objects(email_ledger=True)
    )
    return "\n\n".join(rendered) + "\n"


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
        if table == 'email_actions':
            from app.current_action_schema import current_action_payloads
            physical = set(current_action_payloads().layouts['email_actions'].fields)
            for name, field in by_name.items():
                expected_storage = 'stored' if name in physical else 'derived_projection'
                if field.get('storage') != expected_storage:
                    raise ValueError(f'email_actions.{name}: current source storage differs from current_action_payloads')
        for name, field in by_name.items():
            for key in ('owner', 'disposition', 'evidence', 'inverse_rule', 'consumers'):
                if not field.get(key):
                    raise ValueError(f'{table}.{name}: missing {key}')
            if not isinstance(field['consumers'], list) or not field['consumers'] or any(':' not in item or not item.startswith('app/') for item in field['consumers']):
                raise ValueError(f'{table}.{name}: consumers must name concrete app functions')
            for consumer in field['consumers']:
                path, symbol = consumer.split(':', 1)
                if not (ROOT / path).is_file() or not re.search(rf'\bdef\s+{re.escape(symbol.rsplit(".", 1)[-1])}\s*\(', (ROOT / path).read_text()):
                    raise ValueError(f'{table}.{name}: consumer is not declared: {consumer}')
            inverse = field.get('inverse_mapping')
            if not isinstance(inverse, dict) or inverse.get('owner') != field['owner']:
                raise ValueError(f'{table}.{name}: inverse mapping owner contradicts field owner')
            if field.get('inverse_rule') != _inverse_rule_text(table, name, inverse):
                raise ValueError(f'{table}.{name}: inverse prose contradicts structured inverse mapping')
            owner_tables = {'Action': 'actions', 'Action Receipt': 'action_receipts', 'Action Observer': 'action_observers', 'Action Plan': 'actions'}
            if inverse.get('owner_table') != owner_tables.get(field['owner']):
                raise ValueError(f'{table}.{name}: inverse mapping owner table contradicts field owner')
            target_name = field.get('target')
            expected_owner_field = 'id' if target_name in ('id', 'action_id', 'receipt identity scoped to Action') else target_name
            if field['owner'] == 'Action Observer' and target_name == 'action_id':
                expected_owner_field = 'action_id'
            if field['owner'] != 'Action Plan' and inverse.get('owner_field') != expected_owner_field:
                raise ValueError(f'{table}.{name}: inverse mapping target contradicts declared target')
            consumer_assertions = field.get('consumer_assertions')
            if not isinstance(consumer_assertions, list) or {item.get('location') for item in consumer_assertions} != set(field['consumers']):
                raise ValueError(f'{table}.{name}: consumer assertion coverage is incomplete')
            for assertion in consumer_assertions:
                if (assertion.get('source_field') != f'{table}.{name}'
                        or assertion.get('target_owner') != field['owner']
                        or assertion.get('target_field') != inverse.get('owner_field')):
                    raise ValueError(f'{table}.{name}: consumer assertion source/owner is contradictory')
            if field['owner'] == 'Action':
                action_targets = set(ACTION_COLUMNS) | {'id'}
                if inverse['owner_field'] not in action_targets:
                    raise ValueError(f'{table}.{name}: inverse mapping names no Action field')
            elif field['owner'] == 'Action Receipt':
                receipt_targets = {column['name'] for column in contract.get('target', {}).get('receipt_columns', [])} | {'id'}
                if inverse['owner_field'] not in receipt_targets:
                    raise ValueError(f'{table}.{name}: inverse mapping names no Action Receipt field')
            elif field['owner'] == 'Action Observer' and inverse['owner_field'] not in {'action_id', 'agent_run_id', 'task_id', 'created_at'}:
                raise ValueError(f'{table}.{name}: inverse mapping names no Action Observer field')
            elif field['owner'] == 'Action Plan':
                expected_via = {'from': 'Action.action_plan_id', 'to': 'email_action_plans.action_plan_id', 'immutable': True}
                if (inverse.get('owner_record_type') != 'email_action_plans'
                        or inverse.get('owner_field') != 'config_version'
                        or inverse.get('via') != expected_via):
                    raise ValueError(f'{table}.{name}: inverse mapping must traverse immutable email_action_plans by action_plan_id')
            if table in {'email_actions', 'email_action_attempts'} and name == 'action_id':
                expected_identity = {'function': 'app/email_store.py:_direct_action_id', 'arguments': ['Action.action_plan_id', 'Action.operation']}
                if inverse.get('via') != expected_identity:
                    raise ValueError(f'{table}.{name}: inverse mapping must use the existing direct action identity function')
            if field['disposition'] not in {'retain', 'reference', 'retire'}:
                raise ValueError(f'{table}.{name}: unsupported disposition')
    target = contract.get('target', {})
    if target.get('layout_selector') is not None:
        raise ValueError('target layout cannot branch on source or origin')
    if target.get('action_storage_type') != 'action':
        raise ValueError('all first-release records use action storage type')
    mappings = contract.get('state_mappings', {})
    declared_states = _declared_status_values()
    action_mapping = mappings.get('action_status', {})
    if action_mapping.get('declared_values') != declared_states['action_status']:
        raise ValueError('Action status mapping differs from source lifecycle enums')
    if set(action_mapping.get('mapping', {})) != set(declared_states['action_status']):
        raise ValueError('Action status mapping must account for each source lifecycle value')
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
        raise ValueError('static target DDL differs from the frozen field-contract declaration')
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
    if contract.get('identity', {}).get('public_id_is_identity') is not False or contract.get('identity', {}).get('migration_identity') != 'typed (source_table, source_id) mapping; physical Action id is allocated independently':
        raise ValueError('public source IDs cannot define unified identity')
    if contract.get('receipts', {}).get('preserve_distinct_values') is not True:
        raise ValueError('distinct receipt facts must be retained')
    lineage = contract.get('receipts', {}).get('version_lineage', {})
    if (not all(isinstance(lineage.get(key), str) and lineage[key] for key in (
            'current_version', 'canonical_binding', 'source_correction',
            'prior_mismatch', 'independent_facts'))
            or 'directly to the current immutable provider-result body' not in lineage['current_version']
            or 'first source operation' not in lineage['canonical_binding']
            or 'exact attempt' not in lineage['prior_mismatch']):
        raise ValueError('provider receipt version lineage is incomplete')


def _inverse_rule_text(source_table: str, source_field: str, inverse: dict) -> str:
    """Render inverse prose from the structured owner/path as a consistency check."""
    owner_record_type = inverse.get('owner_record_type')
    owner = inverse['owner_table']
    if owner_record_type:
        owner += f"[record_type={owner_record_type}]"
    via = inverse.get('via')
    path = json.dumps(via, sort_keys=True, separators=(',', ':')) if via is not None else 'direct'
    return (f'Reconstruct {source_table}.{source_field} from {owner}.{inverse["owner_field"]} '
            f'via {path}; preserve source nullness and exact stored representation.')

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
        new_indexes = ['idx_actions_action_external_key', 'idx_actions_action_direct_email_plan_operation', 'idx_actions_action_delivery_key', 'idx_action_receipts_action']
        index_disposition.extend({'name': name, 'disposition': 'added for common Action identity/owned receipt access'} for name in new_indexes)
        index_disposition.append({'name': 'idx_action_receipts_execution_order', 'disposition': 'unique execution/action slot retained from candidate_action_attempts'})
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
            'target_action_index_names': target_indexes + new_indexes[:3],
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
