import pytest
from form_fields import phone_text

@pytest.mark.parametrize("value,placeholder,country,expected", [
    ("(202) 555-0123", "XXXXXXXXXX (use this format for USA phone numbers)", "United States", "2025550123"),
    ("+1 202 555 0123", "XXXXXXXXXX", "United States", "2025550123"),
    ("+44 20 7946 0123", "XXXXXXXXXX", "United Kingdom", "+44 20 7946 0123"),
    ("(202) 555-0123", "Phone", "United States", "(202) 555-0123"),
])
def test_phone_placeholder_preserves_identity(value, placeholder, country, expected):
    assert phone_text(value, placeholder, country) == expected
