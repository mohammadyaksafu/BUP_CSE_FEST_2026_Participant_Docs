"""DETERMINISM TEST: identical input must produce identical output across
runs. Critical for a judge that re-derives KPIs from hourly_plan."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestDeterminism:
    def test_same_input_same_response(self, sample_cases_iter):
        body = sample_cases_iter[0]["input"]
        a = client.post("/optimize-energy", json=body).json()
        b = client.post("/optimize-energy", json=body).json()
        for k in ("total_grid_kwh", "total_cost_bdt", "peak_grid_kwh"):
            assert abs(a[k] - b[k]) <= 0.01
        assert a["hourly_plan"] == b["hourly_plan"]

    @pytest.mark.parametrize("idx", list(range(10)))
    def test_deterministic_across_all_samples(self, sample_cases_iter, idx):
        body = sample_cases_iter[idx]["input"]
        a = client.post("/optimize-energy", json=body).json()
        b = client.post("/optimize-energy", json=body).json()
        assert a["hourly_plan"] == b["hourly_plan"]