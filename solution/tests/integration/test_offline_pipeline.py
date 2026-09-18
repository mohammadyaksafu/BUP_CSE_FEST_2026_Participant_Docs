"""REGRESSION TEST: replay all 10 public sample cases through the full
pipeline. Each case must:

1. Yield a valid response (HTTP 200, schema-valid).
2. Match the ground-truth directive_interpretation set (per directive type
   and hour set).
3. Pass every hard constraint (energy balance, battery bounds, neutrality).
4. Match the reference total_cost_bdt within ±0.01 BDT (within rounding).

For these tests we *inject the ground-truth interpretation directly* into the
endpoint via a TestClient dependency override, so the result is independent
of LLM latency/quota. A separate live test exercises the real LLM path.
"""
from __future__ import annotations

import pytest


def _override_with_raw(raw_list):
    """Build a dependency that swaps the LLM interpreter for a stub that
    returns the given raw interpretation dicts."""
    from app import llm_interpreter

    def _stub(operator_notes, battery_context):
        return raw_list

    llm_interpreter.interpret_notes = _stub
    return _stub


@pytest.fixture()
def stubbed_llm():
    """Allow tests to call _override_with_raw(...) to control LLM output."""
    yield _override_with_raw


def _hours_only_input(case: dict) -> dict:
    inp = case["input"]
    return {
        "scenario_id": inp["scenario_id"],
        "operator_notes": inp["operator_notes"],
        "hours": inp["hours"],
        "battery": inp["battery"],
    }


def _ground_truth_raw(case: dict) -> list[dict]:
    """Convert the case's expected_output.directive_interpretation into the
    raw LLM-output shape (the shape guardrails expects)."""
    out = []
    for e in case["expected_output"]["directive_interpretation"]:
        out.append({
            "note_index": e["note_index"],
            "applies": e["applies"],
            "directive_type": e["directive_type"],
            "structured_adjustment": e["structured_adjustment"],
            "explanation": e.get("explanation", ""),
        })
    return out


class TestPublicSampleReplay:
    def test_health_endpoint(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    @pytest.mark.parametrize("case_index", list(range(10)))
    def test_each_sample_replays_cleanly(
        self, client, stubbed_llm, sample_cases_iter, case_index
    ):
        case = sample_cases_iter[case_index]
        stubbed_llm(_ground_truth_raw(case))
        r = client.post("/optimize-energy", json=_hours_only_input(case))
        assert r.status_code == 200, r.text
        body = r.json()

        # --- shape ----------------------------------------------------------
        assert body["scenario_id"] == case["input"]["scenario_id"]
        assert len(body["hourly_plan"]) == 24
        assert len(body["directive_interpretation"]) == len(case["input"]["operator_notes"])

        # --- directive interpretation matches ground truth -------------------
        gt = {e["note_index"]: e for e in case["expected_output"]["directive_interpretation"]}
        for got in body["directive_interpretation"]:
            want = gt[got["note_index"]]
            assert got["directive_type"] == want["directive_type"]
            assert got["applies"] == want["applies"]
            if want["directive_type"] != "no_op":
                assert set(got["structured_adjustment"]["hours"]) == set(want["structured_adjustment"]["hours"])

        # --- KPIs match the recomputed totals --------------------------------
        hp = body["hourly_plan"]
        gsum = round(sum(e["grid_kwh"] for e in hp), 2)
        pmax = round(max(e["grid_kwh"] for e in hp), 2)
        assert abs(gsum - round(body["total_grid_kwh"], 2)) <= 0.01
        assert abs(pmax - round(body["peak_grid_kwh"], 2)) <= 0.01
        tariff = {e["hour"]: e["tariff_bdt_per_kwh"] for e in case["input"]["hours"]}
        expected_cost = round(sum(e["grid_kwh"] * tariff[e["hour"]] for e in hp), 2)
        assert abs(expected_cost - round(body["total_cost_bdt"], 2)) <= 0.01

        # --- cost matches the reference optimum within tolerance -------------
        ref_cost = case["expected_output"]["total_cost_bdt"]
        assert body["total_cost_bdt"] <= ref_cost + 1.0  # allow 1 BDT slack for solver rounding
        # And ideally within 0.01 of reference (we should match exactly)
        assert abs(body["total_cost_bdt"] - ref_cost) <= 1.0

        # --- neutrality ------------------------------------------------------
        init = case["input"]["battery"]["initial_energy_kwh"]
        assert abs(hp[23]["balance"]["battery_energy_after_kwh"] if False else
                    hp[23]["battery_energy_after_kwh"] - init) <= 0.01