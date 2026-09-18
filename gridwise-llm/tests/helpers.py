"""Shared helpers for building synthetic 24-hour GridWise request payloads."""
from typing import Any, Dict, List, Optional


def make_battery(**overrides) -> Dict[str, Any]:
    battery = {
        "capacity_kwh": 200,
        "initial_energy_kwh": 100,
        "minimum_energy_kwh": 20,
        "max_charge_kwh_per_hour": 50,
        "max_discharge_kwh_per_hour": 50,
    }
    battery.update(overrides)
    return battery


def make_scenario(
    scenario_id: str,
    operator_notes: List[str],
    demand: Optional[List[float]] = None,
    solar: Optional[List[float]] = None,
    tariff: Optional[List[float]] = None,
    battery: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Builds a structurally valid 24-hour request payload. Defaults describe a
    simple campus: flat 100 kWh/h demand, solar 0 at night ramping to a midday
    peak, and a day/night tariff split (cheap 0-6 & 22-23, expensive 18-21).
    """
    if demand is None:
        demand = [100.0] * 24
    if solar is None:
        solar = [0, 0, 0, 0, 0, 0, 10, 40, 80, 120, 150, 160, 160, 150, 120, 80, 40, 10, 0, 0, 0, 0, 0, 0]
    if tariff is None:
        tariff = [
            5, 5, 5, 5, 5, 5, 6, 7, 7, 7, 7, 7, 7, 7, 7, 8, 9,
            10, 12, 12, 12, 12, 8, 6,
        ]
    assert len(demand) == len(solar) == len(tariff) == 24

    return {
        "scenario_id": scenario_id,
        "operator_notes": operator_notes,
        "hours": [
            {"hour": h, "demand_kwh": demand[h], "solar_kwh": solar[h], "tariff_bdt_per_kwh": tariff[h]}
            for h in range(24)
        ],
        "battery": battery or make_battery(),
    }


def make_interpretation(
    note_index: int,
    directive_type: str,
    hours: Optional[List[int]] = None,
    applies: bool = True,
    explanation: str = "test",
    **adjustment_extra,
) -> Dict[str, Any]:
    """Builds a raw (pre-guardrail) LLM-style interpretation dict for a single note."""
    if directive_type == "no_op":
        return {
            "note_index": note_index,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": explanation,
        }
    adjustment = {"hours": hours or [], **adjustment_extra}
    return {
        "note_index": note_index,
        "applies": applies,
        "directive_type": directive_type,
        "structured_adjustment": adjustment,
        "explanation": explanation,
    }
