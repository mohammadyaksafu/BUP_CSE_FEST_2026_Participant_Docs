"""WORST-CASE / ADVERSARIAL TESTS: malformed, boundary, and contradictory
inputs that try to break the system."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _make(notes, demand=100, solar=0, tariff=6, **batt):
    hours = [{"hour": h, "demand_kwh": demand, "solar_kwh": solar,
              "tariff_bdt_per_kwh": tariff} for h in range(24)]
    battery = {
        "capacity_kwh": 200, "initial_energy_kwh": 100, "minimum_energy_kwh": 40,
        "max_charge_kwh_per_hour": 50, "max_discharge_kwh_per_hour": 50,
        **batt,
    }
    return {"scenario_id": "WORST", "operator_notes": notes,
            "hours": hours, "battery": battery}


class TestWorstCase:
    def test_zero_solar_high_tariff_still_returns_valid(self):
        body = _make(["Sports deadline moved"], solar=0, tariff=50)
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200
        # Must still satisfy neutrality and balance
        hp = r.json()["hourly_plan"]
        assert len(hp) == 24

    def test_unicode_note_does_not_crash(self):
        body = _make(["太阳能面板 维护 从 13:00 到 15:00 减少 80%",
                      "🔋 battery must not discharge from 6 PM to 8 PM"])
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200

    def test_gibberish_note_classifies_as_noop(self):
        body = _make(["asdf qwerty zxcvbnm !!! ???",
                      "lorem ipsum dolor sit amet"])
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200
        for d in r.json()["directive_interpretation"]:
            assert d["directive_type"] == "no_op"

    def test_contradictory_notes_dont_crash(self):
        # contradictory: keep reserve high but also can't discharge
        body = _make([
            "Battery must not discharge from 6 PM to 9 PM",
            "Keep at least 90% of battery capacity from 6 PM to 9 PM",
        ])
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200
        # Should still produce a valid plan
        hp = r.json()["hourly_plan"]
        assert len(hp) == 24

    def test_max_constraint_with_zero_rate_limits(self):
        body = _make(["Sports deadline moved"], capacity_kwh=10,
                     initial_energy_kwh=10, minimum_energy_kwh=0,
                     max_charge_kwh_per_hour=0.0, max_discharge_kwh_per_hour=0.0)
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200

    def test_large_demand_does_not_crash(self):
        body = _make(["Sports deadline moved"], demand=1_000_000)
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200

    def test_zero_demand_zero_solar(self):
        body = _make(["Sports deadline moved"], demand=0, solar=0)
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200
        assert r.json()["total_grid_kwh"] == 0.0

    def test_solar_exactly_meets_demand(self):
        body = _make(["Sports deadline moved"], demand=100, solar=100)
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200
        assert r.json()["total_grid_kwh"] == 0.0