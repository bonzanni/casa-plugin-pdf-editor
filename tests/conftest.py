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


@pytest.fixture
def casa_dirs(tmp_path, monkeypatch):
    """Temp outbox and handoff roots, with Casa's two env vars pointing at them."""
    import types
    outbox, handoff = tmp_path / "outbox", tmp_path / "handoff"
    outbox.mkdir()
    handoff.mkdir()
    monkeypatch.setenv("CASA_PLUGIN_OUTBOX_DIR", str(outbox))
    monkeypatch.setenv("CASA_HANDOFF_DIR", str(handoff))
    return types.SimpleNamespace(outbox=str(outbox), handoff=str(handoff))
