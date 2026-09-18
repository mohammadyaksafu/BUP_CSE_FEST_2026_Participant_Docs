"""FastAPI application for the GridWise LLM preliminary challenge.

Pipeline:
  POST /optimize-energy
    -> Pydantic request validation
    -> try LLM (Puku) -- falls back to rule interpreter on any LLMUnavailableError
    -> deterministic guardrails
    -> directive engine
    -> LP optimizer (PuLP)
    -> schedule validator
    -> response builder
    -> JSON
"""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import config
from app.directive_engine import build_constraints
from app.guardrails import validate_interpretations
from app.llm_interpreter import LLMUnavailableError, interpret_notes
from app.models import (
    DirectiveInterpretation,
    OptimizeEnergyRequest,
    OptimizeEnergyResponse,
)
from app.optimizer import OptimizationInfeasibleError, solve
from app.response_builder import build_response
from app.rule_interpreter import interpret_notes as rule_interpret_notes
from app.schedule_validator import ScheduleInvalidError, validate_schedule

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gridwise")

app = FastAPI(title="GridWise LLM", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    # exc.errors() can include non-JSON-serializable values (e.g. ValueError
    # instances inside `ctx`); strip them down to primitives.
    safe = []
    for err in exc.errors():
        clean = {k: v for k, v in err.items() if k != "ctx"}
        safe.append(clean)
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_request", "detail": safe},
    )


@app.exception_handler(ValidationError)
async def _pydantic_validation_handler(request: Request, exc: ValidationError):
    safe = []
    for err in exc.errors():
        clean = {k: v for k, v in err.items() if k != "ctx"}
        safe.append(clean)
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_request", "detail": safe},
    )


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception):
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"error": "internal_error"})


def _all_no_op(num_notes: int, reason: str) -> list[DirectiveInterpretation]:
    return [
        DirectiveInterpretation(
            note_index=i,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation=f"Safe fallback: {reason}",
        )
        for i in range(num_notes)
    ]


@app.post("/optimize-energy", response_model=OptimizeEnergyResponse)
def optimize_energy(payload: OptimizeEnergyRequest):
    start = time.monotonic()
    scenario_id = payload.scenario_id
    battery_context = {
        "capacity_kwh": payload.battery.capacity_kwh,
        "initial_energy_kwh": payload.battery.initial_energy_kwh,
        "minimum_energy_kwh": payload.battery.minimum_energy_kwh,
        "max_charge_kwh_per_hour": payload.battery.max_charge_kwh_per_hour,
        "max_discharge_kwh_per_hour": payload.battery.max_discharge_kwh_per_hour,
    }

    # --- 1. Interpret notes (LLM, then rule fallback) ------------------------
    used_path = "llm"
    try:
        raw = interpret_notes(payload.operator_notes, battery_context)
    except LLMUnavailableError as exc:
        logger.warning("LLM unavailable (%s); using rule interpreter.", exc)
        used_path = "rules"
        raw = rule_interpret_notes(
            payload.operator_notes, payload.battery.capacity_kwh
        )

    # --- 2. Guardrails -------------------------------------------------------
    interpretations = validate_interpretations(
        raw, len(payload.operator_notes), payload.battery.capacity_kwh
    )

    # --- 3. Build constraints & solve ----------------------------------------
    hours_sorted = sorted(payload.hours, key=lambda h: h.hour)
    base_solar = [h.solar_kwh for h in hours_sorted]
    constraints = build_constraints(
        interpretations, base_solar, payload.battery.minimum_energy_kwh
    )

    try:
        plan = solve(hours_sorted, payload.battery, constraints)
    except OptimizationInfeasibleError as exc:
        logger.error("scenario=%s optimization infeasible: %s", scenario_id, exc)
        return JSONResponse(
            status_code=422,
            content={"error": "infeasible_scenario", "detail": str(exc), "scenario_id": scenario_id},
        )

    # --- 4. Schedule validation (defense in depth) ---------------------------
    try:
        validate_schedule(hours_sorted, payload.battery, constraints, plan)
    except ScheduleInvalidError as exc:
        logger.error("scenario=%s schedule failed internal validation: %s", scenario_id, exc)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_schedule_validation_failed", "scenario_id": scenario_id},
        )

    # --- 5. Build response --------------------------------------------------
    tariff_by_hour = {h.hour: h.tariff_bdt_per_kwh for h in hours_sorted}
    response_data = build_response(scenario_id, interpretations, plan, tariff_by_hour)

    elapsed = time.monotonic() - start
    logger.info(
        "scenario=%s path=%s hours=%d total_grid=%.2f total_cost=%.2f peak=%.2f time=%.3fs",
        scenario_id,
        used_path,
        len(plan),
        response_data["total_grid_kwh"],
        response_data["total_cost_bdt"],
        response_data["peak_grid_kwh"],
        elapsed,
    )
    return OptimizeEnergyResponse(**response_data)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=config.PORT)
