"""Optimization/constraint tests: crafted scenarios with a knowable optimal
answer, verifying the optimizer both (a) enforces every hard constraint from
each directive type individually, and (b) actually minimizes cost rather than
merely producing *a* feasible schedule.
"""
from app.directive_engine import build_constraints
from app.models import BatteryInput, DirectiveInterpretation, HourInput
from app.optimizer import solve
from app.schedule_validator import validate_schedule
from tests.helpers import make_battery, make_scenario

TOLERANCE = 0.01


def _solve(payload, interpretations=None):
    hours = sorted([HourInput(**h) for h in payload["hours"]], key=lambda h: h.hour)
    battery = BatteryInput(**payload["battery"])
    constraints = build_constraints(interpretations or [], [h.solar_kwh for h in hours], battery.minimum_energy_kwh)
    plan = solve(hours, battery, constraints)
    validate_schedule(hours, battery, constraints, plan)
    return hours, battery, constraints, plan


def test_battery_arbitrage_beats_naive_no_battery_cost():
    """Cheap hours (0-11: tariff 1) then expensive hours (12-23: tariff 20), flat
    demand, no solar. The optimizer should charge during cheap hours and
    discharge during expensive hours to reduce cost below what grid-only
    (never touching the battery) would cost."""
    payload = make_scenario(
        "opt-1", ["x"],
        demand=[50.0] * 24,
        solar=[0.0] * 24,
        tariff=[1.0] * 12 + [20.0] * 12,
        battery=make_battery(capacity_kwh=500, initial_energy_kwh=100, minimum_energy_kwh=0,
                             max_charge_kwh_per_hour=100, max_discharge_kwh_per_hour=100),
    )
    _, _, _, plan = _solve(payload)
    total_cost = sum(r.grid_kwh * (1.0 if r.hour < 12 else 20.0) for r in plan)

    naive_cost = sum(50.0 * (1.0 if h < 12 else 20.0) for h in range(24))
    assert total_cost < naive_cost - 1.0, "optimizer failed to exploit cheap/expensive tariff spread via battery arbitrage"


def test_solar_reduction_forces_more_grid_use():
    """With solar cut to zero via solar_reduction during a window where demand
    can otherwise be fully solar-covered, grid draw in that window must rise."""
    demand = [0.0] * 24
    solar = [0.0] * 24
    demand[12] = 100.0
    solar[12] = 100.0  # would fully cover hour 12 for free if unreduced
    payload = make_scenario("opt-2", ["x"], demand=demand, solar=solar, tariff=[5.0] * 24)

    baseline_hours, baseline_battery, baseline_constraints, baseline_plan = _solve(payload)
    baseline_hour12 = next(r for r in baseline_plan if r.hour == 12)
    assert baseline_hour12.grid_kwh <= TOLERANCE  # fully solar-covered without any directive

    interp = [DirectiveInterpretation(note_index=0, applies=True, directive_type="solar_reduction",
                                       structured_adjustment={"hours": [12], "factor": 0.0}, explanation="x")]
    hours = sorted([HourInput(**h) for h in payload["hours"]], key=lambda h: h.hour)
    battery = BatteryInput(**payload["battery"])
    constraints = build_constraints(interp, [h.solar_kwh for h in hours], battery.minimum_energy_kwh)
    plan = solve(hours, battery, constraints)
    validate_schedule(hours, battery, constraints, plan)
    hour12 = next(r for r in plan if r.hour == 12)
    assert hour12.solar_used_kwh <= TOLERANCE
    assert hour12.grid_kwh >= 100.0 - TOLERANCE - hour12.battery_kwh  # demand now met via grid (and/or battery)


def test_no_charge_window_blocks_charging_even_when_cost_optimal():
    """Hour 0 is by far the cheapest hour (tariff 0.01) so an unconstrained
    optimizer would want to charge heavily there; a no_charge_window directive
    on hour 0 must still force charge=0 despite the cost incentive."""
    tariff = [0.01] + [10.0] * 23
    payload = make_scenario("opt-3", ["x"], demand=[50.0] * 24, solar=[0.0] * 24, tariff=tariff,
                            battery=make_battery(capacity_kwh=500, initial_energy_kwh=200, minimum_energy_kwh=0,
                                                 max_charge_kwh_per_hour=200, max_discharge_kwh_per_hour=200))
    interp = [DirectiveInterpretation(note_index=0, applies=True, directive_type="no_charge_window",
                                       structured_adjustment={"hours": [0]}, explanation="x")]
    _, _, _, plan = _solve(payload, interp)
    hour0 = next(r for r in plan if r.hour == 0)
    assert hour0.battery_action != "charge"


def test_no_discharge_window_blocks_discharging():
    payload = make_scenario("opt-4", ["x"], demand=[50.0] * 24, solar=[0.0] * 24,
                            tariff=[10.0] * 17 + [0.01] + [10.0] * 6,
                            battery=make_battery(capacity_kwh=500, initial_energy_kwh=200, minimum_energy_kwh=0,
                                                 max_charge_kwh_per_hour=200, max_discharge_kwh_per_hour=200))
    interp = [DirectiveInterpretation(note_index=0, applies=True, directive_type="no_discharge_window",
                                       structured_adjustment={"hours": [17]}, explanation="x")]
    _, _, _, plan = _solve(payload, interp)
    hour17 = next(r for r in plan if r.hour == 17)
    assert hour17.battery_action != "discharge"


def test_minimum_battery_reserve_enforced_even_though_costlier():
    payload = make_scenario("opt-5", ["x"], demand=[50.0] * 24, solar=[0.0] * 24, tariff=[5.0] * 24,
                            battery=make_battery(capacity_kwh=500, initial_energy_kwh=200, minimum_energy_kwh=0,
                                                 max_charge_kwh_per_hour=200, max_discharge_kwh_per_hour=200))
    interp = [DirectiveInterpretation(note_index=0, applies=True, directive_type="minimum_battery_reserve",
                                       structured_adjustment={"hours": [12], "minimum_energy_kwh": 350}, explanation="x")]
    _, _, _, plan = _solve(payload, interp)
    hour12 = next(r for r in plan if r.hour == 12)
    assert hour12.battery_energy_after_kwh >= 350 - TOLERANCE


def test_max_grid_window_forces_battery_to_cover_the_rest():
    """Cap grid at a value below demand during a window with no solar; the
    remaining demand must be covered by battery discharge, not left unmet."""
    payload = make_scenario("opt-6", ["x"], demand=[80.0] * 24, solar=[0.0] * 24, tariff=[5.0] * 24,
                            battery=make_battery(capacity_kwh=500, initial_energy_kwh=300, minimum_energy_kwh=0,
                                                 max_charge_kwh_per_hour=200, max_discharge_kwh_per_hour=200))
    interp = [DirectiveInterpretation(note_index=0, applies=True, directive_type="max_grid_window",
                                       structured_adjustment={"hours": [18], "max_grid_kwh": 30}, explanation="x")]
    _, _, _, plan = _solve(payload, interp)
    hour18 = next(r for r in plan if r.hour == 18)
    assert hour18.grid_kwh <= 30 + TOLERANCE
    # energy balance (already checked by validate_schedule) guarantees the shortfall
    # was covered by discharge, since demand (80) > grid cap (30) and solar is 0
    assert hour18.battery_action == "discharge"
    assert hour18.battery_kwh >= 80 - 30 - TOLERANCE
