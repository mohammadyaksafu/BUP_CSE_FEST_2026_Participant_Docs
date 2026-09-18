"""Unit tests for Pydantic models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import (
    BatteryInput,
    DirectiveInterpretation,
    HourInput,
    HourlyPlanEntry,
    OptimizeEnergyRequest,
    OptimizeEnergyResponse,
)


def _hour(h: int, dem: float = 100, sol: float = 0, tar: float = 6) -> HourInput:
    return HourInput(hour=h, demand_kwh=dem, solar_kwh=sol, tariff_bdt_per_kwh=tar)


class TestHourInput:
    def test_hour_in_range(self):
        _hour(0)
        _hour(23)
        with pytest.raises(ValidationError):
            _hour(24)
        with pytest.raises(ValidationError):
            _hour(-1)

    def test_negative_demand_rejected(self):
        with pytest.raises(ValidationError):
            _hour(0, dem=-1)

    def test_negative_solar_rejected(self):
        with pytest.raises(ValidationError):
            _hour(0, sol=-1)

    def test_negative_tariff_rejected(self):
        with pytest.raises(ValidationError):
            _hour(0, tar=-1)


class TestBatteryInput:
    def _b(self, **kw) -> BatteryInput:
        defaults = dict(
            capacity_kwh=200,
            initial_energy_kwh=100,
            minimum_energy_kwh=40,
            max_charge_kwh_per_hour=50,
            max_discharge_kwh_per_hour=50,
        )
        defaults.update(kw)
        return BatteryInput(**defaults)

    def test_capacity_must_be_positive(self):
        with pytest.raises(ValidationError):
            self._b(capacity_kwh=0)

    def test_min_exceeds_capacity_rejected(self):
        with pytest.raises(ValidationError):
            self._b(minimum_energy_kwh=300, capacity_kwh=200)

    def test_initial_above_capacity_rejected(self):
        with pytest.raises(ValidationError):
            self._b(initial_energy_kwh=300)

    def test_initial_below_minimum_rejected(self):
        with pytest.raises(ValidationError):
            self._b(minimum_energy_kwh=100, initial_energy_kwh=50)


class TestOptimizeEnergyRequest:
    def _hours(self) -> list[HourInput]:
        return [_hour(h) for h in range(24)]

    def _b(self) -> BatteryInput:
        return BatteryInput(
            capacity_kwh=200,
            initial_energy_kwh=100,
            minimum_energy_kwh=40,
            max_charge_kwh_per_hour=50,
            max_discharge_kwh_per_hour=50,
        )

    def test_valid_request(self):
        req = OptimizeEnergyRequest(
            scenario_id="x",
            operator_notes=["a note"],
            hours=self._hours(),
            battery=self._b(),
        )
        assert req.scenario_id == "x"
        assert len(req.hours) == 24

    def test_must_have_exactly_24_hours(self):
        with pytest.raises(ValidationError):
            OptimizeEnergyRequest(
                scenario_id="x",
                operator_notes=["a note"],
                hours=self._hours()[:23],
                battery=self._b(),
            )

    def test_must_have_unique_hours_0_23(self):
        hours = self._hours()
        hours[5] = _hour(0)  # duplicate
        with pytest.raises(ValidationError):
            OptimizeEnergyRequest(
                scenario_id="x",
                operator_notes=["a note"],
                hours=hours,
                battery=self._b(),
            )

    def test_max_3_operator_notes(self):
        with pytest.raises(ValidationError):
            OptimizeEnergyRequest(
                scenario_id="x",
                operator_notes=["n1", "n2", "n3", "n4"],
                hours=self._hours(),
                battery=self._b(),
            )

    def test_min_1_operator_note(self):
        with pytest.raises(ValidationError):
            OptimizeEnergyRequest(
                scenario_id="x",
                operator_notes=[],
                hours=self._hours(),
                battery=self._b(),
            )

    def test_empty_note_rejected(self):
        with pytest.raises(ValidationError):
            OptimizeEnergyRequest(
                scenario_id="x",
                operator_notes=["  "],
                hours=self._hours(),
                battery=self._b(),
            )


class TestDirectiveInterpretation:
    def test_no_op_must_have_applies_false(self):
        di = DirectiveInterpretation(
            note_index=0,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation="x",
        )
        assert di.applies is False

    def test_directive_type_validated(self):
        with pytest.raises(ValidationError):
            DirectiveInterpretation(
                note_index=0,
                applies=True,
                directive_type="invented_type",
                structured_adjustment=None,
                explanation="x",
            )


class TestHourlyPlanEntry:
    def test_battery_action_enum(self):
        e = HourlyPlanEntry(
            hour=0,
            grid_kwh=10,
            solar_used_kwh=0,
            battery_action="charge",
            battery_kwh=5,
            battery_energy_after_kwh=105,
        )
        assert e.battery_action == "charge"

    def test_invalid_battery_action(self):
        with pytest.raises(ValidationError):
            HourlyPlanEntry(
                hour=0,
                grid_kwh=10,
                solar_used_kwh=0,
                battery_action="explode",
                battery_kwh=5,
                battery_energy_after_kwh=105,
            )
