"""Directive engine: converts validated directive interpretations into hard
constraints consumable by the optimizer.
"""
from dataclasses import dataclass, field
from typing import Dict, List

from app.models import DirectiveInterpretation


@dataclass
class OptimizationConstraints:
    effective_solar_kwh: List[float]
    min_battery_reserve_kwh: List[float]  # per-hour effective minimum (after base battery min)
    no_charge_hours: set
    no_discharge_hours: set
    max_grid_kwh: List[float]  # per-hour cap, float("inf") when unconstrained


def build_constraints(
    interpretations: List[DirectiveInterpretation],
    base_solar_kwh: List[float],
    base_min_battery_kwh: float,
) -> OptimizationConstraints:
    effective_solar = list(base_solar_kwh)
    min_reserve = [base_min_battery_kwh] * 24
    no_charge: set = set()
    no_discharge: set = set()
    max_grid = [float("inf")] * 24

    for interp in interpretations:
        if not interp.applies or interp.directive_type == "no_op":
            continue
        adj = interp.structured_adjustment or {}
        hours = adj.get("hours", [])

        if interp.directive_type == "solar_reduction":
            factor = adj["factor"]
            for h in hours:
                effective_solar[h] = base_solar_kwh[h] * factor

        elif interp.directive_type == "minimum_battery_reserve":
            value = adj["minimum_energy_kwh"]
            for h in hours:
                min_reserve[h] = max(min_reserve[h], value)

        elif interp.directive_type == "no_charge_window":
            no_charge.update(hours)

        elif interp.directive_type == "no_discharge_window":
            no_discharge.update(hours)

        elif interp.directive_type == "max_grid_window":
            cap = adj["max_grid_kwh"]
            for h in hours:
                max_grid[h] = min(max_grid[h], cap)

    return OptimizationConstraints(
        effective_solar_kwh=effective_solar,
        min_battery_reserve_kwh=min_reserve,
        no_charge_hours=no_charge,
        no_discharge_hours=no_discharge,
        max_grid_kwh=max_grid,
    )
