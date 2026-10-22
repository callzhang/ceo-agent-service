"""Unselected body-free source-version and Signal representation declarations."""

from app.action_email_definitions import _encoded_value_check
from app.business_candidate_definitions import TRIM_WS


SOURCE_FIELDS = (
    "source_type", "source_ref", "source_time", "conversation_id",
    "author_user_id", "author_name", "author_kind", "identity_key",
    "body_sha256", "body_utf8_bytes", "native_provenance_json",
    "provider_locator_json",
)
REQUIRED_SOURCE_FIELDS = SOURCE_FIELDS[:-2]
SIGNAL_FACT_FIELDS = ("conversation_title", "context_json", "dedupe_key", "created_at")
SIGNAL_FIELDS = (*SOURCE_FIELDS, "provenance_id", *SIGNAL_FACT_FIELDS)
HEX64 = "[0-9a-f]" * 64


def _serialized_locator_rule(column, source_column, source_ref_column, *, native):
    def field(name):
        return f"json_extract({column},'$.{name}')"
    kind = field("runtime_kind" if native else "kind")
    if native:
        required = (
            "runtime_kind", "session_uuid", "literal_encoding_mode",
            "line_index_zero_based", "substring_start_character_zero_based",
            "substring_end_character_exclusive", "decoded_utf8_bytes",
            "raw_line_sha256", "decoded_utf8_sha256", "leaf_path",
        )
        optional = ("path", "line_number_one_based", "raw_line_bytes")
        shape = [
            f"{kind} in ('codex_cli','claude_cli')",
            f"json_type({column},'$.session_uuid')='text' and {field('session_uuid')}<>''",
            f"{field('literal_encoding_mode')}='json_string_literal_utf8'",
            *(f"json_type({column},'$.{name}')='integer' and {field(name)}>=0"
              for name in required[3:7]),
            f"{field('substring_end_character_exclusive')}>={field('substring_start_character_zero_based')}",
            *(f"length({field(name)})=64 and {field(name)} glob '{HEX64}'"
              for name in required[7:9]),
            f"json_type({column},'$.leaf_path')='array' and json_array_length({column},'$.leaf_path')>0",
            f"(json_type({column},'$.path') is null or json_type({column},'$.path')='text')",
            f"(json_type({column},'$.line_number_one_based') is null or "
            f"(json_type({column},'$.line_number_one_based')='integer' and {field('line_number_one_based')}>0))",
            f"(json_type({column},'$.raw_line_bytes') is null or "
            f"(json_type({column},'$.raw_line_bytes')='integer' and {field('raw_line_bytes')}>0))",
            "json_remove(" + column + "," + ",".join(repr("$." + name) for name in (*required, *optional)) + ")='{}'",
        ]
    else:
        has_transcript_excerpts = (
            f"json_extract({column},'$.components') in ("
            "'[\"meeting\",\"todos\",\"transcript_excerpts\"]',"
            "'[\"meeting\",\"todos\",\"transcript_excerpts\",\"meeting_summary\"]')"
        )
        minutes = (
            f"{kind}='ai_minutes' and {source_column}='ai_minutes' "
            f"and instr({source_ref_column},'#todos-sha256=')>1 "
            f"and {field('minutes_id')}=substr({source_ref_column},1,instr({source_ref_column},'#todos-sha256=')-1) "
            f"and json_type({column},'$.minutes_id')='text' and {field('minutes_id')}<>'' "
            f"and json_type({column},'$.components')='array' "
            f"and json_extract({column},'$.components') in ("
            "'[\"meeting\",\"todos\"]','[\"meeting\",\"todos\",\"transcript_excerpts\"]',"
            "'[\"meeting\",\"todos\",\"meeting_summary\"]',"
            "'[\"meeting\",\"todos\",\"transcript_excerpts\",\"meeting_summary\"]') "
            f"and (({has_transcript_excerpts} "
            f"and json_type({column},'$.transcript_excerpt_format')='text' "
            f"and {field('transcript_excerpt_format')} in ('paragraph_v1','sentence_v1')) "
            f"or (not ({has_transcript_excerpts}) "
            f"and json_type({column},'$.transcript_excerpt_format') is null)) "
            f"and json_remove({column},'$.kind','$.minutes_id','$.components','$.transcript_excerpt_format')='{{}}'"
        )
        weekly = (
            f"{kind}='weekly_report' and {source_column} in "
            "('management_weekly_report','project_weekly_report','department_weekly_report') "
            f"and substr({source_ref_column},1,length('dingtalk-doc:'))='dingtalk-doc:' "
            f"and instr({source_ref_column},'#sha256=')>length('dingtalk-doc:')+1 "
            f"and {field('node_id')}=substr({source_ref_column},length('dingtalk-doc:')+1,"
            f"instr({source_ref_column},'#sha256=')-length('dingtalk-doc:')-1) "
            f"and json_type({column},'$.node_id')='text' and {field('node_id')}<>'' "
            f"and json_type({column},'$.url')='text' "
            f"and (json_type({column},'$.historyVersion') is null or "
            f"(json_type({column},'$.historyVersion')='integer' and {field('historyVersion')}>=0)) "
            f"and json_remove({column},'$.kind','$.node_id','$.url','$.historyVersion')='{{}}'"
        )
        shape = [f"(({minutes}) or ({weekly}))"]
    return (f"case when {column} is null then 1 when json_valid({column}) then "
            f"coalesce((json_type({column})='object' and {' and '.join(shape)}),0) else 0 end")


def provenance_objects():
    shape = [
        "id integer primary key",
        "source_type text not null", "source_ref text not null",
        "source_time text not null", "conversation_id text not null",
        "author_user_id text not null", "author_name text not null",
        "author_kind text not null check(author_kind in ('human','system','agent','unknown'))",
        "identity_key text not null unique", "body_sha256 text not null",
        "body_utf8_bytes integer not null check(typeof(body_utf8_bytes)='integer' and body_utf8_bytes>0)",
        "native_provenance_json text", "provider_locator_json text",
        f"check(trim(source_type,{TRIM_WS})<>'')",
        f"check(trim(source_ref,{TRIM_WS})<>'')",
        f"check(length(identity_key)=64 and identity_key glob '{HEX64}')",
        f"check(length(body_sha256)=64 and body_sha256 glob '{HEX64}')",
        "check(native_provenance_json is null or provider_locator_json is null)",
        "check(" + _serialized_locator_rule("native_provenance_json", "source_type", "source_ref", native=True) + ")",
        "check(" + _serialized_locator_rule("provider_locator_json", "source_type", "source_ref", native=False) + ")",
    ]
    return (
        "create table provenance (" + ",".join(shape) + ")",
        "create index idx_provenance_source on provenance(source_type,source_ref,source_time,id)",
        "create trigger trg_provenance_immutable_replace before insert on provenance "
        "when exists(select 1 from provenance where id=new.id or identity_key=new.identity_key) "
        "begin select raise(abort,'provenance binding is immutable'); end",
        "create trigger trg_provenance_native_leaf_insert before insert on provenance "
        "when new.native_provenance_json is not null and json_valid(new.native_provenance_json) "
        "and exists(select 1 from json_each(new.native_provenance_json,'$.leaf_path') "
        "where type not in ('text','integer') "
        "or (type='integer' and value<0)) "
        "begin select raise(abort,'invalid native source leaf path'); end",
        "create trigger trg_provenance_immutable_update before update on provenance begin select raise(abort,'provenance binding is immutable'); end",
        "create trigger trg_provenance_immutable_delete before delete on provenance begin select raise(abort,'provenance binding is immutable'); end",
    )


def physical_signal_checks(column, *, payloads=None):
    def c(field):
        return column(field)
    independent = SIGNAL_FACT_FIELDS
    encoded = [
        _encoded_value_check("payload_json", "$." + field)
        for field in independent
    ]
    encoded += [
        f"(json_type(payload_json,'$.{field}') is null or "
        f"{_encoded_value_check('payload_json', '$.' + field)})"
        for field in (*SOURCE_FIELDS, "provenance_id")
    ]
    present = [f"{c(field)} is not null" for field in independent]
    inline = " and ".join([
        f"{c('provenance_id')} is null",
        *(f"{c(field)} is not null" for field in REQUIRED_SOURCE_FIELDS),
        "json_type(payload_json,'$.provenance_id') is null",
    ])
    shared = " and ".join([
        f"{c('provenance_id')} is not null",
        *(f"{c(field)} is null" for field in SOURCE_FIELDS),
        *(f"json_type(payload_json,'$.{field}') is null" for field in SOURCE_FIELDS),
    ])
    exact = "json_remove(payload_json," + ",".join(repr("$." + field) for field in SIGNAL_FIELDS) + ")='{}'"
    if payloads:
        kind = "business_task_signals"
        encoded = [payloads.check(kind, field) for field in SIGNAL_FIELDS]
        inline = " and ".join([
            f"{c('provenance_id')} is null",
            *(f"{c(field)} is not null" for field in REQUIRED_SOURCE_FIELDS),
        ])
        shared = " and ".join([
            f"{c('provenance_id')} is not null",
            *(f"{c(field)} is null" for field in SOURCE_FIELDS),
        ])
        exact = payloads.shape(kind)
    rules = [*encoded, *present, f"({inline} or {shared})", exact]
    rules += [
        f"trim({c('dedupe_key')},{TRIM_WS})<>''",
        f"json_valid({c('context_json')}) and json_type({c('context_json')})='object'",
        f"({c('author_kind')} is null or {c('author_kind')} in ('human','system','agent','unknown'))",
        f"({c('source_type')} is null or trim({c('source_type')},{TRIM_WS})<>'')",
        f"({c('source_ref')} is null or trim({c('source_ref')},{TRIM_WS})<>'')",
        f"({c('identity_key')} is null or (length({c('identity_key')})=64 and {c('identity_key')} glob '{HEX64}'))",
        f"({c('body_sha256')} is null or (length({c('body_sha256')})=64 and {c('body_sha256')} glob '{HEX64}'))",
        f"({c('body_utf8_bytes')} is null or {c('body_utf8_bytes')}>0)",
        f"({c('body_utf8_bytes')} is null or typeof({c('body_utf8_bytes')})='integer')",
        _serialized_locator_rule(c('native_provenance_json'), c('source_type'), c('source_ref'), native=True),
        _serialized_locator_rule(c('provider_locator_json'), c('source_type'), c('source_ref'), native=False),
        f"({c('native_provenance_json')} is null or {c('provider_locator_json')} is null)",
    ]
    return rules


def physical_promotion_trigger(outer_columns, column, *, payloads=None):
    outside = " and ".join(f"new.{name} is old.{name}" for name in outer_columns if name != "payload_json")
    removed = ",".join(repr("$." + field) for field in SOURCE_FIELDS)
    parent = " and ".join(
        f"p.{field} is old.{column(field)}" for field in SOURCE_FIELDS
    )
    allowed = (
        f"{outside} and old.{column('provenance_id')} is null "
        f"and new.{column('provenance_id')} is not null "
        f"and json_remove(new.payload_json,'$.provenance_id')="
        f"json_remove(old.payload_json,{removed}) "
        f"and exists(select 1 from provenance p where p.id=new.{column('provenance_id')} and {parent})"
    )
    if payloads:
        unchanged = " and ".join(
            payloads.fragment("business_task_signals", field, "new.payload_json") + " is "
            + payloads.fragment("business_task_signals", field, "old.payload_json")
            for field in SIGNAL_FACT_FIELDS
        )
        allowed = (
            f"{outside} and old.{column('provenance_id')} is null "
            f"and new.{column('provenance_id')} is not null and {unchanged} "
            f"and exists(select 1 from provenance p where p.id=new.{column('provenance_id')} and {parent})"
        )
    return (
        "create trigger trg_actions_business_signals_immutable_update before update on actions "
        "when old.record_type='business_task_signals' and not (" + allowed + ") "
        "begin select raise(abort,'business task signals are immutable'); end"
    )
