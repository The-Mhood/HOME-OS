import os
import tempfile
from pathlib import Path
import pytest


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "homeos_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir
