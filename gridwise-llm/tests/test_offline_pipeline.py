"""Offline pipeline test — exercises guardrails + directive engine + optimizer +
schedule validator WITHOUT calling the real LLM, using mocked raw interpretation
output for each public sample case's known ground-truth directive. This lets the
non-LLM part of the pipeline be verified before a real Anthropic key is available.

Usage:
    python tests/test_offline_pipeline.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.directive_engine import build_constraints
from app.guardrails import validate_interpretations
from app.models import BatteryInput, HourInput
from app.optimizer import solve
from app.schedule_validator import validate_schedule

SAMPLE_PATH = Path(__file__).resolve().parents[2] / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"


def main():
    data = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    passed = 0
    for case in data["cases"]:
        case_id = case["id"]
        inp = case["input"]
        expected = case.get("expected_output", case.get("expected"))

        # Use the reference ground-truth interpretation as the "mocked LLM output" —
        # this isolates guardrails/optimizer/validator correctness from LLM accuracy.
        raw = expected["directive_interpretation"]

        battery = BatteryInput(**inp["battery"])
        hours = [HourInput(**h) for h in inp["hours"]]

        interpretations = validate_interpretations(raw, len(inp["operator_notes"]), battery.capacity_kwh)
        base_solar = [h.solar_kwh for h in sorted(hours, key=lambda h: h.hour)]
        constraints = build_constraints(interpretations, base_solar, battery.minimum_energy_kwh)

        try:
            plan = solve(sorted(hours, key=lambda h: h.hour), battery, constraints)
            validate_schedule(sorted(hours, key=lambda h: h.hour), battery, constraints, plan)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {case_id}: {exc}")
            continue

        total_grid = sum(r.grid_kwh for r in plan)
        total_cost = sum(r.grid_kwh * next(h.tariff_bdt_per_kwh for h in hours if h.hour == r.hour) for r in plan)
        print(f"[PASS] {case_id}: total_grid_kwh={total_grid:.2f} total_cost_bdt={total_cost:.2f}")
        passed += 1

    print(f"\n{passed}/{len(data['cases'])} cases passed offline pipeline validation "
          f"(guardrails + optimizer + schedule validator, ground-truth directives, no live LLM call)")


if __name__ == "__main__":
    main()
