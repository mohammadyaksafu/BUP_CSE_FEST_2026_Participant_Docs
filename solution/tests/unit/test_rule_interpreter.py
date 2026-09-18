"""Unit tests for the deterministic rule interpreter."""
from __future__ import annotations

import pytest

from app.rule_interpreter import (
    _extract_grid_cap_kwh,
    _extract_reserve_kwh,
    _extract_solar_factor,
    _extract_time_window,
    _looks_like_distractor,
    interpret_notes,
)


# ---------- Time-window extractor ---------------------------------------------


class TestTimeWindowExtraction:
    def test_1pm_to_3pm_is_13_14(self):
        assert _extract_time_window("wash panels 1 PM to 3 PM") == [13, 14]

    def test_2pm_to_4pm_is_14_15(self):
        assert _extract_time_window("do not charge 2 PM to 4 PM") == [14, 15]

    def test_6pm_to_9pm_is_18_19_20(self):
        assert _extract_time_window("from 6 PM until 9 PM") == [18, 19, 20]

    def test_2am_to_5am(self):
        assert _extract_time_window("charger isolated 2 AM until 5 AM") == [2, 3, 4]

    def test_24h_clock(self):
        assert _extract_time_window("between 13:00 and 15:00") == [13, 14]

    def test_hours_bare(self):
        assert _extract_time_window("hours 14-16") == [14, 15]

    def test_from_hour_to(self):
        assert _extract_time_window("from hour 14 to 16") == [14, 15]

    def test_single_hour_at_pm(self):
        assert _extract_time_window("test at 2 PM") == [14]

    def test_single_hour_at_bare(self):
        assert _extract_time_window("test at hour 14") == [14]

    def test_no_window_returns_none(self):
        assert _extract_time_window("sports office moved next month") is None


# ---------- Distractor classifier ----------------------------------------------


class TestDistractorClassifier:
    @pytest.mark.parametrize(
        "text",
        [
            "The sports office moved next month's registration deadline.",
            "The cafeteria menu changes tomorrow.",
            "Team meeting tomorrow at 10 AM.",
            "HR announced a new holiday schedule.",
            "Parcel delivery at 9 AM.",
        ],
    )
    def test_distractor_phrases(self, text):
        assert _looks_like_distractor(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Solar output will drop to about 20% from 1 PM to 3 PM.",
            "Charger maintenance 2 AM until 5 AM.",
            "Grid import must not exceed 155 kWh from 6 PM to 9 PM.",
            "Battery reserve 100 kWh 7 PM to 9 PM.",
        ],
    )
    def test_real_notes_not_distractors(self, text):
        assert _looks_like_distractor(text) is False


# ---------- Solar factor extraction --------------------------------------------


class TestSolarFactor:
    def test_drops_to_pct(self):
        assert _extract_solar_factor("solar drops to 25%") == 0.25

    def test_reduced_to_pct(self):
        assert _extract_solar_factor("reduced to about 20%") == 0.20

    def test_pct_reduction(self):
        assert _extract_solar_factor("80% reduction in solar") == pytest.approx(0.20)

    def test_factor_explicit(self):
        assert _extract_solar_factor("factor 0.4") == 0.4

    def test_roughly_quarter(self):
        assert _extract_solar_factor("roughly a quarter of normal") == 0.25

    def test_panels_out(self):
        assert _extract_solar_factor("3 of 4 panels out") == 0.25

    def test_half(self):
        assert _extract_solar_factor("about half of normal") == 0.5


# ---------- Reserve extraction -------------------------------------------------


class TestReserveExtraction:
    def test_absolute_kwh(self):
        assert _extract_reserve_kwh("keep at least 100 kWh", 200) == 100

    def test_percentage(self):
        assert _extract_reserve_kwh("50% of battery capacity", 200) == 100

    def test_reserve_of(self):
        assert _extract_reserve_kwh("reserve of 80 kWh", 200) == 80


# ---------- Grid cap extraction -----------------------------------------------


class TestGridCap:
    def test_not_exceed(self):
        assert _extract_grid_cap_kwh("grid import must not exceed 155 kWh") == 155

    def test_capped_at(self):
        assert _extract_grid_cap_kwh("feeder capped at 175 kWh") == 175


# ---------- End-to-end interpret_notes -----------------------------------------


class TestInterpretNotesEnd2End:
    def test_distractor_yields_no_op(self):
        out = interpret_notes(["Sports office moved deadline"], 200)
        assert out[0]["directive_type"] == "no_op"
        assert out[0]["applies"] is False

    def test_solar_reduction(self):
        out = interpret_notes(
            ["Wash the rooftop solar panels from noon until 2 PM, usable solar should be treated as roughly 25% of the forecast."],
            200,
        )
        assert out[0]["directive_type"] == "solar_reduction"
        assert out[0]["applies"] is True
        adj = out[0]["structured_adjustment"]
        assert adj["hours"] == [12, 13]
        assert adj["factor"] == 0.25

    def test_no_charge_window(self):
        out = interpret_notes(
            ["The battery charger will be isolated from 2 AM until 5 AM for electrical maintenance."],
            200,
        )
        assert out[0]["directive_type"] == "no_charge_window"
        assert out[0]["applies"] is True
        assert out[0]["structured_adjustment"]["hours"] == [2, 3, 4]

    def test_minimum_battery_reserve_pct(self):
        out = interpret_notes(
            ["Keep at least 50% of the battery capacity stored in the battery from 6 PM until 9 PM."],
            200,
        )
        assert out[0]["directive_type"] == "minimum_battery_reserve"
        assert out[0]["structured_adjustment"]["hours"] == [18, 19, 20]
        assert out[0]["structured_adjustment"]["minimum_energy_kwh"] == 100

    def test_no_discharge_window(self):
        out = interpret_notes(
            ["For protection testing, the battery must not discharge from 6 PM until 8 PM."],
            200,
        )
        assert out[0]["directive_type"] == "no_discharge_window"
        assert out[0]["structured_adjustment"]["hours"] == [18, 19]

    def test_max_grid_window(self):
        out = interpret_notes(
            ["From 6 PM until 9 PM, campus grid import must not exceed 155 kWh in any hour."],
            200,
        )
        assert out[0]["directive_type"] == "max_grid_window"
        assert out[0]["structured_adjustment"]["hours"] == [18, 19, 20]
        assert out[0]["structured_adjustment"]["max_grid_kwh"] == 155

    def test_count_matches_input(self):
        notes = ["Sports deadline moved", "Wash panels 1 PM to 3 PM"]
        out = interpret_notes(notes, 200)
        assert len(out) == 2
        assert [d["note_index"] for d in out] == [0, 1]
