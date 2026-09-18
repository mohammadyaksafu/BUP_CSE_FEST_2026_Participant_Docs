"""LLM/prompt tests: exercise the REAL configured LLM provider (no stubbing)
through the full /optimize-energy endpoint, to verify semantic interpretation
correctness and paraphrase robustness — i.e. that the model extracts the right
directive_type / hours / numeric values from natural language it has never
seen verbatim (all phrasings here are hand-written, not copied from the
public sample pack).

Marked `llm` and skipped automatically if no provider API key is configured.
Paced with a short delay between calls to stay within free-tier rate limits.
Run explicitly with:  pytest -m llm -v
"""
import time

import pytest

from app import config
from tests.helpers import make_battery, make_scenario

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not config.LLM_API_KEY, reason="no LLM_API_KEY configured"),
]


@pytest.fixture(autouse=True)
def _pace_llm_calls():
    yield
    time.sleep(3)  # stay well under free-tier rate limits between live calls


def _interp(client, notes, **battery_overrides):
    payload = make_scenario("llm-test", notes, battery=make_battery(**battery_overrides) if battery_overrides else None)
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["directive_interpretation"]


def test_llm_solar_reduction_percentage_phrasing(client):
    result = _interp(client, ["PV output will fall to roughly 20% of normal between 1pm and 3pm today."])
    e = result[0]
    assert e["applies"] is True
    assert e["directive_type"] == "solar_reduction"
    assert e["structured_adjustment"]["hours"] == [13, 14]
    assert abs(e["structured_adjustment"]["factor"] - 0.2) <= 0.05


def test_llm_solar_reduction_alternate_wording(client):
    result = _interp(client, ["Because of scheduled panel washing, expect an 80% drop in usable solar from 9am to 11am."])
    e = result[0]
    assert e["directive_type"] == "solar_reduction"
    assert e["structured_adjustment"]["hours"] == [9, 10]
    assert abs(e["structured_adjustment"]["factor"] - 0.2) <= 0.05


def test_llm_no_charge_window_phrasing(client):
    result = _interp(client, ["The charging circuit breaker will be locked out from 2am to 5am for repairs."])
    e = result[0]
    assert e["directive_type"] == "no_charge_window"
    assert e["structured_adjustment"]["hours"] == [2, 3, 4]


def test_llm_no_discharge_window_phrasing(client):
    result = _interp(client, ["For safety testing, make sure the battery is not drained at all between 9pm and 11pm."])
    e = result[0]
    assert e["directive_type"] == "no_discharge_window"
    assert e["structured_adjustment"]["hours"] == [21, 22]


def test_llm_max_grid_window_phrasing(client):
    result = _interp(client, ["Substation limits mean we can't pull more than 120 kWh per hour from the grid between 5pm and 7pm."])
    e = result[0]
    assert e["directive_type"] == "max_grid_window"
    assert e["structured_adjustment"]["hours"] == [17, 18]
    assert abs(e["structured_adjustment"]["max_grid_kwh"] - 120) <= 1.0


def test_llm_minimum_battery_reserve_absolute_phrasing(client):
    result = _interp(client, ["We need a minimum of 60 kWh sitting in the battery at all times from 6pm through 9pm."])
    e = result[0]
    assert e["directive_type"] == "minimum_battery_reserve"
    assert e["structured_adjustment"]["hours"] == [18, 19, 20]
    assert abs(e["structured_adjustment"]["minimum_energy_kwh"] - 60) <= 1.0


def test_llm_minimum_battery_reserve_percentage_phrasing_uses_battery_context(client):
    result = _interp(
        client,
        ["Reserve at least a quarter of the battery's total capacity from 7pm to 9pm."],
        capacity_kwh=240, initial_energy_kwh=200, minimum_energy_kwh=20,
        max_charge_kwh_per_hour=60, max_discharge_kwh_per_hour=60,
    )
    e = result[0]
    assert e["directive_type"] == "minimum_battery_reserve"
    assert e["structured_adjustment"]["hours"] == [19, 20]
    assert abs(e["structured_adjustment"]["minimum_energy_kwh"] - 60) <= 3.0  # 25% of 240 kWh


def test_llm_pure_distractor_marked_no_op(client):
    result = _interp(client, ["The IT department upgraded the campus wifi routers last weekend."])
    e = result[0]
    assert e["applies"] is False
    assert e["directive_type"] == "no_op"
    assert e["structured_adjustment"] is None


def test_llm_multiple_notes_combined_order_and_distractor(client):
    result = _interp(client, [
        "Grid draw must not go above 100 kWh per hour between 6pm and 8pm because of a feeder fault.",
        "The annual sports day has been rescheduled to next month.",
    ])
    assert len(result) == 2
    assert [e["note_index"] for e in result] == [0, 1]
    assert result[0]["directive_type"] == "max_grid_window"
    assert result[0]["structured_adjustment"]["hours"] == [18, 19]
    assert result[1]["directive_type"] == "no_op"
    assert result[1]["applies"] is False
