"""Typed persistence models for feedback processing workflows.

The models in this module intentionally describe only the bounded processing
states persisted by :class:`app.store.AutoReplyStore`.  Extra fields and
implicit coercions are rejected so callers cannot accidentally persist a
different workflow shape.
"""

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from app.store import UserFeedbackItem

FEEDBACK_PROCESSING_CLAIM_ERROR = "feedback processing claim rejected"
FEEDBACK_PROCESSING_ALREADY_PROCESSING_ERROR = "feedback_already_processing"
FEEDBACK_PROCESSING_BATCH_ERROR = "feedback processing batch definition conflict"
FEEDBACK_PROCESSING_CURRENT_ROUND_ID_INVALID = (
    "feedback_processing_current_round_id_invalid"
)
FEEDBACK_REOPEN_INVALID = "feedback_reopen_invalid"
FEEDBACK_REOPEN_PROCESSING = "feedback_reopen_processing"
FEEDBACK_REOPEN_HISTORY_INCOMPLETE = "feedback_reopen_history_incomplete"
FEEDBACK_PROCESSING_SKILL_PATH = "skills/ceo-feedback-processing/SKILL.md"
FEEDBACK_ITERATION_SKILL_PATH = "skills/ceo-feedback-iteration/SKILL.md"
FEEDBACK_ITERATION_DISABLED_ERROR = "feedback_iteration_disabled"
FEEDBACK_ITERATION_ASSOCIATION_MISMATCH_ERROR = "feedback_iteration_association_mismatch"


class FeedbackProcessingClaimError(ValueError):
    """Raised when a feedback batch cannot be claimed atomically."""

    error_code = FEEDBACK_PROCESSING_ALREADY_PROCESSING_ERROR


class FeedbackProcessingBatchError(ValueError):
    """Raised when a batch id is reused with a different key set."""


class FeedbackIterationDisabledError(ValueError):
    """Raised when the separately managed feedback capability is disabled."""

    error_code = FEEDBACK_ITERATION_DISABLED_ERROR


class FeedbackIterationAssociationMismatchError(ValueError):
    """Raised when a decision identity differs from its current processing rounds."""

    error_code = FEEDBACK_ITERATION_ASSOCIATION_MISMATCH_ERROR


class FeedbackProcessingReopenError(ValueError):
    """Raised when a feedback item cannot be reopened safely."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class _StrictProcessingModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_assignment=True,
        validate_default=True,
    )


class FeedbackProcessingBatch(_StrictProcessingModel):
    """A group of feedback items processed together."""

    batch_id: str
    status: Literal["pending", "processing", "resolved"] = "pending"
    requested_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    resolved_at: str = ""


class FeedbackProcessingItem(_StrictProcessingModel):
    """Persisted state and evidence for one feedback event."""

    feedback_key: str
    current_round_id: int = 0
    batch_id: str = ""
    status: Literal["pending", "processing", "resolved"] = "pending"
    workbench_task_id: str = ""
    workbench_turn_id: str = ""
    attempt_id: int = 0
    agent_run_id: int = 0
    commit_sha: str = ""
    test_evidence: dict[str, object] = Field(default_factory=dict)
    restart_evidence: dict[str, object] = Field(default_factory=dict)
    health_evidence: dict[str, object] = Field(default_factory=dict)
    note: str = ""
    resolved_at: str = ""
    created_at: str = ""
    updated_at: str = ""


class FeedbackProcessingRound(_StrictProcessingModel):
    """One immutable processing attempt for a stable feedback key."""

    id: int
    feedback_key: str
    round_number: int = Field(gt=0)
    batch_id: str
    status: Literal["processing", "resolved"]
    workbench_task_id: str = ""
    workbench_turn_id: str = ""
    attempt_id: int = 0
    agent_run_id: int = 0
    commit_sha: str = ""
    test_evidence: dict[str, object] = Field(default_factory=dict)
    restart_evidence: dict[str, object] = Field(default_factory=dict)
    health_evidence: dict[str, object] = Field(default_factory=dict)
    backlog_evidence: dict[str, object] = Field(default_factory=dict)
    scope_receipt: dict[str, object] = Field(default_factory=dict)
    receipt_version: Literal[1, 2] = 1
    note: str = ""
    started_at: str = ""
    resolved_at: str = ""
    reopened_at: str = ""
    reopen_reason: str = ""
    created_at: str = ""
    updated_at: str = ""


class FeedbackProcessingTransition(_StrictProcessingModel):
    """One append-only feedback processing status transition."""

    id: int
    feedback_key: str
    round_id: int = 0
    batch_id: str = ""
    from_status: Literal["", "pending", "processing", "resolved"]
    to_status: Literal["pending", "processing", "resolved"]
    reason: str = ""
    workbench_task_id: str = ""
    workbench_turn_id: str = ""
    created_at: str = ""


class FeedbackImportItem(_StrictProcessingModel):
    """The persisted, non-generative payload used to start a feedback turn.

    ``summary`` and ``references`` are supplied by the store projection.  The
    original feedback body is intentionally not part of this model: importing
    an item must not duplicate or reinterpret user text.
    """

    feedback_key: str
    summary: str = ""
    references: list[dict[str, str]] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return self.feedback_key

    @property
    def persisted_summary(self) -> str:
        return self.summary


class FeedbackIterationTargetSkillRevision(_StrictProcessingModel):
    skill_id: int = Field(gt=0)
    from_revision: int = Field(gt=0)
    to_revision: int = Field(gt=0)


class FeedbackIterationAcceptance(_StrictProcessingModel):
    scenario: str = Field(min_length=1)
    expected_behavior: str = Field(min_length=1)
    verification: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def require_nonblank_values(self) -> "FeedbackIterationAcceptance":
        if not self.scenario.strip() or not self.expected_behavior.strip() or any(
            not value.strip() for value in self.verification
        ):
            raise ValueError("feedback iteration acceptance must be nonblank")
        return self


class FeedbackIterationDecision(_StrictProcessingModel):
    """One explicit, persisted classification before an iteration changes work."""

    scope: Literal["skill_only", "runtime_config", "code", "mixed", "needs_human"]
    root_cause: str = Field(min_length=1)
    feedback_keys: list[str] = Field(min_length=1)
    source_references: list[str] = Field(min_length=1)
    target_skill_revisions: list[FeedbackIterationTargetSkillRevision] = Field(
        default_factory=list
    )
    target_runtime_config_id: int | None = Field(default=None, gt=0)
    why_not_code: str = Field(min_length=1)
    acceptance: FeedbackIterationAcceptance

    @model_validator(mode="after")
    def require_scope_references(self) -> "FeedbackIterationDecision":
        if (
            not self.root_cause.strip()
            or not self.why_not_code.strip()
            or any(not key.strip() for key in self.feedback_keys)
            or any(not reference.strip() for reference in self.source_references)
            or len(set(self.feedback_keys)) != len(self.feedback_keys)
        ):
            raise ValueError("feedback iteration decision references must be nonblank and unique")
        if self.scope == "skill_only" and not self.target_skill_revisions:
            raise ValueError("skill_only decision requires target Skill revisions")
        if len({target.skill_id for target in self.target_skill_revisions}) != len(
            self.target_skill_revisions
        ):
            raise ValueError("feedback iteration target Skill revisions must be unique")
        if self.scope == "runtime_config" and self.target_runtime_config_id is None:
            raise ValueError("runtime_config decision requires target runtime configuration")
        if self.scope == "mixed" and (
            not self.target_skill_revisions or self.target_runtime_config_id is None
        ):
            raise ValueError("mixed decision requires Skill revisions and runtime configuration")
        return self


class FeedbackIterationDecisionRecord(_StrictProcessingModel):
    id: int = Field(gt=0)
    batch_id: str = Field(min_length=1)
    feedback_keys: list[str] = Field(min_length=1)
    round_ids: list[int] = Field(min_length=1)
    workbench_task_id: str = Field(min_length=1)
    workbench_turn_id: str = Field(min_length=1)
    decision: FeedbackIterationDecision
    created_at: str = ""


class FeedbackProcessingAssociation(_StrictProcessingModel):
    """Exact durable association for one feedback item receipt."""

    workbench_task_id: str
    workbench_turn_id: str
    attempt_id: int
    agent_run_id: int


class ResolutionSkillRevision(_StrictProcessingModel):
    """The exact managed revision loaded to resolve a Skill-scoped decision."""

    skill_id: int = Field(gt=0)
    revision_id: int = Field(gt=0)
    sha256: str = Field(min_length=1)


class ResolutionEvidence(_StrictProcessingModel):
    """Evidence receipt required before a processing batch can be resolved."""

    commit_sha: str = ""
    test_evidence: dict[str, Any] = Field(
        default_factory=dict, validation_alias=AliasChoices("test_evidence", "tests")
    )
    restart_evidence: dict[str, Any] = Field(
        default_factory=dict, validation_alias=AliasChoices("restart_evidence", "restart")
    )
    health_evidence: dict[str, Any] = Field(
        default_factory=dict, validation_alias=AliasChoices("health_evidence", "health")
    )
    backlog_evidence: dict[str, Any]
    runtime_config_id: int = 0
    previous_runtime_config_id: int = 0
    load_receipt_id: int = 0
    skill_revisions: list[ResolutionSkillRevision] = Field(default_factory=list)
    # Optional association map used by API callers.  The store also verifies
    # the durable per-item associations, so callers cannot bypass that check.
    associations: dict[str, FeedbackProcessingAssociation] = Field(default_factory=dict)


class SkillOnlyResolutionEvidence(ResolutionEvidence):
    """Receipt for a decision repaired only through managed Skill revisions."""


class RuntimeConfigResolutionEvidence(ResolutionEvidence):
    """Receipt for a decision repaired by a runtime configuration change."""


class CodeResolutionEvidence(ResolutionEvidence):
    """Receipt for a decision repaired by a repository code change."""


class MixedResolutionEvidence(ResolutionEvidence):
    """Receipt for a decision requiring both code and runtime Skill changes."""


def project_feedback_status(source: object, processing: object | None = None) -> str:
    """Return the canonical status shared by API and HTML feedback projections.

    An explicit processing projection is authoritative for reopen rounds.
    Historical source completion fields are only a fallback when no processing
    item has been persisted.
    """

    if processing is not None:
        status = str(getattr(processing, "status", "pending") or "pending").strip().casefold()
        return status if status in {"pending", "processing", "resolved"} else "pending"
    if any(
        str(getattr(source, field, "") or "").strip()
        for field in ("resolved_at", "reviewer_feedback", "corrected_reply_text")
    ):
        return "resolved"
    status = str(
        getattr(processing, "status", getattr(source, "processing_status", "pending"))
        or "pending"
    ).strip().casefold()
    return status if status in {"pending", "processing", "resolved"} else "pending"


def persisted_feedback_summary(item: "UserFeedbackItem") -> str:
    """Return the first non-empty summary already persisted for an attempt.

    The order is deliberately fixed and contains no source feedback body or
    generated fallback: audit summary, reviewer feedback, corrected reviewer
    output, decision reason, then the recorded final or draft reply text.
    """

    for field in (
        "audit_summary",
        "reviewer_feedback",
        "corrected_reply_text",
        "codex_reason",
        "final_reply_text",
        "draft_reply_text",
    ):
        value = str(getattr(item, field, "") or "").strip()
        if value:
            return value
    return ""


def detail_references(item: "UserFeedbackItem") -> list[dict[str, str]]:
    """Build deterministic human labels and routes from actual persisted IDs."""

    refs: list[dict[str, str]] = []

    def add(label: str, route: str) -> None:
        refs.append({"label": label, "route": route})

    attempt_id = int(getattr(item, "attempt_id", 0) or 0)
    if attempt_id > 0:
        add(f"attempt#{attempt_id}", f"/attempts/{attempt_id}")

    run_id = int(getattr(item, "agent_run_id", 0) or 0)
    if run_id > 0:
        if attempt_id > 0:
            role = str(getattr(item, "attempt_role", "") or "").strip().casefold()
            if role in {"consumer", "audit"}:
                add(f"run#{run_id}", f"/attempts/{attempt_id}/execution/{role}")
            else:
                refs.append({"label": f"run#{run_id}", "route": ""})
        else:
            # There is no standalone public run route; retain the human label
            # only rather than manufacturing a route under another ID.
            refs.append({"label": f"run#{run_id}", "route": ""})

    session_id = str(getattr(item, "codex_session_id", "") or "").strip()
    if session_id:
        add(f"codex#{session_id}", f"/codex/{session_id}")

    project_id = int(getattr(item, "project_id", 0) or 0)
    if project_id > 0:
        add(f"task#{project_id}", f"/tasks/{project_id}")
    return refs


def build_feedback_start_message(
    batch_id: str,
    items: Sequence[FeedbackImportItem],
    *,
    runtime_context: dict[str, object] | None = None,
) -> str:
    """Render the deterministic startup instruction for one claimed batch."""

    lines = [
        f"Feedback processing batch: {batch_id}",
        f"Use repository Skill: {FEEDBACK_ITERATION_SKILL_PATH}",
        "Use the bounded brainstorming profile in that Skill only when a material uncertainty remains, then use the local feedback API to persist the decision before changing work.",
        "Process the persisted feedback items below; do not copy the full feedback body.",
    ]
    if runtime_context is not None:
        config_id = runtime_context.get("config_id")
        if type(config_id) is int and config_id > 0:
            lines.append(f"runtime config: {config_id}")
        revisions = runtime_context.get("loaded_revisions")
        if isinstance(revisions, list):
            for revision in revisions:
                if not isinstance(revision, dict):
                    continue
                skill_id = revision.get("skill_id")
                revision_number = revision.get("revision_number")
                sha256 = revision.get("sha256")
                if type(skill_id) is int and type(revision_number) is int and isinstance(sha256, str):
                    lines.append(
                        f"managed Skill {skill_id} revision {revision_number} sha256: {sha256}"
                    )
    for item in items:
        lines.append(f"- key: {item.feedback_key}")
        lines.append(f"  persisted summary: {item.summary}")
        for reference in item.references:
            label = reference.get("label", "").strip()
            route = reference.get("route", "").strip()
            if label and route:
                lines.append(f"  reference: {label} ({route})")
            elif label:
                lines.append(f"  reference: {label}")
    return "\n".join(lines)


_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def _all_test_exit_codes_zero(value: object) -> tuple[bool, bool]:
    if isinstance(value, dict):
        if "exit_code" in value:
            try:
                raw_exit_code = value["exit_code"]
                child_ok = (
                    isinstance(raw_exit_code, int)
                    and not isinstance(raw_exit_code, bool)
                    and raw_exit_code == 0
                )
            except (TypeError, ValueError):
                child_ok = False
            checks = [
                _all_test_exit_codes_zero(child)
                for key, child in value.items()
                if key != "exit_code"
            ]
            return child_ok and all(check[0] for check in checks), True
        checks = [_all_test_exit_codes_zero(child) for child in value.values()]
        return all(check[0] for check in checks), any(check[1] for check in checks)
    if isinstance(value, list):
        checks = [_all_test_exit_codes_zero(child) for child in value]
        return all(check[0] for check in checks), any(check[1] for check in checks)
    return True, False


def validate_resolution_evidence(
    evidence: ResolutionEvidence,
    *,
    commit_is_ancestor: bool,
) -> None:
    """Raise ``ValueError`` unless a complete successful receipt is present."""

    _validate_resolution_evidence_without_backlog(
        evidence,
        commit_is_ancestor=commit_is_ancestor,
    )
    backlog = evidence.backlog_evidence
    required_backlog_counts = {"processing", "failed", "retryable"}
    if not required_backlog_counts <= set(backlog):
        raise ValueError("resolution requires processing, failed, and retryable backlog counts")
    if any(
        not isinstance(backlog[name], int)
        or isinstance(backlog[name], bool)
        or backlog[name] != 0
        for name in required_backlog_counts
    ):
        raise ValueError("resolution requires zero processing, failed, and retryable backlog")


def validate_resolution_receipt(
    decision: FeedbackIterationDecision,
    evidence: ResolutionEvidence,
    *,
    commit_is_ancestor: bool,
) -> None:
    """Validate the receipt shape required by one persisted iteration scope.

    This validates only caller-supplied receipt structure. The store separately
    resolves config, revision, and load-receipt identifiers against immutable
    persisted runtime state before a batch is mutated.
    """

    if decision.scope == "needs_human":
        raise ValueError("needs_human feedback iteration decisions cannot resolve")
    _validate_success_evidence(evidence)
    if decision.scope == "code":
        _validate_code_resolution_evidence(evidence, commit_is_ancestor=commit_is_ancestor)
        return
    if decision.scope == "skill_only":
        _validate_skill_resolution_evidence(evidence)
        return
    if decision.scope == "runtime_config":
        _validate_runtime_config_resolution_evidence(evidence)
        return
    if decision.scope == "mixed":
        _validate_code_resolution_evidence(evidence, commit_is_ancestor=commit_is_ancestor)
        _validate_skill_resolution_evidence(evidence)
        _validate_runtime_config_resolution_evidence(evidence)
        return
    raise ValueError("feedback iteration decision scope is invalid")


def _validate_success_evidence(evidence: ResolutionEvidence) -> None:
    backlog = evidence.backlog_evidence
    required_backlog_counts = {"processing", "failed", "retryable"}
    if not required_backlog_counts <= set(backlog):
        raise ValueError("resolution requires processing, failed, and retryable backlog counts")
    if any(
        not isinstance(backlog[name], int)
        or isinstance(backlog[name], bool)
        or backlog[name] != 0
        for name in required_backlog_counts
    ):
        raise ValueError("resolution requires zero processing, failed, and retryable backlog")
    test_codes_ok, has_test_code = _all_test_exit_codes_zero(evidence.test_evidence)
    if not evidence.test_evidence or not has_test_code or not test_codes_ok:
        raise ValueError("resolution requires successful test evidence")
    _validate_restart_and_health_evidence(evidence)


def _validate_code_resolution_evidence(
    evidence: ResolutionEvidence, *, commit_is_ancestor: bool
) -> None:
    commit_sha = evidence.commit_sha.strip()
    if not _COMMIT_SHA_RE.fullmatch(commit_sha):
        raise ValueError("resolution requires a 40-character commit SHA")
    if not isinstance(commit_is_ancestor, bool) or not commit_is_ancestor:
        raise ValueError("resolution commit is not an ancestor of local main")


def _validate_skill_resolution_evidence(evidence: ResolutionEvidence) -> None:
    if evidence.runtime_config_id <= 0:
        raise ValueError("resolution requires an active runtime configuration")
    if evidence.load_receipt_id <= 0:
        raise ValueError("resolution requires a successful load receipt")
    if not evidence.skill_revisions:
        raise ValueError("resolution requires managed Skill revision evidence")
    if len({revision.skill_id for revision in evidence.skill_revisions}) != len(
        evidence.skill_revisions
    ):
        raise ValueError("resolution managed Skill revision evidence must be unique")


def _validate_runtime_config_resolution_evidence(evidence: ResolutionEvidence) -> None:
    if evidence.previous_runtime_config_id <= 0 or evidence.runtime_config_id <= 0:
        raise ValueError("resolution requires previous and target runtime configuration")
    if evidence.previous_runtime_config_id == evidence.runtime_config_id:
        raise ValueError("resolution requires distinct previous and target runtime configuration")
    if evidence.load_receipt_id <= 0:
        raise ValueError("resolution requires a successful load receipt")


def validate_legacy_resolution_evidence(
    evidence: ResolutionEvidence,
    *,
    commit_is_ancestor: bool,
) -> None:
    """Validate the complete pre-backlog receipt contract for version 1 rounds."""

    _validate_resolution_evidence_without_backlog(
        evidence,
        commit_is_ancestor=commit_is_ancestor,
    )


def _validate_resolution_evidence_without_backlog(
    evidence: ResolutionEvidence,
    *,
    commit_is_ancestor: bool,
) -> None:
    """Validate evidence fields shared by receipt versions 1 and 2."""

    commit_sha = evidence.commit_sha.strip()
    if not _COMMIT_SHA_RE.fullmatch(commit_sha):
        raise ValueError("resolution requires a 40-character commit SHA")
    if not isinstance(commit_is_ancestor, bool) or not commit_is_ancestor:
        raise ValueError("resolution commit is not an ancestor of local main")
    test_codes_ok, has_test_code = _all_test_exit_codes_zero(evidence.test_evidence)
    if not evidence.test_evidence or not has_test_code or not test_codes_ok:
        raise ValueError("resolution requires successful test evidence")

    _validate_restart_and_health_evidence(evidence)


def _validate_restart_and_health_evidence(evidence: ResolutionEvidence) -> None:
    """Validate the restart and local health receipts shared by every scope."""

    restart = evidence.restart_evidence
    label = str(
        restart.get("launchd_label")
        or restart.get("service_label")
        or restart.get("label")
        or ""
    ).strip()
    before = restart.get("before_pid", restart.get("pid_before"))
    after = restart.get("after_pid", restart.get("pid_after"))
    if label != "com.ceo-agent-service.main" or before in (None, "") or after in (None, ""):
        raise ValueError("resolution requires launchd label and before/after PIDs")
    try:
        if (
            not isinstance(before, int)
            or isinstance(before, bool)
            or not isinstance(after, int)
            or isinstance(after, bool)
            or before <= 0
            or after <= 0
            or before == after
        ):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("resolution requires distinct positive before/after PIDs") from exc

    health = evidence.health_evidence
    status = health.get("status_code", health.get("http_status", health.get("status")))
    success = health.get("ok")
    health_url = str(health.get("url") or health.get("endpoint") or "").strip()
    if not health_url:
        raise ValueError("resolution requires local health URL")
    parsed = urlsplit(health_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port != 8765
        or parsed.path != "/healthz"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("resolution health evidence must be local")
    if not isinstance(status, int) or isinstance(status, bool) or status != 200:
        raise ValueError("resolution requires successful local health evidence")
    if not isinstance(success, bool) or success is not True:
        raise ValueError("resolution requires successful local health evidence")
