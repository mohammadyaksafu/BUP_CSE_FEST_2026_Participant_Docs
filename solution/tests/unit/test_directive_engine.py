"""Unit tests for the directive engine."""
from __future__ import annotations

from app.directive_engine import build_constraints
from app.models import DirectiveInterpretation


def _di(idx, dtype, applies=True, adj=None):
    return DirectiveInterpretation(
        note_index=idx, applies=applies, directive_type=dtype,
        structured_adjustment=adj, explanation="x",
    )


class TestBuildConstraints:
    def test_no_directives_returns_baseline(self):
        c = build_constraints([], base_solar_kwh=[10]*24, base_min_battery_kwh=40)
        assert c.effective_solar_kwh == [10]*24
        assert c.min_battery_reserve_kwh == [40]*24
        assert c.no_charge_hours == set()
        assert c.no_discharge_hours == set()
        assert all(m == float("inf") for m in c.max_grid_kwh)

    def test_solar_reduction_overrides_baseline(self):
        di = _di(0, "solar_reduction", adj={"hours": [12, 13], "factor": 0.25})
        c = build_constraints([di], base_solar_kwh=[100]*24, base_min_battery_kwh=40)
        assert c.effective_solar_kwh[12] == 25
        assert c.effective_solar_kwh[13] == 25
        assert c.effective_solar_kwh[0] == 100  # untouched

    def test_min_reserve_raises_minimum(self):
        di = _di(0, "minimum_battery_reserve", adj={"hours": [18], "minimum_energy_kwh": 100})
        c = build_constraints([di], base_solar_kwh=[0]*24, base_min_battery_kwh=40)
        assert c.min_battery_reserve_kwh[18] == 100
        assert c.min_battery_reserve_kwh[0] == 40

    def test_min_reserve_lower_than_baseline_keeps_baseline(self):
        di = _di(0, "minimum_battery_reserve", adj={"hours": [18], "minimum_energy_kwh": 10})
        c = build_constraints([di], base_solar_kwh=[0]*24, base_min_battery_kwh=40)
        assert c.min_battery_reserve_kwh[18] == 40

    def test_no_charge_window(self):
        di = _di(0, "no_charge_window", adj={"hours": [2, 3, 4]})
        c = build_constraints([di], base_solar_kwh=[0]*24, base_min_battery_kwh=40)
        assert c.no_charge_hours == {2, 3, 4}

    def test_max_grid_window(self):
        di = _di(0, "max_grid_window", adj={"hours": [18, 19], "max_grid_kwh": 100})
        c = build_constraints([di], base_solar_kwh=[0]*24, base_min_battery_kwh=40)
        assert c.max_grid_kwh[18] == 100
        assert c.max_grid_kwh[19] == 100
        assert c.max_grid_kwh[0] == float("inf")

    def test_no_op_is_ignored(self):
        di = _di(0, "no_op", applies=False)
        c = build_constraints([di], base_solar_kwh=[100]*24, base_min_battery_kwh=40)
        assert c.effective_solar_kwh[0] == 100

    def test_multiple_directives_compose(self):
        di_a = _di(0, "solar_reduction", adj={"hours": [12], "factor": 0.5})
        di_b = _di(1, "no_charge_window", adj={"hours": [12]})
        c = build_constraints([di_a, di_b], base_solar_kwh=[100]*24, base_min_battery_kwh=40)
        assert c.effective_solar_kwh[12] == 50
        assert c.no_charge_hours == {12}
