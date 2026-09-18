import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLE_PATH = ROOT.parent / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: exercises the real configured LLM provider (consumes API quota)")


@pytest.fixture(scope="session")
def sample_cases():
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))["cases"]


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    # raise_server_exceptions=False mirrors real deployed behavior (uvicorn):
    # an unhandled exception should surface as the app's own 500 JSON response,
    # not re-raise into the test for debugging.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def stub_llm(monkeypatch):
    """Replaces the LLM call with a caller-supplied function, so black-box/API/
    robustness tests can drive deterministic behavior without spending real LLM
    quota. Usage: stub_llm(lambda notes, battery_ctx: [...raw interpretations...])
    """

    def _apply(fn):
        monkeypatch.setattr("app.main.interpret_notes", fn)

    return _apply


@pytest.fixture()
def failing_llm(monkeypatch):
    """Makes the LLM call always raise LLMInterpreterError, to exercise the
    provider-failure safe-fallback path without needing a real outage.
    """
    from app.llm_interpreter import LLMInterpreterError

    def _boom(*args, **kwargs):
        raise LLMInterpreterError("simulated provider outage")

    monkeypatch.setattr("app.main.interpret_notes", _boom)
