"""Reading a place out of the ways forms display it (geo_reference)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import geo_reference


@pytest.mark.parametrize("shown,code", [
    ("United States", "US"),
    ("United States of America (+1)", "US"),
    ("+1 United States", "US"),
    # Workday's phone-code widget says what is chosen in a sentence; the
    # agent read it as no country, took the field for empty, and "answered"
    # it again on every pass.
    ("1 item selected, United States of America (+1)", "US"),
    ("United States of America (+1), press delete to clear value.", "US"),
    ("Åland Islands", "AX"),
    ("Aaland Islands", "AX"),
])
def test_a_country_is_read_from_how_forms_show_it(shown, code):
    assert geo_reference.country_code(shown) == code


@pytest.mark.parametrize("shown", ["Asian (United States of America)", "Select one", "", "1 item selected"])
def test_what_is_not_a_country_is_not_read_as_one(shown):
    """A parenthetical alone never names the place: that is an ethnicity."""
    assert geo_reference.country_code(shown) is None


@pytest.mark.parametrize("shown", ["Virginia", "VA", "Virginia (VA)", "VA - Virginia"])
def test_a_us_state_is_read_in_any_spelling(shown):
    assert geo_reference.us_state_code(shown) == "VA"
