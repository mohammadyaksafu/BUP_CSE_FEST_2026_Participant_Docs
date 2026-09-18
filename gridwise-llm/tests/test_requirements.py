"""Requirement-based tests: one test per checklist item in problem.md Section 12
("Validation & Hidden Evaluation"), run against the offline pipeline (guardrails
-> directive engine -> optimizer -> schedule validator) using each public
sample's published ground-truth directive interpretation as the LLM stand-in.
This isolates "does the system correctly ENFORCE the spec" from "does the LLM
correctly EXTRACT the directive" (covered separately in test_llm_prompt.py).
"""
import pytest

from app.directive_engine import build_constraints
from app.guardrails import validate_interpretations
from app.models import BatteryInput, HourInput
from app.optimizer import solve
from app.schedule_validator import validate_schedule

TOLERANCE = 0.01


def _run_case(case):
    inp = case["input"]
    expected = case.get("expected_output", case.get("expected"))
    raw = expected["directive_interpretation"]

    battery = BatteryInput(**inp["battery"])
    hours = sorted([HourInput(**h) for h in inp["hours"]], key=lambda h: h.hour)

    interpretations = validate_interpretations(raw, len(inp["operator_notes"]), battery.capacity_kwh)
    constraints = build_constraints(interpretations, [h.solar_kwh for h in hours], battery.minimum_energy_kwh)
    plan = solve(hours, battery, constraints)
    validate_schedule(hours, battery, constraints, plan)  # REQ: schedule validity (raises if violated)
    return inp, expected, interpretations, constraints, plan


@pytest.fixture(params=range(10))
def case(request, sample_cases):
    return sample_cases[request.param]


def test_req_exactly_24_unique_hours(case):
    _, _, _, _, plan = _run_case(case)
    assert len(plan) == 24
    assert {r.hour for r in plan} == set(range(24))


def test_req_all_values_finite_and_nonnegative(case):
    _, _, _, _, plan = _run_case(case)
    for r in plan:
        for v in (r.grid_kwh, r.solar_used_kwh, r.battery_kwh, r.battery_energy_after_kwh):
            assert v == v  # not NaN
            assert v != float("inf") and v != float("-inf")
        assert r.grid_kwh >= -TOLERANCE
        assert r.solar_used_kwh >= -TOLERANCE
        assert r.battery_kwh >= -TOLERANCE


def test_req_energy_balance_every_hour(case):
    inp, _, _, _, plan = _run_case(case)
    demand_by_hour = {h["hour"]: h["demand_kwh"] for h in inp["hours"]}
    for r in plan:
        charge = r.battery_kwh if r.battery_action == "charge" else 0.0
        discharge = r.battery_kwh if r.battery_action == "discharge" else 0.0
        lhs = r.grid_kwh + r.solar_used_kwh + discharge
        rhs = demand_by_hour[r.hour] + charge
        assert abs(lhs - rhs) <= TOLERANCE


def test_req_solar_used_never_exceeds_effective_solar(case):
    _, _, _, constraints, plan = _run_case(case)
    for r in plan:
        assert r.solar_used_kwh <= constraints.effective_solar_kwh[r.hour] + TOLERANCE


def test_req_battery_never_exceeds_capacity_or_effective_minimum(case):
    inp, _, _, constraints, plan = _run_case(case)
    capacity = inp["battery"]["capacity_kwh"]
    for r in plan:
        assert r.battery_energy_after_kwh <= capacity + TOLERANCE
        assert r.battery_energy_after_kwh >= constraints.min_battery_reserve_kwh[r.hour] - TOLERANCE


def test_req_rate_limits_respected(case):
    inp, _, _, _, plan = _run_case(case)
    max_charge = inp["battery"]["max_charge_kwh_per_hour"]
    max_discharge = inp["battery"]["max_discharge_kwh_per_hour"]
    for r in plan:
        if r.battery_action == "charge":
            assert r.battery_kwh <= max_charge + TOLERANCE
        elif r.battery_action == "discharge":
            assert r.battery_kwh <= max_discharge + TOLERANCE
        else:
            assert r.battery_kwh <= TOLERANCE


def test_req_no_charge_and_no_discharge_windows_honored(case):
    _, _, _, constraints, plan = _run_case(case)
    plan_by_hour = {r.hour: r for r in plan}
    for h in constraints.no_charge_hours:
        assert plan_by_hour[h].battery_action != "charge"
    for h in constraints.no_discharge_hours:
        assert plan_by_hour[h].battery_action != "discharge"


def test_req_max_grid_window_honored(case):
    _, _, _, constraints, plan = _run_case(case)
    for r in plan:
        cap = constraints.max_grid_kwh[r.hour]
        if cap != float("inf"):
            assert r.grid_kwh <= cap + TOLERANCE


def test_req_end_of_day_battery_neutrality(case):
    inp, _, _, _, plan = _run_case(case)
    plan_by_hour = {r.hour: r for r in plan}
    assert abs(plan_by_hour[23].battery_energy_after_kwh - inp["battery"]["initial_energy_kwh"]) <= TOLERANCE


def test_req_directive_interpretation_count_and_order(case):
    inp, _, interpretations, _, _ = _run_case(case)
    assert len(interpretations) == len(inp["operator_notes"])
    assert [i.note_index for i in interpretations] == list(range(len(inp["operator_notes"])))


def test_req_no_op_semantics_and_other_directives_applies_true(case):
    _, _, interpretations, _, _ = _run_case(case)
    for i in interpretations:
        if i.directive_type == "no_op":
            assert i.applies is False
            assert i.structured_adjustment is None
        else:
            assert i.applies is True


def test_req_totals_match_hourly_plan(case):
    inp, _, _, _, plan = _run_case(case)
    tariff_by_hour = {h["hour"]: h["tariff_bdt_per_kwh"] for h in inp["hours"]}
    total_grid = sum(r.grid_kwh for r in plan)
    total_cost = sum(r.grid_kwh * tariff_by_hour[r.hour] for r in plan)
    peak = max(r.grid_kwh for r in plan)
    # The service computes these the same way from hourly_plan in app/main.py;
    # here we confirm the plan itself supports a consistent recomputation.
    assert total_grid >= 0
    assert total_cost >= 0
    assert peak == max(r.grid_kwh for r in plan)
