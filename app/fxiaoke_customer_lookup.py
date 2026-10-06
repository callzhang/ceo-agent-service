"""Bounded, read-only AccountObj candidate lookup through sharecrm."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Literal


LookupStatus = Literal["matched", "ambiguous", "no_match", "unavailable"]

_ACCOUNT_OBJECT = "AccountObj"
_MAX_CANDIDATES = 50
_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class CrmCustomerCandidate:
    customer_id: str
    name: str
    alias: str = ""
    registered_name: str = ""
    matched_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class CrmCustomerLookup:
    # "matched" means one candidate was returned, not that it was proven unique.
    # Callers must keep it unlinked until a person confirms it.
    status: LookupStatus
    candidates: tuple[CrmCustomerCandidate, ...] = ()
    error_code: str = ""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _unavailable(error_code: str) -> CrmCustomerLookup:
    # Never retain CLI stderr/stdout; it may contain CRM values or diagnostics.
    return CrmCustomerLookup(status="unavailable", error_code=error_code)


def lookup_account_customers(
    label: str,
    *,
    runner: Runner = subprocess.run,
    binary: str = "sharecrm",
) -> CrmCustomerLookup:
    """Resolve a label to displayable CRM candidates without writing to CRM.

    The installed CLI's query-by-name command is a resolver, not a complete
    exact-match enumeration API. Even a single returned result must therefore
    remain a candidate pending explicit human confirmation.
    """
    normalized_label = " ".join(label.split())
    if not normalized_label:
        return CrmCustomerLookup(status="no_match")

    executable = shutil.which(binary)
    if executable is None:
        return _unavailable("cli_missing")

    try:
        completed = runner(
            [
                executable,
                "data",
                "record",
                "query-by-name",
                "--name",
                normalized_label,
                "--object_api_names",
                _ACCOUNT_OBJECT,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return _unavailable("cli_missing")
    except subprocess.TimeoutExpired:
        return _unavailable("timeout")
    except OSError:
        return _unavailable("command_failed")

    if completed.returncode != 0:
        return _unavailable("command_failed")
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError):
        return _unavailable("invalid_response")
    if not isinstance(payload, dict):
        return _unavailable("invalid_response")

    resolution_status = payload.get("resolution_status")
    raw_candidates = payload.get("record_candidates")
    if not isinstance(raw_candidates, list):
        if resolution_status == "NO_MATCH":
            return CrmCustomerLookup(status="no_match")
        return _unavailable("invalid_response")
    if len(raw_candidates) > _MAX_CANDIDATES:
        return _unavailable("too_many_candidates")

    candidates_by_id: dict[str, CrmCustomerCandidate] = {}
    for row in raw_candidates:
        if not isinstance(row, dict):
            return _unavailable("invalid_response")
        if row.get("object_api_name") != _ACCOUNT_OBJECT:
            continue
        customer_id = row.get("record_id")
        name = row.get("matched_name")
        if not isinstance(customer_id, str) or not customer_id.strip():
            return _unavailable("invalid_response")
        if not isinstance(name, str) or not name.strip():
            return _unavailable("invalid_response")
        candidates_by_id[customer_id] = CrmCustomerCandidate(
            customer_id=customer_id,
            name=name,
            matched_fields=("name_resolution",),
        )

    candidates = tuple(candidates_by_id.values())
    if not candidates:
        if resolution_status == "NO_MATCH":
            return CrmCustomerLookup(status="no_match")
        return _unavailable("no_account_candidate")
    return CrmCustomerLookup(
        status="matched" if len(candidates) == 1 else "ambiguous",
        candidates=candidates,
    )
