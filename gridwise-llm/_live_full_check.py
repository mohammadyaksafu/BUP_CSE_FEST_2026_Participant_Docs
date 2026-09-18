"""Comprehensive live test of a deployed GridWise instance, covering every
category that's testable purely over HTTP (can't run white-box/internal-module
tests against a remote deployment, only what's reachable through the API).
"""
import json
import sys
import time

import httpx

sys.path.insert(0, "tests")
from test_public_samples import check_response  # noqa: E402
from helpers import make_battery, make_scenario  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://165.99.219.251"
DATA = json.load(open("../BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json", encoding="utf-8"))["cases"]

results = {"pass": 0, "fail": 0}


def record(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    results["pass" if ok else "fail"] += 1
    print(f"[{tag}] {name}" + (f" -- {detail}" if detail else ""))


def post(payload, timeout=35.0):
    return httpx.post(f"{BASE}/optimize-energy", json=payload, timeout=timeout)


def post_raw(content, timeout=15.0):
    return httpx.post(f"{BASE}/optimize-energy", content=content,
                       headers={"Content-Type": "application/json"}, timeout=timeout)


print(f"=== Testing {BASE} ===\n")

# --- A. Health -------------------------------------------------------------
print("## A. Health")
r = httpx.get(f"{BASE}/health", timeout=10)
record("health returns 200 ok", r.status_code == 200 and r.json() == {"status": "ok"}, f"status={r.status_code} body={r.text}")

# --- B. Public sample cases --------------------------------------------------
print("\n## B. Public sample cases (structural + interpretation + cost)")
for case in DATA:
    inp = case["input"]
    expected = case.get("expected_output", case.get("expected"))
    t0 = time.monotonic()
    r = post(inp)
    dt = time.monotonic() - t0
    if r.status_code != 200:
        record(case["id"], False, f"HTTP {r.status_code}")
        continue
    body = r.json()
    errors = check_response(inp, body)
    cost_ok = abs(body["total_cost_bdt"] - expected["total_cost_bdt"]) <= 0.01
    record(case["id"], not errors and cost_ok, f"cost={body['total_cost_bdt']} ref={expected['total_cost_bdt']} t={dt:.2f}s errs={errors[:2]}")
    time.sleep(1.5)

# --- C. Malformed / edge HTTP requests --------------------------------------
print("\n## C. Malformed / edge requests (expect clean 4xx, service stays up)")

edge_cases = {
    "missing_battery": {"scenario_id": "e1", "operator_notes": ["x"], "hours": []},
    "too_many_notes": make_scenario("e2", ["a", "b", "c", "d"]),
    "short_hours": (lambda p: (p.update(hours=p["hours"][:20]), p)[1])(make_scenario("e3", ["x"])),
    "bad_battery_bounds": (lambda p: (p["battery"].update(minimum_energy_kwh=p["battery"]["capacity_kwh"] + 10), p)[1])(make_scenario("e4", ["x"])),
    "empty_notes": make_scenario("e5", []),
}
for name, payload in edge_cases.items():
    r = post(payload, timeout=15)
    record(f"edge:{name} -> 4xx", 400 <= r.status_code < 500, f"HTTP {r.status_code}")

r = post_raw(b"{not valid json")
record("edge:malformed_json -> 4xx", 400 <= r.status_code < 500, f"HTTP {r.status_code}")

r = post_raw(b"")
record("edge:empty_body -> 4xx", 400 <= r.status_code < 500, f"HTTP {r.status_code}")

r = httpx.get(f"{BASE}/health", timeout=10)
record("service still healthy after malformed requests", r.status_code == 200)

# --- D. Paraphrase robustness (real LLM, novel wording) ---------------------
print("\n## D. Paraphrase robustness (novel wording, real LLM)")

paraphrase_cases = [
    ("PV output will fall to roughly 20% of normal between 1pm and 3pm today.",
     "solar_reduction", [13, 14], "factor", 0.2),
    ("The charging circuit breaker will be locked out from 2am to 5am for repairs.",
     "no_charge_window", [2, 3, 4], None, None),
    ("Substation limits mean we can't pull more than 120 kWh per hour from the grid between 5pm and 7pm.",
     "max_grid_window", [17, 18], "max_grid_kwh", 120),
    ("We need a minimum of 60 kWh sitting in the battery at all times from 6pm through 9pm.",
     "minimum_battery_reserve", [18, 19, 20], "minimum_energy_kwh", 60),
    ("The IT department upgraded the campus wifi routers last weekend.",
     "no_op", None, None, None),
]
for note, expected_type, expected_hours, num_key, num_val in paraphrase_cases:
    payload = make_scenario("paraphrase-test", [note])
    r = post(payload)
    if r.status_code != 200:
        record(f"paraphrase: {note[:40]}...", False, f"HTTP {r.status_code}")
        time.sleep(2)
        continue
    e = r.json()["directive_interpretation"][0]
    ok = e["directive_type"] == expected_type
    detail = f"got={e['directive_type']}"
    if ok and expected_hours is not None:
        ok = e["structured_adjustment"]["hours"] == expected_hours
        detail += f" hours={e['structured_adjustment']['hours']}"
    if ok and num_key is not None:
        ok = abs(e["structured_adjustment"][num_key] - num_val) <= max(1.0, num_val * 0.05)
        detail += f" {num_key}={e['structured_adjustment'][num_key]}"
    record(f"paraphrase: {note[:50]}...", ok, detail)
    time.sleep(2)

# --- E. Degenerate battery / optimizer configs (distractor note, no LLM dependency on directive) ---
print("\n## E. Degenerate battery/optimizer configs")

degenerate_cases = {
    "zero_tariff": make_scenario("d1", ["distractor note"], tariff=[0.0] * 24),
    "huge_battery": make_scenario("d2", ["distractor note"], demand=[100.0] * 24, solar=[0.0] * 24,
                                   battery=make_battery(capacity_kwh=1_000_000, initial_energy_kwh=500_000,
                                                         minimum_energy_kwh=0, max_charge_kwh_per_hour=10_000,
                                                         max_discharge_kwh_per_hour=10_000)),
    "zero_charge_rate": make_scenario("d3", ["distractor note"],
                                       battery=make_battery(max_charge_kwh_per_hour=0)),
    "battery_locked_full": make_scenario("d4", ["distractor note"],
                                          battery=make_battery(capacity_kwh=150, initial_energy_kwh=150, minimum_energy_kwh=150)),
}
for name, payload in degenerate_cases.items():
    r = post(payload)
    if r.status_code != 200:
        record(f"degenerate:{name}", False, f"HTTP {r.status_code}")
        time.sleep(2)
        continue
    body = r.json()
    errors = check_response(payload, body)
    record(f"degenerate:{name}", not errors, f"errs={errors[:2]}")
    time.sleep(2)

# --- F. Stability (repeated identical requests) -----------------------------
print("\n## F. Stability (3x identical requests)")
stable_payload = make_scenario("stability-test", ["distractor note"])
for i in range(3):
    r = post(stable_payload)
    record(f"stability request {i+1}", r.status_code == 200, f"HTTP {r.status_code}")
    time.sleep(1.5)

# --- G. Security: secret leakage, error response sanity ---------------------
print("\n## G. Security")
r = post(make_scenario("sec-test", ["distractor note"]))
record("no 'AQ.' / key-shaped secret in success response", "AQ." not in r.text and "AIza" not in r.text)

r = post_raw(b"{bad json")
record("no stack trace in malformed-request response", "Traceback" not in r.text and "File \"" not in r.text)

# --- Summary -----------------------------------------------------------------
total = results["pass"] + results["fail"]
print(f"\n=== {results['pass']}/{total} PASSED, {results['fail']}/{total} FAILED ===")
