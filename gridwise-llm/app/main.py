import logging
import time

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import config
from app.directive_engine import build_constraints
from app.guardrails import validate_interpretations
from app.llm_interpreter import LLMInterpreterError, interpret_notes
from app.models import (
    DirectiveInterpretation,
    HourlyPlanEntry,
    OptimizeEnergyRequest,
    OptimizeEnergyResponse,
)
from app.optimizer import OptimizationInfeasibleError, solve
from app.schedule_validator import ScheduleInvalidError, validate_schedule

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gridwise")

app = FastAPI(title="GridWise LLM", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok"}


def _safe_validation_detail(errors: list) -> list:
    """pydantic's exc.errors() can embed raw non-JSON-serializable objects (e.g.
    a ValueError instance in ctx["error"] for @model_validator/@field_validator
    failures). Strip request input echoes and stringify anything that isn't
    already JSON-safe so the 400 response itself never crashes."""
    safe = []
    for err in errors:
        err = dict(err)
        err.pop("input", None)
        ctx = err.get("ctx")
        if isinstance(ctx, dict):
            err["ctx"] = {k: (str(v) if isinstance(v, BaseException) else v) for k, v in ctx.items()}
        safe.append(jsonable_encoder(err))
    return safe


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"error": "invalid_request", "detail": _safe_validation_detail(exc.errors())})


@app.exception_handler(ValidationError)
async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
    return JSONResponse(status_code=400, content={"error": "invalid_request", "detail": _safe_validation_detail(exc.errors())})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
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


def _build_summary(scenario_id: str, interpretations: list[DirectiveInterpretation], total_cost: float, total_grid: float) -> str:
    applied = [i for i in interpretations if i.applies]
    if applied:
        kinds = ", ".join(sorted({i.directive_type for i in applied}))
        directive_part = f"Applied directives: {kinds}."
    else:
        directive_part = "No operator directives applied; all notes were non-operational (no_op)."
    return (
        f"Scenario {scenario_id}: optimized 24-hour schedule drawing {total_grid:.2f} kWh from the grid "
        f"at a total cost of {total_cost:.2f} BDT, prioritizing solar and battery use to minimize grid cost. "
        f"{directive_part}"
    )


@app.post("/optimize-energy", response_model=OptimizeEnergyResponse)
def optimize_energy(payload: OptimizeEnergyRequest):
    start = time.monotonic()
    scenario_id = payload.scenario_id

    try:
        battery_context = {
            "capacity_kwh": payload.battery.capacity_kwh,
            "initial_energy_kwh": payload.battery.initial_energy_kwh,
            "minimum_energy_kwh": payload.battery.minimum_energy_kwh,
            "max_charge_kwh_per_hour": payload.battery.max_charge_kwh_per_hour,
            "max_discharge_kwh_per_hour": payload.battery.max_discharge_kwh_per_hour,
        }
        raw_interpretations = interpret_notes(payload.operator_notes, battery_context)
        interpretations = validate_interpretations(
            raw_interpretations, len(payload.operator_notes), payload.battery.capacity_kwh
        )
    except LLMInterpreterError as exc:
        logger.error("scenario=%s LLM interpretation unavailable, falling back to no_op: %s", scenario_id, exc)
        interpretations = _all_no_op(len(payload.operator_notes), "LLM provider unavailable")

    hours_sorted = sorted(payload.hours, key=lambda h: h.hour)
    base_solar = [h.solar_kwh for h in hours_sorted]

    constraints = build_constraints(interpretations, base_solar, payload.battery.minimum_energy_kwh)

    try:
        plan = solve(hours_sorted, payload.battery, constraints)
    except OptimizationInfeasibleError as exc:
        logger.error("scenario=%s optimization infeasible: %s", scenario_id, exc)
        return JSONResponse(
            status_code=422,
            content={"error": "infeasible_scenario", "detail": str(exc), "scenario_id": scenario_id},
        )

    try:
        validate_schedule(hours_sorted, payload.battery, constraints, plan)
    except ScheduleInvalidError as exc:
        logger.error("scenario=%s produced schedule failed internal validation: %s", scenario_id, exc)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_schedule_validation_failed", "scenario_id": scenario_id},
        )

    hourly_plan = [
        HourlyPlanEntry(
            hour=r.hour,
            grid_kwh=r.grid_kwh,
            solar_used_kwh=r.solar_used_kwh,
            battery_action=r.battery_action,
            battery_kwh=r.battery_kwh,
            battery_energy_after_kwh=r.battery_energy_after_kwh,
        )
        for r in plan
    ]

    total_grid_kwh = round(sum(r.grid_kwh for r in plan), 6)
    tariff_by_hour = {h.hour: h.tariff_bdt_per_kwh for h in hours_sorted}
    total_cost_bdt = round(sum(r.grid_kwh * tariff_by_hour[r.hour] for r in plan), 6)
    peak_grid_kwh = round(max(r.grid_kwh for r in plan), 6)

    plan_summary = _build_summary(scenario_id, interpretations, total_cost_bdt, total_grid_kwh)

    elapsed = time.monotonic() - start
    logger.info("scenario=%s optimize-energy completed in %.3fs", scenario_id, elapsed)

    return OptimizeEnergyResponse(
        scenario_id=scenario_id,
        directive_interpretation=sorted(interpretations, key=lambda i: i.note_index),
        hourly_plan=hourly_plan,
        total_grid_kwh=total_grid_kwh,
        total_cost_bdt=total_cost_bdt,
        peak_grid_kwh=peak_grid_kwh,
        plan_summary=plan_summary,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=config.PORT)
