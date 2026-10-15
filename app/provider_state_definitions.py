"""Finite current provider-state table and source-shaped projections."""

from app.action_email_definitions import _encoded_value, _encoded_value_check

OBSERVATION_FIELDS = (
    "account_id", "stable_message_identity", "state", "provider_folder_id",
    "provider_folder_name", "category_key", "important", "observed_at",
    "important_signals_json",
)
FRESH_OBSERVATION_FIELDS = OBSERVATION_FIELDS[:-2] + (
    "important_signals_json", "observed_at",
)
GENERATION_FIELDS = ("account_id", "provider_folder_id", "observed_at")
PAYLOAD_FIELDS = (
    "provider_folder_name", "category_key", "important", "important_signals_json",
)


def _value(field):
    return _encoded_value("payload_json", "$." + field)


def _check(field):
    return _encoded_value_check("payload_json", "$." + field)


PROVIDER_STATE_DDL = f"""
CREATE TABLE provider_state (
 id INTEGER PRIMARY KEY,
 record_type TEXT NOT NULL CHECK(record_type IN ('observation','folder_generation')),
 account_id TEXT NOT NULL CHECK(trim(account_id)!=''),
 stable_message_identity TEXT,
 state TEXT,
 provider_folder_id TEXT,
 observed_at TEXT NOT NULL CHECK(trim(observed_at)!=''),
 payload_json TEXT NOT NULL CHECK(typeof(payload_json)='text' AND json_valid(payload_json) AND json_type(payload_json)='object'),
 provider_folder_name TEXT GENERATED ALWAYS AS ({_value('provider_folder_name')}) VIRTUAL,
 category_key TEXT GENERATED ALWAYS AS ({_value('category_key')}) VIRTUAL,
 important INTEGER GENERATED ALWAYS AS ({_value('important')}) VIRTUAL,
 important_signals_json TEXT GENERATED ALWAYS AS ({_value('important_signals_json')}) VIRTUAL,
 CHECK(
  (record_type='observation' AND stable_message_identity IS NOT NULL
   AND trim(stable_message_identity)!='' AND state IS NOT NULL
   AND state IN ('available','unavailable','excluded')
   AND json_type(payload_json,'$.provider_folder_name') IS NOT NULL
   AND json_type(payload_json,'$.category_key') IS NOT NULL
   AND json_type(payload_json,'$.important') IS NOT NULL
   AND json_type(payload_json,'$.important_signals_json') IS NOT NULL
   AND json_remove(payload_json,'$.provider_folder_name','$.category_key','$.important','$.important_signals_json')='{{}}'
   AND {_check('provider_folder_name')} AND {_check('category_key')}
   AND {_check('important')} AND {_check('important_signals_json')}
   AND (important IS NULL OR important IN (0,1))
   AND (important_signals_json IS NULL OR json_valid(important_signals_json)))
  OR
  (record_type='folder_generation' AND stable_message_identity IS NULL
   AND state IS NULL AND provider_folder_id IS NOT NULL
   AND trim(provider_folder_id)!='' AND payload_json='{{}}')
 )
)
"""
PROVIDER_STATE_INDEXES = (
    "CREATE UNIQUE INDEX idx_provider_state_observation_identity ON provider_state(account_id,stable_message_identity) WHERE record_type='observation'",
    "CREATE UNIQUE INDEX idx_provider_state_generation_identity ON provider_state(account_id,provider_folder_id) WHERE record_type='folder_generation'",
    "CREATE INDEX idx_provider_state_observation_folder ON provider_state(account_id,provider_folder_id) WHERE record_type='observation'",
)

OBSERVATION_PROJECTION = ", ".join(OBSERVATION_FIELDS)
GENERATION_PROJECTION = ", ".join(GENERATION_FIELDS)
