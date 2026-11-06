"""Small release-gate coverage for audited OA manual-rerun projection."""

import pytest

from tests.test_store import (
    test_manual_oa_rerun_reconciles_only_audited_no_action_projection as _exercise_projection,
)


@pytest.mark.parametrize("audit_approved", [True, False])
def test_rerun_projection_requires_current_audit_approval(
    tmp_path, audit_approved: bool,
) -> None:
    _exercise_projection(tmp_path, audit_approved)
