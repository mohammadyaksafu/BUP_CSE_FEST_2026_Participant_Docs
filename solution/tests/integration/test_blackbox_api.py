"""BLACK-BOX API TEST: only HTTP-visible behavior. Treats the service as an
opaque black box; does not import internal modules."""
from __future__ import annotations


class TestHealthEndpoint:
    def test_get_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestOptimizeEndpointContract:
    def test_post_returns_json(self, client, sample_cases_iter):
        body = sample_cases_iter[0]["input"]
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")

    def test_response_top_level_keys(self, client, sample_cases_iter):
        body = sample_cases_iter[0]["input"]
        r = client.post("/optimize-energy", json=body)
        j = r.json()
        for k in ("scenario_id", "directive_interpretation", "hourly_plan",
                  "total_grid_kwh", "total_cost_bdt", "peak_grid_kwh", "plan_summary"):
            assert k in j

    def test_hourly_plan_shape(self, client, sample_cases_iter):
        body = sample_cases_iter[0]["input"]
        r = client.post("/optimize-energy", json=body)
        hp = r.json()["hourly_plan"]
        assert len(hp) == 24
        seen = set()
        for entry in hp:
            for k in ("hour", "grid_kwh", "solar_used_kwh", "battery_action",
                      "battery_kwh", "battery_energy_after_kwh"):
                assert k in entry
            assert entry["battery_action"] in ("charge", "discharge", "idle")
            assert entry["hour"] not in seen
            seen.add(entry["hour"])

    def test_interpretation_count_matches_notes(self, client, sample_cases_iter):
        body = sample_cases_iter[5]["input"]  # 3 notes
        r = client.post("/optimize-energy", json=body)
        j = r.json()
        assert len(j["directive_interpretation"]) == len(body["operator_notes"])

    def test_plan_summary_is_nonempty_string(self, client, sample_cases_iter):
        body = sample_cases_iter[0]["input"]
        r = client.post("/optimize-energy", json=body)
        assert isinstance(r.json()["plan_summary"], str)
        assert len(r.json()["plan_summary"]) > 0


class TestOptimizeEndpointValidation:
    def test_malformed_json_400(self, client):
        r = client.post("/optimize-energy", data="not json", headers={"Content-Type": "application/json"})
        assert r.status_code == 400

    def test_missing_hours_400(self, client, sample_cases_iter):
        body = sample_cases_iter[0]["input"]
        del body["hours"]
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 400

    def test_wrong_number_of_hours_400(self, client, sample_cases_iter):
        body = sample_cases_iter[0]["input"]
        body["hours"] = body["hours"][:23]
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 400

    def test_four_notes_400(self, client, sample_cases_iter):
        body = sample_cases_iter[0]["input"]
        body["operator_notes"] = body["operator_notes"] + ["extra", "extra2"]
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 400

    def test_empty_notes_400(self, client, sample_cases_iter):
        body = sample_cases_iter[0]["input"]
        body["operator_notes"] = [""]
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 400

    def test_negative_demand_400(self, client, sample_cases_iter):
        body = sample_cases_iter[0]["input"]
        body["hours"][5]["demand_kwh"] = -1
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 400


class TestOptimizeInvariantsAcrossInputs:
    def test_total_grid_kwh_matches_sum(self, client, sample_cases_iter):
        body = sample_cases_iter[1]["input"]
        r = client.post("/optimize-energy", json=body)
        j = r.json()
        s = sum(e["grid_kwh"] for e in j["hourly_plan"])
        assert abs(s - j["total_grid_kwh"]) <= 0.02

    def test_peak_grid_kwh_matches_max(self, client, sample_cases_iter):
        body = sample_cases_iter[2]["input"]
        r = client.post("/optimize-energy", json=body)
        j = r.json()
        m = max(e["grid_kwh"] for e in j["hourly_plan"])
        assert abs(m - j["peak_grid_kwh"]) <= 0.02

    def test_battery_neutrality(self, client, sample_cases_iter):
        body = sample_cases_iter[0]["input"]
        r = client.post("/optimize-energy", json=body)
        j = r.json()
        init = body["battery"]["initial_energy_kwh"]
        final = j["hourly_plan"][23]["battery_energy_after_kwh"]
        assert abs(final - init) <= 0.02