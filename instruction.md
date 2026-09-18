# GridWise LLM — BUP CSE Fest 2026 Preliminary Round

An LLM-assisted energy optimization API that interprets natural-language operator notes and produces a valid, cost-minimized 24-hour energy schedule.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Setup](#setup)
- [Environment Variables](#environment-variables)
- [Run Command](#run-command)
- [API Reference](#api-reference)
- [LLM Role](#llm-role)
- [Guardrails](#guardrails)
- [Optimizer](#optimizer)
- [Docker](#docker)
- [Testing](#testing)
- [Dependencies](#dependencies)
- [Limitations](#limitations)

---

## Overview

This service exposes two HTTP endpoints:

| Endpoint           | Method | Purpose                              |
|--------------------|--------|--------------------------------------|
| `/health`          | GET    | Readiness check (responds within 60s)|
| `/optimize-energy` | POST   | Full LLM → guardrail → optimize flow |

**Pipeline:**

```
operator_notes (natural language)
        ↓
   LLM Interpreter
        ↓
  directive_interpretation (structured)
        ↓
  Deterministic Guardrails
        ↓
  Directive Engine (hard constraints)
        ↓
  Optimizer (LP / CP-SAT / custom solver)
        ↓
  Validation Engine
        ↓
  Final JSON Response (24-hour hourly_plan)
```

---

## Architecture

```
POST /optimize-energy
         │
         ▼
 Request Validator
         │
         ▼
   LLM Interpreter
   operator_notes → structured directives
         │
         ▼
 Deterministic Guardrails
   - schema validation
   - hours: integer, 0–23, unique, ascending
   - numeric range checks
   - directive type allowlist
   - applies / no_op enforcement
         │
         ▼
  Directive Engine
   converts notes → hard constraints
         │
         ▼
     Optimizer
   minimizes grid cost under constraints
         │
         ▼
  Validation Engine
   - energy balance per hour
   - effective solar limits
   - battery bounds / charge / discharge rates
   - grid cap / window constraints
   - end-of-day battery neutrality
         │
         ▼
  Final JSON Response
   directive_interpretation + hourly_plan + totals
```

---

## Setup

### Prerequisites

- Python 3.10+ (or Node.js 18+ — update as per your stack)
- pip / npm
- Docker (for fallback image)

### Install dependencies

```bash
pip install -r requirements.txt
```

### Configure environment

Copy the example env file and fill in values:

```bash
cp .env.example .env
# Edit .env with your actual values (never commit .env)
```

---

## Environment Variables

| Variable          | Description                                      | Required |
|-------------------|--------------------------------------------------|----------|
| `LLM_API_KEY`     | API key for your LLM provider (e.g. OpenAI)      | Yes      |
| `LLM_MODEL`       | Model identifier (e.g. `gpt-4o`)                 | Yes      |
| `LLM_BASE_URL`    | Base URL for LLM API (leave blank for default)   | No       |
| `PORT`            | Port the service listens on (default: `8000`)    | No       |

> ⚠️ **Never commit actual secret values.** Use `.env` locally and inject secrets via environment in production/Docker.

---

## Run Command

```bash
# Local development
uvicorn main:app --host 0.0.0.0 --port 8000

# Or with Python directly
python main.py
```

Service must be ready (health endpoint responding) **within 60 seconds** of start.

---

## API Reference

### GET /health

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{
  "status": "ok"
}
```

### POST /optimize-energy

```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

> **Refer to the Problem Statement PDF for the exact request/response JSON schema.** The schema below is a conceptual illustration only.

**Conceptual request shape:**

```json
{
  "scenario_id": "example-001",
  "operator_notes": [
    "Reduce solar availability by 80% from 1 PM to 3 PM.",
    "Do not charge the battery between 2 PM and 4 PM.",
    "The team had a great meeting today."
  ],
  "demand": [...],
  "solar_forecast": [...],
  "tariff": [...],
  "battery": { ... }
}
```

**Conceptual response shape:**

```json
{
  "scenario_id": "example-001",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "explanation": "Solar availability reduced to 20% during hours 13–14.",
      "structured_adjustment": { ... }
    },
    {
      "note_index": 1,
      "applies": true,
      "directive_type": "no_charge_window",
      "explanation": "Battery charging disabled during hours 14–15.",
      "structured_adjustment": { ... }
    },
    {
      "note_index": 2,
      "applies": false,
      "directive_type": "no_op",
      "explanation": "Note has no applicable effect on energy optimization.",
      "structured_adjustment": null
    }
  ],
  "hourly_plan": [ ... ],
  "total_grid_kwh": 12.5,
  "total_cost_bdt": 187.3,
  "plan_summary": "..."
}
```

**directive_interpretation rules:**

- One entry per operator note, in the same order as the input.
- Relevant note → `applies: true`, correct `directive_type`, valid `structured_adjustment`.
- Irrelevant note → `applies: false`, `directive_type: "no_op"`, `structured_adjustment: null`.

**hours array rules (within structured_adjustment):**

- Integers only, range `0–23`.
- No duplicates.
- Must be in ascending order.

**Timeout:** Each request must complete within **30 seconds**.

---

## LLM Role

The LLM is **mandatory** in the operator-note interpretation path. It is not used merely for generating `plan_summary` text.

**What the LLM does:**

1. Reads each `operator_notes` entry.
2. Determines if it is relevant to energy optimization.
3. Extracts the semantic meaning: directive type, affected hours, numeric parameters.
4. Outputs a structured interpretation for each note.

**Example (conceptual):**

Input note: `"Reduce solar availability by 80% from 1 PM to 3 PM."`

LLM output (structured):

```
directive_type = solar_reduction
hours          = [13, 14]      ← end-exclusive: 1 PM → 3 PM maps to hours 13, 14
factor         = 0.2           ← 80% reduction → 20% usable (factor = 1 - 0.80)
```

The LLM handles **paraphrased/rewording** of the same directive, not just exact phrases. Hard-coded phrase matching alone is not compliant.

---

## Guardrails

LLM output is probabilistic and must pass deterministic validation before reaching the optimizer.

**Checks performed:**

| Check                | Rule                                                      |
|----------------------|-----------------------------------------------------------|
| Directive type       | Must be in the supported allowlist (from Problem Statement) |
| `applies` value      | Correct boolean semantics                                  |
| Hours array          | Integers, `0–23`, unique, ascending                        |
| Numeric values       | Within valid defined ranges                                |
| `structured_adjustment` shape | Matches expected schema for the directive type  |
| Invented directives  | Rejected — LLM cannot create unsupported directive types   |

If validation fails → safe fallback / controlled error (no crash, no 5xx on valid input).

---

## Optimizer

> Refer to the **Problem Statement PDF** for exact energy balance equations, battery model, and grid constraints. Do not use self-invented equations.

**What the optimizer enforces:**

- Hourly energy balance (demand = solar + battery discharge + grid draw)
- Effective solar limits (reduced by `solar_reduction` directive if applicable)
- Battery bounds (capacity, charge rate, discharge rate, state transitions)
- Grid constraints (`max_grid_window`, window limits)
- No-charge / no-discharge windows
- Minimum battery reserve
- **End-of-day battery neutrality:** `battery_end == battery_initial`

**Objective:** Minimize total grid cost (`total_cost_bdt`) subject to all hard constraints.

**Solver options (choose based on your implementation):**

- Linear Programming (e.g. `scipy.optimize.linprog`, `PuLP`, `OR-Tools`)
- Constraint Programming (e.g. `CP-SAT` from OR-Tools)
- Custom greedy/DP solver

**Numeric tolerance:** `0.01 kWh` / `0.01 BDT` for floating-point comparisons.

---

## Docker

### Build

```bash
docker build -t gridwise-llm:latest .
```

### Run

```bash
docker run -p 8000:8000 \
  -e LLM_API_KEY=your_key_here \
  -e LLM_MODEL=your_model_here \
  gridwise-llm:latest
```

### Pull from registry

```bash
docker pull <your-registry>/<image-name>:<tag-or-digest>
```

> Provide the exact tag or digest in your submission form.

**Docker requirements:**

- Service binds to `0.0.0.0` (not `127.0.0.1`)
- Exposes the correct port
- `/health` responds within 60 seconds of container start
- No secrets baked into the image — inject via environment variables at runtime

---

## Testing

### Health check

```bash
curl http://localhost:8000/health
# Expected: {"status":"ok"}
```

### Sample optimize-energy request

```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

> `sample_request.json` — use the public sample provided in the Problem Statement.

### Validate response manually

- Every `operator_notes[i]` has a `directive_interpretation[i]` (same count, same order).
- `no_op` entries have `applies: false` and `structured_adjustment: null`.
- `total_grid_kwh` and `total_cost_bdt` match the sum computed from `hourly_plan`.
- Battery at end of hour 23 equals initial battery level.

---

## Dependencies

| Package / Tool      | Purpose                              |
|---------------------|--------------------------------------|
| `fastapi`           | HTTP API framework                   |
| `uvicorn`           | ASGI server                          |
| `openai` / `anthropic` / `google-generativeai` | LLM SDK (update as applicable) |
| `pydantic`          | Request/response validation          |
| `pulp` / `ortools`  | Optimization solver                  |
| `python-dotenv`     | Environment variable loading         |

> See `requirements.txt` for exact versions.

---

## Limitations

- LLM response latency affects p95. Target `p95 ≤ 5 seconds` per request.
- Hosted LLM API quota and rate limits are the team's responsibility during evaluation.
- If the LLM provider is unreliable, consider caching or retry logic with exponential backoff.
- Floating-point rounding errors in the optimizer may occasionally require tolerance handling (`0.01` absolute).
- Contradictory directives are not expected in valid judge scenarios, but the guardrail layer should still handle gracefully.
- This service uses only the supplied synthetic challenge data — no real campus, utility, or personal data.

---

## Security

- No API keys, tokens, passwords, or `.env` files are committed to this repository.
- Secrets are injected via environment variables only.
- No sensitive data appears in API responses or logs.
- No real-world personal or utility data is used.

---

## Repository Notes

- Repository was created **after** question reveal (per competition rules).
- Kept **private** during the event window.
- Made **public** after submission deadline for organizer evaluation.

---

## 3-Minute Video

A short technical walkthrough video (≤ 3 minutes) covering:

1. Problem statement
2. System architecture
3. LLM interpretation approach
4. Guardrail validation logic
5. Optimizer design
6. How to run and test the service

> Video link: *(add after recording)*

---

## Scoring Reference

| Category                                        | Points |
|-------------------------------------------------|--------|
| LLM Directive Interpretation                    | 25     |
| Directive Application & Constraint Correctness  | 25     |
| Optimization Quality                            | 10     |
| API Contract & Schema                           | 10     |
| Performance & Reliability                       | 10     |
| Deployment & Docker                             | 10     |
| Documentation & Local Reproducibility           | 10     |
| **Total**                                       | **100**|

> The 3-minute video is used only as a **tie-breaker** and contributes 0 points to the base score.
