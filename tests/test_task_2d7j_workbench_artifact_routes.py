import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_task_2d7j_workbench_artifact_routes.py"
_SPEC = importlib.util.spec_from_file_location("task_2d7j_artifact_routes", _SCRIPT)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_predecessor_pin_accepts_exact_store_and_api_hashes():
    actual = {"store.py": "a" * 64, "api.py": "b" * 64}

    _MODULE._verify_predecessor_hashes(actual, actual["store.py"], actual["api.py"])


def test_predecessor_pin_mismatch_stops_before_output_data_mutation(tmp_path: Path):
    predecessor = tmp_path / "predecessor"
    source = predecessor / "app" / "workbench"
    source.mkdir(parents=True)
    store_bytes = b"pinned store source"
    api_bytes = b"pinned api source"
    store_file, api_file = source / "store.py", source / "api.py"
    store_file.write_bytes(store_bytes)
    api_file.write_bytes(api_bytes)
    expected_store = hashlib.sha256(b"different store pin").hexdigest()
    expected_api = hashlib.sha256(api_bytes).hexdigest()
    output_root = tmp_path / "output"
    output_root.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--predecessor-root",
            str(predecessor),
            "--expected-store-sha256",
            expected_store,
            "--expected-api-sha256",
            expected_api,
            "--output-root",
            str(output_root),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "predecessor source hash pin mismatch" in result.stderr
    assert "store.py" in result.stderr
    assert list(output_root.iterdir()) == []
    assert store_file.read_bytes() == store_bytes
    assert api_file.read_bytes() == api_bytes
