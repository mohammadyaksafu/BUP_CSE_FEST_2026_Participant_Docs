# BUP CSE Fest 2026 — Smart Campus Energy Optimization Challenge
## LLM-Assisted Operator Directive Interpretation — Problem Statement

> **This document is the canonical specification** for the Preliminary Challenge. All endpoint definitions, input/output schemas, operator-note interpretation rules, optimization logic, battery rules, and validation criteria are defined here. Deployment, submission, scoring, and penalties are covered in the separate **Participant Guide & Evaluation Rubric**.

---

## Table of Contents

1. [Challenge Overview](#1-challenge-overview)
2. [Basic Information](#2-basic-information)
3. [What to Build](#3-what-to-build)
4. [End-to-End Flow](#4-end-to-end-flow)
5. [Supported Directive Types](#5-supported-directive-types)
6. [Time Window Rule](#6-time-window-rule)
7. [API Contract](#7-api-contract)
8. [Request Schema](#8-request-schema)
9. [LLM Interpretation Guardrails](#9-llm-interpretation-guardrails)
10. [Battery & Energy Accounting](#10-battery--energy-accounting)
11. [Response Schema](#11-response-schema)
12. [Validation & Hidden Evaluation](#12-validation--hidden-evaluation)
13. [Top 10 Implementation Traps](#13-top-10-implementation-traps)
14. [Quick End-to-End Example](#14-quick-end-to-end-example)

---

## 1. Challenge Overview

Build an **HTTP API service** that optimizes a Smart Campus's next **24 hours of electricity usage** across three energy sources:

| Source | Description |
|--------|-------------|
| Grid electricity | Purchased from the utility grid |
| Rooftop solar | Generated on-site; cannot be exported to grid |
| Battery storage | Charged and discharged to shift energy |

Per-hour inputs provided:
- `demand_kwh` — campus electricity requirement
- `solar_kwh` — available solar generation (before operator adjustments)
- `tariff_bdt_per_kwh` — grid electricity price

The campus operator also provides **1–3 natural-language notes** per scenario. Your system must:

1. Use an **LLM** to interpret those notes into structured directives
2. **Validate** the LLM output deterministically
3. **Apply** the directives as hard constraints to the optimizer
4. Return a **valid, cost-minimized 24-hour schedule**

> ⚠️ Using LLM only to generate `plan_summary` text does **not** satisfy the LLM requirement. The LLM must be in the operator-note interpretation path.

All scenarios and operator notes are **synthetic**. Do not use real campus, utility, billing, or personal data.

---

## 2. Basic Information

| Field | Value |
|-------|-------|
| Round | Online Preliminary |
| Time | 7:00 PM – 11:00 PM |
| Duration | 4 hours |
| Challenge Type | LLM-assisted energy scheduling and optimization |
| Required Service | Public HTTP API |
| Health Endpoint | `GET /health` |
| Main Endpoint | `POST /optimize-energy` |
| Planning Horizon | 24 hourly intervals (hour 0–23) |
| Operator Notes per Scenario | 1–3 |
| Response Format | Structured JSON |

---

## 3. What to Build

Your service takes as input:
- A 24-hour energy scenario (demand, solar, tariff per hour)
- Battery parameters
- 1–3 operator notes in natural language

And returns:
- A **structured interpretation** of every operator note
- A **valid 24-hour optimized energy schedule**

### Per-note decision

For each operator note, the LLM must decide:

| Condition | Result |
|-----------|--------|
| Note affects energy schedule | `applies: true`, correct `directive_type`, valid `structured_adjustment` |
| Note is irrelevant (distractor) | `applies: false`, `directive_type: "no_op"`, `structured_adjustment: null` |

### Core requirement

```
Correct interpretation  →  Correct application in schedule
```

Interpreting correctly but not applying the directive in the optimizer = **incorrect**.

---

## 4. End-to-End Flow

```
operator_notes (natural language)
         │
         ▼
    LLM Interpreter
    (semantic understanding, not keyword matching)
         │
         ▼
  Structured Directives
         │
         ▼
  Deterministic Validator
  (schema, types, ranges, hours, note mapping)
         │
         ▼
  Directive Engine
  (hard constraints applied to optimizer)
         │
         ▼
      Optimizer
  (minimize total grid cost subject to all constraints)
         │
         ▼
  Schedule Validator
  (energy balance, battery, solar, EOD neutrality)
         │
         ▼
  Final JSON Response
```

> **LLM output must be treated as untrusted data.** Always pass it through a deterministic validation layer before the optimizer.

---

## 5. Supported Directive Types

Only these **6 directive types** are valid. Any other type invented by the LLM must be rejected.

---

### 5.1 `solar_reduction`

Reduces available solar during specific hours.

**Structure:**
```json
{
  "hours": [13, 14],
  "factor": 0.2
}
```

- `factor` = fraction of solar that **remains** (not the reduction amount)
- 80% reduction → `factor = 0.2` (20% remains)
- Effective solar: `effective_solar[h] = original_solar[h] × factor`
- Valid range: `0 ≤ factor ≤ 1`

**Example note:** *"Solar output will drop to about 20% from 1 PM to 3 PM."*

---

### 5.2 `minimum_battery_reserve`

Battery energy must stay at or above a threshold after specific hours.

**Structure:**
```json
{
  "hours": [18, 19, 20],
  "minimum_energy_kwh": 120
}
```

- `battery_energy_after_kwh[h] >= minimum_energy_kwh` for each listed hour
- If this directive reserve > base `minimum_energy_kwh`, the directive value applies
- Valid range: `0 ≤ minimum_energy_kwh ≤ battery.capacity_kwh`

**Example note:** *"Keep at least 120 kWh in reserve from 6 PM until 9 PM."*

---

### 5.3 `no_charge_window`

Battery charging is forbidden during specific hours.

**Structure:**
```json
{
  "hours": [14, 15]
}
```

- `battery_charge[h] = 0` for each listed hour

**Example note:** *"Do not charge the battery between 2 PM and 4 PM."*

---

### 5.4 `no_discharge_window`

Battery discharging is forbidden during specific hours.

**Structure:**
```json
{
  "hours": [8, 9, 10]
}
```

- `battery_discharge[h] = 0` for each listed hour

---

### 5.5 `max_grid_window`

Grid usage is capped at a maximum value during specific hours.

**Structure:**
```json
{
  "hours": [18, 19, 20],
  "max_grid_kwh": 100
}
```

- `grid_kwh[h] ≤ max_grid_kwh` for each listed hour
- Valid range: `max_grid_kwh ≥ 0`

---

### 5.6 `no_op`

The note has no applicable effect on energy optimization.

**Structure:**
```json
{
  "applies": false,
  "directive_type": "no_op",
  "structured_adjustment": null
}
```

**Example note:** *"The cafeteria menu changes tomorrow."*

---

## 6. Time Window Rule

> **Start hour is included. End hour is excluded.**

| Note says | Maps to hours array |
|-----------|---------------------|
| 1 PM to 3 PM | `[13, 14]` — NOT `[13, 14, 15]` |
| 2 PM to 4 PM | `[14, 15]` — NOT `[14, 15, 16]` |
| 6 PM to 9 PM | `[18, 19, 20]` |

Mathematical notation: `[start, end)` — half-open interval.

### `hours` array strict rules

| Rule | Valid | Invalid |
|------|-------|---------|
| Must be integers | `[13, 14]` | `[13.5, 14]` |
| Range 0–23 | `[0, 23]` | `[13, 24]` |
| No duplicates | `[13, 14]` | `[13, 13, 14]` |
| Ascending order | `[13, 14, 15]` | `[14, 13, 15]` |

---

## 7. API Contract

### GET /health

```http
GET /health
```

**Response (HTTP 200):**
```json
{
  "status": "ok"
}
```

Must respond within **60 seconds** of service start.

---

### POST /optimize-energy

```http
POST /optimize-energy
Content-Type: application/json
```

**Timeouts:**
- Must complete within **30 seconds** per request

**HTTP Status Codes:**

| Code | Meaning |
|------|---------|
| `200` | Successful optimization |
| `400` | Malformed JSON or structurally invalid request |
| `422` | Well-formed but semantically invalid (optional) |
| `500` | Controlled internal error — must not expose secrets or raw stack traces |

---

## 8. Request Schema

### Full structure

```json
{
  "scenario_id": "GRID-101",
  "operator_notes": [
    "Solar output will drop to about 20% from 1 PM to 3 PM.",
    "Do not charge the battery between 2 PM and 4 PM.",
    "The cafeteria menu changes tomorrow."
  ],
  "hours": [
    {
      "hour": 0,
      "demand_kwh": 180,
      "solar_kwh": 0,
      "tariff_bdt_per_kwh": 7
    },
    "... 22 more entries ...",
    {
      "hour": 23,
      "demand_kwh": 200,
      "solar_kwh": 0,
      "tariff_bdt_per_kwh": 9
    }
  ],
  "battery": {
    "capacity_kwh": 500,
    "initial_energy_kwh": 200,
    "minimum_energy_kwh": 50,
    "max_charge_kwh_per_hour": 100,
    "max_discharge_kwh_per_hour": 100
  }
}
```

---

### Field definitions

#### `scenario_id`
- Type: `string`
- Unique identifier for the scenario
- Must be echoed back in the response

#### `operator_notes`
- Type: `array[string]`
- Length: 1–3
- Each entry: non-empty natural-language string
- Not all notes are relevant; some are distractors

#### `hours`
- Type: `array[object]`
- Length: exactly 24 entries (hours 0–23, unique)

| Field | Type | Description |
|-------|------|-------------|
| `hour` | integer (0–23) | Hour identifier |
| `demand_kwh` | number | Campus electricity requirement |
| `solar_kwh` | number | Base available solar **before** operator adjustments |
| `tariff_bdt_per_kwh` | number | Grid electricity price |

#### `battery`

| Field | Type | Description |
|-------|------|-------------|
| `capacity_kwh` | number | Maximum energy the battery can hold |
| `initial_energy_kwh` | number | Energy in battery at start of hour 0 |
| `minimum_energy_kwh` | number | Battery must never fall below this |
| `max_charge_kwh_per_hour` | number | Maximum charge in any single hour |
| `max_discharge_kwh_per_hour` | number | Maximum discharge in any single hour |

---

## 9. LLM Interpretation Guardrails

LLM output must be treated as **untrusted structured data**. A deterministic validator must check every field before the directives reach the optimizer.

### Required checks

| Check | Rule |
|-------|------|
| Directive type | Must be one of the 6 supported types |
| Note mapping | Every `note_index` maps to an existing note; each note appears exactly once |
| `applies` semantics | Only `no_op` may have `applies: false` |
| `solar_reduction` factor | `0 ≤ factor ≤ 1` |
| `minimum_energy_kwh` | `0 ≤ value ≤ battery.capacity_kwh`, finite |
| `max_grid_kwh` | `value ≥ 0`, finite |
| `hours` array | Integer, 0–23, unique, ascending |
| No invention rule | LLM must not modify `demand`, `solar`, `tariff`, `battery` params, or invent unsupported directive types |

### On failure

If LLM produces malformed output:
- ❌ Do NOT crash
- ❌ Do NOT invent a new directive
- ❌ Do NOT silently ignore and proceed
- ✅ Handle with a controlled safe failure

---

## 10. Battery & Energy Accounting

The judge independently replays every hour of your schedule. All of the following must hold.

### Battery state equation

| Action | State change |
|--------|-------------|
| `charge` | `E_after = E_before + battery_kwh` |
| `discharge` | `E_after = E_before - battery_kwh` |
| `idle` | `E_after = E_before` (and `battery_kwh = 0`) |

### Battery bounds (every hour)

```
minimum_energy_kwh  ≤  E_after  ≤  capacity_kwh
```

If `minimum_battery_reserve` directive applies, effective minimum for those hours:
```
effective_min[h] = max(battery.minimum_energy_kwh, directive.minimum_energy_kwh)
```

### Rate limits

```
action = charge    →  battery_kwh ≤ max_charge_kwh_per_hour
action = discharge →  battery_kwh ≤ max_discharge_kwh_per_hour
```

### Solar usage

```
0  ≤  solar_used_kwh  ≤  effective_solar_kwh
```

- Unused solar is curtailed (not exported to grid)
- After `solar_reduction` directive: `effective_solar[h] = original_solar[h] × factor`

### Energy balance (every hour — most important equation)

```
grid_kwh + solar_used_kwh + battery_discharge_kwh
  =
demand_kwh + battery_charge_kwh
```

Both sides must be equal for every hour.

### End-of-day battery neutrality

```
battery_energy_after_kwh[hour=23]  =  battery.initial_energy_kwh
```

The battery must end the day at its starting level. Starting battery energy must not be used as a free energy source.

### Optimization objective

```
Minimize:  SUM( grid_kwh[h] × tariff_bdt_per_kwh[h] )   for h = 0 … 23
```

Subject to all energy, battery, directive, and neutrality constraints.

---

## 11. Response Schema

### Top-level structure

```json
{
  "scenario_id": "GRID-101",
  "directive_interpretation": [...],
  "hourly_plan": [...],
  "total_grid_kwh": 2400.0,
  "total_cost_bdt": 18750.0,
  "peak_grid_kwh": 280.0,
  "plan_summary": "Solar was prioritized when available..."
}
```

| Field | Description |
|-------|-------------|
| `scenario_id` | Must exactly match the request `scenario_id` |
| `directive_interpretation` | One entry per operator note, in input order |
| `hourly_plan` | Exactly 24 entries (hours 0–23) |
| `total_grid_kwh` | `SUM(grid_kwh)` across all hours |
| `total_cost_bdt` | `SUM(grid_kwh[h] × tariff[h])` across all hours |
| `peak_grid_kwh` | `MAX(grid_kwh[h])` across all hours |
| `plan_summary` | Short human-readable explanation (optional LLM use here) |

> `hourly_plan` is the **source of truth** for totals. The judge will recalculate and compare.

---

### `directive_interpretation` entry

```json
{
  "note_index": 0,
  "applies": true,
  "directive_type": "solar_reduction",
  "structured_adjustment": {
    "hours": [13, 14],
    "factor": 0.2
  },
  "explanation": "Solar availability is reduced during panel cleaning."
}
```

```json
{
  "note_index": 2,
  "applies": false,
  "directive_type": "no_op",
  "structured_adjustment": null,
  "explanation": "This note does not affect today's energy schedule."
}
```

| Field | Rule |
|-------|------|
| `note_index` | Zero-based index matching input `operator_notes` position |
| `applies` | `true` for all directives except `no_op`; `false` only for `no_op` |
| `directive_type` | One of the 6 supported types |
| `structured_adjustment` | Per-directive schema (see Section 5); `null` for `no_op` |
| `explanation` | Short natural-language description (not byte-matched by judge) |

**Count rule:** If input has N notes → response must have exactly N interpretation entries, in order `0, 1, …, N-1`.

---

### `hourly_plan` entry

```json
{
  "hour": 13,
  "grid_kwh": 100.0,
  "solar_used_kwh": 100.0,
  "battery_action": "discharge",
  "battery_kwh": 100.0,
  "battery_energy_after_kwh": 100.0
}
```

| Field | Type | Rule |
|-------|------|------|
| `hour` | integer (0–23) | Unique across plan |
| `grid_kwh` | number ≥ 0 | Grid draw this hour |
| `solar_used_kwh` | number ≥ 0 | Solar used (≤ effective solar) |
| `battery_action` | `"charge"` / `"discharge"` / `"idle"` | Exactly one of these three strings |
| `battery_kwh` | number ≥ 0 | Magnitude; must be `0` when `idle` |
| `battery_energy_after_kwh` | number | Battery state after this hour's action |

---

## 12. Validation & Hidden Evaluation

The judge checks **all** of the following. A single failure invalidates the case.

### Interpretation checks

- [ ] Each relevant note correctly identified (not marked `no_op`)
- [ ] Each irrelevant note correctly marked `no_op`
- [ ] Correct `directive_type` for each relevant note
- [ ] Correct `hours` array (range, uniqueness, order)
- [ ] Correct numeric values (`factor`, `minimum_energy_kwh`, `max_grid_kwh`)
- [ ] Paraphrased/reworded notes handled correctly (semantic, not keyword-based)
- [ ] Exactly one interpretation entry per note, in input order
- [ ] `applies` semantics correct

### Directive application checks

- [ ] `solar_reduction`: effective solar correctly reduced in schedule
- [ ] `minimum_battery_reserve`: battery level meets or exceeds reserve at specified hours
- [ ] `no_charge_window`: no charging at specified hours
- [ ] `no_discharge_window`: no discharging at specified hours
- [ ] `max_grid_window`: grid usage does not exceed cap at specified hours

### Schedule validity checks

- [ ] Exactly 24 hourly entries (hours 0–23, all unique)
- [ ] All numeric values finite and non-negative
- [ ] Battery state transitions correct
- [ ] Battery never exceeds `capacity_kwh`
- [ ] Battery never falls below effective minimum
- [ ] Charge ≤ `max_charge_kwh_per_hour` per hour
- [ ] Discharge ≤ `max_discharge_kwh_per_hour` per hour
- [ ] Solar used ≤ effective solar per hour
- [ ] Energy balance holds every hour
- [ ] Final battery = initial battery (end-of-day neutrality)

### Totals consistency

- [ ] Reported `total_grid_kwh` matches sum of `hourly_plan`
- [ ] Reported `total_cost_bdt` matches computed cost from `hourly_plan`
- [ ] Reported `peak_grid_kwh` matches max from `hourly_plan`

### Paraphrase robustness (hidden tests)

The same directive may appear in many forms. Examples:

| Wording | Directive |
|---------|-----------|
| "PV production will drop to about 20% between 13:00 and 15:00." | `solar_reduction`, hours `[13,14]`, factor `0.2` |
| "Panel washing from one until three will leave roughly one-fifth of normal solar output." | same |
| "Expect an 80% reduction in rooftop solar during the 1–3 PM maintenance window." | same |

Hard-coded phrase matching is **not sufficient**. Semantic understanding via LLM is required.

### Note on exact matching

The judge does **not** do byte-for-byte comparison of your schedule against a reference. Equivalent valid optimal schedules are accepted, provided:
- Interpretation is correct
- Directives are correctly applied
- All constraints are satisfied
- Reported totals match the schedule

**Numeric tolerance:** `0.01 kWh` / `0.01 BDT` absolute.

---

## 13. Top 10 Implementation Traps

| # | Trap | Correct Behavior |
|---|------|-----------------|
| 1 | `1 PM–3 PM` → `[13,14,15]` | Must be `[13,14]` (end-exclusive) |
| 2 | `80% reduction` → `factor = 0.8` | Must be `factor = 0.2` (fraction remaining) |
| 3 | Distractor note → apply some directive | Must be `no_op` |
| 4 | Valid directive → `applies: false` | Only `no_op` may use `applies: false` |
| 5 | Trust LLM output directly | Always validate deterministically |
| 6 | Correct interpretation, wrong schedule | Directive must be applied in optimizer |
| 7 | Use more solar than effective solar | `solar_used ≤ effective_solar` always |
| 8 | Ignore battery capacity/minimum/rate limits | All battery bounds are hard constraints |
| 9 | Final battery ≠ initial battery | End-of-day neutrality is mandatory |
| 10 | Manually report wrong totals | Judge recalculates from `hourly_plan` |

---

## 14. Quick End-to-End Example

**Input (one hour):**
```
hour       = 12
demand     = 300 kWh
solar      = 200 kWh
tariff     = 10 BDT/kWh
battery_before = 200 kWh
```

**Operator note:** *"Solar output will be reduced to 50% from 12 PM to 2 PM."*

**LLM output (validated):**
```json
{
  "directive_type": "solar_reduction",
  "structured_adjustment": { "hours": [12, 13], "factor": 0.5 }
}
```

**Effective solar at hour 12:**
```
effective_solar = 200 × 0.5 = 100 kWh
```

**One valid schedule for hour 12:**
```
grid_kwh             = 100 kWh
solar_used_kwh       = 100 kWh
battery_action       = discharge
battery_kwh          = 100 kWh
battery_energy_after = 200 - 100 = 100 kWh
```

**Energy balance check:**
```
100 (grid) + 100 (solar) + 100 (discharge) = 300 (demand) + 0 (charge)  ✓
```

> Note: battery end-of-day neutrality must still be maintained across all 24 hours.

---

## Recommended Module Structure

```
api/          →  HTTP layer, request parsing, response assembly
llm/          →  LLM prompt construction, API call, output parsing
directives/   →  Deterministic guardrail validation
optimizer/    →  Mathematical optimization (LP / CP-SAT / custom)
validation/   →  Post-schedule energy balance and constraint checks
models/       →  Shared data models / schemas
```

---

## Summary

```
Natural-language operator notes
              ↓
             LLM  (semantic interpretation, not keyword matching)
              ↓
   Supported structured directives
              ↓
   Deterministic validation
              ↓
   Apply directives as hard constraints
              ↓
   Optimize 24-hour energy schedule
   (minimize total grid cost)
              ↓
   Validate: battery + energy balance + directives + EOD neutrality
              ↓
   Return JSON
```

**A case is valid only when all checks pass. Only then does cost minimization quality count.**
