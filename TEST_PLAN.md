# GridWise LLM — Test Plan (BUP CSE Fest 2026 Preliminary)

> Goal: prove the service scores 100/100 (Correctness 50 + Robustness 25 + Submission 25).
> Every test below is repeatable, automated, and traceable to a rubric item.

---

## 1. Test Strategy

| Layer | Tool | What it covers |
|---|---|---|
| Unit | `pytest` | Directive interpreter, optimizer math, validators |
| Integration | `pytest` + FastAPI `TestClient` | `POST /optimize-energy` end-to-end |
| Replay | `replay.py` (custom) | All 10 public sample cases |
| Property | Hypothesis | Randomized valid inputs |
| Performance | `pytest-benchmark` / shell | p95 latency, cold-start |
| Container | shell + `curl` | Docker boot, `/health`, end-to-end inside container |

Tolerance: absolute differences ≤ 0.01 kWh or 0.01 BDT are treated as equivalent unless the judge specifies tighter.

---

## 2. Public Sample-Case Replay (the highest-value test)

For each case `SAMPLE-01..SAMPLE-10`:

| ID | Directive Type(s) | Edge Phrasing to Verify |
|---|---|---|
| SAMPLE-01 | `solar_reduction` + `no_op` (distractor) | "25% of forecast" + unrelated "registration deadline" |
| SAMPLE-02 | `no_charge_window` | "2 AM until 5 AM" / "charger isolated" |
| SAMPLE-03 | `minimum_battery_reserve` | "50% of the battery capacity" → compute 100 kWh of 200 kWh |
| SAMPLE-04 | `no_discharge_window` | "6 PM until 8 PM" / "protection testing" |
| SAMPLE-05 | `max_grid_window` | "must not exceed 155 kWh … 6 PM until 9 PM" / "feeder limit" |
| SAMPLE-06 | `solar_reduction` + `no_charge_window` + `no_op` | distractor in 3-note case |
| SAMPLE-07 | `minimum_battery_reserve` + `max_grid_window` | reserve + transformer cap combined |
| SAMPLE-08 | `no_charge_window` + `no_discharge_window` | separate outage windows |
| SAMPLE-09 | `solar_reduction` + `no_op` | "drop to about 25%" wording normalization |
| SAMPLE-10 | `minimum_battery_reserve` + `max_grid_window` + `no_op` | multi-constraint evening operation |

For each case, replay asserts:
1. HTTP 200 from `POST /optimize-energy`.
2. `scenario_id` echoed.
3. One `directive_interpretation` entry per `operator_note` in `note_index` order.
4. For each entry: `directive_type`, `applies`, and the `hours` set match ground truth (set-equal).
5. For `solar_reduction`: `factor` within ±0.01 of reference.
6. For `minimum_battery_reserve`: `minimum_energy_kwh` within ±0.5 kWh of reference.
7. For `max_grid_window`: `max_grid_kwh_per_hour` within ±0.5 kWh of reference.
8. Energy balance holds for every hour.
9. `solar_used_kwh ≤ effective_solar_kwh` after reductions.
10. Battery `battery_energy_after_kwh` ∈ `[active_minimum, capacity]` per hour.
11. Hour 23 `battery_energy_after_kwh` == `initial_energy_kwh`.
12. `battery_kwh == 0` whenever `battery_action == idle`.
13. `battery_action` ∈ `{charge, discharge, idle}`.
14. Recomputed `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` match the response values within 0.01.
15. `total_cost_bdt` is ≤ reference `total_cost_bdt + 1 BDT` (allow equivalent optima within rounding).

Pass criterion: 10/10 cases pass all 15 checks.

---

## 3. Unit Tests — Directive Interpreter

| ID | Input note | Expected type | Expected hours | Extra |
|---|---|---|---|---|
| UT-D-01 | "wash panels noon to 2 PM, treat as 25%" | solar_reduction | [12,13] | factor 0.25 |
| UT-D-02 | "panels dirty 1 PM to 3 PM, only 20% usable" | solar_reduction | [13,14] | factor 0.20 |
| UT-D-03 | "charger isolated 2 AM until 5 AM" | no_charge_window | [2,3,4] | — |
| UT-D-04 | "no charging 6 PM–9 PM" | no_charge_window | [18,19,20] | — |
| UT-D-05 | "battery must not discharge 6 PM until 8 PM" | no_discharge_window | [18,19] | — |
| UT-D-06 | "keep 100 kWh reserve 7–9 PM" | minimum_battery_reserve | [19,20,21]? | resolve window correctly |
| UT-D-07 | "keep 50% of battery 6 PM–9 PM" | minimum_battery_reserve | [18,19,20] | factor × capacity |
| UT-D-08 | "grid import must not exceed 155 kWh 6 PM–9 PM" | max_grid_window | [18,19,20] | cap 155 |
| UT-D-09 | "feeder limit 175 kWh 5 PM–7 PM" | max_grid_window | [17,18] | cap 175 |
| UT-D-10 | "sports office moved registration deadline" | no_op | — | applies=false |
| UT-D-11 | "team meeting tomorrow at 10" | no_op | — | applies=false |
| UT-D-12 | "payroll processed on Friday" | no_op | — | applies=false |
| UT-D-13 | "delivery at 8 AM, no grid impact" | no_op | — | applies=false |
| UT-D-14 | "sooty panels 11 AM–1 PM, drop to ~25%" | solar_reduction | [11,12] | factor 0.25 |
| UT-D-15 | "tree shadow 3 PM–5 PM, ~half output" | solar_reduction | [15,16] | factor 0.50 |

End-time rule under test everywhere: `start-inclusive, end-exclusive`. "1 PM to 3 PM" must map to `[13, 14]`, never `[13, 14, 15]`.

---

## 4. Unit Tests — Optimizer

| ID | What it checks |
|---|---|
| UT-O-01 | With no directives, schedule meets demand balance and returns to initial energy at hour 23 |
| UT-O-02 | Battery never goes below `minimum_energy_kwh` |
| UT-O-03 | Battery never exceeds `capacity_kwh` |
| UT-O-04 | Charge and discharge per-hour ≤ rate limits |
| UT-O-05 | `idle` ⇒ `battery_kwh == 0` |
| UT-O-06 | `no_charge_window` ⇒ `battery_action == "idle"` or `"discharge"` in listed hours |
| UT-O-07 | `no_discharge_window` ⇒ `battery_action == "idle"` or `"charge"` in listed hours |
| UT-O-08 | `minimum_battery_reserve` ⇒ `battery_energy_after_kwh ≥ requested` in listed hours |
| UT-O-09 | `max_grid_window` ⇒ `grid_kwh ≤ cap` in listed hours |
| UT-O-10 | `solar_reduction` ⇒ `solar_used_kwh ≤ solar_kwh × factor` in listed hours |
| UT-O-11 | `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` recomputed from plan == response values |
| UT-O-12 | Tie-break: among optimal-cost plans, prefer lower `peak_grid_kwh` |
| UT-O-13 | Determinism: identical inputs → byte-equal plan (or within 0.01 rounding) |

---

## 5. Integration Tests (API)

| ID | Request | Expected |
|---|---|---|
| IT-01 | `GET /health` | 200, `{"status":"ok"}` |
| IT-02 | `POST /optimize-energy` with SAMPLE-01 | 200 + valid response |
| IT-03 | Same request twice | identical KPIs (deterministic) |
| IT-04 | Missing `hours` | 400 with helpful error |
| IT-05 | 25 hours provided | 400 (rejected) |
| IT-O-06 | 4 operator notes | 400 (rejected; max 3) |
| IT-O-07 | Empty operator note string | 400 (rejected) |
| IT-O-08 | Negative `demand_kwh` | 400 (rejected) |
| IT-O-09 | Battery capacity < initial | 400 (invalid config) |
| IT-O-10 | Unknown directive_type in response | caught by validator; never returned |

---

## 6. Property-Based / Fuzz Tests

Hypothesis strategies:

- **PBT-01 — Random valid scenarios**: generate 100 random inputs (within bounds), run the optimizer, assert all hard constraints hold and hour-23 neutrality holds.
- **PBT-02 — Random directive combinations**: for each of the 5 directive types × {1, 2, 3 notes} × 50 random phrasings, the interpreter produces a structurally valid `DirectiveInterpretation` that the engine can apply without crashing.
- **PBT-03 — Adversarial notes**: strings with unicode, embedded numbers, mixed casing, off-by-one times ("1 to 4 PM"), overlapping windows → interpreter must return a valid hours set (sorted unique in 0..23) or `no_op`.
- **PBT-04 — Round-trip**: KPI recomputation never disagrees with the response by more than 0.01.

---

## 7. Performance Tests

| ID | Target | Method |
|---|---|---|
| PT-01 | Cold-start time ≤ 30 s | `time docker run … && curl /health` |
| PT-02 | Warm `/health` p95 ≤ 200 ms | 100 sequential GETs |
| PT-03 | Warm `POST /optimize-energy` p95 ≤ 5 s over 10 sample cases | replay script with timing |
| PT-04 | 60 s sustained 5 RPS with no failed requests | load loop with `wrk`/`hey` |
| PT-05 | Memory ceiling ≤ 1 GB RSS under PT-04 | `docker stats` |

---

## 8. Container / Submission Tests

| ID | Check |
|---|---|
| CT-01 | `docker build . -t gridwise-llm:latest` exits 0 |
| CT-02 | `docker run -d -p 8000:8000 gridwise-llm:latest` starts; `/health` returns 200 within 30 s |
| CT-03 | Inside the running container, `POST /optimize-energy` with SAMPLE-01..10 all succeed |
| CT-04 | Manifest JSON (if required by the portal) validates against the field schema in the Participant Guide |
| CT-05 | Image contains no `.env`, no API keys, no `.git` history |

---

## 9. Robustness / Truthfulness Tests (rubric: 25 pts)

| ID | Check |
|---|---|
| RT-01 | Response always includes exactly 24 hourly_plan entries |
| RT-02 | `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` always equal the values recomputed from `hourly_plan` |
| RT-03 | `scenario_id` echoed exactly |
| RT-04 | `plan_summary` mentions at least one of: directive names, peak hour, total cost; never fabricates a metric not derivable from the plan |
| RT-05 | Identical inputs (same scenario, same notes) → identical response (no LLM temperature noise in judge path) |
| RT-06 | LLM unreachable → deterministic path still returns a valid response within SLA |
| RT-07 | No silent drops of notes: count of `directive_interpretation` == count of `operator_notes` |

---

## 10. Hard-Coding Guard Tests (anti-leak)

| ID | Check |
|---|---|
| HC-01 | No branch in the codebase keys on `scenario_id in {"SAMPLE-01",...,"SAMPLE-10"}` |
| HC-02 | No constant dict maps sample note text → fixed `hours`/`factor` |
| HC-03 | Renaming all `SAMPLE-NN` IDs to `HIDDEN-NN` and replaying yields the same pass rate on equivalent paraphrased inputs |

---

## 11. Sign-Off Criteria

The build is "ready to submit" only when:

- [ ] All 10 public samples pass replay checks 1–15
- [ ] All unit, integration, property, performance, container, robustness, and hard-coding tests pass
- [ ] `total_cost_bdt` on each sample is within tolerance of the reference optimum
- [ ] Docker image runs cleanly with the documented command
- [ ] Submission package (code + Dockerfile + README + manifest) is finalized