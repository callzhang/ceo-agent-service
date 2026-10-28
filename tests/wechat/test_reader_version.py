import shutil
import sys
from pathlib import Path

from app.wechat import reader_version


PACKAGE = Path(reader_version.__file__).resolve().parent


def test_version_is_a_digest_of_exactly_the_reader_modules(tmp_path):
    copy = tmp_path / "wechat"
    copy.mkdir()
    for name in reader_version.READER_SOURCE_MODULES:
        shutil.copy(PACKAGE / name, copy / name)
    assert reader_version.expected_reader_version(copy) == reader_version.expected_reader_version()

    (copy / "schema.py").write_bytes((copy / "schema.py").read_bytes() + b"\n# changed\n")
    assert reader_version.expected_reader_version(copy) != reader_version.expected_reader_version()

    # Modules the Reader does not bundle (app.config, the service) do not move it.
    (copy / "service.py").write_text("print('not part of the reader')\n")
    changed = reader_version.expected_reader_version(copy)
    (copy / "service.py").write_text("print('still not')\n")
    assert reader_version.expected_reader_version(copy) == changed


def test_every_listed_module_exists_and_the_helper_imports_are_covered():
    for name in reader_version.READER_SOURCE_MODULES:
        assert (PACKAGE / name).is_file(), name
    helper = (PACKAGE / "reader_helper.py").read_text()
    for line in helper.splitlines():
        if line.startswith("from app.wechat."):
            module = line.split()[1].rsplit(".", 1)[1] + ".py"
            assert module in reader_version.READER_SOURCE_MODULES, module


def test_running_version_outside_a_bundle_is_the_source_digest(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert reader_version.running_reader_version() == reader_version.expected_reader_version()


def test_running_version_inside_a_bundle_reads_the_build_stamp(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert reader_version.running_reader_version() == reader_version.UNSTAMPED
    (tmp_path / reader_version.STAMP_FILE).write_text("abc123abc123abc1\n")
    assert reader_version.running_reader_version() == "abc123abc123abc1"


def test_module_prints_the_expected_version_for_the_build_script(capsys):
    assert reader_version.main() == 0
    assert capsys.readouterr().out.strip() == reader_version.expected_reader_version()
