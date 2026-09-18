"""BEST-CASE TESTS: ideal scenarios where the answer is trivial or obvious.

A correct system should handle these without surprises.
"""
from __future__ import annotations

from app.models import BatteryInput, HourInput, OptimizeEnergyRequest
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def _make(scenario_id, notes, demand=100, solar=0, tariff=6, **batt):
    hours = [
        HourInput(hour=h, demand_kwh=demand, solar_kwh=solar, tariff_bdt_per_kwh=tariff).model_dump()
        for h in range(24)
    ]
    battery = {
        "capacity_kwh": 200, "initial_energy_kwh": 100, "minimum_energy_kwh": 40,
        "max_charge_kwh_per_hour": 50, "max_discharge_kwh_per_hour": 50,
        **batt,
    }
    return {"scenario_id": scenario_id, "operator_notes": notes,
            "hours": hours, "battery": battery}


class TestBestCase:
    def test_zero_demand_all_day(self):
        body = _make("ZERO", ["Sports deadline moved"], demand=0)
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200
        j = r.json()
        assert j["total_grid_kwh"] == 0.0
        assert j["total_cost_bdt"] == 0.0

    def test_flat_tariff_zero_solar(self):
        body = _make("FLAT", ["Sports deadline moved"], demand=100, tariff=5)
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200
        # total cost = 100 * 24 * 5 = 12000 BDT (no battery flexibility helps)
        assert abs(r.json()["total_cost_bdt"] - 12000.0) <= 1.0

    def test_no_notes_at_all(self):
        body = _make("NONOTE", ["HR announced a new holiday"], demand=100, solar=0)
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200
        j = r.json()
        # All notes are no_op
        for d in j["directive_interpretation"]:
            assert d["directive_type"] == "no_op"
            assert d["applies"] is False

    def test_zero_capacity_battery_is_idle(self):
        body = _make("NOBATT", ["Sports deadline moved"], demand=100,
                     capacity_kwh=0.01, initial_energy_kwh=0.01,
                     minimum_energy_kwh=0.0,
                     max_charge_kwh_per_hour=0.0, max_discharge_kwh_per_hour=0.0)
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200
        j = r.json()
        # With zero rate limits, no charge/discharge ever happens
        for entry in j["hourly_plan"]:
            assert entry["battery_action"] == "idle"
            assert entry["battery_kwh"] == 0.0