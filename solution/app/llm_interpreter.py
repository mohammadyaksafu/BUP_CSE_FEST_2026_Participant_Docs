"""LLM interpreter: calls Puku (OpenAI-compatible) to interpret operator notes.

If Puku is unreachable, raises LLMUnavailableError so the caller can fall back
to the deterministic rule interpreter. Output is UNTRUSTED -- callers must run
it through app.guardrails before passing it to the optimizer.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openai import OpenAI

from app import config

logger = logging.getLogger("gridwise.llm")

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system_prompt.txt"


class LLMUnavailableError(Exception):
    """Raised when the LLM provider cannot be reached, returns malformed
    output after all retries, or has no API key configured."""


_INTERPRETATION_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "emit_interpretations",
        "description": "Emit one structured directive interpretation per operator note, in order.",
        "parameters": {
            "type": "object",
            "properties": {
                "interpretations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "note_index": {"type": "integer"},
                            "applies": {"type": "boolean"},
                            "directive_type": {
                                "type": "string",
                                "enum": [
                                    "solar_reduction",
                                    "minimum_battery_reserve",
                                    "no_charge_window",
                                    "no_discharge_window",
                                    "max_grid_window",
                                    "no_op",
                                ],
                            },
                            "structured_adjustment": {
                                "type": ["object", "null"],
                                "properties": {
                                    "hours": {"type": "array", "items": {"type": "integer"}},
                                    "factor": {"type": "number"},
                                    "minimum_energy_kwh": {"type": "number"},
                                    "max_grid_kwh": {"number": "number"},
                                },
                            },
                            "explanation": {"type": "string"},
                        },
                        "required": [
                            "note_index",
                            "applies",
                            "directive_type",
                            "structured_adjustment",
                            "explanation",
                        ],
                    },
                }
            },
            "required": ["interpretations"],
        },
    },
}


def _build_client() -> OpenAI:
    if not config.PUKU_API_KEY:
        raise LLMUnavailableError("PUKU_API_KEY is not configured")
    return OpenAI(
        api_key=config.PUKU_API_KEY,
        base_url=config.PUKU_BASE_URL,
        timeout=config.PUKU_TIMEOUT_SECONDS,
    )


def _load_system_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("system prompt file not found at %s", _PROMPT_PATH)
        return "You are the GridWise operator-note interpreter."


def _user_prompt(operator_notes: list[str], battery_context: dict[str, Any]) -> str:
    numbered = "\n".join(f"{i}: {note}" for i, note in enumerate(operator_notes))
    return (
        f"There are {len(operator_notes)} operator note(s). Interpret each one and "
        f"call emit_interpretations with exactly {len(operator_notes)} entries, "
        f"note_index 0..{len(operator_notes) - 1}, in that order.\n\n"
        f"Battery parameters (read-only context, only for resolving relative/"
        f"percentage quantities into absolute kWh values): "
        f"{json.dumps(battery_context)}\n\n"
        f"Operator notes:\n{numbered}"
    )


def _extract_tool_arguments(message: Any) -> dict[str, Any] | None:
    """Pull the tool-call arguments dict out of an OpenAI chat completion
    message, regardless of where the SDK placed them."""
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        return None
    for call in tool_calls:
        fn = getattr(call, "function", None)
        if fn is None:
            continue
        if getattr(fn, "name", None) != "emit_interpretations":
            continue
        raw = getattr(fn, "arguments", None)
        if not raw:
            return None
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def interpret_notes(
    operator_notes: list[str],
    battery_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Call Puku and return a list of RAW (untrusted) interpretation dicts,
    one per note, in note_index order. Raises LLMUnavailableError if the
    provider is unreachable or unusable after retries."""
    if not config.PUKU_API_KEY:
        raise LLMUnavailableError("PUKU_API_KEY is not configured")

    client = _build_client()
    system_prompt = _load_system_prompt()

    last_error: Exception | None = None
    for attempt in range(config.PUKU_MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=config.PUKU_MODEL,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": _user_prompt(operator_notes, battery_context)},
                ],
                tool_choice={
                    "type": "function",
                    "function": {"name": "emit_interpretations"},
                },
                tools=[_INTERPRETATION_TOOL],
            )
            if not response.choices:
                raise LLMUnavailableError("Puku returned no choices")
            args = _extract_tool_arguments(response.choices[0].message)
            if args is None:
                raise LLMUnavailableError("Puku response missing tool call")
            interpretations = args.get("interpretations")
            if not isinstance(interpretations, list):
                raise LLMUnavailableError("Puku tool call missing interpretations list")
            return interpretations
        except LLMUnavailableError as exc:
            last_error = exc
            logger.warning(
                "LLM attempt %d failed (unavailable): %s", attempt + 1, exc
            )
        except Exception as exc:  # noqa: BLE001 - any provider/network error
            last_error = exc
            logger.warning("LLM attempt %d failed (provider error): %s", attempt + 1, exc)

    raise LLMUnavailableError(
        f"Puku interpretation failed after {config.PUKU_MAX_RETRIES + 1} attempts: {last_error}"
    )