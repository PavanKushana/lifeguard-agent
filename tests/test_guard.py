"""
test_guard.py
Checks the free parts of guard.py: the code rules in front of Nano, and plain_dashes(),
which keeps long dashes (they read as AI-written) out of the sentences shown on the page.

No AI is used here: Nano's own judgement needs a real model call and is tried by hand.
Run all tests from the project folder with:  python -m pytest
"""

from app.guard import check_rules, plain_dashes


def test_long_dash_becomes_a_comma():
    """The real refusal Nano wrote on 24 Oct."""
    assert (plain_dashes("This isn't a server issue—it's a poem request.")
            == "This isn't a server issue, it's a poem request.")


def test_spaced_and_short_long_dashes():
    """With spaces around it, and the slightly shorter en dash, give the same plain comma."""
    assert plain_dashes("Not a fault – try a crash") == "Not a fault, try a crash"
    assert plain_dashes("Not a fault — try a crash") == "Not a fault, try a crash"


def test_number_range_keeps_a_hyphen():
    """A range of numbers reads better with a normal hyphen than with a comma."""
    assert plain_dashes("Wait 3–5 minutes") == "Wait 3-5 minutes"


def test_no_double_or_stray_commas():
    """A dash after a comma, or at the end, must not leave odd punctuation behind."""
    assert plain_dashes("Not a fault,— try again") == "Not a fault, try again"
    assert plain_dashes("Try a crash—") == "Try a crash"


def test_text_without_dashes_is_unchanged():
    text = "Looks like a server fault we can build: the cache-crash idea is fine."
    assert plain_dashes(text) == text


def test_rules_refuse_text_without_letters():
    """Only numbers or signs: refused by the code at once, no model call needed."""
    assert check_rules("12345") is not None
    assert check_rules("!!!???") is not None
    assert check_rules("") is not None


def test_rules_allow_typos_and_numbers():
    """Typos and numbers inside a sentence pass the code rules; Nano then judges the meaning."""
    assert check_rules("the disk is 95% full") is None
    assert check_rules("teh databse dsik is ful") is None


def test_rules_refuse_very_long_text():
    """Nobody can paste a whole prompt into the box."""
    assert "under 300 characters" in check_rules("the cache crashed " * 20)
