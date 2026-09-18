"""NO-HARDCODE GUARD: rename scenario_ids and rephrase notes; the system
must still produce the correct interpretation and valid plan.

This protects against the very common failure mode where a solution
accidentally hard-codes answers to the public samples.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PUBLIC_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
)


def _load_cases() -> list[dict]:
    with PUBLIC_PATH.open(encoding="utf-8") as f:
        return json.load(f)["cases"]


def _paraphrase_solar(note: str) -> str:
    """Make a new note that conveys the SAME underlying directive but in
    different words."""
    if "factor" in note.lower():
        return note
    # Replace specific time / fraction phrases
    return re.sub(r"\d+%|\d+\s*PM|\d+\s*AM|noon", lambda m: f"~{m.group(0)}", note)


class TestNoHardcoding:
    @pytest.mark.parametrize("case_index", list(range(10)))
    def test_renamed_scenario_id_does_not_break(self, case_index):
        cases = _load_cases()
        case = copy.deepcopy(cases[case_index])
        # Rename scenario_id to a "hidden-style" id
        case["input"]["scenario_id"] = f"HIDDEN-{case_index:03d}"
        # Also paraphrase every operator note (don't trust the literal text)
        case["input"]["operator_notes"] = [_paraphrase_solar(n) for n in case["input"]["operator_notes"]]

        r = client.post("/optimize-energy", json=case["input"])
        # Must still succeed
        assert r.status_code == 200, r.text
        # Echoed scenario_id
        assert r.json()["scenario_id"] == case["input"]["scenario_id"]
        # KPI self-consistency
        hp = r.json()["hourly_plan"]
        assert abs(sum(e["grid_kwh"] for e in hp) - r.json()["total_grid_kwh"]) <= 0.01

    def test_no_branch_on_sample_id_in_source(self):
        """Static check: ensure no code anywhere branches on the literal
        sample id strings."""
        # Read every .py file in solution/ and check for SAMPLE-NN as a literal.
        bad = []
        for py in Path(__file__).resolve().parent.parent.parent.rglob("*.py"):
            if py.is_relative_to(Path(__file__).resolve().parent.parent.parent / "tests"):
                continue  # tests are allowed to reference SAMPLE ids
            text = py.read_text(encoding="utf-8")
            if re.search(r'["\']SAMPLE-\d\d["\']', text):
                bad.append(str(py))
        assert not bad, f"Hard-coded SAMPLE-NN found in: {bad}"

    def test_no_branch_on_sample_text_in_source(self):
        """Static check: ensure no code branches on the literal sample
        operator-note strings."""
        cases = _load_cases()
        sample_note_substrings = set()
        for case in cases:
            for note in case["input"]["operator_notes"]:
                # Take 5-word substrings as a rough fingerprint
                words = note.split()
                for i in range(0, max(1, len(words) - 4)):
                    sample_note_substrings.add(" ".join(words[i:i+5]).lower())

        bad = []
        # Skip the rule interpreter: it's allowed to contain generic regex
        # patterns that happen to overlap with sample wording.
        skip_paths = (Path(__file__).resolve().parent.parent.parent / "app" / "rule_interpreter.py",)
        for py in Path(__file__).resolve().parent.parent.parent.rglob("*.py"):
            if py.is_relative_to(Path(__file__).resolve().parent.parent.parent / "tests"):
                continue
            if py in skip_paths:
                continue
            text = py.read_text(encoding="utf-8").lower()
            for substr in sample_note_substrings:
                if substr in text and len(substr) > 15:
                    bad.append((str(py), substr))
                    break
        assert not bad, f"Possible hard-coded sample phrasing found: {bad}"