"""Unit tests for the PuLP optimizer."""
from __future__ import annotations

import pytest

from app.directive_engine import OptimizationConstraints
from app.models import BatteryInput, HourInput
from app.optimizer import OptimizationInfeasibleError, solve


def _hours(demands=None, solar=None, tariff=None) -> list[HourInput]:
    if demands is None:
        demands = [100]*24
    if solar is None:
        solar = [0]*24
    if tariff is None:
        tariff = [6]*24
    return [HourInput(hour=h, demand_kwh=demands[h], solar_kwh=solar[h],
                      tariff_bdt_per_kwh=tariff[h]) for h in range(24)]


def _battery(**kw) -> BatteryInput:
    base = dict(capacity_kwh=200, initial_energy_kwh=100, minimum_energy_kwh=40,
                max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50)
    base.update(kw)
    return BatteryInput(**base)


def _constraints(solar=None, min_reserve=40) -> OptimizationConstraints:
    if solar is None:
        solar = [0]*24
    return OptimizationConstraints(
        effective_solar_kwh=solar,
        min_battery_reserve_kwh=[min_reserve]*24,
        no_charge_hours=set(),
        no_discharge_hours=set(),
        max_grid_kwh=[float("inf")]*24,
    )


class TestBasicSolve:
    def test_zero_solar_uses_grid(self):
        out = solve(_hours(), _battery(), _constraints())
        assert len(out) == 24
        assert all(r.hour == i for i, r in enumerate(out))

    def test_end_of_day_neutrality(self):
        out = solve(_hours(), _battery(), _constraints())
        last = sorted(out, key=lambda r: r.hour)[-1]
        assert abs(last.battery_energy_after_kwh - 100) <= 0.01

    def test_battery_never_below_min(self):
        out = solve(_hours(), _battery(), _constraints())
        for r in out:
            assert r.battery_energy_after_kwh >= 40 - 0.01

    def test_battery_never_above_capacity(self):
        out = solve(_hours(), _battery(), _constraints())
        for r in out:
            assert r.battery_energy_after_kwh <= 200 + 0.01

    def test_energy_balance_per_hour(self):
        out = solve(_hours(demands=[150]*24), _battery(), _constraints())
        for r in out:
            ch = r.battery_kwh if r.battery_action == "charge" else 0
            dis = r.battery_kwh if r.battery_action == "discharge" else 0
            lhs = r.grid_kwh + r.solar_used_kwh + dis
            rhs = 150 + ch
            assert abs(lhs - rhs) <= 0.01


class TestWithDirectives:
    def test_no_charge_hours_battery_idle_or_discharge(self):
        c = OptimizationConstraints(
            effective_solar_kwh=[0]*24, min_battery_reserve_kwh=[40]*24,
            no_charge_hours={2, 3, 4}, no_discharge_hours=set(),
            max_grid_kwh=[float("inf")]*24,
        )
        out = solve(_hours(), _battery(), c)
        for r in out:
            if r.hour in {2, 3, 4}:
                assert r.battery_action in ("idle", "discharge")

    def test_max_grid_window(self):
        c = OptimizationConstraints(
            effective_solar_kwh=[0]*24, min_battery_reserve_kwh=[40]*24,
            no_charge_hours=set(), no_discharge_hours=set(),
            max_grid_kwh=[float("inf")]*18 + [100, 100, 100] + [float("inf")]*3,
        )
        out = solve(_hours(), _battery(), c)
        for r in out:
            if r.hour in {18, 19, 20}:
                assert r.grid_kwh <= 100.01

    def test_solar_reduction_bounds_solar(self):
        c = OptimizationConstraints(
            effective_solar_kwh=[0]*12 + [25] + [0]*11,
            min_battery_reserve_kwh=[40]*24,
            no_charge_hours=set(), no_discharge_hours=set(),
            max_grid_kwh=[float("inf")]*24,
        )
        out = solve(_hours(solar=[0]*12 + [100] + [0]*11), _battery(), c)
        for r in out:
            if r.hour == 12:
                assert r.solar_used_kwh <= 25.01
