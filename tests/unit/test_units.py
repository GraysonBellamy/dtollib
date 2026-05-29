"""Tests for :func:`dtollib.units.to_pint`."""

from __future__ import annotations

import pytest

from dtollib import to_pint


@pytest.mark.parametrize(
    ("incoming", "expected"),
    [
        (None, None),
        ("V", "V"),
        ("mV", "mV"),
        ("degC", "degC"),
        ("degF", "degF"),
        ("K", "K"),
        ("Hz", "Hz"),
        ("psi", "psi"),
        ("ohm", "ohm"),
        ("Ohm", "ohm"),  # case-folded onto canonical lower-case.
    ],
)
def test_known_strings_passthrough(incoming: str | None, expected: str | None) -> None:
    """Every documented string maps to the expected pint form."""
    assert to_pint(incoming) == expected


def test_unknown_string_returned_as_is() -> None:
    """Unfamiliar units are not silently dropped — returned unchanged."""
    assert to_pint("custom-unit") == "custom-unit"


def test_object_with_name_attribute() -> None:
    """An object with a ``name`` attribute resolves through the table."""

    class _Enum:
        name = "V"

    assert to_pint(_Enum()) == "V"


def test_object_without_name_returns_none() -> None:
    """An opaque object with no ``name`` returns ``None``."""

    class _Opaque:
        pass

    assert to_pint(_Opaque()) is None
