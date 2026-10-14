import pytest

from app.task_agent import _validate_current_project_citation
from app.task_semantic_models import SourceCitation
from app.task_models import WorkItem


def test_current_citation_error_reports_known_expected_and_received_reference():
    work = WorkItem.model_validate({"summary": "Exact source fact", "source": {"type": "local_file", "ref": 'source:original/"key"'}, "context": {"source_conversation_kind": "file"}})
    citation = SourceCitation(signal_id=None, source_ref="current:document-version", source_excerpt=work.summary)
    with pytest.raises(ValueError) as error:
        _validate_current_project_citation(citation, work_item=work)
    assert f"expected={work.source.ref!r}" in str(error.value)
    assert f"received={citation.source_ref!r}" in str(error.value)
    _validate_current_project_citation(citation.model_copy(update={"source_ref": work.source.ref}), work_item=work)
