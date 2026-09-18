"""Response builder: assembles totals + plan_summary and validates KPI
consistency against hourly_plan."""
from __future__ import annotations

from typing import Any

from app.models import DirectiveInterpretation, HourlyPlanEntry
from app.optimizer import HourResult


def _round(x: float, n: int = 6) -> float:
    return round(x, n)


def build_response(
    scenario_id: str,
    interpretations: list[DirectiveInterpretation],
    plan: list[HourResult],
    tariff_by_hour: dict[int, float],
) -> dict[str, Any]:
    """Compute totals from `plan` and a deterministic plan_summary string."""
    hourly_plan = [
        HourlyPlanEntry(
            hour=r.hour,
            grid_kwh=r.grid_kwh,
            solar_used_kwh=r.solar_used_kwh,
            battery_action=r.battery_action,
            battery_kwh=r.battery_kwh,
            battery_energy_after_kwh=r.battery_energy_after_kwh,
        )
        for r in plan
    ]

    total_grid_kwh = _round(sum(r.grid_kwh for r in plan))
    total_cost_bdt = _round(
        sum(r.grid_kwh * tariff_by_hour[r.hour] for r in plan)
    )
    peak_grid_kwh = _round(max(r.grid_kwh for r in plan), n=4)

    applied = [i for i in interpretations if i.applies]
    if applied:
        kinds = ", ".join(sorted({i.directive_type for i in applied}))
        directive_part = f"Applied directives: {kinds}."
    else:
        directive_part = "No operator directives applied; all notes were non-operational (no_op)."

    plan_summary = (
        f"Scenario {scenario_id}: produced a 24-hour schedule drawing "
        f"{total_grid_kwh:.2f} kWh from the grid at a total cost of "
        f"{total_cost_bdt:.2f} BDT, with peak hourly grid draw of "
        f"{peak_grid_kwh:.2f} kWh. {directive_part}"
    )

    return {
        "scenario_id": scenario_id,
        "directive_interpretation": sorted(interpretations, key=lambda i: i.note_index),
        "hourly_plan": hourly_plan,
        "total_grid_kwh": total_grid_kwh,
        "total_cost_bdt": total_cost_bdt,
        "peak_grid_kwh": peak_grid_kwh,
        "plan_summary": plan_summary,
    }


def assert_totals_consistent(response: dict[str, Any], tol: float = 0.01) -> None:
    """Defense-in-depth: re-derive totals from hourly_plan and compare with
    the response values. Raises AssertionError on mismatch."""
    hp = response["hourly_plan"]
    gsum = sum(e["grid_kwh"] for e in hp)
    pmax = max(e["grid_kwh"] for e in hp)
    # Need tariff; the caller doesn't pass it here so just check grid sum/peak.
    assert abs(gsum - response["total_grid_kwh"]) <= tol, (
        f"total_grid_kwh mismatch: {gsum} vs {response['total_grid_kwh']}"
    )
    assert abs(pmax - response["peak_grid_kwh"]) <= tol, (
        f"peak_grid_kwh mismatch: {pmax} vs {response['peak_grid_kwh']}"
    )
