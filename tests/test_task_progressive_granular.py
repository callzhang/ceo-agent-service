from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "evals/trajectory_optimization/task-progressive-granular"


def test_project_reference_partition_is_lossless():
    current = (ROOT / "ci/shared-skills/ceo-work-tracking/SKILL.md").read_bytes()
    details = current[current.index(b"## Project context first"):]
    names = ("project-registration", "project-context", "project-links", "suggestions", "task-updates", "assignment", "assessment", "evidence", "service")
    actual = b"".join((SKILL / "references" / f"{name}.md").read_bytes() for name in names)
    assert actual == details


def test_entry_links_resolve_without_preloading_details():
    entry = (SKILL / "SKILL.md").read_text()
    links = re.findall(r"\]\((references/[^)]+)\)", entry)
    assert len(links) == 10
    assert len(links) == len(set(links))
    for link in links:
        assert (SKILL / link).is_file()
    assert "6. Original human work" not in entry
    assert "owner_kind" not in entry
