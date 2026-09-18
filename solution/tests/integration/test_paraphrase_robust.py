"""PARAPHRASE ROBUSTNESS TEST: same directive, many phrasings.

The system must produce equivalent (or equivalent-enough) interpretations
across dozens of rewordings of the same underlying directive. This protects
against LLM drift and exercises the rule-based fallback path too.
"""
from __future__ import annotations

import pytest

from app.rule_interpreter import interpret_notes


SOLAR_PARAPHRASES = [
    ("Solar output will drop to about 25% from 1 PM to 3 PM.",
     "solar_reduction", [13, 14], 0.25),
    ("Wash the rooftop solar panels from noon until 2 PM, usable solar should be treated as roughly 25% of the forecast.",
     "solar_reduction", [12, 13], 0.25),
    ("Expect an 80% reduction in rooftop solar during the 1–3 PM maintenance window.",
     "solar_reduction", [13, 14], 0.20),
    ("PV production will drop to about 20% between 13:00 and 15:00.",
     "solar_reduction", [13, 14], 0.20),
    ("Tree shadow from 3 PM to 5 PM will halve the solar output.",
     "solar_reduction", [15, 16], 0.50),
    ("Panels dirty from 11 AM to 1 PM, drop to ~25%.",
     "solar_reduction", [11, 12], 0.25),
]

NO_CHARGE_PARAPHRASES = [
    ("The battery charger will be isolated from 2 AM until 5 AM for electrical maintenance.",
     "no_charge_window", [2, 3, 4]),
    ("Do not charge the battery between 2 PM and 4 PM.",
     "no_charge_window", [14, 15]),
    ("Charging is unavailable from 6 PM to 9 PM.",
     "no_charge_window", [18, 19, 20]),
    ("Charger maintenance from 10:00 to 12:00.",
     "no_charge_window", [10, 11]),
]

NO_DISCHARGE_PARAPHRASES = [
    ("For protection testing, the battery must not discharge from 6 PM until 8 PM.",
     "no_discharge_window", [18, 19]),
    ("Battery discharge disabled from 14:00 to 16:00 for testing.",
     "no_discharge_window", [14, 15]),
    ("Do not discharge the battery between 8 PM and 10 PM.",
     "no_discharge_window", [20, 21]),
]

RESERVE_PARAPHRASES = [
    ("Keep at least 100 kWh from 7 PM to 9 PM.",
     "minimum_battery_reserve", [19, 20], 100),
    ("Keep at least 50% of the battery capacity from 6 PM until 9 PM.",
     "minimum_battery_reserve", [18, 19, 20], 100),  # 50% of 200
    ("Reserve of 80 kWh from 5 PM to 7 PM.",
     "minimum_battery_reserve", [17, 18], 80),
]

GRID_CAP_PARAPHRASES = [
    ("From 6 PM until 9 PM, campus grid import must not exceed 155 kWh in any hour because the feeder is operating under a temporary limit.",
     "max_grid_window", [18, 19, 20], 155),
    ("Feeder limit 175 kWh from 5 PM to 7 PM.",
     "max_grid_window", [17, 18], 175),
    ("Grid import capped at 100 kWh from 14:00 to 16:00.",
     "max_grid_window", [14, 15], 100),
]

DISTRACTOR_PARAPHRASES = [
    "The sports office moved next month's registration deadline.",
    "Cafeteria menu changes tomorrow.",
    "HR announced a holiday schedule.",
    "Parcel delivery at 9 AM.",
    "Team meeting tomorrow at 10 AM.",
]


class TestParaphraseRobustness:
    @pytest.mark.parametrize("text,expected_type,expected_hours,expected_factor",
                             SOLAR_PARAPHRASES)
    def test_solar_reduction_paraphrases(self, text, expected_type, expected_hours, expected_factor):
        out = interpret_notes([text], battery_capacity_kwh=200)
        d = out[0]
        assert d["directive_type"] == expected_type
        assert d["applies"] is True
        assert set(d["structured_adjustment"]["hours"]) == set(expected_hours)
        assert d["structured_adjustment"]["factor"] == pytest.approx(expected_factor, abs=0.05)

    @pytest.mark.parametrize("text,expected_type,expected_hours",
                             NO_CHARGE_PARAPHRASES)
    def test_no_charge_paraphrases(self, text, expected_type, expected_hours):
        out = interpret_notes([text], battery_capacity_kwh=200)
        d = out[0]
        assert d["directive_type"] == expected_type
        assert d["applies"] is True
        assert set(d["structured_adjustment"]["hours"]) == set(expected_hours)

    @pytest.mark.parametrize("text,expected_type,expected_hours",
                             NO_DISCHARGE_PARAPHRASES)
    def test_no_discharge_paraphrases(self, text, expected_type, expected_hours):
        out = interpret_notes([text], battery_capacity_kwh=200)
        d = out[0]
        assert d["directive_type"] == expected_type
        assert d["applies"] is True
        assert set(d["structured_adjustment"]["hours"]) == set(expected_hours)

    @pytest.mark.parametrize("text,expected_type,expected_hours,expected_kwh",
                             RESERVE_PARAPHRASES)
    def test_reserve_paraphrases(self, text, expected_type, expected_hours, expected_kwh):
        out = interpret_notes([text], battery_capacity_kwh=200)
        d = out[0]
        assert d["directive_type"] == expected_type
        assert d["applies"] is True
        assert set(d["structured_adjustment"]["hours"]) == set(expected_hours)
        assert d["structured_adjustment"]["minimum_energy_kwh"] == pytest.approx(expected_kwh, abs=1.0)

    @pytest.mark.parametrize("text,expected_type,expected_hours,expected_kwh",
                             GRID_CAP_PARAPHRASES)
    def test_grid_cap_paraphrases(self, text, expected_type, expected_hours, expected_kwh):
        out = interpret_notes([text], battery_capacity_kwh=200)
        d = out[0]
        assert d["directive_type"] == expected_type
        assert d["applies"] is True
        assert set(d["structured_adjustment"]["hours"]) == set(expected_hours)
        assert d["structured_adjustment"]["max_grid_kwh"] == pytest.approx(expected_kwh, abs=1.0)

    @pytest.mark.parametrize("text", DISTRACTOR_PARAPHRASES)
    def test_distractor_paraphrases(self, text):
        out = interpret_notes([text], battery_capacity_kwh=200)
        d = out[0]
        assert d["directive_type"] == "no_op"
        assert d["applies"] is False