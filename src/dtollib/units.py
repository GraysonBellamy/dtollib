""":func:`to_pint` — best-effort unit-name → pint-compatible string conversion.

Pint is **not** a runtime dependency of dtollib. This module returns plain
strings that pint accepts (``"degC"``, ``"V"``, ``"K"``, ...) so downstream
consumers who do use pint can parse them via ``pint.UnitRegistry().Unit()``.

Lossy by design — same rule as the sibling libraries. ``None`` means "no
mapping known"; callers should treat that as a passthrough hint rather
than an error.
"""

from __future__ import annotations

__all__ = ["to_pint"]


# Plain-string passthrough table. The instrument-side unit name on the left,
# the pint-canonical string on the right. Engineering-units strings that the
# DT-Open Layers SDK / dtollib emits all live here.
_STRING_PASSTHROUGH: dict[str, str] = {
    "degC": "degC",
    "degF": "degF",
    "K": "K",
    "degR": "degR",
    "V": "V",
    "mV": "mV",
    "A": "A",
    "mA": "mA",
    "Hz": "Hz",
    "kHz": "kHz",
    "MHz": "MHz",
    "Pa": "Pa",
    "kPa": "kPa",
    "psi": "psi",
    "g": "g",  # standard gravities; pint treats as "gravity" when contextual
    "ohm": "ohm",
    "Ohm": "ohm",
    "strain": "dimensionless",
    "counts": "dimensionless",
}


def to_pint(unit: object) -> str | None:
    """Return a pint-compatible unit string for ``unit``, or ``None``.

    Accepts:
        - ``None`` → ``None``.
        - A string already in pint form (``"degC"``, ``"V"``, ...) — passed
          through unchanged when it's in the known set; otherwise returned
          as-is so unfamiliar units don't get silently dropped.

    Lossy by design: no tuple, no discriminator, no exception on unknown
    units — same contract as the sibling libraries.
    """
    if unit is None:
        return None
    if isinstance(unit, str):
        return _STRING_PASSTHROUGH.get(unit, unit)
    # Last-ditch: enums and similar objects often expose .name as a SHOUTING
    # string. We don't try to be clever; let callers handle the unknown case.
    name = getattr(unit, "name", None)
    if isinstance(name, str):
        return _STRING_PASSTHROUGH.get(name)
    return None
