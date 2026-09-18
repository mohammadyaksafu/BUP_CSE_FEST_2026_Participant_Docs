"""Unit tests for the schedule validator (every branch)."""
from __future__ import annotations

import pytest

from app.directive_engine import OptimizationConstraints
from app.models import BatteryInput, HourInput
from app.optimizer import HourResult
from app.schedule_validator import ScheduleInvalidError, validate_schedule


def _hours() -> list[HourInput]:
    return [HourInput(hour=h, demand_kwh=100, solar_kwh=0, tariff_bdt_per_kwh=6)
            for h in range(24)]


def _battery() -> BatteryInput:
    return BatteryInput(capacity_kwh=200, initial_energy_kwh=100, minimum_energy_kwh=40,
                        max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50)


def _constraints(no_charge=set(), no_discharge=set(),
                 max_grid=None, min_reserve=40) -> OptimizationConstraints:
    if max_grid is None:
        max_grid = [float("inf")]*24
    return OptimizationConstraints(
        effective_solar_kwh=[0]*24, min_battery_reserve_kwh=[min_reserve]*24,
        no_charge_hours=no_charge, no_discharge_hours=no_discharge,
        max_grid_kwh=max_grid,
    )


def _result(h, grid, solar, action, magnitude, energy_after):
    return HourResult(hour=h, grid_kwh=grid, solar_used_kwh=solar,
                      battery_action=action, battery_kwh=magnitude,
                      battery_energy_after_kwh=energy_after)


class TestShapeChecks:
    def test_wrong_length_rejected(self):
        with pytest.raises(ScheduleInvalidError):
            validate_schedule(_hours(), _battery(), _constraints(),
                              [_result(0, 100, 0, "idle", 0, 100)])

    def test_missing_hours_rejected(self):
        plan = [_result(h, 100, 0, "idle", 0, 100) for h in range(23)]
        with pytest.raises(ScheduleInvalidError):
            validate_schedule(_hours(), _battery(), _constraints(), plan)


class TestPerHourChecks:
    def _valid_plan(self) -> list[HourResult]:
        # Battery stays at 100, idle all day
        return [_result(h, 100, 0, "idle", 0, 100) for h in range(24)]

    def test_valid_plan_passes(self):
        validate_schedule(_hours(), _battery(), _constraints(), self._valid_plan())

    def test_negative_grid_rejected(self):
        plan = self._valid_plan()
        plan[5] = _result(5, -10, 0, "idle", 0, 100)
        with pytest.raises(ScheduleInvalidError):
            validate_schedule(_hours(), _battery(), _constraints(), plan)

    def test_solar_over_effective_rejected(self):
        plan = self._valid_plan()
        plan[3] = _result(3, 90, 10, "idle", 0, 100)  # effective_solar = 0
        with pytest.raises(ScheduleInvalidError):
            validate_schedule(_hours(), _battery(), _constraints(), plan)

    def test_idle_with_magnitude_rejected(self):
        plan = self._valid_plan()
        plan[7] = _result(7, 95, 0, "idle", 5, 100)
        with pytest.raises(ScheduleInvalidError):
            validate_schedule(_hours(), _battery(), _constraints(), plan)

    def test_no_charge_violation_rejected(self):
        c = _constraints(no_charge={10})
        plan = [_result(h, 50, 0, "idle", 0, 100) for h in range(24)]
        plan[10] = _result(10, 50, 0, "charge", 50, 150)
        with pytest.raises(ScheduleInvalidError):
            validate_schedule(_hours(), _battery(), c, plan)

    def test_no_discharge_violation_rejected(self):
        c = _constraints(no_discharge={10})
        plan = [_result(h, 50, 0, "idle", 0, 100) for h in range(24)]
        plan[10] = _result(10, 150, 0, "discharge", 50, 50)
        with pytest.raises(ScheduleInvalidError):
            validate_schedule(_hours(), _battery(), c, plan)

    def test_max_grid_cap_violation_rejected(self):
        c = _constraints(max_grid=[float("inf")]*18 + [100, 100, 100] + [float("inf")]*3)
        plan = [_result(h, 50, 0, "idle", 0, 100) for h in range(24)]
        plan[18] = _result(18, 150, 0, "idle", 0, 100)
        with pytest.raises(ScheduleInvalidError):
            validate_schedule(_hours(), _battery(), c, plan)

    def test_energy_balance_violation_rejected(self):
        plan = [_result(h, 100, 0, "idle", 0, 100) for h in range(24)]
        plan[5] = _result(5, 80, 0, "idle", 0, 100)  # demand=100, lhs=80
        with pytest.raises(ScheduleInvalidError):
            validate_schedule(_hours(), _battery(), _constraints(), plan)

    def test_below_reserve_rejected(self):
        c = _constraints(min_reserve=80)
        plan = [_result(h, 100, 0, "idle", 0, 100) for h in range(24)]
        plan[10] = _result(10, 100, 0, "discharge", 40, 60)  # <80
        with pytest.raises(ScheduleInvalidError):
            validate_schedule(_hours(), _battery(), c, plan)

    def test_above_capacity_rejected(self):
        plan = [_result(h, 0, 0, "charge", 200, 250) for h in range(24)]
        with pytest.raises(ScheduleInvalidError):
            validate_schedule(_hours(), _battery(), _constraints(), plan)


class TestEndOfDay:
    def test_neutrality_violation_rejected(self):
        # Drift battery to 110 by hour 23 instead of returning to 100
        plan = [_result(h, 100, 0, "idle", 0, 110) for h in range(24)]
        with pytest.raises(ScheduleInvalidError):
            validate_schedule(_hours(), _battery(), _constraints(), plan)
