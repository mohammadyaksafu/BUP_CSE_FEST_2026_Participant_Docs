"""Optimizer: linear program that minimizes total grid cost subject to the
energy balance, battery, and directive constraints defined in the Problem
Statement. Uses PuLP with the bundled CBC solver.
"""
from dataclasses import dataclass
from typing import List

import pulp

from app.directive_engine import OptimizationConstraints
from app.models import BatteryInput, HourInput

TOLERANCE = 0.01


class OptimizationInfeasibleError(Exception):
    """Raised when no schedule satisfies all hard constraints."""


@dataclass
class HourResult:
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: str
    battery_kwh: float
    battery_energy_after_kwh: float


def solve(
    hours: List[HourInput],
    battery: BatteryInput,
    constraints: OptimizationConstraints,
) -> List[HourResult]:
    demand = [h.demand_kwh for h in hours]
    tariff = [h.tariff_bdt_per_kwh for h in hours]

    for h in range(24):
        if constraints.min_battery_reserve_kwh[h] > battery.capacity_kwh + TOLERANCE:
            raise OptimizationInfeasibleError(
                f"hour {h}: effective minimum reserve ({constraints.min_battery_reserve_kwh[h]}) "
                f"exceeds battery capacity ({battery.capacity_kwh})"
            )

    prob = pulp.LpProblem("gridwise_energy_schedule", pulp.LpMinimize)

    grid = [pulp.LpVariable(f"grid_{h}", lowBound=0) for h in range(24)]
    solar_used = [
        pulp.LpVariable(f"solar_used_{h}", lowBound=0, upBound=max(constraints.effective_solar_kwh[h], 0))
        for h in range(24)
    ]
    charge = [
        pulp.LpVariable(
            f"charge_{h}",
            lowBound=0,
            upBound=0 if h in constraints.no_charge_hours else battery.max_charge_kwh_per_hour,
        )
        for h in range(24)
    ]
    discharge = [
        pulp.LpVariable(
            f"discharge_{h}",
            lowBound=0,
            upBound=0 if h in constraints.no_discharge_hours else battery.max_discharge_kwh_per_hour,
        )
        for h in range(24)
    ]
    battery_energy = [
        pulp.LpVariable(
            f"battery_energy_{h}",
            lowBound=constraints.min_battery_reserve_kwh[h],
            upBound=battery.capacity_kwh,
        )
        for h in range(24)
    ]

    # Objective: minimize total grid cost
    prob += pulp.lpSum(grid[h] * tariff[h] for h in range(24))

    for h in range(24):
        # Energy balance
        prob += grid[h] + solar_used[h] + discharge[h] == demand[h] + charge[h], f"balance_{h}"

        # Battery state transition
        prev_energy = battery.initial_energy_kwh if h == 0 else battery_energy[h - 1]
        prob += battery_energy[h] == prev_energy + charge[h] - discharge[h], f"battery_state_{h}"

        # max_grid_window cap
        cap = constraints.max_grid_kwh[h]
        if cap != float("inf"):
            prob += grid[h] <= cap, f"grid_cap_{h}"

    # End-of-day battery neutrality
    prob += battery_energy[23] == battery.initial_energy_kwh, "eod_neutrality"

    solver = pulp.PULP_CBC_CMD(msg=False)
    try:
        prob.solve(solver)
    except pulp.PulpError as exc:
        # CBC can fail to even run (rather than reporting "Infeasible") when given
        # a structurally invalid model, e.g. a variable with lowBound > upBound.
        raise OptimizationInfeasibleError(f"solver could not run: {exc}") from exc

    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        raise OptimizationInfeasibleError(f"solver status: {status}")

    results: List[HourResult] = []
    for h in range(24):
        c = max(pulp.value(charge[h]) or 0.0, 0.0)
        d = max(pulp.value(discharge[h]) or 0.0, 0.0)
        g = max(pulp.value(grid[h]) or 0.0, 0.0)
        s = max(pulp.value(solar_used[h]) or 0.0, 0.0)
        energy_after = pulp.value(battery_energy[h]) or 0.0

        # Net any degenerate simultaneous charge+discharge (never changes battery_energy).
        net = c - d
        if abs(net) <= TOLERANCE:
            action = "idle"
            magnitude = 0.0
        elif net > 0:
            action = "charge"
            magnitude = net
        else:
            action = "discharge"
            magnitude = -net

        results.append(
            HourResult(
                hour=h,
                grid_kwh=round(g, 6),
                solar_used_kwh=round(s, 6),
                battery_action=action,
                battery_kwh=round(magnitude, 6),
                battery_energy_after_kwh=round(energy_after, 6),
            )
        )

    return results
