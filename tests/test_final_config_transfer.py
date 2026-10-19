"""The Config migration serializes its explicit intermediate Actions declaration."""
from tests.test_configs_target_integrated import target as target
from tests.test_configs_schema_transfer import native, seed, start_target
from app.configs_schema_transfer import capture_config_declarations, copy_config_rows
from app.action_skill_config_storage import load_receipt_projection


def test_config_transfer_copies_nonempty_native_receipts_before_final_conversion(target):
    with native('actual_email52') as source:
        seed(source)
        metadata = capture_config_declarations(source)
        start_target(target)
        counts = copy_config_rows(source, target, metadata)
        assert counts['managed_skill_export_receipts'] == 1
        assert counts['runtime_skill_load_receipts'] == 2
        physical_id = target.execute("select id from actions where record_type='runtime_skill_load_receipts' and legacy_key='9001'").fetchone()[0]
        expected = source.execute("select loaded_json from runtime_skill_load_receipts where id=9001").fetchone()[0]
        assert load_receipt_projection(target, physical_id).loaded_json == expected
        assert target.execute('pragma foreign_key_check').fetchall() == []
