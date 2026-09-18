"""TOTAL CONSISTENCY TEST: across many randomized valid scenarios, the
reported totals must always equal values recomputed from hourly_plan."""
from __future__ import annotations

import random

import pytest

from app.models import BatteryInput, HourInput, OptimizeEnergyRequest
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def _make(seed: int) -> dict:
    rng = random.Random(seed)
    hours = []
    for h in range(24):
        hours.append({
            "hour": h,
            "demand_kwh": rng.uniform(50, 300),
            "solar_kwh": rng.uniform(0, 150) if 6 <= h <= 18 else 0,
            "tariff_bdt_per_kwh": rng.uniform(4, 30),
        })
    return {
        "scenario_id": f"RAND-{seed}",
        "operator_notes": [
            "Sports office moved registration deadline",  # distractor
            rng.choice([
                "Wash panels 1 PM to 3 PM, 80% reduction",
                "Charger maintenance 2 AM to 5 AM",
                "Battery must not discharge from 6 PM to 8 PM",
                "Keep at least 50% of battery capacity from 6 PM to 9 PM",
                "Grid import capped at 150 kWh from 6 PM to 9 PM",
            ]),
        ],
        "hours": hours,
        "battery": {
            "capacity_kwh": rng.uniform(100, 300),
            "initial_energy_kwh": rng.uniform(50, 200),
            "minimum_energy_kwh": rng.uniform(10, 50),
            "max_charge_kwh_per_hour": rng.uniform(20, 80),
            "max_discharge_kwh_per_hour": rng.uniform(20, 80),
        },
    }


class TestTotalConsistency:
    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 42, 99, 123])
    def test_totals_match_hourly_plan(self, seed):
        body = _make(seed)
        r = client.post("/optimize-energy", json=body)
        # Some randomized scenarios violate Pydantic invariants (e.g. random
        # initial_energy > capacity) — these surface as 400. Some are
        # physically infeasible under the directives — those surface as 422.
        # We only validate the totals when the optimizer actually returns a
        # plan, which is the contract this test is exercising.
        if r.status_code in (400, 422):
            # Either rejection mode is acceptable behavior for a randomized
            # generator whose RNG chose a slightly inconsistent state.
            assert r.json().get("error") in ("invalid_request", "infeasible_scenario")
            return
        assert r.status_code == 200, r.text
        j = r.json()
        hp = j["hourly_plan"]
        gsum = round(sum(e["grid_kwh"] for e in hp), 2)
        pmax = round(max(e["grid_kwh"] for e in hp), 2)
        tariff = {e["hour"]: e["tariff_bdt_per_kwh"] for e in body["hours"]}
        cost = round(sum(e["grid_kwh"] * tariff[e["hour"]] for e in hp), 2)
        assert abs(gsum - round(j["total_grid_kwh"], 2)) <= 0.02
        assert abs(pmax - round(j["peak_grid_kwh"], 2)) <= 0.02
        assert abs(cost - round(j["total_cost_bdt"], 2)) <= 0.02