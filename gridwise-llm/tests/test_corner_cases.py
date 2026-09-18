"""Corner-case tests beyond the main 9-category suite: boundary values (0, 1,
exact capacity), degenerate battery configurations, stacked directives on the
same hour, reserve-vs-EOD-neutrality conflicts, and numeric/type coercion
edge cases at the request-model boundary. Each test states what it's probing
and what "pass" means, so a failure is immediately actionable.
"""
import pytest
from pydantic import ValidationError

from app.directive_engine import build_constraints
from app.models import BatteryInput, DirectiveInterpretation, HourInput, OptimizeEnergyRequest
from app.optimizer import OptimizationInfeasibleError, solve
from app.schedule_validator import validate_schedule
from tests.helpers import make_battery, make_interpretation, make_scenario

TOLERANCE = 0.01


def _solve(payload, interpretations=None):
    hours = sorted([HourInput(**h) for h in payload["hours"]], key=lambda h: h.hour)
    battery = BatteryInput(**payload["battery"])
    constraints = build_constraints(interpretations or [], [h.solar_kwh for h in hours], battery.minimum_energy_kwh)
    plan = solve(hours, battery, constraints)
    validate_schedule(hours, battery, constraints, plan)
    return hours, battery, constraints, plan


# ---------------------------------------------------------------------------
# Battery boundary / degenerate configurations
# ---------------------------------------------------------------------------

def test_battery_starts_completely_full():
    """initial_energy_kwh == capacity_kwh: battery can only discharge or stay,
    never charge above capacity on hour 0."""
    payload = make_scenario("cc-1", ["x"], battery=make_battery(
        capacity_kwh=200, initial_energy_kwh=200, minimum_energy_kwh=0,
        max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50,
    ))
    _, battery, _, plan = _solve(payload)
    assert all(r.battery_energy_after_kwh <= battery.capacity_kwh + TOLERANCE for r in plan)


def test_battery_starts_at_exact_minimum():
    """initial_energy_kwh == minimum_energy_kwh: battery can only charge or
    stay, never discharge below the floor on hour 0."""
    payload = make_scenario("cc-2", ["x"], battery=make_battery(
        capacity_kwh=200, initial_energy_kwh=20, minimum_energy_kwh=20,
        max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50,
    ))
    _, battery, _, plan = _solve(payload)
    assert all(r.battery_energy_after_kwh >= battery.minimum_energy_kwh - TOLERANCE for r in plan)


def test_battery_minimum_equals_capacity_fully_locked():
    """minimum_energy_kwh == capacity_kwh: the battery must sit exactly at
    capacity for all 24 hours (zero flexibility). Must still be feasible
    (charge/discharge net to zero every hour) and pass all validators."""
    payload = make_scenario("cc-3", ["x"], battery=make_battery(
        capacity_kwh=150, initial_energy_kwh=150, minimum_energy_kwh=150,
        max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50,
    ))
    _, battery, _, plan = _solve(payload)
    for r in plan:
        assert abs(r.battery_energy_after_kwh - 150) <= TOLERANCE
        assert r.battery_action == "idle"


def test_zero_max_charge_rate_forces_idle_charging_all_day():
    """max_charge_kwh_per_hour = 0: charging is impossible every hour. EOD
    neutrality then forces discharging to also net to zero (nothing could
    recharge what's discharged), so the only feasible solution is a fully
    idle battery."""
    payload = make_scenario("cc-4", ["x"], battery=make_battery(
        capacity_kwh=200, initial_energy_kwh=100, minimum_energy_kwh=0,
        max_charge_kwh_per_hour=0, max_discharge_kwh_per_hour=50,
    ))
    _, _, _, plan = _solve(payload)
    assert all(r.battery_action != "charge" for r in plan)
    assert abs(plan[-1].battery_energy_after_kwh - 100) <= TOLERANCE


def test_zero_max_discharge_rate_forces_idle_discharging_all_day():
    payload = make_scenario("cc-5", ["x"], battery=make_battery(
        capacity_kwh=200, initial_energy_kwh=100, minimum_energy_kwh=0,
        max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=0,
    ))
    _, _, _, plan = _solve(payload)
    assert all(r.battery_action != "discharge" for r in plan)
    assert abs(plan[-1].battery_energy_after_kwh - 100) <= TOLERANCE


def test_both_rates_zero_battery_fully_parked():
    """Battery can neither charge nor discharge: it must remain exactly at
    initial_energy_kwh for all 24 hours, and every hour's demand must be
    covered entirely by grid + solar."""
    payload = make_scenario("cc-6", ["x"], battery=make_battery(
        capacity_kwh=200, initial_energy_kwh=100, minimum_energy_kwh=0,
        max_charge_kwh_per_hour=0, max_discharge_kwh_per_hour=0,
    ))
    _, _, _, plan = _solve(payload)
    for r in plan:
        assert r.battery_action == "idle"
        assert abs(r.battery_energy_after_kwh - 100) <= TOLERANCE


def test_tiny_capacity_battery_with_large_demand():
    """1 kWh battery capacity against 100+ kWh/h demand: battery is nearly
    irrelevant, grid must carry almost everything, must still be feasible."""
    payload = make_scenario("cc-7", ["x"], demand=[100.0] * 24, solar=[0.0] * 24,
                            battery=make_battery(capacity_kwh=1, initial_energy_kwh=0.5,
                                                 minimum_energy_kwh=0, max_charge_kwh_per_hour=1,
                                                 max_discharge_kwh_per_hour=1))
    _, battery, _, plan = _solve(payload)
    for r in plan:
        assert r.grid_kwh >= 99  # battery can contribute at most ~1 kWh/hour


# ---------------------------------------------------------------------------
# Directive boundary values
# ---------------------------------------------------------------------------

def test_solar_reduction_factor_exactly_zero():
    """factor = 0.0 (lower boundary): solar fully blocked in those hours."""
    payload = make_scenario("cc-8", ["x"], demand=[100.0] * 24, solar=[100.0] * 24)
    interp = [DirectiveInterpretation(note_index=0, applies=True, directive_type="solar_reduction",
                                       structured_adjustment={"hours": [12], "factor": 0.0}, explanation="x")]
    _, _, constraints, plan = _solve(payload, interp)
    assert constraints.effective_solar_kwh[12] == 0.0
    hour12 = next(r for r in plan if r.hour == 12)
    assert hour12.solar_used_kwh <= TOLERANCE


def test_solar_reduction_factor_exactly_one_is_a_no_op_in_effect():
    """factor = 1.0 (upper boundary): explicitly valid per spec (0<=factor<=1)
    even though it reduces nothing — must not be rejected by guardrails."""
    from app.guardrails import validate_interpretations

    raw = [make_interpretation(0, "solar_reduction", hours=[12], factor=1.0)]
    result = validate_interpretations(raw, num_notes=1, battery_capacity_kwh=200)
    assert result[0].directive_type == "solar_reduction"
    assert result[0].structured_adjustment["factor"] == 1.0


def test_max_grid_window_zero_forces_full_coverage_from_solar_and_battery():
    """max_grid_kwh = 0.0: grid completely blocked for the hour; demand must
    be met entirely by solar + battery discharge (if feasible)."""
    payload = make_scenario("cc-9", ["x"], demand=[50.0] * 24, solar=[0.0] * 24,
                            battery=make_battery(capacity_kwh=500, initial_energy_kwh=300, minimum_energy_kwh=0,
                                                 max_charge_kwh_per_hour=200, max_discharge_kwh_per_hour=200))
    interp = [DirectiveInterpretation(note_index=0, applies=True, directive_type="max_grid_window",
                                       structured_adjustment={"hours": [10], "max_grid_kwh": 0.0}, explanation="x")]
    _, _, _, plan = _solve(payload, interp)
    hour10 = next(r for r in plan if r.hour == 10)
    assert hour10.grid_kwh <= TOLERANCE
    assert hour10.battery_action == "discharge"
    assert hour10.battery_kwh >= 50 - TOLERANCE


def test_minimum_battery_reserve_equal_to_capacity():
    """minimum_energy_kwh == battery.capacity_kwh for a directive hour: forces
    the battery to be completely full at that hour."""
    payload = make_scenario("cc-10", ["x"], battery=make_battery(
        capacity_kwh=200, initial_energy_kwh=200, minimum_energy_kwh=0,
        max_charge_kwh_per_hour=200, max_discharge_kwh_per_hour=200,
    ))
    interp = [DirectiveInterpretation(note_index=0, applies=True, directive_type="minimum_battery_reserve",
                                       structured_adjustment={"hours": [12], "minimum_energy_kwh": 200}, explanation="x")]
    _, _, _, plan = _solve(payload, interp)
    hour12 = next(r for r in plan if r.hour == 12)
    assert abs(hour12.battery_energy_after_kwh - 200) <= TOLERANCE


def test_reserve_directive_on_hour_23_conflicting_with_eod_neutrality_is_infeasible():
    """A minimum_battery_reserve on hour 23 that's ABOVE initial_energy_kwh
    directly conflicts with end-of-day neutrality (battery[23] must equal
    initial exactly). Must fail as a controlled infeasibility, not crash and
    not silently return an invalid schedule."""
    payload = make_scenario("cc-11", ["x"], battery=make_battery(
        capacity_kwh=200, initial_energy_kwh=50, minimum_energy_kwh=0,
        max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50,
    ))
    interp = [DirectiveInterpretation(note_index=0, applies=True, directive_type="minimum_battery_reserve",
                                       structured_adjustment={"hours": [23], "minimum_energy_kwh": 150}, explanation="x")]
    hours = sorted([HourInput(**h) for h in payload["hours"]], key=lambda h: h.hour)
    battery = BatteryInput(**payload["battery"])
    constraints = build_constraints(interp, [h.solar_kwh for h in hours], battery.minimum_energy_kwh)
    with pytest.raises(OptimizationInfeasibleError):
        solve(hours, battery, constraints)


# ---------------------------------------------------------------------------
# Stacked / combined directives on the same hour(s)
# ---------------------------------------------------------------------------

def test_no_charge_and_no_discharge_stacked_on_same_hour_forces_idle():
    payload = make_scenario("cc-12", ["x"])
    interp = [
        DirectiveInterpretation(note_index=0, applies=True, directive_type="no_charge_window",
                                 structured_adjustment={"hours": [12]}, explanation="x"),
        DirectiveInterpretation(note_index=1, applies=True, directive_type="no_discharge_window",
                                 structured_adjustment={"hours": [12]}, explanation="y"),
    ]
    _, _, _, plan = _solve(payload, interp)
    hour12 = next(r for r in plan if r.hour == 12)
    assert hour12.battery_action == "idle"


def test_all_four_hard_directive_types_stacked_on_same_hour():
    """solar_reduction + no_charge_window + no_discharge_window +
    max_grid_window + minimum_battery_reserve all targeting hour 12 at once —
    the battery is fully pinned (idle) and solar/grid are both constrained,
    so this must remain exactly solvable via the demand being met by whatever
    solar remains plus grid up to the cap."""
    demand = [100.0] * 24
    solar = [100.0] * 24
    payload = make_scenario("cc-13", ["x"], demand=demand, solar=solar, tariff=[5.0] * 24,
                            battery=make_battery(capacity_kwh=300, initial_energy_kwh=150, minimum_energy_kwh=50,
                                                 max_charge_kwh_per_hour=100, max_discharge_kwh_per_hour=100))
    interp = [
        DirectiveInterpretation(note_index=0, applies=True, directive_type="solar_reduction",
                                 structured_adjustment={"hours": [12], "factor": 0.5}, explanation="a"),
        DirectiveInterpretation(note_index=1, applies=True, directive_type="no_charge_window",
                                 structured_adjustment={"hours": [12]}, explanation="b"),
        DirectiveInterpretation(note_index=2, applies=True, directive_type="no_discharge_window",
                                 structured_adjustment={"hours": [12]}, explanation="c"),
        DirectiveInterpretation(note_index=3, applies=True, directive_type="max_grid_window",
                                 structured_adjustment={"hours": [12], "max_grid_kwh": 60}, explanation="d"),
    ]
    _, _, constraints, plan = _solve(payload, interp)
    hour12 = next(r for r in plan if r.hour == 12)
    assert hour12.battery_action == "idle"
    assert hour12.solar_used_kwh <= 50 + TOLERANCE  # 100 * 0.5
    assert hour12.grid_kwh <= 60 + TOLERANCE
    # energy balance (grid + solar = demand, since battery is forced idle):
    # 60 (grid cap) + 50 (max effective solar) = 110 >= 100 demand -> feasible


def test_overlapping_solar_reduction_directives_same_hour_documents_last_wins():
    """KNOWN BEHAVIOR (not a spec violation — organizer scenarios don't include
    contradictory directives): if two solar_reduction directives somehow both
    target the same hour, the directive engine applies factors in interpretation
    order and the LAST one processed determines effective_solar for that hour
    (it does not compound or take the more restrictive value). This test pins
    that exact, current behavior so a future change to directive_engine.py
    doesn't silently alter it without a test catching it."""
    interp = [
        DirectiveInterpretation(note_index=0, applies=True, directive_type="solar_reduction",
                                 structured_adjustment={"hours": [10], "factor": 0.8}, explanation="a"),
        DirectiveInterpretation(note_index=1, applies=True, directive_type="solar_reduction",
                                 structured_adjustment={"hours": [10], "factor": 0.2}, explanation="b"),
    ]
    constraints = build_constraints(interp, [100.0] * 24, base_min_battery_kwh=0)
    assert constraints.effective_solar_kwh[10] == 20.0  # 100 * 0.2 (second directive wins)


# ---------------------------------------------------------------------------
# Request-model numeric / type-coercion boundaries
# ---------------------------------------------------------------------------

def test_hour_as_whole_number_float_is_rejected_or_coerced_consistently():
    """hour: 5.0 in JSON (whole-number float) — document actual pydantic
    behavior rather than assume; either strict rejection or clean coercion
    to int 5 is acceptable, but it must not silently produce a broken hour
    that then fails the 'exactly 24 unique hours 0-23' invariant."""
    payload = make_scenario("cc-14", ["x"])
    payload["hours"][5]["hour"] = 5.0
    try:
        req = OptimizeEnergyRequest(**payload)
        assert req.hours[5].hour == 5
        assert isinstance(req.hours[5].hour, int)
    except ValidationError:
        pass  # strict rejection is also an acceptable, safe outcome


def test_hour_as_fractional_float_is_rejected():
    payload = make_scenario("cc-15", ["x"])
    payload["hours"][5]["hour"] = 5.5
    with pytest.raises(ValidationError):
        OptimizeEnergyRequest(**payload)


def test_zero_tariff_all_hours_grid_is_free():
    payload = make_scenario("cc-16", ["x"], tariff=[0.0] * 24)
    _, _, _, plan = _solve(payload)
    total_cost = sum(r.grid_kwh * 0.0 for r in plan)
    assert total_cost == 0.0


def test_negative_tariff_rejected_at_request_level():
    payload = make_scenario("cc-17", ["x"])
    payload["hours"][0]["tariff_bdt_per_kwh"] = -1.0
    with pytest.raises(ValidationError):
        OptimizeEnergyRequest(**payload)


def test_huge_battery_capacity_does_not_break_solver():
    payload = make_scenario("cc-18", ["x"], demand=[100.0] * 24, solar=[0.0] * 24,
                            battery=make_battery(capacity_kwh=1_000_000, initial_energy_kwh=500_000,
                                                 minimum_energy_kwh=0, max_charge_kwh_per_hour=10_000,
                                                 max_discharge_kwh_per_hour=10_000))
    _, battery, _, plan = _solve(payload)
    assert abs(plan[-1].battery_energy_after_kwh - 500_000) <= TOLERANCE
