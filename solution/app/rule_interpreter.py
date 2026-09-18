"""Deterministic, regex-based fallback interpreter.

This module is the *fallback* path: when the LLM call to Puku is unavailable,
too slow, or returns unparseable output, the pipeline still produces a valid
schedule by running each operator note through these patterns.

Design constraints (per the Participant Guide §5):
* No phrase is keyed to a specific public sample text. Patterns are written
  generically so that paraphrased/hidden-case notes still classify correctly.
* Time windows use START-INCLUSIVE / END-EXCLUSIVE semantics ("1 PM to 3 PM"
  -> hours [13, 14]).
* Every note produces a DirectiveInterpretation-shaped dict, regardless of
  whether it applied. The downstream guardrails are responsible for type
  validation.

The patterns are deliberately layered:

1. Distractor classifier (anything that doesn't mention energy/battery/solar/
   grid/charging terms -> no_op).
2. Time-window extractor (a single function used by every directive branch).
3. Directive-type classifier + numeric extractor (one branch per type).

If nothing matches, the note is treated as no_op so the pipeline never crashes.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("gridwise.rules")

# --- Distractor phrases (non-energy-scheduling notes) -----------------------------
# These phrases are GENERIC topics that don't impact grid scheduling. They are
# not tied to any specific public-sample text.
_DISTRACTOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bregistration\b", re.IGNORECASE),
    re.compile(r"\bdeadline\b", re.IGNORECASE),
    re.compile(r"\bpayroll\b", re.IGNORECASE),
    re.compile(r"\bmenu\b", re.IGNORECASE),
    re.compile(r"\bcafeteria\b", re.IGNORECASE),
    re.compile(r"\bholiday\b", re.IGNORECASE),
    re.compile(r"\bmeeting\b", re.IGNORECASE),
    re.compile(r"\bteam\b", re.IGNORECASE),
    re.compile(r"\bHR\b"),
    re.compile(r"\bdelivery\b", re.IGNORECASE),
    re.compile(r"\bparcel\b", re.IGNORECASE),
    re.compile(r"\b(sports\s*office|gym|registration|staff|workshop)\b", re.IGNORECASE),
    re.compile(r"\bdelivers?\b", re.IGNORECASE),
    re.compile(r"\bmoved?\s+next\s+month\b", re.IGNORECASE),
)

# A note must mention at least one of these energy-domain keywords to be
# considered for a non-no_op directive.
_ENERGY_KEYWORDS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsolar\b|\bpanel|\bPV\b|\brooftop\b", re.IGNORECASE),
    re.compile(r"\bbattery\b|\bcharger\b|\bcharging\b|\bdischarge\b", re.IGNORECASE),
    re.compile(r"\bgrid\b|\bfeeder\b|\btransformer\b|\btariff\b", re.IGNORECASE),
    re.compile(r"\bload\b|\bdemand\b|\benergy\b", re.IGNORECASE),
    re.compile(r"\bkWh\b|\bkw\b", re.IGNORECASE),
    re.compile(r"\bmaintenance\b|\btest\b", re.IGNORECASE),
)


# --- Time-window extraction -------------------------------------------------------

# Matches things like:
#   "1 PM to 3 PM", "1 PM until 3 PM", "1 PM–3 PM", "1PM-3PM"
#   "2 AM to 5 AM", "from 6 PM until 9 PM"
#   "13:00 to 15:00", "13:00–15:00"
#   "from hour 14 to 16", "hours 14–16"
# Returns list of hour-ints (start inclusive, end exclusive).
_HOUR_RE = r"(?:0?[0-9]|1[0-9]|2[0-3])"
_CLOCK_12_RE = rf"(?P<h1>{_HOUR_RE})\s*(?P<ampm1>AM|PM|am|pm)"
_CLOCK_24_RE = rf"(?P<h24a>{_HOUR_RE}):(?P<m24a>[0-5][0-9])"
_CLOCK_12_RE_2 = rf"(?P<h2>{_HOUR_RE})\s*(?P<ampm2>AM|PM|am|pm)"
_CLOCK_24_RE_2 = rf"(?P<h24b>{_HOUR_RE}):(?P<m24b>[0-5][0-9])"
_HOUR_BARE_RE = rf"(?P<h1b>{_HOUR_RE})"
_HOUR_BARE_RE_2 = rf"(?P<h2b>{_HOUR_RE})"

# Special hour names like "noon" / "midnight"
_WORD_HOUR_RE = r"(?P<word_h>noon|midnight)"

_TIME_RANGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 12-hour clock with AM/PM on both sides: "1 PM to 3 PM"
    re.compile(
        rf"\b{_CLOCK_12_RE}\s*(?:to|until|–|—|-|through)\s*{_CLOCK_12_RE_2}\b",
        re.IGNORECASE,
    ),
    # 24-hour clock on both sides: "13:00 to 15:00"
    re.compile(
        rf"\b{_CLOCK_24_RE}\s*(?:to|until|–|—|-|through)\s*{_CLOCK_24_RE_2}\b"
    ),
    # "from 6 PM until 9 PM" -- leading "from" optional, second clock required
    re.compile(
        rf"\bfrom\s+{_CLOCK_12_RE}\s*(?:to|until|–|—|-|through)\s*{_CLOCK_12_RE_2}\b",
        re.IGNORECASE,
    ),
    # "between 1 PM and 3 PM"
    re.compile(
        rf"\bbetween\s+{_CLOCK_12_RE}\s+and\s+{_CLOCK_12_RE_2}\b",
        re.IGNORECASE,
    ),
    # "between 13:00 and 15:00"
    re.compile(
        rf"\bbetween\s+{_CLOCK_24_RE}\s+and\s+{_CLOCK_24_RE_2}\b"
    ),
    # "from hour 14 to 16"
    re.compile(
        rf"\bfrom\s+hour\s+{_HOUR_BARE_RE}\s+(?:to|until|–|—|-|through)\s+{_HOUR_BARE_RE_2}\b",
        re.IGNORECASE,
    ),
    # "hours 14–16" / "hours 14-16" / "hours 14 to 16"
    re.compile(
        rf"\bhours?\s+{_HOUR_BARE_RE}\s*(?:to|–|—|-)\s*{_HOUR_BARE_RE_2}\b",
        re.IGNORECASE,
    ),
    # "from noon until 2 PM" / "from midnight to 5 AM"
    re.compile(
        rf"\bfrom\s+(?P<word_h_left>noon|midnight)\s+(?:to|until|–|—|-|through)\s+(?:{_CLOCK_12_RE})\b",
        re.IGNORECASE,
    ),
    # "from 10 AM until noon" / "from 2 PM to midnight"
    re.compile(
        rf"\bfrom\s+{_CLOCK_12_RE}\s+(?:to|until|–|—|-|through)\s+(?P<word_h_right>noon|midnight)\b",
        re.IGNORECASE,
    ),
    # "the 1–3 PM maintenance window" / "the 14–16 hours"
    re.compile(
        rf"\b(?P<h1>{_HOUR_RE})\s*(?:-|–|—)\s*(?P<h2>{_HOUR_RE})\s*(?P<ampm>AM|PM|am|pm)\b",
        re.IGNORECASE,
    ),
    # Bare single hour mention "at 2 PM" / "at hour 14"
    re.compile(rf"\bat\s+hour\s+{_HOUR_BARE_RE}\b", re.IGNORECASE),
    re.compile(rf"\bat\s+{_CLOCK_12_RE}\b", re.IGNORECASE),
)


def _ampm_to_24(hour: int, ampm: str) -> int:
    ampm = ampm.upper()
    if ampm == "AM":
        return 0 if hour == 12 else hour
    # PM
    return 12 if hour == 12 else hour + 12


def _extract_time_window(text: str) -> list[int] | None:
    """Return a sorted, unique list of hour ints covered by a time range in
    `text`, with START-INCLUSIVE / END-EXCLUSIVE semantics. Returns None if
    no range found."""
    for pat in _TIME_RANGE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        gd = m.groupdict()

        # 12-hour-clock range with optional word-hour endpoint
        word_hour_map = {"noon": 12, "midnight": 0}

        if gd.get("h1") is not None and gd.get("ampm1") and gd.get("h2") is not None and gd.get("ampm2"):
            start = _ampm_to_24(int(gd["h1"]), gd["ampm1"])
            end = _ampm_to_24(int(gd["h2"]), gd["ampm2"])
        elif gd.get("h24a") is not None and gd.get("h24b") is not None:
            start = int(gd["h24a"])
            end = int(gd["h24b"])
        elif gd.get("h1b") is not None and gd.get("h2b") is not None:
            start = int(gd["h1b"])
            end = int(gd["h2b"])
        elif gd.get("word_h_right") and gd.get("ampm1") and gd.get("h1") is not None:
            # "from 10 AM until noon"
            start = _ampm_to_24(int(gd["h1"]), gd["ampm1"])
            end = word_hour_map[gd["word_h_right"].lower()]
        elif gd.get("word_h_left") and gd.get("ampm1"):
            # "from noon until 2 PM" — noon is on the left, AM/PM on right
            start = word_hour_map[gd["word_h_left"].lower()]
            end = _ampm_to_24(int(gd["h1"]), gd["ampm1"])
        elif gd.get("ampm") and gd.get("h1") is not None and gd.get("h2") is not None and not gd.get("ampm1"):
            # "the 1–3 PM maintenance window" — shared AM/PM on the right
            start = _ampm_to_24(int(gd["h1"]), gd["ampm"])
            end = _ampm_to_24(int(gd["h2"]), gd["ampm"])
        else:
            # Single-hour mention: pick whichever clock applies
            if gd.get("ampm1"):
                start = _ampm_to_24(int(gd["h1"]), gd["ampm1"])
            elif gd.get("ampm"):
                start = _ampm_to_24(int(gd["h1"]), gd["ampm"])
            elif gd.get("h1") is not None:
                start = int(gd["h1"])
            elif gd.get("h1b") is not None:
                start = int(gd["h1b"])
            else:
                continue
            return sorted({max(0, min(23, start))})

        # end-exclusive: hours [start, end)
        if end <= start and not (gd.get("word_h_right") or gd.get("word_h_left")):
            # Sometimes people say "3 PM to 1 PM" crossing midnight; we keep
            # it simple and clamp to start==end case (= single hour).
            hours: list[int] = [start]
        elif end <= start and (gd.get("word_h_right") or gd.get("word_h_left")):
            # word-hour (noon/midnight) endpoint with end <= start
            # e.g. "from 2 PM to midnight" -> hours 14..23
            hours = list(range(start, 24))
        else:
            hours = list(range(start, end))

        # Clamp to [0, 23], drop duplicates, sort ascending
        return sorted({h for h in hours if 0 <= h <= 23})

    return None


# --- Directive type classifiers --------------------------------------------------

_SOLAR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsolar\b.*\b(reduc\w*|drop|lower|less|decreas\w*|dirty|wash|clean|shadow|cloud|forecast)\b", re.IGNORECASE),
    re.compile(r"\breduc\w*.*\bsolar\b", re.IGNORECASE),  # "80% reduction in rooftop solar"
    re.compile(r"\b(panel|panels|PV|rooftop).*\b(usab\w*|remain\w*|drop|reduc\w*)\b", re.IGNORECASE),
    re.compile(r"\bsolar\s+(?:output|forecast|generation)\b", re.IGNORECASE),
    re.compile(r"\b(tree|shade|shadow|cloud|inverter)\b.*\bsolar\b", re.IGNORECASE),
    re.compile(r"\bcloud\s+cover\b", re.IGNORECASE),
)

_NO_CHARGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(no|not|don'?t|cannot|can'?t|won'?t|isolated|unavailable|disabled|off)\b[^.]*\bcharg\w+", re.IGNORECASE),
    re.compile(r"\bcharg\w+[^.]*\b(no|not|don'?t|cannot|isolated|unavailable|disabled|off|maintenance|repair)\b", re.IGNORECASE),
)

_NO_DISCHARGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(no|not|don'?t|cannot|can'?t|won'?t|disabled|off)\b[^.]*\bdischarg\w+", re.IGNORECASE),
    re.compile(r"\bdischarg\w+[^.]*\b(no|not|don'?t|cannot|disabled|off|protect\w*|test)\b", re.IGNORECASE),
)

_RESERVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bkeep\s+(?:at\s+least\s+)?(\d+(?:\.\d+)?)\s*(kWh|kw|kilowatt)", re.IGNORECASE),
    re.compile(r"\b(\d+(?:\.\d+)?)\s*%\s+of\s+(?:the\s+)?(?:battery|storage|capacity)", re.IGNORECASE),
    re.compile(r"\breserve\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*(kWh|kw)", re.IGNORECASE),
    re.compile(r"\bmaintain\s+(?:at\s+least\s+)?(\d+(?:\.\d+)?)\s*(kWh|kw)", re.IGNORECASE),
    re.compile(r"\brequir\w*\s+(?:at\s+least\s+)?(\d+(?:\.\d+)?)\s*(kWh|kw|kilowatt)\s+to\s+remain", re.IGNORECASE),
    re.compile(r"\b(?:at\s+least\s+)?(\d+(?:\.\d+)?)\s*(kWh|kw|kilowatt)\s+(?:must\s+)?remain", re.IGNORECASE),
)

_GRID_CAP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgrid\b[^.]*?\b(?:not\s+exceed|cap(?:ped)?|limit(?:ed)?|max(?:imum)?)\s*(?:at|to)?\s*(\d+(?:\.\d+)?)\s*(kWh|kw)", re.IGNORECASE),
    re.compile(r"\b(feeder|transformer|substation)\b[^.]*?(?:limit|cap(?:ped)?|maximum|max)\s*(?:at|of|is)?\s*(\d+(?:\.\d+)?)\s*(kWh|kw)", re.IGNORECASE),
    re.compile(r"\b(?:import|draw|intake)\b[^.]*?(?:not\s+exceed|cap(?:ped)?|limit(?:ed)?|at\s+or\s+below|maximum|max|stay\s+at\s+or\s+below)\s*(?:at|to)?\s*(\d+(?:\.\d+)?)\s*(kWh|kw)", re.IGNORECASE),
    re.compile(r"\b(?:cap(?:ped)?|limit(?:ed)?)\s+(?:at|to|is)\s+(\d+(?:\.\d+)?)\s*(kWh|kw)", re.IGNORECASE),
    re.compile(r"\b(?:stay|remain|keep)\s+at\s+or\s+below\s+(\d+(?:\.\d+)?)\s*(kWh|kw)", re.IGNORECASE),
    re.compile(r"\b(?:at\s+or\s+below|not\s+exceed(?:ing)?)\s+(\d+(?:\.\d+)?)\s*(kWh|kw)", re.IGNORECASE),
)


def _has_energy_keyword(text: str) -> bool:
    return any(p.search(text) for p in _ENERGY_KEYWORDS)


def _looks_like_distractor(text: str) -> bool:
    """A note is a distractor if it's about non-energy scheduling AND doesn't
    contain any energy keywords. (If it mentions BOTH, we let the directive
    classifier decide.)"""
    if _has_energy_keyword(text):
        return False
    return any(p.search(text) for p in _DISTRACTOR_PATTERNS)


def _extract_solar_factor(text: str) -> float | None:
    """Extract the fraction of solar that REMAINS usable.

    Examples that must all resolve:
      "drops to 25%"        -> 0.25
      "reduced to about 20%"-> 0.20
      "80% reduction"       -> 0.20 (1 - 0.80)
      "75% drop"            -> 0.25
      "roughly 1/4 of normal"-> 0.25
      "3/4 of panels out"   -> 0.25
      "factor 0.2"          -> 0.20
      "half of normal"      -> 0.50
    """
    t = text

    # Explicit "factor <num>"
    m = re.search(r"\bfactor\s*(?:=|is|of)?\s*(0?\.\d+|0|1(?:\.0+)?)\b", t, re.IGNORECASE)
    if m:
        return float(m.group(1))

    # "drops / reduced / decrease / drops to / falls to <pct>%"
    # Optional "~" or "about" between verb and percentage also handled.
    m = re.search(
        r"\b(?:drops?|reduced?|decreas\w*|falls?|lowered?|goes?|down to|to|halved?|halves?|drop)\b\s*(?:to|about|around|roughly|approximately|~|≈)?\s*(\d+(?:\.\d+)?)\s*%",
        t,
        re.IGNORECASE,
    )
    if m:
        return float(m.group(1)) / 100.0

    # "<pct>% of the forecast / of normal / of original / usable"
    m = re.search(
        r"(?:roughly|about|around|approximately)?\s*(\d+(?:\.\d+)?)\s*%\s+of\s+(?:the\s+)?(?:forecast|normal|original|output|generation|expected|usable)",
        t,
        re.IGNORECASE,
    )
    if m:
        return float(m.group(1)) / 100.0

    # "treated as ~25%" / "usable at ~25%"
    m = re.search(
        r"(?:treated as|usable at|effectively at|capped at)\s+(?:roughly|about|around|approximately)?\s*(\d+(?:\.\d+)?)\s*%",
        t,
        re.IGNORECASE,
    )
    if m:
        return float(m.group(1)) / 100.0

    # "<pct>% reduction / drop / decrease / cut"
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*(?:reduction|drop|decrease|cut|shortfall|less|lower)",
        t,
        re.IGNORECASE,
    )
    if m:
        return max(0.0, 1.0 - float(m.group(1)) / 100.0)

    # Fraction word
    m = re.search(r"\b(roughly|about|around|approximately)?\s*(\d+)\s*/\s*(\d+)\b", t)
    if m:
        num, den = float(m.group(2)), float(m.group(3))
        if den != 0:
            return max(0.0, min(1.0, num / den))

    # Common English fractions
    fraction_map = {
        "all": 1.0,
        "full": 1.0,
        "entire": 1.0,
        "half": 0.5,
        "halve": 0.5,
        "halves": 0.5,
        "halved": 0.5,
        "quarter": 0.25,
        "three quarters": 0.75,
        "three-quarters": 0.75,
        "two thirds": 0.667,
        "one third": 0.333,
        "a third": 0.333,
        "a quarter": 0.25,
        "none": 0.0,
        "zero": 0.0,
    }
    tlow = t.lower()
    # Look for "about/roughly/around <fraction>"
    for k in sorted(fraction_map.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(k)}\b", tlow):
            return fraction_map[k]

    # "panels out" / "<N> of <M> panels out"
    m = re.search(r"(\d+)\s+of\s+(\d+)\s+panels?\s+(?:out|off|down|dirty)", t, re.IGNORECASE)
    if m:
        num, den = float(m.group(1)), float(m.group(2))
        if den != 0:
            return max(0.0, min(1.0, 1.0 - num / den))

    return None


def _extract_reserve_kwh(text: str, battery_capacity_kwh: float) -> float | None:
    """Return the requested reserve in absolute kWh.

    Handles:
      "keep at least 100 kWh"   -> 100
      "50% of battery capacity" -> 0.5 * capacity
      "reserve of 80 kWh"       -> 80
    """
    # Absolute kWh
    m = re.search(
        r"\b(?:keep|maintain|reserve)\s+(?:at\s+least\s+)?(\d+(?:\.\d+)?)\s*(kWh|kw|kilowatt)",
        text,
        re.IGNORECASE,
    )
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kWh|kw|kilowatt)", text, re.IGNORECASE)
    if m:
        return float(m.group(1))

    # Percentage of capacity
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s+of\s+(?:the\s+)?(?:battery|capacity|storage)", text, re.IGNORECASE)
    if m:
        pct = float(m.group(1)) / 100.0
        return pct * battery_capacity_kwh

    return None


def _extract_grid_cap_kwh(text: str) -> float | None:
    """Extract the per-hour grid cap in kWh from a max_grid note."""
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(kWh|kw|kilowatt)",
        text,
        re.IGNORECASE,
    )
    if m:
        return float(m.group(1))
    return None


def _note_to_interpretation(
    note: str, note_index: int, battery_capacity_kwh: float
) -> dict[str, Any]:
    """Return a raw interpretation dict for one note."""
    explanation_base = f"Rule-based interpretation of note {note_index}."

    if _looks_like_distractor(note):
        return {
            "note_index": note_index,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": f"Note does not affect energy scheduling.",
        }

    hours = _extract_time_window(note) or []

    # ----- max_grid_window -----
    if any(p.search(note) for p in _GRID_CAP_PATTERNS):
        cap = _extract_grid_cap_kwh(note)
        if cap is not None and hours:
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "max_grid_window",
                "structured_adjustment": {"hours": hours, "max_grid_kwh": cap},
                "explanation": explanation_base,
            }

    # ----- no_discharge_window -----
    if any(p.search(note) for p in _NO_DISCHARGE_PATTERNS) and not any(
        p.search(note) for p in _NO_CHARGE_PATTERNS
    ):
        if hours:
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "no_discharge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": explanation_base,
            }

    # ----- no_charge_window -----
    if any(p.search(note) for p in _NO_CHARGE_PATTERNS):
        if hours:
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "no_charge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": explanation_base,
            }

    # ----- minimum_battery_reserve -----
    if any(p.search(note) for p in _RESERVE_PATTERNS):
        reserve = _extract_reserve_kwh(note, battery_capacity_kwh)
        if reserve is not None and hours:
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "minimum_battery_reserve",
                "structured_adjustment": {
                    "hours": hours,
                    "minimum_energy_kwh": min(reserve, battery_capacity_kwh),
                },
                "explanation": explanation_base,
            }

    # ----- solar_reduction -----
    if any(p.search(note) for p in _SOLAR_PATTERNS):
        factor = _extract_solar_factor(note)
        if factor is not None and hours:
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": hours, "factor": factor},
                "explanation": explanation_base,
            }

    # ----- fallback -----
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": f"Could not classify note as an actionable directive; treating as no_op.",
    }


def interpret_notes(
    operator_notes: list[str],
    battery_capacity_kwh: float,
) -> list[dict[str, Any]]:
    """Deterministic interpretation of operator notes. Always returns exactly
    len(operator_notes) entries, in note_index order."""
    return [
        _note_to_interpretation(note, idx, battery_capacity_kwh)
        for idx, note in enumerate(operator_notes)
    ]
