"""The gate's fixed file list must not repeatedly enumerate the same directory."""

from app.deploy import TEST_GATE_ARGS

pytest_plugins = ["pytester"]
PLUGIN = "tests.support.gate_collection"


def _prepare(pytester):
    pytester.makeconftest('''
import pytest
scans = {}

@pytest.hookimpl(wrapper=True, trylast=True)
def pytest_collect_directory(path, parent):
    collector = yield
    if collector is not None:
        original = collector.collect
        def counted():
            scans[str(path)] = scans.get(str(path), 0) + 1
            return original()
        collector.collect = counted
    return collector

def pytest_sessionfinish(session):
    print("MAX_DIRECTORY_ENUMERATIONS=" + str(max(scans.values(), default=0)))
''')
    pytester.makepyfile(
        test_first="def test_first(): assert True",
        test_second="def test_second(): assert True",
        test_third="def test_third(): assert True",
        test_unselected="def test_unselected(): assert False, 'not in manifest'",
    )
    directory = pytester.path / "checks"
    directory.mkdir()
    files = {}
    for name in ("test_first", "test_second", "test_third", "test_unselected"):
        files[name] = directory / f"{name}.py"
        (pytester.path / f"{name}.py").rename(files[name])
    return files


def _selected(files):
    return [str(files[name]) for name in ("test_first", "test_second", "test_third")]


def test_gate_loads_directory_collection_plugin():
    assert ("-p", PLUGIN) in tuple(zip(TEST_GATE_ARGS, TEST_GATE_ARGS[1:]))


def test_gate_enumerates_directory_once_and_runs_only_requested_files(pytester):
    files = _prepare(pytester)
    baseline = pytester.runpytest("-q", *_selected(files))
    baseline.assert_outcomes(passed=3)
    assert "MAX_DIRECTORY_ENUMERATIONS=3" in baseline.stdout.str()

    cached = pytester.runpytest("-q", "-p", PLUGIN, *_selected(files))
    cached.assert_outcomes(passed=3)
    assert "MAX_DIRECTORY_ENUMERATIONS=1" in cached.stdout.str()


def test_gate_keeps_exact_node_ids_parameterization_and_package_fixtures(pytester):
    package = pytester.path / "checks"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "conftest.py").write_text('''
import pytest
@pytest.fixture(scope="package")
def marker():
    return "package fixture"
''', encoding="utf-8")
    for name in ("first", "second"):
        (package / f"test_{name}.py").write_text('''
import pytest
@pytest.mark.parametrize("value", [1, 2])
def test_value(marker, value):
    assert marker == "package fixture"
    assert value in (1, 2)
''', encoding="utf-8")
    paths = [str(package / "test_first.py"), str(package / "test_second.py")]
    baseline = pytester.runpytest("--collect-only", "-q", *paths)
    cached = pytester.runpytest("--collect-only", "-q", "-p", PLUGIN, *paths)
    baseline_ids = [line for line in baseline.stdout.lines if "::test_value[" in line]
    cached_ids = [line for line in cached.stdout.lines if "::test_value[" in line]
    assert len(baseline_ids) == 4
    assert cached_ids == baseline_ids
    pytester.runpytest("-q", "-p", PLUGIN, *paths).assert_outcomes(passed=4)


def test_gate_does_not_hide_collection_errors(pytester):
    pytester.makepyfile(
        test_good="def test_good(): assert True",
        test_broken="raise RuntimeError('original collection error')",
    )
    result = pytester.runpytest("-q", "-p", PLUGIN,
                              str(pytester.path / "test_good.py"),
                              str(pytester.path / "test_broken.py"))
    result.assert_outcomes(errors=1)
    assert "original collection error" in result.stdout.str()


def test_gate_does_not_hide_test_failures(pytester):
    path = pytester.makepyfile("def test_failure(): assert False, 'original assertion failure'")
    result = pytester.runpytest("-q", "-p", PLUGIN, str(path))
    result.assert_outcomes(failed=1)
    assert "original assertion failure" in result.stdout.str()


def test_gate_removes_passed_temporary_files_but_keeps_failed_evidence(pytester):
    path = pytester.makepyfile("""
def test_passed(tmp_path):
    (tmp_path / 'passed-evidence').write_text('passed')

def test_failed(tmp_path):
    (tmp_path / 'failed-evidence').write_text('failed')
    assert False, 'original failure stays a failure'
""")
    baseline_root = pytester.path / "baseline-temp"
    baseline = pytester.runpytest("-q", "--basetemp", str(baseline_root), str(path))
    baseline.assert_outcomes(passed=1, failed=1)
    assert (baseline_root / "test_passed0" / "passed-evidence").is_file()
    assert (baseline_root / "test_failed0" / "failed-evidence").is_file()

    gate_root = pytester.path / "gate-temp"
    actual = pytester.runpytest(*TEST_GATE_ARGS[2:], "--basetemp", str(gate_root), str(path))
    actual.assert_outcomes(passed=1, failed=1)
    assert "original failure stays a failure" in actual.stdout.str()
    assert not (gate_root / "test_passed0").exists()
    assert (gate_root / "test_failed0" / "failed-evidence").read_text() == "failed"
