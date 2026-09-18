# GridWise LLM — Manual Testing Guide

Step-by-step instructions for testing the service **by hand** (curl + your eyes), no pytest
required. Use this to sanity-check the service yourself, or to demo it. For automated coverage
(215 tests across 9 categories), see the "Automated test suite" section at the end instead.

All commands assume PowerShell on Windows and that you're in the `gridwise-llm/` folder unless
noted otherwise.

---

## Step 0 — Start the service (pick ONE way)

### Option A — already running
The service may already be running. Check first:
```powershell
curl http://localhost:8000/health
```
If you get `{"status":"ok"}`, skip to Step 1.

### Option B — run locally with Python
```powershell
.venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Option C — run via local Docker image
```powershell
docker run -d --name gridwise-manual -p 8000:8000 `
  -e LLM_PROVIDER=gemini `
  -e GEMINI_API_KEY=your_key_here `
  -e LLM_MODEL=gemini-flash-lite-latest `
  gridwise-llm:latest
```

### Option D — run via the published Docker Hub image (no build needed)
```powershell
docker run -d --name gridwise-manual -p 8000:8000 `
  -e LLM_PROVIDER=gemini `
  -e GEMINI_API_KEY=your_key_here `
  -e LLM_MODEL=gemini-flash-lite-latest `
  mohammadyaksafu/gridwise-llm:latest
```

Either Docker option: wait a couple seconds, then confirm the container is up:
```powershell
docker ps --filter name=gridwise-manual
```

---

## Step 1 — Health check

```powershell
curl http://localhost:8000/health
```
**Expect:** HTTP 200, body exactly `{"status":"ok"}`.

---

## Step 2 — One manual request, read every field

```powershell
curl -X POST http://localhost:8000/optimize-energy `
  -H "Content-Type: application/json" `
  -d "@manual_test_cases/SAMPLE-01.json"
```

`manual_test_cases/SAMPLE-01.json` contains this scenario:
- Note 1: *"Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning,
  usable solar should be treated as roughly 25% of the forecast."*
- Note 2 (distractor): *"The sports office moved next month's registration deadline."*

**What to check in the response JSON:**
| Field | What it should look like |
|---|---|
| `scenario_id` | `"SAMPLE-01"` (echoed exactly) |
| `directive_interpretation[0]` | `directive_type: "solar_reduction"`, `applies: true`, `structured_adjustment.hours: [12, 13]`, `structured_adjustment.factor: 0.25` |
| `directive_interpretation[1]` | `directive_type: "no_op"`, `applies: false`, `structured_adjustment: null` |
| `hourly_plan` | exactly 24 entries, `hour` 0 through 23, each with `grid_kwh`, `solar_used_kwh`, `battery_action` (`"charge"`/`"discharge"`/`"idle"`), `battery_kwh`, `battery_energy_after_kwh` |
| `total_grid_kwh` | should equal `2692.5` (reference value) |
| `total_cost_bdt` | should equal `38365` (reference value) — small deviations are fine only if your schedule is a *different but equally valid* optimum; exact match is expected here since the optimizer is deterministic |
| `peak_grid_kwh` | matches the max `grid_kwh` you see across the 24 `hourly_plan` entries |

If everything above matches, the full pipeline (LLM → guardrails → optimizer → validator →
response) is working correctly end-to-end.

---

## Step 3 — Run all 10 public samples and compare against the reference table

Each file in `manual_test_cases/SAMPLE-0N.json` is one of the 10 published cases. Run each one:

```powershell
curl -X POST http://localhost:8000/optimize-energy -H "Content-Type: application/json" -d "@manual_test_cases/SAMPLE-02.json"
curl -X POST http://localhost:8000/optimize-energy -H "Content-Type: application/json" -d "@manual_test_cases/SAMPLE-03.json"
# ...through SAMPLE-10.json
```

Compare what you get against this reference table (expected directive(s) and expected total cost):

| Case | Note summary | Expected directive_type(s) | Expected hours | Expected numeric value | Reference `total_cost_bdt` |
|---|---|---|---|---|---|
| SAMPLE-01 | panel cleaning + distractor | `solar_reduction` | [12,13] | factor 0.25 | 38365 |
| SAMPLE-02 | charger maintenance 2-5am | `no_charge_window` | [2,3,4] | — | 42885 |
| SAMPLE-03 | keep 50% capacity 6-9pm | `minimum_battery_reserve` | [18,19,20] | 100 kWh (50% of 200 kWh capacity) | 35480 |
| SAMPLE-04 | no discharge 6-8pm (protection test) | `no_discharge_window` | [18,19] | — | 40495 |
| SAMPLE-05 | feeder cap 6-9pm | `max_grid_window` | [18,19,20] | 155 kWh | 33950 |
| SAMPLE-06 | cloud cover 10am-noon + charger outage 2-4pm + distractor | `solar_reduction`, `no_charge_window` | [10,11] / [14,15] | factor 0.5 | 34090 |
| SAMPLE-07 | reserve 6-10pm + transformer cap 7-9pm | `minimum_battery_reserve`, `max_grid_window` | [18,19,20,21] / [19,20] | 90 kWh / 180 kWh | 38550 |
| SAMPLE-08 | charger outage 11am-1pm + no-discharge 5-7pm | `no_charge_window`, `no_discharge_window` | [11,12] / [17,18] | — | 37665 |
| SAMPLE-09 | 80% solar cut 11am-2pm + distractor | `solar_reduction` | [11,12,13] | factor 0.2 | 34873 |
| SAMPLE-10 | reserve 6-10pm + grid cap 7-10pm + distractor | `minimum_battery_reserve`, `max_grid_window` | [18,19,20,21] / [19,20,21] | 80 kWh / 190 kWh | 41620 |

For each case, verify:
1. Every note got an interpretation entry, in the right order.
2. Distractor notes are `no_op` with `applies:false` and `structured_adjustment:null`.
3. Real directives have `applies:true` and the hours/numbers roughly match the table (the LLM may
   phrase `explanation` differently — that's fine, only the structured fields matter).
4. `total_cost_bdt` matches the reference column (exact match expected; the optimizer is a linear
   program that always finds the same optimum for these inputs).
5. Skim a few `hourly_plan` entries: for hours inside a `no_charge_window`, `battery_action` should
   never be `"charge"`; for `no_discharge_window` hours, never `"discharge"`; for `max_grid_window`
   hours, `grid_kwh` should never exceed the cap.
6. The **last** `hourly_plan` entry (`hour: 23`) should have `battery_energy_after_kwh` equal to the
   scenario's `battery.initial_energy_kwh` (check the corresponding `manual_test_cases/SAMPLE-0N.json`
   file for that value) — this is the end-of-day battery neutrality rule.

---

## Step 4 — Manually test edge cases / robustness

These confirm the service degrades safely instead of crashing.

**Missing required field (`battery`) → expect HTTP 400:**
```powershell
curl -i -X POST http://localhost:8000/optimize-energy -H "Content-Type: application/json" -d "@manual_test_cases/_edge_missing_battery.json"
```

**Battery `minimum_energy_kwh` above `capacity_kwh` → expect HTTP 400:**
```powershell
curl -i -X POST http://localhost:8000/optimize-energy -H "Content-Type: application/json" -d "@manual_test_cases/_edge_bad_battery.json"
```

**4 operator notes (max is 3) → expect HTTP 400:**
```powershell
curl -i -X POST http://localhost:8000/optimize-energy -H "Content-Type: application/json" -d "@manual_test_cases/_edge_too_many_notes.json"
```

**Only 20 of 24 required hours → expect HTTP 400:**
```powershell
curl -i -X POST http://localhost:8000/optimize-energy -H "Content-Type: application/json" -d "@manual_test_cases/_edge_short_hours.json"
```

**Malformed JSON body → expect HTTP 400:**
```powershell
curl -i -X POST http://localhost:8000/optimize-energy -H "Content-Type: application/json" -d "{not valid json"
```

**After every one of the above, confirm the service is still alive:**
```powershell
curl http://localhost:8000/health
```
It must still return `{"status":"ok"}` — none of these malformed requests should ever crash or
hang the service.

**Response body check for all 400s above:** should look like
`{"error":"invalid_request","detail":[...]}` — never an HTML page, never a raw Python traceback,
never a 500.

---

## Step 5 — Paraphrase test (type your own note)

Write your own operator note that says the same thing as one of the samples but in different
words, and confirm the LLM still extracts the same directive. Example — save as
`manual_test_cases/_my_test.json` (copy `SAMPLE-05.json` and just replace `operator_notes`):

```json
"operator_notes": ["Because the feeder is under a temporary limit, don't draw more than 155 kWh per hour from the grid during the 6 to 9 PM window."]
```
```powershell
curl -X POST http://localhost:8000/optimize-energy -H "Content-Type: application/json" -d "@manual_test_cases/_my_test.json"
```
**Expect:** same result as SAMPLE-05 — `max_grid_window`, hours `[18,19,20]`, `max_grid_kwh: 155` —
even though none of these exact words were in the original sample.

---

## Step 6 — Docker-specific manual checks

```powershell
# Build
docker build -t gridwise-llm:latest .

# Run
docker run -d --name gridwise-dockertest -p 8000:8000 -e LLM_PROVIDER=gemini -e GEMINI_API_KEY=your_key_here -e LLM_MODEL=gemini-flash-lite-latest gridwise-llm:latest

# Confirm health
curl http://localhost:8000/health

# Confirm no secrets baked into the image
docker history gridwise-llm:latest | findstr /I "key secret token"
# (expect no output, or only an unrelated public GPG key line from the Python base image build)

# Confirm it binds 0.0.0.0 not 127.0.0.1 (should show 0.0.0.0:8000->8000/tcp)
docker ps --filter name=gridwise-dockertest --format "{{.Ports}}"

# Clean up
docker rm -f gridwise-dockertest
```

To test the **published** image the same way, swap `gridwise-llm:latest` for
`mohammadyaksafu/gridwise-llm:latest` and skip the build step — it should behave identically.

---

## Step 7 — Automated test suite (optional but recommended)

For full coverage beyond what you can practically check by hand (215 tests across white-box,
black-box, requirement-based, regression, robustness, LLM/prompt, optimizer/constraint,
API/integration, and security categories):

```powershell
.venv\Scripts\activate
pip install -r requirements-dev.txt

# Everything except live LLM calls (fast, free, ~206 tests)
pytest -m "not llm" -v

# Only the tests that hit the real configured LLM provider (~9 tests, costs quota)
pytest -m llm -v

# Absolutely everything
pytest -v
```
All should show `passed`, none `failed`. See `PROGRESS.md` for the full breakdown and the two real
bugs this suite already caught and fixed.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `curl: (7) Failed to connect` | Service isn't running — go back to Step 0 |
| `directive_interpretation` all `no_op` when they shouldn't be | LLM provider call failed (check API key / quota) — service falls back to safe `no_op` rather than crash; check container/terminal logs for `LLM interpretation attempt ... failed` |
| Response has `"error":"internal_error"` | An unexpected server error (500) — check logs; this should be rare and is always logged server-side without ever leaking details in the response |
| `429 RESOURCE_EXHAUSTED` in logs | Gemini free-tier rate limit hit — wait a bit, or confirm `LLM_MODEL` is a `-lite` variant (much higher free quota than preview flash models) |
