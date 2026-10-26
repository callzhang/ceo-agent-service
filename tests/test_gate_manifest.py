"""The deploy gate runs exactly the files in tests/gate_files.txt."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "gate_files.txt"


def _listed() -> list[str]:
    return MANIFEST.read_text().split()


def test_manifest_lists_existing_test_files_once_each():
    listed = _listed()
    assert listed, "the gate suite must not be empty"
    assert len(listed) == len(set(listed)), "a file is listed twice"
    for name in listed:
        path = ROOT / name
        assert path.is_file(), f"{name} is listed but does not exist"
        assert path.name.startswith("test_") and path.suffix == ".py", name


def test_manifest_excludes_opt_in_suites():
    assert not [name for name in _listed() if name.startswith(("tests/browser/", "tests/e2e/"))]


def test_manifest_contains_the_tests_of_the_gate_itself():
    listed = set(_listed())
    assert {
        "tests/test_memory_limit.py",
        "tests/test_deploy_test_gate.py",
        "tests/test_gate_manifest.py",
    } <= listed
