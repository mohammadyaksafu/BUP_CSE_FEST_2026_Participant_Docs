"""API/integration tests: exercise the full stack (HTTP -> guardrails ->
directive engine -> optimizer -> schedule validator -> response) through the
public endpoint with multiple directives combined in a single request, and
cross-check every response invariant a judge would recompute independently.
"""
from tests.helpers import make_battery, make_interpretation, make_scenario

TOLERANCE = 0.01


def _cross_check(payload, body):
    plan = body["hourly_plan"]
    assert len(plan) == 24 and {p["hour"] for p in plan} == set(range(24))

    hours_by_num = {h["hour"]: h for h in payload["hours"]}
    battery = payload["battery"]
    prev_energy = battery["initial_energy_kwh"]
    total_grid = 0.0
    total_cost = 0.0

    for h in range(24):
        p = next(x for x in plan if x["hour"] == h)
        demand = hours_by_num[h]["demand_kwh"]
        tariff = hours_by_num[h]["tariff_bdt_per_kwh"]
        charge = p["battery_kwh"] if p["battery_action"] == "charge" else 0.0
        discharge = p["battery_kwh"] if p["battery_action"] == "discharge" else 0.0

        assert abs((p["grid_kwh"] + p["solar_used_kwh"] + discharge) - (demand + charge)) <= TOLERANCE
        assert abs((prev_energy + charge - discharge) - p["battery_energy_after_kwh"]) <= TOLERANCE
        assert p["battery_energy_after_kwh"] <= battery["capacity_kwh"] + TOLERANCE

        prev_energy = p["battery_energy_after_kwh"]
        total_grid += p["grid_kwh"]
        total_cost += p["grid_kwh"] * tariff

    assert abs(prev_energy - battery["initial_energy_kwh"]) <= TOLERANCE
    assert abs(total_grid - body["total_grid_kwh"]) <= TOLERANCE
    assert abs(total_cost - body["total_cost_bdt"]) <= TOLERANCE
    assert abs(max(p["grid_kwh"] for p in plan) - body["peak_grid_kwh"]) <= TOLERANCE


def test_three_directives_combined_all_applied(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [
        make_interpretation(0, "solar_reduction", hours=[10, 11], factor=0.3),
        make_interpretation(1, "no_charge_window", hours=[2, 3]),
        make_interpretation(2, "max_grid_window", hours=[18, 19, 20], max_grid_kwh=90),
    ])
    payload = make_scenario(
        "int-1",
        ["panel cleaning", "maintenance window", "feeder cap"],
        battery=make_battery(capacity_kwh=300, initial_energy_kwh=150, minimum_energy_kwh=20,
                             max_charge_kwh_per_hour=80, max_discharge_kwh_per_hour=80),
    )

    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    _cross_check(payload, body)

    plan_by_hour = {p["hour"]: p for p in body["hourly_plan"]}
    for h in (2, 3):
        assert plan_by_hour[h]["battery_action"] != "charge"
    for h in (18, 19, 20):
        assert plan_by_hour[h]["grid_kwh"] <= 90 + TOLERANCE
    for h in (10, 11):
        assert plan_by_hour[h]["solar_used_kwh"] <= payload["hours"][h]["solar_kwh"] * 0.3 + TOLERANCE


def test_percentage_based_reserve_resolved_via_battery_context(client, stub_llm):
    """Simulates the LLM correctly using the battery-capacity context to turn
    '50% of capacity' into an absolute kWh value, then verifies it's enforced."""
    captured = {}

    def _fn(notes, battery_ctx):
        captured["battery_ctx"] = battery_ctx
        half_capacity = battery_ctx["capacity_kwh"] * 0.5
        return [make_interpretation(0, "minimum_battery_reserve", hours=[18, 19], minimum_energy_kwh=half_capacity)]

    stub_llm(_fn)
    payload = make_scenario("int-2", ["keep half the battery in reserve from 6pm to 8pm"],
                            battery=make_battery(capacity_kwh=200, initial_energy_kwh=150, minimum_energy_kwh=10))

    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    _cross_check(payload, body)

    assert captured["battery_ctx"]["capacity_kwh"] == 200
    plan_by_hour = {p["hour"]: p for p in body["hourly_plan"]}
    for h in (18, 19):
        assert plan_by_hour[h]["battery_energy_after_kwh"] >= 100 - TOLERANCE


def test_all_distractor_notes_produce_baseline_schedule(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [make_interpretation(i, "no_op") for i in range(len(notes))])
    payload = make_scenario("int-3", ["meeting notes", "cafeteria update", "parking update"])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    _cross_check(payload, body)
    assert all(not e["applies"] for e in body["directive_interpretation"])


def test_scenario_id_echoed_exactly(client, stub_llm):
    stub_llm(lambda notes, battery_ctx: [make_interpretation(0, "no_op")])
    payload = make_scenario("weird/ID with spaces & symbols!", ["x"])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    assert resp.json()["scenario_id"] == payload["scenario_id"]
