# GridWise LLM — BUP CSE Fest 2026 Preliminary Round

An LLM-assisted energy optimization API that interprets natural-language operator notes and
produces a valid, cost-minimized 24-hour energy schedule for a smart campus.

## Overview

```
operator_notes (natural language)
        v
   LLM Interpreter (Google Gemini or Anthropic Claude, forced structured JSON output)
        v
  Deterministic Guardrails (schema/type/range/hours validation, safe no_op fallback)
        v
  Directive Engine (converts validated directives into hard constraints)
        v
  Optimizer (linear program, PuLP/CBC) - minimizes grid cost
        v
  Schedule Validator (independent re-check: energy balance, battery, directives, EOD neutrality)
        v
  Final JSON Response
```

Two endpoints:

| Endpoint            | Method | Purpose                               |
|----------------------|--------|----------------------------------------|
| `/health`            | GET    | Readiness check                        |
| `/optimize-energy`   | POST   | Full LLM -> guardrail -> optimize flow |

## LLM role (mandatory path)

`app/llm_interpreter.py` supports two providers, selected via `LLM_PROVIDER`
(`gemini` or `anthropic`):

- **Gemini** (default): `client.models.generate_content(...)` with
  `response_mime_type="application/json"` and an explicit `response_schema`, forcing
  structured JSON output.
- **Anthropic**: the Messages API with a forced tool call (`emit_interpretations`).

Either way the model must return one structured interpretation per operator note as JSON —
no free-text parsing, no keyword/regex matching. The system prompt teaches the model the 6
supported directive types, the half-open time-window rule (`1 PM–3 PM` -> `[13,14]`), and
the `factor = fraction remaining` convention, and is given the scenario's battery
parameters as read-only context so it can resolve percentage-based notes (e.g. "keep 50% of
battery capacity in reserve") into the required absolute kWh value. The LLM's output is
then treated as **untrusted** and passed through `app/guardrails.py` before any of it can
reach the optimizer — this satisfies the "LLM must be part of the interpretation path, not
just plan_summary" requirement.

## Guardrails

`app/guardrails.py` deterministically validates every field the LLM returns:

- `directive_type` must be one of the 6 supported types (or rejected)
- exactly one entry per note, in `note_index` order (missing/duplicate -> safe fallback)
- `applies` semantics (`no_op` => `false`, everything else => `true`)
- `hours`: integers, `0-23`, unique, ascending
- `factor` in `[0,1]`, `minimum_energy_kwh` in `[0, capacity]`, `max_grid_kwh >= 0`
- `structured_adjustment` shape matches the directive type

Any note whose LLM output fails a check is **safely downgraded to `no_op`** for that note
only — the service never crashes and never invents an unsupported directive. If the LLM
provider itself is unreachable, all notes fall back to `no_op` and a valid schedule is
still returned (controlled degradation, no 5xx).

## Optimizer

`app/optimizer.py` builds a linear program (PuLP + bundled CBC solver) with one hour block
(`grid`, `solar_used`, `charge`, `discharge`, `battery_energy`) per hour, enforcing:

- Hourly energy balance: `grid + solar_used + discharge == demand + charge`
- `solar_used <= effective_solar` (reduced by `solar_reduction` directives)
- Battery bounds: `effective_min_reserve <= battery_energy <= capacity`
- Rate limits: `charge <= max_charge_kwh_per_hour`, `discharge <= max_discharge_kwh_per_hour`
  (forced to 0 in `no_charge_window` / `no_discharge_window` hours)
- `grid <= max_grid_kwh` cap where a `max_grid_window` directive applies
- End-of-day neutrality: `battery_energy[23] == battery.initial_energy_kwh`
- Objective: `minimize sum(grid[h] * tariff[h])`

`app/schedule_validator.py` then independently re-checks the solved plan against every one
of the same constraints before it is returned, as a defense-in-depth sanity pass.

## Setup

### Prerequisites

- Python 3.10+
- An API key for one LLM provider: Google Gemini (free tier at
  [aistudio.google.com/apikey](https://aistudio.google.com/apikey)) or Anthropic Claude
- Docker (optional, for the fallback image)

### Install

```bash
cd gridwise-llm
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Configure environment

```bash
cp .env.example .env
# Edit .env: set LLM_PROVIDER (gemini or anthropic) and LLM_API_KEY
```

## Environment variables

| Variable       | Description                                                                 | Required |
|----------------|-------------------------------------------------------------------------------|----------|
| `LLM_PROVIDER` | `gemini` or `anthropic`. Defaults to `gemini` if `GEMINI_API_KEY` is set, else `anthropic` | No |
| `LLM_API_KEY`  | API key for the selected provider (or use `GEMINI_API_KEY` / `ANTHROPIC_API_KEY`) | Yes  |
| `LLM_MODEL`    | Model id (default `gemini-3.6-flash` for Gemini, `claude-haiku-4-5-20251001` for Anthropic) | No |
| `LLM_BASE_URL` | Override API base URL, Anthropic only (leave blank for default)               | No       |
| `PORT`         | Port the service listens on (default `8000`)                                  | No       |

> Never commit `.env` or real key values. `.env` is gitignored.

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Test

### Health check

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### Sample request

```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "demo-1",
    "operator_notes": ["Reduce solar availability by 80% from 1 PM to 3 PM."],
    "hours": [ ... 24 hourly entries ... ],
    "battery": {
      "capacity_kwh": 500,
      "initial_energy_kwh": 200,
      "minimum_energy_kwh": 50,
      "max_charge_kwh_per_hour": 100,
      "max_discharge_kwh_per_hour": 100
    }
  }'
```

### Run all public sample cases

With the service running locally:

```bash
python tests/test_public_samples.py http://localhost:8000
```

This posts each of the 10 cases in `BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json`
(one directory above `gridwise-llm/`) and independently re-verifies energy balance,
effective-solar, battery bounds/rates, directive constraints, end-of-day neutrality, and
totals consistency against the response — it does not byte-compare schedules.

## Docker

### Build

```bash
docker build -t gridwise-llm:latest .
```

### Run

```bash
docker run -p 8000:8000 \
  -e LLM_PROVIDER=gemini \
  -e GEMINI_API_KEY=your_key_here \
  -e LLM_MODEL=gemini-flash-lite-latest \
  gridwise-llm:latest
```

### Pull from registry

Public image, no login required:

```bash
docker pull mohammadyaksafu/gridwise-llm:latest
# or pin the exact digest:
docker pull mohammadyaksafu/gridwise-llm@sha256:30fd8b4f62a8c57dfc738c0d107d4668edb428663dce9aad27bff7efbe782f57

docker run -p 8000:8000 \
  -e LLM_PROVIDER=gemini \
  -e GEMINI_API_KEY=your_key_here \
  -e LLM_MODEL=gemini-flash-lite-latest \
  mohammadyaksafu/gridwise-llm:latest
```

Docker Hub: <https://hub.docker.com/r/mohammadyaksafu/gridwise-llm>

The image binds to `0.0.0.0:8000`, contains no baked-in secrets, and expects all
configuration via environment variables at runtime.

## Dependencies

| Package     | Purpose                          |
|-------------|-----------------------------------|
| `fastapi`   | HTTP API framework                |
| `uvicorn`   | ASGI server                       |
| `google-genai` | LLM SDK (Google Gemini, default provider) |
| `anthropic` | LLM SDK (Anthropic Claude, alternate provider) |
| `pydantic`  | Request/response schema validation|
| `pulp`      | Linear programming solver (CBC)   |
| `python-dotenv` | `.env` loading                |
| `httpx`     | Used by the local sample test script |
| `pytest`    | Test suite (`requirements-dev.txt`) |

## Limitations

- Depends on Anthropic API availability/quota/latency; p95 target is 5s per request.
- If the LLM call fails after retries, the service falls back to treating all notes as
  `no_op` rather than failing the request, so a valid (if directive-less) schedule is
  still returned.
- Floating-point comparisons use an absolute tolerance of `0.01` kWh/BDT.
- Uses only synthetic challenge data; no real campus/utility/personal data.

## Repository & security notes

- No API keys, tokens, or `.env` files are committed. Secrets are injected via environment
  variables only, both locally and in Docker.
- No secrets or stack traces are ever included in API responses or logs; unhandled errors
  return a generic `{"error": "internal_error"}` with a 500 status.
