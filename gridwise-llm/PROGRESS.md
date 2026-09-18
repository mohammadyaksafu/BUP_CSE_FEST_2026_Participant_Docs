# GridWise LLM — Build Progress Tracker

Tracks implementation status against `problem.md` (canonical spec) and `instruction.md` (README/deployment expectations).

## Status legend
- [x] Done
- [~] In progress / partially done
- [ ] Not started / blocked

## Submission checklist (mirrors the Participant Guide's own final pre-submit checklist)

- [x] `GET /health` reachable and returns `{"status":"ok"}` — verified **locally** (uvicorn and
      Docker); **not yet reachable from the public internet**.
- [x] `POST /optimize-energy` accepts the exact request schema — verified **locally only** (same
      caveat: not yet publicly reachable).
- [x] Exactly one `directive_interpretation` entry per note, in order; `no_op` uses
      `applies:false` + `null` adjustment; all others use `applies:true` — enforced by
      `app/guardrails.py`, covered by 120 requirement-based tests + 9 live LLM tests, all passing.
- [x] LLM output deterministically guardrailed before optimization; hours unique/ascending/0-23;
      numeric ranges validated; invalid model output cannot invent a constraint — `app/guardrails.py`,
      35 white-box tests + dedicated robustness/security tests, all passing.
- [x] `hourly_plan` obeys directives + energy balance + effective-solar + battery + rate + grid-cap
      + EOD rules — enforced by `app/schedule_validator.py`, covered by 120 requirement tests + 6
      optimizer/constraint tests + 4 integration tests, all passing.
- [x] `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` match values recalculated from
      `hourly_plan` — verified in tests and computed directly from the plan in `app/main.py`.
- [x] README is self-contained with clean local quickstart, env vars, model/provider, LLM role,
      guardrails, optimizer/solver, dependencies, run command, `/health` + sample test, limitations,
      no committed secrets.
- [ ] **Repository created after question reveal, private during event, public after deadline** —
      NOT DONE. No git repo exists yet in `gridwise-llm/` and `gh` is not authenticated on this
      machine.
- [ ] **Fallback Docker image submitted with an exact pullable tag/digest, verified `docker pull` +
      `docker run` reaching `/health`** — PARTIALLY DONE. Image builds and runs correctly *locally*
      (`gridwise-llm:latest`), but it has not been pushed to any registry, so there is no pullable
      reference yet — `docker login` has not been run for any registry on this machine.
- [ ] **3-minute video** covering problem, architecture, LLM→guardrail→optimizer flow, run/test —
      NOT DONE.
- [ ] **Working public endpoint reachable by the judge** (no login/VPN) — NOT DONE. Current
      instance is `localhost:8000` only, reachable from this machine, not the internet.

**Bottom line: the core system (LLM interpretation, guardrails, optimizer, validation, API
contract, Docker image, README, and a 215-test suite) is fully built and verified. What's left is
entirely submission logistics — GitHub repo, registry push, public hosting, and the video — each of
which needs your account/credentials, per your last instruction to hold off on those for now.**

## Core pipeline

- [x] Project scaffold (FastAPI app, config, requirements, .env.example, .gitignore)
- [x] Pydantic request/response models matching Section 8 & 11 of problem.md exactly
- [x] `GET /health` endpoint (`{"status":"ok"}`)
- [x] `POST /optimize-energy` endpoint
- [x] LLM interpreter — provider-agnostic (`LLM_PROVIDER=gemini` or `anthropic`), forced structured
      JSON output either way (Gemini: `response_schema` JSON mode; Anthropic: forced tool call).
      Battery params passed as read-only context so percentage-of-capacity notes like "keep 50% of
      battery capacity" resolve to correct absolute kWh values. Retries with exponential backoff
      (1.5s/3s/5s) on transient provider errors (429/503).
- [x] Deterministic guardrail validator — schema/type/range/hours checks, safe per-note fallback to
      `no_op` on any validation failure (never crashes, never invents a directive)
- [x] Directive engine — converts validated directives into optimizer constraints (effective solar,
      no-charge/discharge windows, min reserve, max grid cap)
- [x] Optimizer (PuLP LP, CBC solver) — minimizes grid cost subject to energy balance, battery
      bounds/rates, end-of-day neutrality
- [x] Schedule validation engine — independently re-verifies energy balance, battery bounds,
      directive compliance, EOD neutrality before responding (defense-in-depth on top of the LP's
      own constraints)
- [x] Response assembly — totals (`total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh`) computed
      from `hourly_plan`, deterministic `plan_summary` text
- [x] Error handling — malformed JSON/schema → 400; optimizer infeasible → 422; LLM/provider
      failure → safe all-`no_op` fallback (still 200 with a valid schedule); any other uncaught
      exception → controlled 500 with no stack trace leak

## Testing — ALL GREEN as of this session

- [x] Offline pipeline test (`tests/test_offline_pipeline.py`) — guardrails + directive engine +
      optimizer + schedule validator against all 10 public sample cases using ground-truth
      interpretations (no live LLM call). **10/10 pass**, and `total_cost_bdt`/`total_grid_kwh`
      match the reference optimal values exactly on every case.
- [x] **Live end-to-end test** (real HTTP calls to a running `uvicorn` instance, real Gemini API
      calls via `LLM_PROVIDER=gemini`, model `gemini-flash-lite-latest`):
      - Structural/constraint validation (`tests/test_public_samples.py` logic): **10/10 pass**
        (energy balance, effective-solar, battery bounds/rates, directive constraints, EOD
        neutrality, totals consistency all verified against the live response).
      - Interpretation accuracy vs. each case's published ground-truth `directive_interpretation`
        (directive_type, applies, hours, numeric values): **10/10 match**.
      - Latency: 3.45s–4.27s per request (avg 3.83s), comfortably under the 5s p95 target and the
        30s per-request timeout.
- [x] `app.main` import/startup sanity check (no import errors, dependencies installed)
- [x] **Full pytest suite added** (`tests/`, run with `pytest` — see `pytest.ini`), covering every
      testing category requested: requirement-based, black-box, white-box, regression, robustness,
      LLM/prompt, optimization/constraint, API/integration, and security/failure testing.
      **Final result: 215/215 passed** (206 fast/offline + mocked-LLM tests, plus 9 tests that hit
      the real Gemini API live, run with `pytest -m llm`). Two real bugs were found and fixed by
      this suite:
      1. **Crash-on-validation-error bug**: a request that tripped a custom `@model_validator`
         (e.g. `battery.minimum_energy_kwh > capacity_kwh`) embedded a raw Python `ValueError`
         object inside the 400 error response's `ctx` field, which is not JSON-serializable — this
         crashed the error handler itself into an unhandled 500 instead of returning the intended
         400. Fixed in `app/main.py` (`_safe_validation_detail`) by stringifying any embedded
         exception objects before serializing.
      2. **Optimizer crash on structurally-invalid constraints**: if a constraint ever produced an
         LP variable with `lowBound > upBound` (e.g. an effective minimum battery reserve above
         capacity), the CBC solver subprocess failed to run at all and PuLP raised `PulpSolverError`
         instead of a clean infeasibility result, which would have surfaced as an unhandled 500.
         Fixed in `app/optimizer.py` with an explicit pre-solve bounds check plus a `try/except`
         around `prob.solve()`, both converted into the existing controlled
         `OptimizationInfeasibleError` (-> HTTP 422).
      Also hardened the LLM system prompt after a live test caught real ambiguity: "6 PM through
      9 PM" was initially interpreted as inclusive (hours [18,19,20,21]) because "through" reads as
      inclusive in everyday English, unlike the spec's demonstrated "to"/"until" wording. The
      prompt now explicitly calls out "to"/"until"/"till"/"through"/dash ranges as all being
      end-exclusive per the GridWise convention, and the live test now passes.

### Test suite breakdown by category (`gridwise-llm/tests/`)

| File | Category | Tests | Notes |
|---|---|---|---|
| `test_white_box.py` | White-box | 35 | Directly exercises guardrails, directive engine, optimizer, schedule validator, pydantic models — including internals not reachable via the public API alone |
| `test_black_box.py` | Black-box | 10 | Only the public HTTP contract (status codes, schema), LLM stubbed |
| `test_requirements.py` | Requirement-based | 120 | 12 checks per problem.md Section 12 checklist, parametrized over all 10 public cases |
| `test_regression_samples.py` | Regression | 10 | Locks in reference-optimal cost/grid totals per public case |
| `test_robustness.py` | Robustness | 15 | Malformed/empty/huge/unicode input, provider outage, garbage LLM output — never crashes |
| `test_llm_prompt.py` | LLM/Prompt (live) | 9 | Real Gemini calls, hand-written paraphrases per directive type, distractors, multi-note ordering |
| `test_optimizer_constraints.py` | Optimization/Constraint | 6 | Crafted scenarios with a knowable optimal answer; proves cost-minimization, not just feasibility |
| `test_api_integration.py` | API/Integration | 4 | Multi-directive combos through the full stack, full totals cross-check |
| `test_security.py` | Security & Failure | 6 | Secret leakage, stack-trace leakage, prompt-injection-shaped LLM output neutralized by guardrails |
| **Total** | | **215** (206 free + 9 live) | Run everything: `pytest`. Skip live LLM calls: `pytest -m "not llm"`. Only live: `pytest -m llm` |

## Deployment & submission

- [x] Dockerfile (binds 0.0.0.0, no baked-in secrets, exposes port 8000)
- [x] requirements.txt pinned (now includes `google-genai` alongside `anthropic`)
- [x] README.md (setup, env vars, both providers, LLM role, guardrails, optimizer, run command,
      curl examples, public-sample test command, dependencies, limitations)
- [x] Local smoke test: `/health` + all public samples end-to-end over real HTTP — done, all pass
- [x] Docker build + run smoke test: `docker build` succeeds (image `gridwise-llm:latest`, 399MB,
      based on `python:3.11-slim` + `coinor-cbc`); container started with only env vars (no baked
      secrets — verified via `docker history`, only a public GPG key from the base image's Python
      build step shows up, no `.env`/API keys anywhere in the image filesystem); `/health` returned
      `{"status":"ok"}` immediately; a live public-sample request through the container returned
      the correct interpretation and matched the reference optimal cost (38365.0 BDT) exactly.
- [x] **Running locally as a persistent Docker container**: `gridwise-llm` container is up
      (`docker run -d --name gridwise-llm --restart unless-stopped -p 8000:8000 ...`), bound to
      `0.0.0.0:8000`, `/health` verified reachable. This satisfies "run via Docker" for local use;
      it is NOT publicly reachable from the internet (only from this machine).
- [ ] **Deliberately deferred by user request** ("no need to deploy just run docker"): pushing the
      image to a registry (Docker Hub/GHCR) and deploying a publicly reachable instance. Neither
      `gh` (GitHub CLI) nor `docker` is authenticated to any account on this machine — both need the
      user to log in themselves before either step can happen. **This means the submission is not
      yet complete**: the Participant Guide requires a working public endpoint reachable by the
      judge, a pullable registry image, and a GitHub repo — all three still need to happen before
      the actual submission, whenever the user is ready to provide credentials/make those accounts.
- [ ] Create GitHub repo (after question reveal), keep private during event, public after deadline —
      blocked on `gh auth login` (not yet run) or the user creating it manually on github.com
- [ ] Record 3-minute architecture/solution video (tie-break only, not base score)

## Remaining work (in priority order) — all blocked on user credentials/decisions

1. GitHub repo: create after question reveal, private during event, public after deadline. Needs
   `gh auth login` (not yet run) or manual creation on github.com.
2. Push the image to a registry (Docker Hub or GHCR) and record the exact pullable tag/digest.
   Needs `docker login` (not yet run) to a chosen registry.
3. Deploy a publicly reachable instance (Render, Fly.io, Railway, a VM, etc. — any reachable
   platform is allowed). Needs the user's choice of platform + its credentials.
4. Record the 3-minute video (problem, architecture, LLM->guardrail->optimizer flow, run/test demo).
5. Optional hardening: test a few more paraphrased/hand-written notes beyond the 10 public cases
   and the 9 already covered in `tests/test_llm_prompt.py` to further stress paraphrase robustness
   before hidden judging.

## Notes / decisions

- LLM provider: **Google Gemini** (`gemini-flash-lite-latest`), switched from the original plan of
  Anthropic Claude because the user could not obtain billed Anthropic access. Anthropic support is
  still fully implemented and selectable via `LLM_PROVIDER=anthropic` if a key becomes available.
- Gemini model choice matters a lot for free-tier quota: newer "flash" preview models
  (`gemini-3.6-flash` and similar) were capped at only **20 requests/day** for this new API key and
  got exhausted almost immediately during testing. `gemini-flash-lite-latest` (and other `-lite`
  variants) has a much higher free-tier quota and was used for all successful live testing — this
  is the recommended default for the actual submission too, to avoid the judge harness hitting a
  quota wall during hidden testing.
- Optimizer: PuLP (CBC solver bundled), linear program — verified to reproduce the reference
  optimal cost exactly on all 10 public sample cases.
- Per-note guardrail failure policy: invalid LLM output for a single note is safely downgraded to
  `no_op` rather than failing the whole request (never crash, never invent directives).
- Security: never pasted the user's `pk_live_...` (Puku editor credential, not usable here) or the
  Gemini key into chat-visible tool output — the Gemini key was written directly into the
  gitignored `.env` file and never echoed back.
