"""LIVE TEST: hits the real Puku API. Skipped unless PUKU_API_KEY is set.

This is the only test that exercises the actual LLM path. Run it before
submission to confirm the prompt + tool-call schema still works against the
real provider.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


PUBLIC_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
)


@pytest.mark.skipif(not os.getenv("PUKU_API_KEY"), reason="PUKU_API_KEY not set")
@pytest.mark.parametrize("case_index", [0, 1, 5, 9])  # spot-check 4 diverse cases
def test_live_puku_call(case_index):
    with PUBLIC_PATH.open(encoding="utf-8") as f:
        case = json.load(f)["cases"][case_index]

    r = client.post("/optimize-energy", json=case["input"])
    assert r.status_code == 200, r.text
    j = r.json()

    # Echo
    assert j["scenario_id"] == case["input"]["scenario_id"]

    # Interpretation must include one entry per note
    assert len(j["directive_interpretation"]) == len(case["input"]["operator_notes"])

    # Directive type for each note must be one of the 6 allowed
    allowed = {"solar_reduction", "minimum_battery_reserve", "no_charge_window",
               "no_discharge_window", "max_grid_window", "no_op"}
    for d in j["directive_interpretation"]:
        assert d["directive_type"] in allowed
        if d["directive_type"] != "no_op":
            assert d["applies"] is True
            assert isinstance(d["structured_adjustment"], dict)
            assert "hours" in d["structured_adjustment"]
            assert all(0 <= h <= 23 for h in d["structured_adjustment"]["hours"])
        else:
            assert d["applies"] is False

    # Neutrality
    init = case["input"]["battery"]["initial_energy_kwh"]
    final = j["hourly_plan"][23]["battery_energy_after_kwh"]
    assert abs(final - init) <= 0.1

    # KPI self-consistency
    hp = j["hourly_plan"]
    assert abs(sum(e["grid_kwh"] for e in hp) - j["total_grid_kwh"]) <= 0.1
    assert abs(max(e["grid_kwh"] for e in hp) - j["peak_grid_kwh"]) <= 0.1