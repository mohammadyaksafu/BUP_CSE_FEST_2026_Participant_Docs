"""Regression tests: locks in that the optimizer/pipeline continues to
reproduce each public sample's published reference optimal cost. If a future
change to the optimizer, directive engine, or guardrails regresses cost
quality or constraint handling, these tests catch it immediately without
needing a live LLM call (uses each case's ground-truth directive as input).
"""
import pytest

from app.directive_engine import build_constraints
from app.guardrails import validate_interpretations
from app.models import BatteryInput, HourInput
from app.optimizer import solve
from app.schedule_validator import validate_schedule

TOLERANCE = 0.01


@pytest.fixture(params=range(10))
def case(request, sample_cases):
    return sample_cases[request.param]


def test_regression_matches_reference_optimal_cost(case):
    inp = case["input"]
    expected = case.get("expected_output", case.get("expected"))
    raw = expected["directive_interpretation"]

    battery = BatteryInput(**inp["battery"])
    hours = sorted([HourInput(**h) for h in inp["hours"]], key=lambda h: h.hour)

    interpretations = validate_interpretations(raw, len(inp["operator_notes"]), battery.capacity_kwh)
    constraints = build_constraints(interpretations, [h.solar_kwh for h in hours], battery.minimum_energy_kwh)
    plan = solve(hours, battery, constraints)
    validate_schedule(hours, battery, constraints, plan)

    tariff_by_hour = {h["hour"]: h["tariff_bdt_per_kwh"] for h in inp["hours"]}
    total_grid = sum(r.grid_kwh for r in plan)
    total_cost = sum(r.grid_kwh * tariff_by_hour[r.hour] for r in plan)

    assert abs(total_grid - expected["total_grid_kwh"]) <= TOLERANCE, (
        f"{case['id']}: total_grid_kwh regressed ({total_grid} vs reference {expected['total_grid_kwh']})"
    )
    assert abs(total_cost - expected["total_cost_bdt"]) <= TOLERANCE, (
        f"{case['id']}: total_cost_bdt regressed ({total_cost} vs reference {expected['total_cost_bdt']})"
    )
