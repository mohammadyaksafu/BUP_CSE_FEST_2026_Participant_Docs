"""White-box tests: exercise internal modules directly (guardrails, directive
engine, optimizer, schedule validator, pydantic models), with full knowledge of
their implementation, to hit branches that are hard to reach only through the
public HTTP interface.
"""
import pytest
from pydantic import ValidationError

from app.directive_engine import build_constraints
from app.guardrails import validate_interpretations
from app.models import BatteryInput, HourInput, OptimizeEnergyRequest
from app.optimizer import OptimizationInfeasibleError, solve
from app.schedule_validator import ScheduleInvalidError, validate_schedule
from tests.helpers import make_battery, make_interpretation, make_scenario


# ---------------------------------------------------------------------------
# guardrails.validate_interpretations
# ---------------------------------------------------------------------------

def test_guardrail_accepts_valid_directive():
    raw = [make_interpretation(0, "solar_reduction", hours=[13, 14], factor=0.2)]
    result = validate_interpretations(raw, num_notes=1, battery_capacity_kwh=200)
    assert result[0].applies is True
    assert result[0].directive_type == "solar_reduction"
    assert result[0].structured_adjustment == {"hours": [13, 14], "factor": 0.2}


def test_guardrail_accepts_valid_no_op():
    raw = [make_interpretation(0, "no_op", explanation="irrelevant")]
    result = validate_interpretations(raw, num_notes=1, battery_capacity_kwh=200)
    assert result[0].applies is False
    assert result[0].directive_type == "no_op"
    assert result[0].structured_adjustment is None


def test_guardrail_missing_note_index_falls_back_to_no_op():
    raw = [make_interpretation(0, "no_charge_window", hours=[1, 2])]  # only note 0 given
    result = validate_interpretations(raw, num_notes=2, battery_capacity_kwh=200)
    assert len(result) == 2
    assert result[1].directive_type == "no_op"
    assert result[1].applies is False


def test_guardrail_duplicate_note_index_falls_back_to_no_op():
    raw = [
        make_interpretation(0, "no_charge_window", hours=[1]),
        make_interpretation(0, "no_discharge_window", hours=[2]),
    ]
    result = validate_interpretations(raw, num_notes=1, battery_capacity_kwh=200)
    assert result[0].directive_type == "no_op"


def test_guardrail_rejects_invented_directive_type():
    raw = [make_interpretation(0, "shutdown_campus", hours=[1])]
    result = validate_interpretations(raw, num_notes=1, battery_capacity_kwh=200)
    assert result[0].directive_type == "no_op"
    assert result[0].applies is False


@pytest.mark.parametrize(
    "hours",
    [
        [13.5, 14],  # non-integer
        [24],  # out of range
        [-1],  # out of range
        [13, 13],  # duplicate
        [14, 13],  # not ascending
        [],  # empty
    ],
)
def test_guardrail_rejects_invalid_hours(hours):
    raw = [make_interpretation(0, "no_charge_window", hours=hours)]
    result = validate_interpretations(raw, num_notes=1, battery_capacity_kwh=200)
    assert result[0].directive_type == "no_op"


@pytest.mark.parametrize("factor", [-0.1, 1.1, "high", None])
def test_guardrail_rejects_invalid_solar_factor(factor):
    raw = [make_interpretation(0, "solar_reduction", hours=[1], factor=factor)]
    result = validate_interpretations(raw, num_notes=1, battery_capacity_kwh=200)
    assert result[0].directive_type == "no_op"


def test_guardrail_rejects_reserve_above_capacity():
    raw = [make_interpretation(0, "minimum_battery_reserve", hours=[1], minimum_energy_kwh=999)]
    result = validate_interpretations(raw, num_notes=1, battery_capacity_kwh=200)
    assert result[0].directive_type == "no_op"


def test_guardrail_rejects_negative_max_grid():
    raw = [make_interpretation(0, "max_grid_window", hours=[1], max_grid_kwh=-5)]
    result = validate_interpretations(raw, num_notes=1, battery_capacity_kwh=200)
    assert result[0].directive_type == "no_op"


def test_guardrail_rejects_no_op_with_applies_true():
    raw = [{"note_index": 0, "applies": True, "directive_type": "no_op", "structured_adjustment": None, "explanation": "x"}]
    result = validate_interpretations(raw, num_notes=1, battery_capacity_kwh=200)
    assert result[0].directive_type == "no_op"
    assert result[0].applies is False  # safely corrected, never propagates the bad flag


def test_guardrail_rejects_directive_with_applies_false():
    raw = [make_interpretation(0, "no_charge_window", hours=[1], applies=False)]
    result = validate_interpretations(raw, num_notes=1, battery_capacity_kwh=200)
    assert result[0].directive_type == "no_op"


def test_guardrail_rejects_non_dict_adjustment():
    raw = [{"note_index": 0, "applies": True, "directive_type": "no_charge_window", "structured_adjustment": "hours 1-2", "explanation": "x"}]
    result = validate_interpretations(raw, num_notes=1, battery_capacity_kwh=200)
    assert result[0].directive_type == "no_op"


def test_guardrail_never_crashes_on_completely_garbage_input():
    raw = [None, {"garbage": True}, 42, "not even a dict"]
    result = validate_interpretations(raw, num_notes=3, battery_capacity_kwh=200)
    assert len(result) == 3
    assert all(r.directive_type == "no_op" for r in result)


# ---------------------------------------------------------------------------
# directive_engine.build_constraints
# ---------------------------------------------------------------------------

def test_directive_engine_solar_reduction_scales_only_listed_hours():
    from app.models import DirectiveInterpretation

    interp = [DirectiveInterpretation(note_index=0, applies=True, directive_type="solar_reduction",
                                       structured_adjustment={"hours": [10, 11], "factor": 0.25}, explanation="x")]
    base_solar = [100.0] * 24
    c = build_constraints(interp, base_solar, base_min_battery_kwh=20)
    assert c.effective_solar_kwh[10] == 25.0
    assert c.effective_solar_kwh[11] == 25.0
    assert c.effective_solar_kwh[9] == 100.0


def test_directive_engine_combines_two_max_grid_windows_as_min():
    from app.models import DirectiveInterpretation

    interp = [
        DirectiveInterpretation(note_index=0, applies=True, directive_type="max_grid_window",
                                 structured_adjustment={"hours": [18], "max_grid_kwh": 150}, explanation="x"),
        DirectiveInterpretation(note_index=1, applies=True, directive_type="max_grid_window",
                                 structured_adjustment={"hours": [18], "max_grid_kwh": 100}, explanation="y"),
    ]
    c = build_constraints(interp, [0.0] * 24, base_min_battery_kwh=20)
    assert c.max_grid_kwh[18] == 100  # tighter cap wins
    assert c.max_grid_kwh[0] == float("inf")


def test_directive_engine_min_reserve_never_lowers_base_minimum():
    from app.models import DirectiveInterpretation

    interp = [DirectiveInterpretation(note_index=0, applies=True, directive_type="minimum_battery_reserve",
                                       structured_adjustment={"hours": [5], "minimum_energy_kwh": 10}, explanation="x")]
    c = build_constraints(interp, [0.0] * 24, base_min_battery_kwh=30)
    assert c.min_battery_reserve_kwh[5] == 30  # base min (30) > directive value (10)


def test_directive_engine_no_op_and_non_applying_ignored():
    from app.models import DirectiveInterpretation

    interp = [
        DirectiveInterpretation(note_index=0, applies=False, directive_type="no_op", structured_adjustment=None, explanation="x"),
    ]
    c = build_constraints(interp, [50.0] * 24, base_min_battery_kwh=10)
    assert c.effective_solar_kwh == [50.0] * 24
    assert c.no_charge_hours == set()


# ---------------------------------------------------------------------------
# optimizer.solve / schedule_validator.validate_schedule
# ---------------------------------------------------------------------------

def _solve_scenario(payload):
    hours = sorted([HourInput(**h) for h in payload["hours"]], key=lambda h: h.hour)
    battery = BatteryInput(**payload["battery"])
    from app.models import DirectiveInterpretation

    constraints = build_constraints([], [h.solar_kwh for h in hours], battery.minimum_energy_kwh)
    return hours, battery, constraints, solve(hours, battery, constraints)


def test_optimizer_produces_feasible_baseline_schedule():
    payload = make_scenario("wb-1", ["irrelevant note"])
    hours, battery, constraints, plan = _solve_scenario(payload)
    validate_schedule(hours, battery, constraints, plan)  # should not raise


def test_optimizer_infeasible_raises_controlled_error():
    from app.directive_engine import OptimizationConstraints

    payload = make_scenario("wb-2", ["x"], battery=make_battery(
        capacity_kwh=200, initial_energy_kwh=100, minimum_energy_kwh=20,
        max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50,
    ))
    hours = sorted([HourInput(**h) for h in payload["hours"]], key=lambda h: h.hour)
    battery = BatteryInput(**payload["battery"])
    # Force an impossible reserve requirement above capacity via a constraint object
    # crafted directly (bypasses the 0..capacity guardrail range check on purpose,
    # to confirm the optimizer itself fails safely rather than crashing).
    bad_constraints = OptimizationConstraints(
        effective_solar_kwh=[h.solar_kwh for h in hours],
        min_battery_reserve_kwh=[battery.capacity_kwh + 50] * 24,
        no_charge_hours=set(),
        no_discharge_hours=set(),
        max_grid_kwh=[float("inf")] * 24,
    )
    with pytest.raises(OptimizationInfeasibleError):
        solve(hours, battery, bad_constraints)


def test_schedule_validator_catches_corrupted_energy_balance():
    payload = make_scenario("wb-3", ["x"])
    hours, battery, constraints, plan = _solve_scenario(payload)
    plan[0].grid_kwh += 1000  # corrupt one field
    with pytest.raises(ScheduleInvalidError):
        validate_schedule(hours, battery, constraints, plan)


def test_schedule_validator_catches_eod_neutrality_violation():
    payload = make_scenario("wb-4", ["x"])
    hours, battery, constraints, plan = _solve_scenario(payload)
    plan[-1].battery_energy_after_kwh += 10
    with pytest.raises(ScheduleInvalidError):
        validate_schedule(hours, battery, constraints, plan)


# ---------------------------------------------------------------------------
# pydantic request models
# ---------------------------------------------------------------------------

def test_battery_model_rejects_min_above_capacity():
    with pytest.raises(ValidationError):
        BatteryInput(capacity_kwh=100, initial_energy_kwh=50, minimum_energy_kwh=150,
                     max_charge_kwh_per_hour=10, max_discharge_kwh_per_hour=10)


def test_battery_model_rejects_initial_below_minimum():
    with pytest.raises(ValidationError):
        BatteryInput(capacity_kwh=100, initial_energy_kwh=5, minimum_energy_kwh=20,
                     max_charge_kwh_per_hour=10, max_discharge_kwh_per_hour=10)


def test_request_model_rejects_wrong_hour_count():
    payload = make_scenario("wb-5", ["x"])
    payload["hours"] = payload["hours"][:23]
    with pytest.raises(ValidationError):
        OptimizeEnergyRequest(**payload)


def test_request_model_rejects_duplicate_hour():
    payload = make_scenario("wb-6", ["x"])
    payload["hours"][1]["hour"] = payload["hours"][0]["hour"]
    with pytest.raises(ValidationError):
        OptimizeEnergyRequest(**payload)


def test_request_model_rejects_too_many_notes():
    payload = make_scenario("wb-7", ["a", "b", "c", "d"])
    with pytest.raises(ValidationError):
        OptimizeEnergyRequest(**payload)


def test_request_model_rejects_empty_notes():
    payload = make_scenario("wb-8", [])
    with pytest.raises(ValidationError):
        OptimizeEnergyRequest(**payload)
