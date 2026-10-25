"""Reject a mistaken source path before the native acceptance writes any data."""
import pytest

from scripts.validate_a5_native_selection import run


@pytest.mark.parametrize("outside", [False, True])
def test_native_selection_requires_distinct_owned_database_copies(tmp_path, outside):
    baseline = tmp_path / "baseline.sqlite3"
    baseline.write_bytes(b"original baseline sentinel")
    candidate = tmp_path.parent / "outside.sqlite3" if outside else baseline
    with pytest.raises(ValueError, match="distinct database and baseline copies"):
        run(tmp_path, database=candidate, baseline=baseline)
    assert baseline.read_bytes() == b"original baseline sentinel"
    if outside:
        assert not candidate.exists()
