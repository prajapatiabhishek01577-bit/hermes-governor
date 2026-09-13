import os
import tempfile
from pathlib import Path

# Isolate before collection imports, not only before test functions.
_collection_home = tempfile.TemporaryDirectory(prefix="hermes-governor-tests-")
os.environ["HERMES_HOME"] = _collection_home.name
for key in list(os.environ):
    if key.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_CREDENTIALS")):
        os.environ.pop(key, None)

import pytest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    yield home
