import os
from collections.abc import Mapping
from pathlib import Path


MINIMUM_PYTHON = (3, 12)


def central_conda_prefix(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = values.get("CEO_CONDA_PREFIX", "").strip()
    if configured:
        return Path(configured).expanduser()
    home = Path(values.get("HOME", str(Path.home()))).expanduser()
    return home / "miniforge3"


def central_python(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = values.get("CEO_PYTHON", "").strip()
    if configured:
        return Path(configured).expanduser()
    return central_conda_prefix(values) / "bin" / "python"
