"""Declared per-turn output contracts, shared by native Workbench adapters."""

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from app.agent_result import parse_typed_agent_result
from app.agent_runtime_contracts import (
    LOCAL_SERVICE_RUNTIME_CAPABILITIES,
    PROBE_VERIFIED_RUNTIME_CAPABILITIES,
)
from app.automatic_repair_contracts import (
    PRRepairResult,
    RepairDiagnosis,
    REPAIR_DIAGNOSIS_CONTRACT,
    REPAIR_PR_CONTRACT,
    REPAIR_MERGE_CONTRACT,
    RepairMergeResult,
)


@dataclass(frozen=True)
class WorkbenchResultContract:
    identity: str
    model: type[BaseModel]
    schema_path: Path
    required_capabilities: frozenset[str]

    def parse_stream(self, raw: str) -> str:
        return parse_typed_agent_result(raw, self.model).model_dump_json()


_SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "schemas"
_CONTRACTS = {
    identity: WorkbenchResultContract(
        identity,
        model,
        _SCHEMA_ROOT / f"{identity}.schema.json",
        LOCAL_SERVICE_RUNTIME_CAPABILITIES | PROBE_VERIFIED_RUNTIME_CAPABILITIES,
    )
    for identity, model in (
        (REPAIR_DIAGNOSIS_CONTRACT, RepairDiagnosis),
        (REPAIR_PR_CONTRACT, PRRepairResult),
        (REPAIR_MERGE_CONTRACT, RepairMergeResult),
    )
}


def get_result_contract(identity: str) -> WorkbenchResultContract | None:
    if identity == "":
        return None
    if identity not in _CONTRACTS:
        raise ValueError("unknown Workbench result contract")
    return _CONTRACTS[identity]
