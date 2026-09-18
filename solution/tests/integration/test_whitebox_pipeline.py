"""WHITE-BOX PIPELINE TEST: drives each stage of the pipeline with mocked
inputs to verify the integration points between modules."""
from __future__ import annotations

from app.directive_engine import build_constraints
from app.guardrails import validate_interpretations
from app.models import (
    BatteryInput,
    DirectiveInterpretation,
    HourInput,
)
from app.optimizer import solve
from app.rule_interpreter import interpret_notes
from app.schedule_validator import validate_schedule


def _hours() -> list[HourInput]:
    return [HourInput(hour=h, demand_kwh=100, solar_kwh=0, tariff_bdt_per_kwh=6)
            for h in range(24)]


def _battery() -> BatteryInput:
    return BatteryInput(
        capacity_kwh=200, initial_energy_kwh=100, minimum_energy_kwh=40,
        max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50,
    )


class TestFullPipeline:
    def test_rule_to_guardrails_to_constraints_to_solve(self):
        # Stage 1: rules produce raw interpretations
        raw = interpret_notes(
            ["Sports deadline moved", "Wash panels 1 PM to 3 PM, 80% reduction"],
            battery_capacity_kwh=200,
        )
        # Stage 2: guardrails validate
        di = validate_interpretations(raw, num_notes=2, battery_capacity_kwh=200)
        assert len(di) == 2

        # Stage 3: directive engine builds constraints
        base_solar = [0] * 24
        constraints = build_constraints(di, base_solar, base_min_battery_kwh=40)
        # only the second note applies, hours 13-14 factor 0.2
        # Both 13 and 14 are 0 solar anyway, but the constraint shape is right.
        assert all(m == 40 for m in constraints.min_battery_reserve_kwh)

        # Stage 4: solver
        plan = solve(_hours(), _battery(), constraints)
        assert len(plan) == 24

        # Stage 5: schedule validator (defense in depth)
        validate_schedule(_hours(), _battery(), constraints, plan)

    def test_pipeline_with_minimum_reserve_directive(self):
        raw = interpret_notes(
            ["Keep at least 50% of battery capacity from 6 PM until 9 PM"],
            battery_capacity_kwh=200,
        )
        di = validate_interpretations(raw, 1, 200)
        c = build_constraints(di, [0]*24, base_min_battery_kwh=40)
        # reserve hours 18, 19, 20 raised to 100
        assert c.min_battery_reserve_kwh[18] == 100
        assert c.min_battery_reserve_kwh[19] == 100
        assert c.min_battery_reserve_kwh[20] == 100
        # other hours untouched
        assert c.min_battery_reserve_kwh[0] == 40

        plan = solve(_hours(), _battery(), c)
        validate_schedule(_hours(), _battery(), c, plan)
        # at hours 18-20, battery must be >= 100
        for r in plan:
            if r.hour in (18, 19, 20):
                assert r.battery_energy_after_kwh >= 99.99