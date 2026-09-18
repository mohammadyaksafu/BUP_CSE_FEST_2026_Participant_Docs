"""Post-optimization validator. Re-checks the schedule against all hard
constraints. Defense in depth on top of the LP's own constraints."""
from __future__ import annotations

from app.directive_engine import OptimizationConstraints
from app.models import BatteryInput, HourInput
from app.optimizer import HourResult

TOLERANCE = 0.01


class ScheduleInvalidError(Exception):
    pass


def validate_schedule(
    hours: list[HourInput],
    battery: BatteryInput,
    constraints: OptimizationConstraints,
    plan: list[HourResult],
) -> None:
    if len(plan) != 24 or {r.hour for r in plan} != set(range(24)):
        raise ScheduleInvalidError("hourly_plan must contain exactly hours 0-23")

    if len({r.hour for r in plan}) != 24:
        raise ScheduleInvalidError("hourly_plan contains duplicate hours")

    prev_energy = battery.initial_energy_kwh
    for r in sorted(plan, key=lambda x: x.hour):
        h = r.hour
        demand = hours[h].demand_kwh

        if r.grid_kwh < -TOLERANCE or r.solar_used_kwh < -TOLERANCE or r.battery_kwh < -TOLERANCE:
            raise ScheduleInvalidError(f"hour {h}: negative energy value")

        if r.solar_used_kwh > constraints.effective_solar_kwh[h] + TOLERANCE:
            raise ScheduleInvalidError(f"hour {h}: solar_used exceeds effective solar")

        if r.battery_action not in ("charge", "discharge", "idle"):
            raise ScheduleInvalidError(f"hour {h}: invalid battery_action")

        if r.battery_action == "idle" and r.battery_kwh > TOLERANCE:
            raise ScheduleInvalidError(f"hour {h}: idle battery_kwh must be 0")

        charge = r.battery_kwh if r.battery_action == "charge" else 0.0
        discharge = r.battery_kwh if r.battery_action == "discharge" else 0.0

        if h in constraints.no_charge_hours and charge > TOLERANCE:
            raise ScheduleInvalidError(f"hour {h}: charging during no_charge_window")
        if h in constraints.no_discharge_hours and discharge > TOLERANCE:
            raise ScheduleInvalidError(f"hour {h}: discharging during no_discharge_window")

        if charge > battery.max_charge_kwh_per_hour + TOLERANCE:
            raise ScheduleInvalidError(f"hour {h}: charge exceeds max_charge_kwh_per_hour")
        if discharge > battery.max_discharge_kwh_per_hour + TOLERANCE:
            raise ScheduleInvalidError(f"hour {h}: discharge exceeds max_discharge_kwh_per_hour")

        cap = constraints.max_grid_kwh[h]
        if cap != float("inf") and r.grid_kwh > cap + TOLERANCE:
            raise ScheduleInvalidError(f"hour {h}: grid_kwh exceeds max_grid_window cap")

        balance_lhs = r.grid_kwh + r.solar_used_kwh + discharge
        balance_rhs = demand + charge
        if abs(balance_lhs - balance_rhs) > TOLERANCE:
            raise ScheduleInvalidError(f"hour {h}: energy balance violated")

        expected_energy_after = prev_energy + charge - discharge
        if abs(expected_energy_after - r.battery_energy_after_kwh) > TOLERANCE:
            raise ScheduleInvalidError(f"hour {h}: battery state transition inconsistent")

        min_reserve = constraints.min_battery_reserve_kwh[h]
        if r.battery_energy_after_kwh < min_reserve - TOLERANCE:
            raise ScheduleInvalidError(f"hour {h}: battery below effective minimum reserve")
        if r.battery_energy_after_kwh > battery.capacity_kwh + TOLERANCE:
            raise ScheduleInvalidError(f"hour {h}: battery exceeds capacity")

        prev_energy = r.battery_energy_after_kwh

    if abs(prev_energy - battery.initial_energy_kwh) > TOLERANCE:
        raise ScheduleInvalidError("end-of-day battery neutrality violated")
