"""
test_presets.py
Checks every hand-written preset fault: it passes the validator, and its own fix really fixes it.

If someone changes the simulator, the tools or a preset and breaks a fault by accident,
these tests fail right away, instead of during the demo.
"""

import copy
import json
from pathlib import Path

import pytest

from app.faults import PRESETS, apply_fault
from app.fixes import apply_fix, verify
from app.simulator import Server
from app.validator import find_fix, validate_fault

# One test per preset, so a failure names the preset that broke.
ALL_PRESETS = sorted(PRESETS)


@pytest.mark.parametrize("name", ALL_PRESETS)
def test_preset_passes_the_validator(name):
    """Presets must follow the same rules as Ultra's custom faults."""
    assert validate_fault(copy.deepcopy(PRESETS[name])) == []


def test_a_fault_with_damaged_data_is_rejected():
    """26 Oct evaluation: a custom fault's database logs said "corrupted data"; no action can repair that."""
    fault = json.loads((Path("results") / "eval_faults" / "c06_database_crash_during_checkpoint.json")
                       .read_text(encoding="utf-8"))
    assert any("damaged data or a failing disk" in problem for problem in validate_fault(fault))


@pytest.mark.parametrize("name", ALL_PRESETS)
def test_preset_is_broken_before_the_fix(name):
    """A fault that already counts as fixed would test nothing."""
    server = Server()
    apply_fault(server, PRESETS[name])
    assert verify(server, PRESETS[name])["verdict"] == "NOT FIXED"


@pytest.mark.parametrize("name", ALL_PRESETS)
def test_preset_is_fixed_by_its_own_fix(name):
    """The fix built from fixed_when, approved and applied, must end in FIXED."""
    fault = PRESETS[name]
    server = Server()
    apply_fault(server, fault)
    steps, problems = find_fix(fault, server)
    assert problems == []

    decision = {"approved": True, "option": {"steps": steps}, "by": "pytest"}
    apply_fix(server, decision)
    assert verify(server, fault)["verdict"] == "FIXED"
