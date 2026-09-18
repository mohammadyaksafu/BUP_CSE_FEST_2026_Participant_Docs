"""Directive engine: converts validated directive interpretations into hard
optimizer constraints."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models import DirectiveInterpretation


@dataclass
class OptimizationConstraints:
    effective_solar_kwh: list[float]
    min_battery_reserve_kwh: list[float]
    no_charge_hours: set[int] = field(default_factory=set)
    no_discharge_hours: set[int] = field(default_factory=set)
    max_grid_kwh: list[float] = field(default_factory=list)


def build_constraints(
    interpretations: list[DirectiveInterpretation],
    base_solar_kwh: list[float],
    base_min_battery_kwh: float,
) -> OptimizationConstraints:
    effective_solar = list(base_solar_kwh)
    min_reserve = [base_min_battery_kwh] * 24
    no_charge: set[int] = set()
    no_discharge: set[int] = set()
    max_grid = [float("inf")] * 24

    for interp in interpretations:
        if not interp.applies or interp.directive_type == "no_op":
            continue
        adj = interp.structured_adjustment or {}
        hours = adj.get("hours", [])

        if interp.directive_type == "solar_reduction":
            factor = float(adj["factor"])
            for h in hours:
                effective_solar[h] = base_solar_kwh[h] * factor

        elif interp.directive_type == "minimum_battery_reserve":
            value = float(adj["minimum_energy_kwh"])
            for h in hours:
                min_reserve[h] = max(min_reserve[h], value)

        elif interp.directive_type == "no_charge_window":
            no_charge.update(hours)

        elif interp.directive_type == "no_discharge_window":
            no_discharge.update(hours)

        elif interp.directive_type == "max_grid_window":
            cap = float(adj["max_grid_kwh"])
            for h in hours:
                max_grid[h] = min(max_grid[h], cap)

    return OptimizationConstraints(
        effective_solar_kwh=effective_solar,
        min_battery_reserve_kwh=min_reserve,
        no_charge_hours=no_charge,
        no_discharge_hours=no_discharge,
        max_grid_kwh=max_grid,
    )
