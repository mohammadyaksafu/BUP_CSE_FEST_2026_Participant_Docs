"""Shared pytest fixtures for the GridWise solution test suite."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure solution/ is on sys.path so `import app.*` works from any cwd.
SOLUTION_ROOT = Path(__file__).resolve().parent
if str(SOLUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLUTION_ROOT))

SAMPLES_PATH = (
    SOLUTION_ROOT.parent
    / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
)


@pytest.fixture(scope="session")
def sample_cases() -> dict:
    """Load the 10 public sample cases. Fails loud if the file is missing."""
    with SAMPLES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def sample_cases_iter(sample_cases: dict) -> list[dict]:
    """Per-test deep-copy of the public sample cases — safe to mutate
    inside an individual test. Loading the underlying JSON happens once at
    session scope via the `sample_cases` fixture, then each test gets its
    own private copy of the cases list.
    """
    import copy

    return copy.deepcopy(sample_cases["cases"])


@pytest.fixture()
def client():
    """FastAPI test client bound to the solution's app."""
    # Import lazily so test collection doesn't require the env at collect time.
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture()
def no_puku_env(monkeypatch):
    """Make sure tests don't accidentally call Puku during offline runs."""
    monkeypatch.setenv("PUKU_API_KEY", "")
    # Force re-import of config so the cleared env var is honored.
    from app import config as config_mod

    config_mod.PUKU_API_KEY = ""
    yield


@pytest.fixture()
def patched_llm(monkeypatch):
    """Context manager helper to monkeypatch `interpret_notes`. Yields a
    setter function: call `patched_llm.set_fn(your_fn)` to override."""
    from app import llm_interpreter

    state = {"fn": None}
    monkeypatch.setattr(llm_interpreter, "interpret_notes", lambda *a, **kw: state["fn"](*a, **kw))
    return state


@pytest.fixture()
def zero_puku_seed(monkeypatch):
    """Force determinism for any randomness in the pipeline."""
    import random

    random.seed(0)
    yield
