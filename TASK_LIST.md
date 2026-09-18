# GridWise LLM — Task List (BUP CSE Fest 2026 Preliminary)

> Build an HTTP service exposing `POST /optimize-energy` that:
> 1. Uses an LLM (or equivalent) to interpret 1–3 free-text operator notes into one of 6 structured directives.
> 2. Validates the interpretation deterministically.
> 3. Runs a deterministic optimizer that produces a valid 24-hour battery schedule.
> 4. Returns a schema-valid response with KPIs, an executable plan, and a plan summary.
>
> Scoring (Total = 100): Correctness 50 + Robustness/Truthfulness 25 + Submission/Performance 25.

---

## Phase 0 — Onboarding & Setup

- [ ] T0.1 Read both PDFs end-to-end (Problem Statement + Participant Guide) and bookmark the rubric.
- [ ] T0.2 Read all 10 `SAMPLE-NN` cases in `BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json`; note directive mix and edge phrasing.
- [ ] T0.3 Decide the LLM provider and API contract; store key in `.env` (never commit). Default plan: a deterministic regex/rule pipeline + LLM only as fallback.
- [ ] T0.4 Confirm `gridwise-llm/` scaffolding (FastAPI app, venv, Dockerfile, requirements.txt) is present and importable.
- [ ] T0.5 Create `tasks/` working directory for logs of each public-case run.

---

## Phase 1 — Input/Output Schema & Validation (Correctness foundation)

- [ ] T1.1 Implement Pydantic models for `ScenarioInput`, `Hour`, `Battery`, `OperatorDirective`, `DirectiveInterpretation`, `HourlyPlan`, `OptimizeResponse`. Match the JSON Schema in the problem statement exactly.
- [ ] T1.2 Implement input validator: exactly 24 unique hours 0–23, ≥1 and ≤3 non-empty `operator_notes`, battery fields present and positive.
- [ ] T1.3 Implement response validator:
  - exactly 24 entries in `hourly_plan`, hours 0..23
  - one `directive_interpretation` per note in `note_index` order
  - `directive_type ∈ {solar_reduction, minimum_battery_reserve, no_charge_window, no_discharge_window, max_grid_window, no_op}`
  - `battery_action ∈ {charge, discharge, idle}`, `battery_kwh=0` when `idle`
  - hourly energy balance: `grid + solar_used + battery_discharge = demand + battery_charge`
  - `solar_used ≤ effective_solar` after any `solar_reduction`
  - battery energy within `[active_minimum, capacity]`
  - charge/discharge per-hour ≤ respective rate limit
  - `battery_energy_after_kwh[hour 23] == initial_energy_kwh`
  - `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` match recomputed values (tol 0.01)

---

## Phase 2 — Directive Interpreter (LLM-Assisted)

- [ ] T2.1 Build a deterministic rule-based interpreter first (no LLM):
  - Time phrases (e.g., "noon to 2 PM", "6 PM until 9 PM", "2 AM until 5 AM", "from 1 PM to 3 PM") → ascending unique `hours` 0..23 with start-inclusive / end-exclusive semantics.
  - Keyword mapping → directive type:
    - "wash/clean panels", "dust on panels", "sooty panels", "forecast reduced", "usable solar reduced", "tree shadow", "heavy cloud" → `solar_reduction`
    - "do not charge", "charger isolated", "no charging", "charger maintenance" → `no_charge_window`
    - "do not discharge", "must not discharge", "protection test", "no discharging" → `no_discharge_window`
    - "keep … reserve", "keep at least …", "emergency reserve", "at least X kWh", "at least X%" → `minimum_battery_reserve`
    - "grid import must not exceed", "feeder limit", "temporary limit", "transformer cap" → `max_grid_window`
- [ ] T2.2 Factor extraction for `solar_reduction`: "25% of the forecast" / "75% drop" → `factor = 0.25`. Normalize phrasing ("roughly 25%", "about a quarter", "3/4 of panels out", "drop to ~25%") to a single fraction.
- [ ] T2.3 Reserve computation for `minimum_battery_reserve`: handle both absolute kWh ("100 kWh") and percentage ("50% of the battery capacity") by multiplying with `battery.capacity_kwh`. Use the larger of `minimum_energy_kwh` and the requested reserve.
- [ ] T2.4 Implement "unrelated/distractor" detection: notes that mention deadlines, meetings, registration, holidays, deliveries, payroll, sports office → return `no_op` with `applies=false`, `structured_adjustment=null`.
- [ ] T2.5 LLM fallback (only when rules are unsure): constrain the model with a strict JSON schema; parse, validate, retry once on JSON error, fall back to `no_op` on second failure.
- [ ] T2.6 Cross-validation pass: ensure `hours` arrays are ascending unique integers in 0..23; clamp values; reject contradictory duplicates silently.

---

## Phase 3 — Directive Engine (Apply to Scenario)

- [ ] T3.1 Apply `solar_reduction` → produce `effective_solar[h] = solar_kwh[h] * factor` for the listed hours.
- [ ] T3.2 Apply `minimum_battery_reserve` → raise active floor to `max(minimum_energy_kwh, requested_kwh)` for the listed hours.
- [ ] T3.3 Apply `no_charge_window` / `no_discharge_window` → hard-zero the corresponding battery flow in the listed hours.
- [ ] T3.4 Apply `max_grid_window` → cap `grid_kwh[h]` to the stated kWh per hour.
- [ ] T3.5 Combine multiple directives in the order received; later directives do not override earlier ones unless explicitly allowed.

---

## Phase 4 — Deterministic Optimizer

- [ ] T4.1 Choose optimization strategy: LP/MILP (PuLP or scipy) with safe fallback to a heuristic (priority queue by tariff + battery state) if LP is unavailable.
- [ ] T4.2 Decision variables per hour h:
  - `grid_kwh[h] ≥ 0`
  - `solar_used_kwh[h] ∈ [0, effective_solar[h]]`
  - `b_charge[h] ∈ [0, max_charge]` (0 if in `no_charge_window`)
  - `b_discharge[h] ∈ [0, max_discharge]` (0 if in `no_discharge_window`)
  - `battery_energy_after[h] ∈ [active_minimum[h], capacity]`
  - `battery_action[h]` categorical derived from sign of flow
- [ ] T4.3 Constraints:
  - Energy balance per hour
  - Battery energy update: `battery_energy_after[h] = battery_energy_after[h-1] + η_c·b_charge − b_discharge` (η_c=1 unless stated)
  - Hour 23 battery energy = `initial_energy_kwh`
  - Per-hour rate caps and window caps
- [ ] T4.4 Objective: minimize `Σ tariff[h] · grid_kwh[h]`; tie-break on lower `peak_grid_kwh`.
- [ ] T4.5 Post-process: derive `battery_action` enum and `battery_kwh` from chosen flows; recompute KPIs.
- [ ] T4.6 Round to 0.01 kWh / 0.01 BDT for output, then revalidate all constraints numerically.

---

## Phase 5 — API Layer (FastAPI)

- [ ] T5.1 `POST /optimize-energy` → returns `OptimizeResponse` or RFC-7807-style error JSON with HTTP 400.
- [ ] T5.2 `GET /health` → `{ "status": "ok" }` for the warm-up probe.
- [ ] T5.3 `POST /validate-plan` (optional, recommended) → revalidate any hourly_plan+directives payload against constraints; useful for self-tests.
- [ ] T5.4 Add request size limit and per-request timeout (≤ 8 s) to stay under the 10 s judge SLA.
- [ ] T5.5 Structured logging: scenario_id, total_cost_bdt, peak_grid_kwh, wall_time_ms, directive counts.

---

## Phase 6 — Robustness / Truthfulness (25 pts)

- [ ] T6.1 Always produce exactly 24 hour entries, even for malformed edge inputs (reject early with a clear error).
- [ ] T6.2 Never return KPIs that disagree with the hourly_plan (cross-check inside the handler before responding).
- [ ] T6.3 Never silently mutate the scenario: `scenario_id` echoed, no rounding beyond 0.01.
- [ ] T6.4 `plan_summary` must reference the active directives and the cost/peaking outcomes in plain English; no fabricated metrics.
- [ ] T6.5 Determinism: identical input + identical directive interpretation → identical response (fixed seeds, no random LLM temperature for judge runs).
- [ ] T6.6 Guardrails: reject inputs with >3 notes, missing hours, non-24 hour sets, negative values; never crash.
- [ ] T6.7 LLM failure mode: if the LLM is unreachable, deterministic rules must still complete within the SLA.

---

## Phase 7 — Submission & Performance (25 pts)

- [ ] T7.1 Author `Dockerfile` exposing the API on port 8000 with `uvicorn` workers.
- [ ] T7.2 Provide `docker run -d -p 8000:8000 gridwise-llm:latest` instructions in README.
- [ ] T7.3 Cold-start ≤ 30 s target, warm `POST /optimize-energy` ≤ 5 s p95.
- [ ] T7.4 Add `/warmup` endpoint that pre-loads any heavy resources (LP solver, model cache).
- [ ] T7.5 Verify container boots and `/health` returns 200 within 30 s after `docker run`.
- [ ] T7.6 Provide `submission.json`/manifest file as required by the portal (verify exact field names against the guide).
- [ ] T7.7 Verify the exact endpoint path, port, and request body field names match the public sample cases (`scenario_id`, `operator_notes`, `hours`, `battery`).

---

## Phase 8 — Self-Testing & Replay Harness

- [ ] T8.1 Build a CLI `replay.py` that iterates `SAMPLE-01..SAMPLE-10`:
  - POST each `case.input` to `/optimize-energy`
  - Compare directive interpretation against `case.expected_output.directive_interpretation` (type, applies, hours set, key numeric fields)
  - Validate response against all constraints
  - Print per-case: pass/fail, total_cost_bdt, peak_grid_kwh, ±Δ vs reference
- [ ] T8.2 Add a unit test suite for the directive interpreter (per directive type and per distractor phrase).
- [ ] T8.3 Add a unit test suite for the optimizer (energy balance, neutrality, window caps, rate limits).
- [ ] T8.4 Add a fuzz/property test that randomizes demands, tariffs, solar, and battery parameters (within bounds) and checks constraints hold.

---

## Phase 9 — Hard-Coding Guard (per the public sample guidance)

- [ ] T9.1 Confirm no code path branches on `scenario_id == "SAMPLE-NN"` or specific note wording.
- [ ] T9.2 Confirm no constant arrays map to expected `hours`/`factor`/`minimum_energy_kwh` values from the public pack.
- [ ] T9.3 Hide/abstract all directives behind generic patterns; re-run replay after refactor to ensure zero regression.

---

## Phase 10 — Final Pre-Submission Checklist

- [ ] T10.1 `docker build . -t gridwise-llm:latest` succeeds.
- [ ] T10.2 Container serves `/health` 200 and `POST /optimize-energy` returns valid JSON for all 10 samples.
- [ ] T10.3 All 10 public cases: directive_interpretation matches ground truth (within rule tolerance), all constraints satisfied.
- [ ] T10.4 `total_cost_bdt` is within tolerance of the reference schedule on each sample; if not, document why a stricter optimum was chosen.
- [ ] T10.5 Submission package: code, Dockerfile, README with run instructions, manifest JSON.
- [ ] T10.6 Submit before the cutoff (no late submissions accepted).