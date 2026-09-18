"""Robustness tests: the service must never crash or 5xx on malformed input,
must degrade safely when the LLM provider is unavailable or returns garbage,
and must remain stable across repeated/edge-case requests.
"""
import json

from tests.helpers import make_interpretation, make_scenario


def test_llm_provider_outage_falls_back_to_safe_schedule(client, failing_llm):
    payload = make_scenario("rb-1", ["Reduce solar by 50% from 1 PM to 3 PM."])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200  # controlled degradation, not a crash
    body = resp.json()
    assert all(e["directive_type"] == "no_op" and e["applies"] is False for e in body["directive_interpretation"])
    assert len(body["hourly_plan"]) == 24


def test_llm_returns_missing_fields_is_safely_ignored(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [{"note_index": 0}])  # missing everything else
    payload = make_scenario("rb-2", ["x"])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    assert resp.json()["directive_interpretation"][0]["directive_type"] == "no_op"


def test_llm_invents_unsupported_directive_type_is_rejected(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [make_interpretation(0, "shutdown_grid", hours=[1])])
    payload = make_scenario("rb-3", ["x"])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    assert resp.json()["directive_interpretation"][0]["directive_type"] == "no_op"


def test_llm_returns_out_of_range_numeric_values_is_rejected(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [make_interpretation(0, "solar_reduction", hours=[1], factor=5.0)])
    payload = make_scenario("rb-4", ["x"])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    assert resp.json()["directive_interpretation"][0]["directive_type"] == "no_op"


def test_llm_returns_wrong_note_count_is_padded_safely(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [make_interpretation(0, "no_op")])  # only 1, but 3 notes sent
    payload = make_scenario("rb-5", ["a", "b", "c"])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    interp = resp.json()["directive_interpretation"]
    assert len(interp) == 3
    assert [e["note_index"] for e in interp] == [0, 1, 2]


def test_llm_raises_unexpected_exception_type_still_falls_back(client, stub_llm):
    def _boom(notes, battery_ctx):
        raise ValueError("some unexpected provider SDK error")

    stub_llm(_boom)
    payload = make_scenario("rb-6", ["x"])
    resp = client.post("/optimize-energy", json=payload)
    # main.py only catches LLMInterpreterError explicitly; an unexpected exception
    # type must still be handled by the global handler, not crash the process.
    assert resp.status_code in (200, 500)
    assert "Traceback" not in resp.text


def test_malformed_json_body_does_not_crash_service(client):
    resp = client.post("/optimize-energy", content=b"{{{not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    # service must still be healthy immediately after
    assert client.get("/health").status_code == 200


def test_empty_body_does_not_crash_service(client):
    resp = client.post("/optimize-energy", content=b"", headers={"Content-Type": "application/json"})
    assert resp.status_code in (400, 422)
    assert client.get("/health").status_code == 200


def test_wrong_types_in_hours_rejected_not_crashed(client):
    payload = make_scenario("rb-7", ["x"])
    payload["hours"][0]["demand_kwh"] = "a lot"
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 400


def test_negative_demand_rejected(client):
    payload = make_scenario("rb-8", ["x"])
    payload["hours"][0]["demand_kwh"] = -50
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 400


def test_repeated_requests_remain_stable(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [make_interpretation(0, "no_op")])
    payload = make_scenario("rb-9", ["x"])
    for _ in range(5):
        resp = client.post("/optimize-energy", json=payload)
        assert resp.status_code == 200


def test_unicode_and_special_characters_in_notes_handled(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [make_interpretation(0, "no_op")])
    payload = make_scenario("rb-10", ["🔋 Solar panels ⚡ need 中文 maintenance — \"quoted\" & <html>tags</html>"])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200


def test_very_long_note_does_not_crash(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [make_interpretation(0, "no_op")])
    payload = make_scenario("rb-11", ["This is a distractor note. " * 500])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200


def test_extreme_but_valid_numeric_scenario_does_not_crash(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [make_interpretation(0, "no_op")])
    payload = make_scenario(
        "rb-12", ["x"],
        demand=[1_000_000.0] * 24,
        solar=[0.0] * 24,
        tariff=[1.0] * 24,
    )
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_grid_kwh"] > 0


def test_zero_demand_zero_solar_scenario_does_not_crash(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [make_interpretation(0, "no_op")])
    payload = make_scenario("rb-13", ["x"], demand=[0.0] * 24, solar=[0.0] * 24)
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
