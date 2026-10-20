"""Declared Actions JSONB slots; source factories remain explicit object SQL."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.action_email_definitions import _encoded_value, _encoded_value_check
from app.storage_payload import ACTION_RECORD_TYPES, PayloadLayout


@dataclass(frozen=True)
class ActionPayloads:
    layouts: Mapping[str, PayloadLayout]
    record_types: object = ACTION_RECORD_TYPES

    def __post_init__(self):
        object.__setattr__(self, "layouts", MappingProxyType(dict(self.layouts)))

    def path(self, kind, field):
        return self.layouts[kind].path(field)

    def value(self, kind, field, document="payload_json"):
        return _encoded_value(document, self.path(kind, field))

    def field_value(self, field, kinds=None, document="payload_json"):
        kinds = self.layouts if kinds is None else kinds
        cases = ["when " + str(self.record_types.code(kind)) + " then " + self.value(kind, field, document)
                 for kind in kinds if field in self.layouts[kind].fields]
        return "case _kind " + " ".join(cases) + " end" if cases else "null"

    def scoped_value(self, kind, field, document="payload_json"):
        return "case when _kind=" + str(self.record_types.code(kind)) + " then " + self.value(kind, field, document) + " end"

    def check(self, kind, field):
        return _encoded_value_check("payload_json", self.path(kind, field))

    def shape(self, kind):
        return self.layouts[kind].shape_sql()

    def pack(self, kind, values):
        from app.action_storage import _encoded_native_sqlite_value
        return self.layouts[kind].pack(values, scalar_codec=_encoded_native_sqlite_value)

    def fragment(self, kind, field, document="payload_json"):
        return document + "->" + repr(self.path(kind, field))
