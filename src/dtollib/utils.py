"""Pure-Python helpers: NIST ITS-90 thermocouple math + rosette transforms.

These helpers do not depend on the SDK and are testable on any
platform without hardware.  They exist for two use cases:

1. Client-side validation in :class:`~dtollib.tasks.ThermocoupleInput`
   ``__post_init__`` — reject TC ranges outside the operating envelope
   *before* the SDK rejects them, with a precise error message.
2. Application-side TC linearisation (deferred-by-default).
   When ``OLSSC_SUP_LINEARIZE_TC`` is ``False`` on a subsystem, the
   wrapper applies these polynomials itself.

Coefficient source: NIST Monograph 175 (Burns et al., 1993), the
ITS-90 reference functions for letter-designated thermocouples.

Status by type:

- **Type K** — full forward + inverse polynomial.
- **Type J** — full forward + inverse polynomial.
- Other types — operating range only; the forward/inverse polynomials
  are not implemented.

Calling an unsupported type at the polynomial layer raises
:class:`~dtollib.errors.DtolValidationError` naming the supported set
(J, K) and the NIST reference needed to add others.
"""

from __future__ import annotations

from typing import Final

from dtollib.errors import DtolValidationError, ErrorContext

__all__ = [
    "compute_delta_rosette",
    "compute_rectangular_rosette",
    "convert_temperature_to_volts",
    "convert_volts_to_temperature",
    "get_thermocouple_range",
]


# Range break points used by the piecewise NIST polynomials.
# The forward polynomial for Type J switches at 760 °C; the inverse
# polynomials switch at the documented emf boundaries.  Named here so
# the conditionals downstream stay readable and the ruff PLR2004
# noise is silenced.

_TYPE_J_FORWARD_BREAKPOINT_C: Final[float] = 760.0
_TYPE_K_INVERSE_MID_BREAKPOINT_MV: Final[float] = 20.644
_TYPE_J_INVERSE_MID_BREAKPOINT_MV: Final[float] = 42.919


# ---- Thermocouple operating ranges ----------------------------------------
#
# (min_degc, max_degc) per NIST Monograph 175 §1 Tables 1.1, 2.1, ...
# Used by :class:`ThermocoupleInput.__post_init__` to reject impossible
# ranges before the SDK does.

_TC_RANGES: Final[dict[str, tuple[float, float]]] = {
    "J": (-210.0, 1200.0),
    "K": (-270.0, 1372.0),
    "T": (-270.0, 400.0),
    "E": (-270.0, 1000.0),
    "R": (-50.0, 1768.1),
    "S": (-50.0, 1768.1),
    "B": (0.0, 1820.0),
    "N": (-270.0, 1300.0),
}


def get_thermocouple_range(tc_type: str) -> tuple[float, float]:
    """Operating temperature range for a thermocouple type.

    Args:
        tc_type: Single-letter type designation (``"J"``, ``"K"``,
            ``"T"``, ``"E"``, ``"R"``, ``"S"``, ``"B"``, ``"N"``).
            Case-insensitive.

    Returns:
        ``(min_degc, max_degc)`` per NIST Monograph 175.

    Raises:
        DtolValidationError: ``tc_type`` is not one of the supported
            letter designations.
    """
    key = tc_type.upper()
    rng = _TC_RANGES.get(key)
    if rng is None:
        raise DtolValidationError(
            f"unknown thermocouple type {tc_type!r}; expected one of {sorted(_TC_RANGES.keys())}",
            context=ErrorContext(operation="get_thermocouple_range"),
        )
    return rng


# ---- NIST ITS-90 polynomial coefficients ----------------------------------
#
# Coefficients are from NIST Monograph 175 (Burns et al., 1993).
# Forward polynomials produce thermo-emf in millivolts from
# temperature in degrees Celsius.  Inverse polynomials produce
# temperature in degrees Celsius from thermo-emf in millivolts.
#
# Type K (chromel-alumel) forward — emf(T) over two ranges, with an
# exponential correction term in the upper range.

_TYPE_K_FORWARD_LOW: Final[tuple[float, ...]] = (
    0.000000000000e00,
    0.394501280250e-01,
    0.236223735980e-04,
    -0.328589067840e-06,
    -0.499048287770e-08,
    -0.675090591730e-10,
    -0.574103274280e-12,
    -0.310888728940e-14,
    -0.104516093650e-16,
    -0.198892668780e-19,
    -0.163226974860e-22,
)

_TYPE_K_FORWARD_HIGH: Final[tuple[float, ...]] = (
    -0.176004136860e-01,
    0.389212049750e-01,
    0.185587700320e-04,
    -0.994575928740e-07,
    0.318409457190e-09,
    -0.560728448890e-12,
    0.560750590590e-15,
    -0.320207200030e-18,
    0.971511471520e-22,
    -0.121047212750e-25,
)

_TYPE_K_FORWARD_EXPONENTIAL: Final[tuple[float, float, float]] = (
    0.118597600000e00,
    -0.118343200000e-03,
    0.126968600000e03,
)

# Type K inverse polynomials — temperature(emf) over three ranges.

_TYPE_K_INVERSE_LOW: Final[tuple[float, ...]] = (
    0.0000000e00,
    2.5173462e01,
    -1.1662878e00,
    -1.0833638e00,
    -8.9773540e-01,
    -3.7342377e-01,
    -8.6632643e-02,
    -1.0450598e-02,
    -5.1920577e-04,
)

_TYPE_K_INVERSE_MID: Final[tuple[float, ...]] = (
    0.000000e00,
    2.508355e01,
    7.860106e-02,
    -2.503131e-01,
    8.315270e-02,
    -1.228034e-02,
    9.804036e-04,
    -4.413030e-05,
    1.057734e-06,
    -1.052755e-08,
)

_TYPE_K_INVERSE_HIGH: Final[tuple[float, ...]] = (
    -1.318058e02,
    4.830222e01,
    -1.646031e00,
    5.464731e-02,
    -9.650715e-04,
    8.802193e-06,
    -3.110810e-08,
)


# Type J (iron-constantan) forward — single polynomial over two ranges.

_TYPE_J_FORWARD_LOW: Final[tuple[float, ...]] = (
    0.000000000000e00,
    0.503811878150e-01,
    0.304758369300e-04,
    -0.856810657200e-07,
    0.132281952950e-09,
    -0.170529583370e-12,
    0.209480906970e-15,
    -0.125383953360e-18,
    0.156317256970e-22,
)

_TYPE_J_FORWARD_HIGH: Final[tuple[float, ...]] = (
    0.296456256810e03,
    -0.149761277860e01,
    0.317871039240e-02,
    -0.318476867010e-05,
    0.157208190040e-08,
    -0.306913690730e-12,
)

# Type J inverse polynomials.

_TYPE_J_INVERSE_LOW: Final[tuple[float, ...]] = (
    0.0000000e00,
    1.9528268e01,
    -1.2286185e00,
    -1.0752178e00,
    -5.9086933e-01,
    -1.7256713e-01,
    -2.8131513e-02,
    -2.3963370e-03,
    -8.3823321e-05,
)

_TYPE_J_INVERSE_MID: Final[tuple[float, ...]] = (
    0.000000e00,
    1.978425e01,
    -2.001204e-01,
    1.036969e-02,
    -2.549687e-04,
    3.585153e-06,
    -5.344285e-08,
    5.099890e-10,
)

_TYPE_J_INVERSE_HIGH: Final[tuple[float, ...]] = (
    -3.11358187e03,
    3.00543684e02,
    -9.94773230e00,
    1.70276630e-01,
    -1.43033468e-03,
    4.73886084e-06,
)


def _horner(coeffs: tuple[float, ...], x: float) -> float:
    """Evaluate polynomial using Horner's method."""
    result = 0.0
    for c in reversed(coeffs):
        result = result * x + c
    return result


def convert_temperature_to_volts(tc_type: str, temperature_c: float) -> float:
    """NIST ITS-90 forward polynomial: temperature → thermo-emf.

    Args:
        tc_type: Single-letter TC type (``"K"``, ``"J"``, ...).
        temperature_c: Temperature in degrees Celsius.

    Returns:
        Thermo-emf in **volts** (not millivolts — the SDK's
        ``olDaSetTriggerThresholdLevel`` and friends use volts).

    Raises:
        DtolValidationError: ``temperature_c`` outside the type's
            NIST-documented operating range, or ``tc_type`` is not
            yet implemented.
    """
    key = tc_type.upper()
    lo, hi = get_thermocouple_range(key)
    if not (lo <= temperature_c <= hi):
        raise DtolValidationError(
            f"temperature {temperature_c} °C outside Type {key} range [{lo}, {hi}]",
            context=ErrorContext(operation="convert_temperature_to_volts"),
        )

    mv = _temperature_to_mv(key, temperature_c)
    return mv * 1e-3


def convert_volts_to_temperature(
    tc_type: str,
    volts: float,
    *,
    cjc_temperature_c: float = 0.0,
) -> float:
    """NIST ITS-90 inverse polynomial + CJC: thermo-emf → temperature.

    The thermocouple emf is measured **relative to** the cold
    junction; to recover the absolute hot-junction temperature we add
    the emf the cold junction would emit at its measured temperature,
    then invert the polynomial.

    Args:
        tc_type: Single-letter TC type.
        volts: Measured thermo-emf in **volts**.
        cjc_temperature_c: Cold-junction temperature in degrees
            Celsius.  Defaults to ``0.0`` for the (uncommon) ice-bath
            case.

    Returns:
        Hot-junction temperature in degrees Celsius.

    Raises:
        DtolValidationError: ``tc_type`` is unsupported (only J and K
            have polynomials) or a temperature/voltage is outside range.
    """
    key = tc_type.upper()
    lo, hi = get_thermocouple_range(key)
    if not (lo <= cjc_temperature_c <= hi):
        raise DtolValidationError(
            f"CJC temperature {cjc_temperature_c} °C outside Type {key} range [{lo}, {hi}]",
            context=ErrorContext(operation="convert_volts_to_temperature"),
        )

    measured_mv = volts * 1e3
    cjc_mv = _temperature_to_mv(key, cjc_temperature_c)
    total_mv = measured_mv + cjc_mv
    return _mv_to_temperature(key, total_mv)


def _temperature_to_mv(key: str, temperature_c: float) -> float:
    """Internal: forward polynomial for ``key`` evaluated at ``temperature_c``."""
    if key == "K":
        if temperature_c < 0.0:
            return _horner(_TYPE_K_FORWARD_LOW, temperature_c)
        base = _horner(_TYPE_K_FORWARD_HIGH, temperature_c)
        a0, a1, a2 = _TYPE_K_FORWARD_EXPONENTIAL
        import math  # noqa: PLC0415

        return base + a0 * math.exp(a1 * (temperature_c - a2) ** 2)
    if key == "J":
        if temperature_c <= _TYPE_J_FORWARD_BREAKPOINT_C:
            return _horner(_TYPE_J_FORWARD_LOW, temperature_c)
        return _horner(_TYPE_J_FORWARD_HIGH, temperature_c)
    raise DtolValidationError(
        f"Type {key} forward polynomial is not supported; supported "
        "thermocouple types are J and K. See NIST Monograph 175 for the "
        f"Type {key} coefficients if you need to add it.",
        context=ErrorContext(operation="_temperature_to_mv"),
    )


def _mv_to_temperature(key: str, mv: float) -> float:
    """Internal: inverse polynomial for ``key`` evaluated at ``mv``."""
    if key == "K":
        if mv < 0.0:
            return _horner(_TYPE_K_INVERSE_LOW, mv)
        if mv < _TYPE_K_INVERSE_MID_BREAKPOINT_MV:
            return _horner(_TYPE_K_INVERSE_MID, mv)
        return _horner(_TYPE_K_INVERSE_HIGH, mv)
    if key == "J":
        if mv < 0.0:
            return _horner(_TYPE_J_INVERSE_LOW, mv)
        if mv < _TYPE_J_INVERSE_MID_BREAKPOINT_MV:
            return _horner(_TYPE_J_INVERSE_MID, mv)
        return _horner(_TYPE_J_INVERSE_HIGH, mv)
    raise DtolValidationError(
        f"Type {key} inverse polynomial is not supported; supported "
        "thermocouple types are J and K.",
        context=ErrorContext(operation="_mv_to_temperature"),
    )


# ---- Strain rosette transforms --------------------------------------------
#
# Standard rectangular and delta rosette equations for plane strain.
# Reference: any strain-gauge handbook; the Vishay TN-515 application
# note has a clean derivation.


def compute_rectangular_rosette(
    eps_0: float,
    eps_45: float,
    eps_90: float,
) -> tuple[float, float, float]:
    """Compute principal strains from a 0/45/90 rectangular rosette.

    Args:
        eps_0: Strain reading on the 0° gauge.
        eps_45: Strain reading on the 45° gauge.
        eps_90: Strain reading on the 90° gauge.

    Returns:
        ``(eps_max, eps_min, theta_p_rad)`` — principal strains and
        the angle of the principal axis from the 0° gauge, in
        radians.
    """
    import math  # noqa: PLC0415

    mean = (eps_0 + eps_90) / 2.0
    diff = (eps_0 - eps_90) / 2.0
    shear_half = (eps_0 + eps_90) / 2.0 - eps_45
    radius = math.hypot(diff, shear_half)
    eps_max = mean + radius
    eps_min = mean - radius
    theta_p = 0.5 * math.atan2(-2.0 * shear_half, eps_0 - eps_90) if eps_0 != eps_90 else 0.0
    return (eps_max, eps_min, theta_p)


def compute_delta_rosette(
    eps_0: float,
    eps_60: float,
    eps_120: float,
) -> tuple[float, float, float]:
    """Compute principal strains from a 0/60/120 delta rosette.

    Args:
        eps_0: Strain reading on the 0° gauge.
        eps_60: Strain reading on the 60° gauge.
        eps_120: Strain reading on the 120° gauge.

    Returns:
        ``(eps_max, eps_min, theta_p_rad)`` — principal strains and
        the angle of the principal axis from the 0° gauge.
    """
    import math  # noqa: PLC0415

    mean = (eps_0 + eps_60 + eps_120) / 3.0
    a = eps_0 - mean
    b = (eps_60 - eps_120) / math.sqrt(3.0)
    radius = math.hypot(a, b)
    eps_max = mean + radius
    eps_min = mean - radius
    theta_p = 0.5 * math.atan2(b, a) if a != 0.0 or b != 0.0 else 0.0
    return (eps_max, eps_min, theta_p)
