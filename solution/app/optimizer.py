"""Optimizer: linear program minimizing total grid cost, subject to energy
balance, battery, and directive constraints. Uses PuLP with the bundled CBC."""
from __future__ import annotations

from dataclasses import dataclass

import pulp

from app.directive_engine import OptimizationConstraints
from app.models import BatteryInput, HourInput

TOLERANCE = 0.01


class OptimizationInfeasibleError(Exception):
    """No schedule satisfies all hard constraints."""


@dataclass
class HourResult:
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: str
    battery_kwh: float
    battery_energy_after_kwh: float


def solve(
    hours: list[HourInput],
    battery: BatteryInput,
    constraints: OptimizationConstraints,
) -> list[HourResult]:
    demand = [h.demand_kwh for h in hours]
    tariff = [h.tariff_bdt_per_kwh for h in hours]

    prob = pulp.LpProblem("gridwise_energy_schedule", pulp.LpMinimize)

    grid = [pulp.LpVariable(f"grid_{h}", lowBound=0) for h in range(24)]
    solar_used = [
        pulp.LpVariable(
            f"solar_used_{h}",
            lowBound=0,
            upBound=max(constraints.effective_solar_kwh[h], 0),
        )
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

    prob += pulp.lpSum(grid[h] * tariff[h] for h in range(24))

    for h in range(24):
        prob += (
            grid[h] + solar_used[h] + discharge[h] == demand[h] + charge[h],
            f"balance_{h}",
        )
        prev_energy = battery.initial_energy_kwh if h == 0 else battery_energy[h - 1]
        prob += (
            battery_energy[h] == prev_energy + charge[h] - discharge[h],
            f"battery_state_{h}",
        )
        cap = constraints.max_grid_kwh[h]
        if cap != float("inf"):
            prob += grid[h] <= cap, f"grid_cap_{h}"

    prob += battery_energy[23] == battery.initial_energy_kwh, "eod_neutrality"

    solver = pulp.PULP_CBC_CMD(msg=False)
    prob.solve(solver)

    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        raise OptimizationInfeasibleError(f"solver status: {status}")

        # Round all LP values to a fixed precision so that the validator's
    # per-hour equality checks (energy balance, state transition, EOD
    # neutrality) all hold exactly. 4 decimal places = 0.1 Wh precision,
    # well below any meaningful demand or charge unit.
    DECIMALS = 4

    results: list[HourResult] = []
    for h in range(24):
        c_raw = round(max(pulp.value(charge[h]) or 0.0, 0.0), DECIMALS)
        d_raw = round(max(pulp.value(discharge[h]) or 0.0, 0.0), DECIMALS)
        g_raw = round(max(pulp.value(grid[h]) or 0.0, 0.0), DECIMALS)
        s_raw = round(max(pulp.value(solar_used[h]) or 0.0, 0.0), DECIMALS)
        energy_after_lp = round(
            pulp.value(battery_energy[h]) or 0.0, DECIMALS
        )

        # Treat any net flow under 0.1 Wh as truly idle so the reported
        # action/magnitude are self-consistent with the validator's per-hour
        # energy-balance and state-transition checks.
        net = c_raw - d_raw
        if abs(net) < TOLERANCE:
            action = "idle"
            magnitude = 0.0
        elif net > 0:
            action = "charge"
            magnitude = net
        else:
            action = "discharge"
            magnitude = -net

        # Use the LP's battery_energy[h] directly — it is consistent with
        # the per-hour state-transition constraint at LP precision. The
        # validator's check `prev + c - d == energy_after` then matches
        # within rounding (since the LP itself satisfies the equation).
        energy_after = energy_after_lp

        # Re-derive grid so the per-hour energy balance
        # `grid + solar + discharge = demand + charge` holds exactly with
        # the snapped battery action.
        g_canonical = round(demand[h] - s_raw + c_raw - d_raw, DECIMALS)
        if g_canonical < 0:
            # Edge case: snap pushed grid negative. Trust the LP value
            # since it's the actual optimal solution; the rounding error is
            # small enough that no hard constraint is violated.
            g_canonical = g_raw

        results.append(
            HourResult(
                hour=h,
                grid_kwh=g_canonical,
                solar_used_kwh=s_raw,
                battery_action=action,
                battery_kwh=magnitude,
                battery_energy_after_kwh=energy_after,
            )
        )

    return results
