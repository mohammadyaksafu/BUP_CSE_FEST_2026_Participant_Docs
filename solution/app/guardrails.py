"""Deterministic guardrails. Validates raw LLM (or rule-interpreter) output
before it reaches the optimizer. On any per-note failure, that note is safely
downgraded to a no_op; we never crash and never invent directives."""
from __future__ import annotations

import logging
from typing import Any

from app.models import DIRECTIVE_TYPES, DirectiveInterpretation

logger = logging.getLogger("gridwise.guardrails")


def _safe_no_op(note_index: int, reason: str) -> DirectiveInterpretation:
    logger.warning("note_index=%d guardrail fallback to no_op: %s", note_index, reason)
    return DirectiveInterpretation(
        note_index=note_index,
        applies=False,
        directive_type="no_op",
        structured_adjustment=None,
        explanation=f"Safe fallback: {reason}",
    )


def _validate_hours(hours: Any) -> list[int]:
    if not isinstance(hours, list) or len(hours) == 0:
        raise ValueError("hours must be a non-empty list")
    int_hours: list[int] = []
    for h in hours:
        if isinstance(h, bool) or not isinstance(h, int):
            raise ValueError(f"hour {h!r} is not an integer")
        if h < 0 or h > 23:
            raise ValueError(f"hour {h} out of range 0-23")
        int_hours.append(h)
    if len(set(int_hours)) != len(int_hours):
        raise ValueError("hours contains duplicates")
    if int_hours != sorted(int_hours):
        raise ValueError("hours must be in ascending order")
    return int_hours


def _clamp_factor(value: Any) -> float:
    """Clamp a factor to [0,1] rather than rejecting out-of-range values.
    Defensive against LLM drift; logs and clamps."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("factor must be numeric")
    f = float(value)
    if f < 0 or f > 1:
        logger.warning("factor %s out of range, clamping to [0,1]", f)
        f = max(0.0, min(1.0, f))
    return f


def _validate_adjustment(
    directive_type: str, adjustment: Any, battery_capacity_kwh: float
) -> dict[str, Any]:
    if not isinstance(adjustment, dict):
        raise ValueError("structured_adjustment must be an object")

    hours = _validate_hours(adjustment.get("hours"))

    if directive_type == "solar_reduction":
        return {"hours": hours, "factor": _clamp_factor(adjustment.get("factor"))}

    if directive_type == "minimum_battery_reserve":
        value = adjustment.get("minimum_energy_kwh")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("minimum_energy_kwh must be numeric")
        value = float(value)
        if value < 0 or value > battery_capacity_kwh:
            raise ValueError("minimum_energy_kwh out of range 0-capacity")
        return {"hours": hours, "minimum_energy_kwh": value}

    if directive_type == "no_charge_window":
        return {"hours": hours}

    if directive_type == "no_discharge_window":
        return {"hours": hours}

    if directive_type == "max_grid_window":
        value = adjustment.get("max_grid_kwh")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("max_grid_kwh must be numeric")
        value = float(value)
        if value < 0:
            raise ValueError("max_grid_kwh must be >= 0")
        return {"hours": hours, "max_grid_kwh": value}

    raise ValueError(f"unsupported directive_type {directive_type!r}")


def validate_interpretations(
    raw_interpretations: list[dict[str, Any]],
    num_notes: int,
    battery_capacity_kwh: float,
) -> list[DirectiveInterpretation]:
    """Validate raw interpreter output. Always returns exactly num_notes
    entries, in note_index order. Any failing entry is replaced with a
    per-note no_op interpretation."""
    by_index: dict[int, Any] = {}
    for entry in raw_interpretations:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("note_index")
        if isinstance(idx, bool) or not isinstance(idx, int):
            continue
        if idx in by_index:
            by_index[idx] = "__DUPLICATE__"
            continue
        by_index[idx] = entry

    results: list[DirectiveInterpretation] = []
    for idx in range(num_notes):
        entry = by_index.get(idx)

        if entry is None:
            results.append(_safe_no_op(idx, "missing interpretation for this note"))
            continue
        if entry == "__DUPLICATE__":
            results.append(_safe_no_op(idx, "duplicate note_index in interpreter output"))
            continue

        try:
            directive_type = entry.get("directive_type")
            if directive_type not in DIRECTIVE_TYPES:
                raise ValueError(f"unsupported directive_type {directive_type!r}")

            applies = entry.get("applies")
            if not isinstance(applies, bool):
                raise ValueError("applies must be a boolean")

            explanation = entry.get("explanation")
            if not isinstance(explanation, str) or not explanation.strip():
                explanation = "No explanation provided."

            if directive_type == "no_op":
                if applies is not False:
                    raise ValueError("no_op must have applies = false")
                results.append(
                    DirectiveInterpretation(
                        note_index=idx,
                        applies=False,
                        directive_type="no_op",
                        structured_adjustment=None,
                        explanation=explanation,
                    )
                )
                continue

            if applies is not True:
                raise ValueError(f"{directive_type} must have applies = true")

            adjustment = _validate_adjustment(
                directive_type, entry.get("structured_adjustment"), battery_capacity_kwh
            )
            results.append(
                DirectiveInterpretation(
                    note_index=idx,
                    applies=True,
                    directive_type=directive_type,
                    structured_adjustment=adjustment,
                    explanation=explanation,
                )
            )
        except (ValueError, TypeError, AttributeError) as exc:
            results.append(_safe_no_op(idx, str(exc)))

    return results
