"""Black-box tests: drive the service purely through its public HTTP interface
(GET /health, POST /optimize-energy), asserting only on the documented
request/response contract from problem.md Section 7/8/11 — no knowledge of
internal implementation. The LLM call is stubbed to a deterministic function so
these tests are fast, free, and independent of provider availability.
"""
from tests.helpers import make_interpretation, make_scenario


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_optimize_energy_happy_path_schema(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [make_interpretation(0, "no_op")])
    payload = make_scenario("bb-1", ["The cafeteria menu changes tomorrow."])

    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    body = resp.json()

    assert body["scenario_id"] == "bb-1"
    assert isinstance(body["directive_interpretation"], list)
    assert len(body["directive_interpretation"]) == 1
    entry = body["directive_interpretation"][0]
    assert entry["note_index"] == 0
    assert entry["applies"] is False
    assert entry["directive_type"] == "no_op"
    assert entry["structured_adjustment"] is None

    assert isinstance(body["hourly_plan"], list)
    assert len(body["hourly_plan"]) == 24
    assert {h["hour"] for h in body["hourly_plan"]} == set(range(24))
    for h in body["hourly_plan"]:
        assert set(h.keys()) >= {"hour", "grid_kwh", "solar_used_kwh", "battery_action", "battery_kwh", "battery_energy_after_kwh"}
        assert h["battery_action"] in ("charge", "discharge", "idle")

    for key in ("total_grid_kwh", "total_cost_bdt", "peak_grid_kwh", "plan_summary"):
        assert key in body


def test_optimize_energy_applies_directive_end_to_end(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [make_interpretation(0, "no_charge_window", hours=[2, 3, 4])])
    payload = make_scenario("bb-2", ["Do not charge between 2 and 5 AM."])

    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    plan_by_hour = {h["hour"]: h for h in body["hourly_plan"]}
    for h in (2, 3, 4):
        assert plan_by_hour[h]["battery_action"] != "charge"

    interp = body["directive_interpretation"][0]
    assert interp["applies"] is True
    assert interp["directive_type"] == "no_charge_window"
    assert interp["structured_adjustment"]["hours"] == [2, 3, 4]


def test_note_count_and_order_preserved_for_multiple_notes(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [
        make_interpretation(0, "no_op"),
        make_interpretation(1, "max_grid_window", hours=[18, 19], max_grid_kwh=120),
        make_interpretation(2, "no_op"),
    ])
    payload = make_scenario("bb-3", ["distractor one", "grid cap note", "distractor two"])

    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    interp = body["directive_interpretation"]
    assert len(interp) == 3
    assert [e["note_index"] for e in interp] == [0, 1, 2]
    assert interp[1]["directive_type"] == "max_grid_window"


def test_malformed_json_body_returns_400(client):
    resp = client.post(
        "/optimize-energy",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_missing_required_field_returns_400(client):
    payload = make_scenario("bb-4", ["x"])
    del payload["battery"]
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 400


def test_wrong_hours_length_returns_400(client):
    payload = make_scenario("bb-5", ["x"])
    payload["hours"] = payload["hours"][:20]
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 400


def test_too_many_operator_notes_returns_400(client):
    payload = make_scenario("bb-6", ["a", "b", "c", "d"])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 400


def test_empty_operator_notes_returns_400(client):
    payload = make_scenario("bb-7", [])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 400


def test_battery_min_above_capacity_returns_400(client):
    payload = make_scenario("bb-8", ["x"])
    payload["battery"]["minimum_energy_kwh"] = payload["battery"]["capacity_kwh"] + 10
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 400
