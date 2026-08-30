import inspect

import app.email_classifier_contracts as contracts


def test_email_contract_has_no_generic_all_actions_through_audit_adapter():
    source = inspect.getsource(contracts)

    assert not hasattr(contracts, "build_email_audit_proposal")
    assert "ConsumerProposal" not in source
    assert "ProposedAction" not in source
    assert "ProposalFact" not in source
