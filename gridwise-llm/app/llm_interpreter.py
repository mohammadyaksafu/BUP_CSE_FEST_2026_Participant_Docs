"""LLM interpreter: turns natural-language operator_notes into structured
directive candidates using a language-capable generative model (Anthropic
Claude or Google Gemini, selected via LLM_PROVIDER). Output is UNTRUSTED and
must be passed through app.guardrails before it reaches the optimizer.
"""
import json
import logging
import time
from typing import Any, Dict, List

from app import config

logger = logging.getLogger("gridwise.llm")

SYSTEM_PROMPT = """You are the operator-note interpreter for GridWise, a campus energy \
scheduling system. You read short natural-language notes from a facilities operator and \
turn each one into a structured directive for a downstream optimizer.

There are exactly 6 supported directive types. Never invent any other type.

1. solar_reduction
   structured_adjustment = {"hours": [int, ...], "factor": number}
   - factor is the FRACTION OF SOLAR THAT REMAINS USABLE, not the reduction amount.
   - "reduced by 80%" / "drops to 20%" / "80% reduction" all mean factor = 0.2.
   - 0 <= factor <= 1.

2. minimum_battery_reserve
   structured_adjustment = {"hours": [int, ...], "minimum_energy_kwh": number}
   - Battery energy must stay at or above this value after each listed hour.

3. no_charge_window
   structured_adjustment = {"hours": [int, ...]}
   - Battery charging is forbidden during these hours.

4. no_discharge_window
   structured_adjustment = {"hours": [int, ...]}
   - Battery discharging is forbidden during these hours.

5. max_grid_window
   structured_adjustment = {"hours": [int, ...], "max_grid_kwh": number}
   - Grid draw is capped at this value during these hours.

6. no_op
   - Use this when the note has NO applicable effect on energy scheduling
     (e.g. unrelated announcements, menu changes, meeting notes, HR news).
   - structured_adjustment MUST be null and applies MUST be false.
   - Every other directive type MUST have applies = true.

CRITICAL TIME WINDOW RULE — half-open interval, start inclusive, end exclusive.
This applies no matter which connector word the note uses: "to", "until", "till",
"through", or a dash/range like "6-9 PM" — ALL of them are end-exclusive here,
even though "through" can sound inclusive in everyday English. Always follow the
GridWise convention, not the everyday-English reading of the connector word:
   "1 PM to 3 PM"      -> hours [13, 14]   (NOT 15)
   "2 PM until 4 PM"   -> hours [14, 15]   (NOT 16)
   "6 PM through 9 PM" -> hours [18, 19, 20]   (NOT 21)
A single hour mention like "at 2 PM" or "during hour 14" maps to just [14].
hours must be integers 0-23, unique, and in ascending order.

Notes may paraphrase the same underlying directive in many different ways (percentages,
fractions, "roughly X%", whole-hour clock expressions, 24-hour time, etc). Interpret the
SEMANTIC MEANING, not exact keywords. Do not rely on exact phrase matching.

You must never modify or invent values for demand, solar, tariff, or battery parameters.
You only decide, per note: whether it applies, which of the 6 directive types it is, the
affected hours, and any required numeric parameters.

You are given the scenario's battery parameters as read-only context. Use them ONLY to
resolve notes that reference a RELATIVE or PERCENTAGE quantity (e.g. "keep at least 50% of
battery capacity in reserve") into the required ABSOLUTE kWh value. For example, if
battery capacity_kwh is 200 and a note says "keep at least 50% of capacity in reserve",
minimum_energy_kwh must be 100. If a note already gives an absolute kWh value, use it
directly.

For every note in the input, in order, call the emit_interpretations tool exactly once with
one entry per note, note_index equal to its zero-based position in the input list."""

INTERPRETATION_TOOL = {
    "name": "emit_interpretations",
    "description": "Emit one structured directive interpretation per operator note, in order.",
    "input_schema": {
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
                                "max_grid_kwh": {"type": "number"},
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
}


class LLMInterpreterError(Exception):
    """Raised when the LLM cannot be reached or returns an unusable response."""


def _user_prompt(operator_notes: List[str], battery_context: Dict[str, Any]) -> str:
    numbered = "\n".join(f"{i}: {note}" for i, note in enumerate(operator_notes))
    return (
        f"There are {len(operator_notes)} operator note(s). Interpret each one and call "
        f"emit_interpretations with exactly {len(operator_notes)} entries, note_index 0.."
        f"{len(operator_notes) - 1}, in that order.\n\n"
        f"Battery parameters (read-only context, only for resolving relative/percentage "
        f"quantities into absolute kWh values): {json.dumps(battery_context)}\n\n"
        f"Operator notes:\n{numbered}"
    )


def _interpret_anthropic(operator_notes: List[str], battery_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    import anthropic

    kwargs: Dict[str, Any] = {"api_key": config.LLM_API_KEY, "timeout": config.LLM_TIMEOUT_SECONDS}
    if config.LLM_BASE_URL:
        kwargs["base_url"] = config.LLM_BASE_URL
    client = anthropic.Anthropic(**kwargs)

    response = client.messages.create(
        model=config.LLM_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[INTERPRETATION_TOOL],
        tool_choice={"type": "tool", "name": "emit_interpretations"},
        messages=[{"role": "user", "content": _user_prompt(operator_notes, battery_context)}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_interpretations":
            raw = block.input.get("interpretations")
            if not isinstance(raw, list):
                raise LLMInterpreterError("LLM tool call missing interpretations list")
            return raw
    raise LLMInterpreterError("LLM response did not include a tool_use block")


def _interpret_gemini(operator_notes: List[str], battery_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.LLM_API_KEY)

    response_schema = {
        "type": "OBJECT",
        "properties": {
            "interpretations": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "note_index": {"type": "INTEGER"},
                        "applies": {"type": "BOOLEAN"},
                        "directive_type": {
                            "type": "STRING",
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
                            "type": "OBJECT",
                            "nullable": True,
                            "properties": {
                                "hours": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                                "factor": {"type": "NUMBER"},
                                "minimum_energy_kwh": {"type": "NUMBER"},
                                "max_grid_kwh": {"type": "NUMBER"},
                            },
                        },
                        "explanation": {"type": "STRING"},
                    },
                    "required": ["note_index", "applies", "directive_type", "explanation"],
                },
            }
        },
        "required": ["interpretations"],
    }

    response = client.models.generate_content(
        model=config.LLM_MODEL,
        contents=_user_prompt(operator_notes, battery_context),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0,
        ),
    )

    text = response.text
    if not text:
        raise LLMInterpreterError("Gemini response had no text content")
    data = json.loads(text)
    raw = data.get("interpretations")
    if not isinstance(raw, list):
        raise LLMInterpreterError("Gemini JSON response missing interpretations list")
    return raw


_PROVIDERS = {
    "anthropic": _interpret_anthropic,
    "gemini": _interpret_gemini,
}


def interpret_notes(operator_notes: List[str], battery_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Calls the configured LLM provider and returns a list of RAW (untrusted)
    interpretation dicts, one per note, in note_index order as returned by the
    model (caller must still validate/guardrail this output before use).

    `battery_context` (capacity_kwh, initial_energy_kwh, minimum_energy_kwh,
    max_charge_kwh_per_hour, max_discharge_kwh_per_hour) is passed as read-only
    context so the model can resolve percentage-based notes (e.g. "50% of battery
    capacity") into absolute kWh values; it must never be echoed back as a directive
    on its own.

    Raises LLMInterpreterError on unrecoverable provider failure after retries.
    """
    if not config.LLM_API_KEY:
        raise LLMInterpreterError("LLM_API_KEY is not configured")

    provider_fn = _PROVIDERS.get(config.LLM_PROVIDER)
    if provider_fn is None:
        raise LLMInterpreterError(f"unsupported LLM_PROVIDER: {config.LLM_PROVIDER!r}")

    last_error: Exception | None = None
    for attempt in range(config.LLM_MAX_RETRIES + 1):
        try:
            return provider_fn(operator_notes, battery_context)
        except Exception as exc:  # noqa: BLE001 - provider/network errors are all treated the same
            last_error = exc
            logger.warning("LLM interpretation attempt %d (%s) failed: %s", attempt + 1, config.LLM_PROVIDER, exc)
            if attempt < config.LLM_MAX_RETRIES:
                backoff = min(1.5 * (2 ** attempt), 5.0)
                time.sleep(backoff)

    raise LLMInterpreterError(f"LLM interpretation failed after retries: {last_error}")
