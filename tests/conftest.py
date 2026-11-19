import os
import sys

_SERVER = os.path.join(os.path.dirname(__file__), "..", "server")
# `server/` for the plugin's own modules; `server/vendor/` for its vendored
# dependencies (mcp, pymupdf). Without vendor the suite
# only imports when PYTHONPATH happens to be set externally.
sys.path.insert(0, _SERVER)
sys.path.insert(1, os.path.join(_SERVER, "vendor"))

import pytest


@pytest.fixture(autouse=True)
def _restore_handoff_env(monkeypatch):
    # `fixtures.in_handoff` points CASA_HANDOFF_DIR at a temp root; restore it
    # after every test so no test sees another test's root.
    monkeypatch.delenv("CASA_HANDOFF_DIR", raising=False)


@pytest.fixture
def store(tmp_path):
    from documents import Store
    return Store(str(tmp_path / "data"))
