"""Runs every case in the public sample pack against a running GridWise LLM
service and checks structural/constraint validity of the response. This does
NOT byte-compare against the reference schedule (equivalent optimal schedules
are accepted) but it does verify every hard constraint from the Problem
Statement, plus totals consistency.

Usage:
    python tests/test_public_samples.py [base_url]

Default base_url: http://localhost:8000
"""
import json
import sys
from pathlib import Path

import httpx

TOLERANCE = 0.01
SAMPLE_PATH = Path(__file__).resolve().parents[2] / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"


def check_response(case_input: dict, resp: dict) -> list[str]:
    errors = []

    if resp.get("scenario_id") != case_input["scenario_id"]:
        errors.append("scenario_id mismatch")

    notes = case_input["operator_notes"]
    interp = resp.get("directive_interpretation", [])
    if len(interp) != len(notes):
        errors.append(f"expected {len(notes)} directive_interpretation entries, got {len(interp)}")
    for i, entry in enumerate(interp):
        if entry.get("note_index") != i:
            errors.append(f"directive_interpretation[{i}].note_index != {i}")
        if entry.get("directive_type") == "no_op":
            if entry.get("applies") is not False or entry.get("structured_adjustment") is not None:
                errors.append(f"note {i}: no_op must have applies=false and null adjustment")
        else:
            if entry.get("applies") is not True:
                errors.append(f"note {i}: non-no_op directive must have applies=true")
            adj = entry.get("structured_adjustment") or {}
            hours = adj.get("hours", [])
            if hours != sorted(set(hours)) or any(not (0 <= h <= 23) for h in hours):
                errors.append(f"note {i}: hours array invalid (must be unique, ascending, 0-23)")

    plan = resp.get("hourly_plan", [])
    if len(plan) != 24 or {p["hour"] for p in plan} != set(range(24)):
        errors.append("hourly_plan must contain exactly hours 0-23")
        return errors

    plan_by_hour = {p["hour"]: p for p in plan}
    hours_by_num = {h["hour"]: h for h in case_input["hours"]}
    battery = case_input["battery"]

    # Build effective constraints from the reported (validated) interpretation only
    # as a structural sanity check (ground-truth directive checking happens on the
    # judge side; here we just check the response is internally consistent).
    effective_solar = {h: hours_by_num[h]["solar_kwh"] for h in range(24)}
    min_reserve = {h: battery["minimum_energy_kwh"] for h in range(24)}
    no_charge, no_discharge = set(), set()
    max_grid = {h: float("inf") for h in range(24)}

    for entry in interp:
        if not entry.get("applies"):
            continue
        adj = entry.get("structured_adjustment") or {}
        hrs = adj.get("hours", [])
        t = entry.get("directive_type")
        if t == "solar_reduction":
            for h in hrs:
                effective_solar[h] = hours_by_num[h]["solar_kwh"] * adj["factor"]
        elif t == "minimum_battery_reserve":
            for h in hrs:
                min_reserve[h] = max(min_reserve[h], adj["minimum_energy_kwh"])
        elif t == "no_charge_window":
            no_charge.update(hrs)
        elif t == "no_discharge_window":
            no_discharge.update(hrs)
        elif t == "max_grid_window":
            for h in hrs:
                max_grid[h] = min(max_grid[h], adj["max_grid_kwh"])

    prev_energy = battery["initial_energy_kwh"]
    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0
    for h in range(24):
        p = plan_by_hour[h]
        demand = hours_by_num[h]["demand_kwh"]
        tariff = hours_by_num[h]["tariff_bdt_per_kwh"]
        grid = p["grid_kwh"]
        solar = p["solar_used_kwh"]
        action = p["battery_action"]
        mag = p["battery_kwh"]
        after = p["battery_energy_after_kwh"]

        charge = mag if action == "charge" else 0.0
        discharge = mag if action == "discharge" else 0.0

        if solar > effective_solar[h] + TOLERANCE:
            errors.append(f"hour {h}: solar_used exceeds effective solar")
        if h in no_charge and charge > TOLERANCE:
            errors.append(f"hour {h}: charged during no_charge_window")
        if h in no_discharge and discharge > TOLERANCE:
            errors.append(f"hour {h}: discharged during no_discharge_window")
        if charge > battery["max_charge_kwh_per_hour"] + TOLERANCE:
            errors.append(f"hour {h}: charge exceeds max rate")
        if discharge > battery["max_discharge_kwh_per_hour"] + TOLERANCE:
            errors.append(f"hour {h}: discharge exceeds max rate")
        if grid > max_grid[h] + TOLERANCE:
            errors.append(f"hour {h}: grid exceeds max_grid_window cap")
        if abs((grid + solar + discharge) - (demand + charge)) > TOLERANCE:
            errors.append(f"hour {h}: energy balance violated")
        if abs((prev_energy + charge - discharge) - after) > TOLERANCE:
            errors.append(f"hour {h}: battery transition inconsistent")
        if after < min_reserve[h] - TOLERANCE:
            errors.append(f"hour {h}: battery below effective min reserve")
        if after > battery["capacity_kwh"] + TOLERANCE:
            errors.append(f"hour {h}: battery exceeds capacity")

        prev_energy = after
        total_grid += grid
        total_cost += grid * tariff
        peak_grid = max(peak_grid, grid)

    if abs(prev_energy - battery["initial_energy_kwh"]) > TOLERANCE:
        errors.append("end-of-day battery neutrality violated")

    if abs(total_grid - resp.get("total_grid_kwh", -1)) > TOLERANCE:
        errors.append("total_grid_kwh does not match hourly_plan sum")
    if abs(total_cost - resp.get("total_cost_bdt", -1)) > TOLERANCE:
        errors.append("total_cost_bdt does not match hourly_plan sum")
    if abs(peak_grid - resp.get("peak_grid_kwh", -1)) > TOLERANCE:
        errors.append("peak_grid_kwh does not match hourly_plan max")

    return errors


def main():
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    data = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]

    passed = 0
    for case in cases:
        case_id = case["id"]
        case_input = case["input"]
        try:
            resp = httpx.post(f"{base_url}/optimize-energy", json=case_input, timeout=35.0)
        except httpx.HTTPError as exc:
            print(f"[FAIL] {case_id}: request error: {exc}")
            continue

        if resp.status_code != 200:
            print(f"[FAIL] {case_id}: HTTP {resp.status_code}: {resp.text[:300]}")
            continue

        errors = check_response(case_input, resp.json())
        if errors:
            print(f"[FAIL] {case_id}: {'; '.join(errors[:5])}")
        else:
            print(f"[PASS] {case_id}")
            passed += 1

    print(f"\n{passed}/{len(cases)} cases passed structural validation")


if __name__ == "__main__":
    main()
