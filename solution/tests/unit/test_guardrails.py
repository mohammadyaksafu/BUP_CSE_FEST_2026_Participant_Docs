"""Unit tests for the deterministic guardrails validator."""
from __future__ import annotations

import pytest

from app.guardrails import validate_interpretations


def _ok_interp(idx: int, dtype: str = "no_op", **adj) -> dict:
    base = {"note_index": idx, "applies": False, "directive_type": dtype,
            "structured_adjustment": None, "explanation": "x"}
    if dtype != "no_op":
        base["applies"] = True
        base["structured_adjustment"] = adj
    return base


class TestValidationHappyPath:
    def test_all_no_op(self):
        out = validate_interpretations(
            [_ok_interp(0), _ok_interp(1)], num_notes=2, battery_capacity_kwh=200
        )
        assert all(i.directive_type == "no_op" for i in out)

    def test_solar_reduction_valid(self):
        raw = [_ok_interp(0, "solar_reduction", hours=[12, 13], factor=0.25)]
        out = validate_interpretations(raw, 1, 200)
        assert out[0].directive_type == "solar_reduction"
        assert out[0].applies is True
        assert out[0].structured_adjustment == {"hours": [12, 13], "factor": 0.25}

    def test_minimum_reserve(self):
        raw = [_ok_interp(0, "minimum_battery_reserve", hours=[18], minimum_energy_kwh=100)]
        out = validate_interpretations(raw, 1, 200)
        assert out[0].directive_type == "minimum_battery_reserve"


class TestValidationFailurePath:
    def test_missing_note_falls_back_to_no_op(self):
        # only one entry but two notes expected
        out = validate_interpretations(
            [_ok_interp(0)], num_notes=2, battery_capacity_kwh=200
        )
        assert out[0].directive_type == "no_op"
        assert out[1].directive_type == "no_op"

    def test_duplicate_note_index_falls_back(self):
        raw = [_ok_interp(0, "solar_reduction", hours=[12], factor=0.5),
               _ok_interp(0, "solar_reduction", hours=[13], factor=0.3)]
        out = validate_interpretations(raw, 1, 200)
        assert out[0].directive_type == "no_op"

    def test_no_op_must_have_applies_false(self):
        raw = [{"note_index": 0, "applies": True, "directive_type": "no_op",
                "structured_adjustment": None, "explanation": "x"}]
        out = validate_interpretations(raw, 1, 200)
        assert out[0].directive_type == "no_op"

    def test_invented_directive_type_falls_back(self):
        raw = [{"note_index": 0, "applies": True, "directive_type": "made_up",
                "structured_adjustment": {"hours": [0]}, "explanation": "x"}]
        out = validate_interpretations(raw, 1, 200)
        assert out[0].directive_type == "no_op"

    def test_factor_clamped_to_unit_interval(self):
        raw = [_ok_interp(0, "solar_reduction", hours=[12], factor=1.5)]
        out = validate_interpretations(raw, 1, 200)
        assert out[0].directive_type == "solar_reduction"
        assert out[0].structured_adjustment["factor"] == 1.0

    def test_factor_negative_clamped(self):
        raw = [_ok_interp(0, "solar_reduction", hours=[12], factor=-0.2)]
        out = validate_interpretations(raw, 1, 200)
        assert out[0].structured_adjustment["factor"] == 0.0

    def test_hours_out_of_range_falls_back(self):
        raw = [_ok_interp(0, "solar_reduction", hours=[24], factor=0.5)]
        out = validate_interpretations(raw, 1, 200)
        assert out[0].directive_type == "no_op"

    def test_hours_unsorted_falls_back(self):
        raw = [_ok_interp(0, "solar_reduction", hours=[14, 13], factor=0.5)]
        out = validate_interpretations(raw, 1, 200)
        assert out[0].directive_type == "no_op"

    def test_min_reserve_above_capacity_falls_back(self):
        raw = [_ok_interp(0, "minimum_battery_reserve", hours=[18], minimum_energy_kwh=300)]
        out = validate_interpretations(raw, 1, 200)
        assert out[0].directive_type == "no_op"

    def test_max_grid_negative_falls_back(self):
        raw = [_ok_interp(0, "max_grid_window", hours=[18], max_grid_kwh=-10)]
        out = validate_interpretations(raw, 1, 200)
        assert out[0].directive_type == "no_op"

    def test_empty_entries_ignored(self):
        raw = ["garbage", None, {"note_index": 0, "applies": True,
                                  "directive_type": "no_charge_window",
                                  "structured_adjustment": {"hours": [14]},
                                  "explanation": "x"}]
        out = validate_interpretations(raw, 1, 200)
        assert out[0].directive_type == "no_charge_window"


class TestValidationCountConsistency:
    def test_exactly_num_notes_entries(self):
        out = validate_interpretations([], num_notes=3, battery_capacity_kwh=200)
        assert len(out) == 3
        assert [i.note_index for i in out] == [0, 1, 2]
        assert all(i.directive_type == "no_op" for i in out)
