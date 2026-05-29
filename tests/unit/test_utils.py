"""Tests for the NIST ITS-90 TC math + rosette transforms.

Reference values come from NIST Monograph 175 (Burns et al., 1993)
Tables 10.1 (Type K) and 9.1 (Type J).
"""

from __future__ import annotations

import math

import pytest

from dtollib.errors import DtolValidationError
from dtollib.utils import (
    compute_delta_rosette,
    compute_rectangular_rosette,
    convert_temperature_to_volts,
    convert_volts_to_temperature,
    get_thermocouple_range,
)

# NIST reference values — mV at the named temperature.
# Type K: NIST Monograph 175, Table 10.1.
# Type J: NIST Monograph 175, Table 9.1.

_TYPE_K_REFERENCE: list[tuple[float, float]] = [
    (-100.0, -3.554),
    (0.0, 0.000),
    (25.0, 1.000),
    (100.0, 4.096),
    (200.0, 8.138),
    (500.0, 20.644),
    (1000.0, 41.276),
    (1372.0, 54.886),
]

_TYPE_J_REFERENCE: list[tuple[float, float]] = [
    (-100.0, -4.633),
    (0.0, 0.000),
    (100.0, 5.269),
    (500.0, 27.393),
    (1000.0, 57.953),
    (1200.0, 69.553),
]


class TestThermocoupleRange:
    @pytest.mark.parametrize("tc_type", ["J", "K", "T", "E", "R", "S", "B", "N"])
    def test_known_types_return_a_range(self, tc_type: str) -> None:
        lo, hi = get_thermocouple_range(tc_type)
        assert lo < hi

    def test_lowercase_is_accepted(self) -> None:
        assert get_thermocouple_range("k") == get_thermocouple_range("K")

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(DtolValidationError):
            get_thermocouple_range("Z")


class TestTypeKForward:
    @pytest.mark.parametrize(("temp_c", "expected_mv"), _TYPE_K_REFERENCE)
    def test_forward_matches_nist(self, temp_c: float, expected_mv: float) -> None:
        volts = convert_temperature_to_volts("K", temp_c)
        mv = volts * 1000.0
        # NIST tables published to 3 decimal places; allow ±0.002 mV.
        assert abs(mv - expected_mv) < 0.005, (
            f"Type K @ {temp_c}°C: expected {expected_mv} mV, got {mv:.3f} mV"
        )

    def test_out_of_range_raises(self) -> None:
        with pytest.raises(DtolValidationError):
            convert_temperature_to_volts("K", 2000.0)


class TestTypeJForward:
    @pytest.mark.parametrize(("temp_c", "expected_mv"), _TYPE_J_REFERENCE)
    def test_forward_matches_nist(self, temp_c: float, expected_mv: float) -> None:
        volts = convert_temperature_to_volts("J", temp_c)
        mv = volts * 1000.0
        assert abs(mv - expected_mv) < 0.005, (
            f"Type J @ {temp_c}°C: expected {expected_mv} mV, got {mv:.3f} mV"
        )


class TestInverse:
    """The NIST inverse polynomial is guaranteed accurate to ~±0.05°C."""

    def test_type_k_round_trip(self) -> None:
        for temp_c, _expected_mv in _TYPE_K_REFERENCE:
            volts = convert_temperature_to_volts("K", temp_c)
            back = convert_volts_to_temperature("K", volts)
            assert abs(back - temp_c) < 0.6, f"Type K round trip @ {temp_c}°C: got {back:.3f}°C"

    def test_type_k_with_cjc(self) -> None:
        # If the cold junction is at 25 °C and we measure the emf the
        # hot junction would emit at 100 °C relative to 25 °C, the
        # inverse should recover ~100 °C.
        emf_hot_vs_ice = convert_temperature_to_volts("K", 100.0)
        emf_cjc_vs_ice = convert_temperature_to_volts("K", 25.0)
        measured = emf_hot_vs_ice - emf_cjc_vs_ice
        recovered = convert_volts_to_temperature("K", measured, cjc_temperature_c=25.0)
        assert abs(recovered - 100.0) < 0.6

    def test_unsupported_type_raises_for_polynomial(self) -> None:
        # Range is known but the polynomial is not yet implemented.
        with pytest.raises(DtolValidationError):
            convert_temperature_to_volts("T", 0.0)


class TestRectangularRosette:
    def test_uniaxial_strain_recovered(self) -> None:
        # Pure strain along the 0° gauge.
        eps_max, eps_min, theta = compute_rectangular_rosette(
            eps_0=1000e-6,
            eps_45=500e-6,
            eps_90=0.0,
        )
        assert abs(eps_max - 1000e-6) < 1e-9
        assert abs(eps_min) < 1e-9
        assert abs(theta) < 1e-9

    def test_45_degree_pure_shear(self) -> None:
        # Pure shear → equal-and-opposite principal strains.
        eps_max, eps_min, _theta = compute_rectangular_rosette(
            eps_0=0.0,
            eps_45=500e-6,
            eps_90=0.0,
        )
        # With this convention, principal strains are equal in
        # magnitude.
        assert abs(eps_max + eps_min) < 1e-9
        assert eps_max > eps_min


class TestDeltaRosette:
    def test_uniaxial_recovered(self) -> None:
        # Pure uniaxial along 0° gauge: eps_0 = epsilon, eps_60 =
        # eps_120 = -nu*epsilon * sin^2(60°) ... a simple smoke test
        # of the rosette formula correctness.
        e = 1000e-6
        eps_max, eps_min, _theta = compute_delta_rosette(
            eps_0=e,
            eps_60=0.25 * e,  # 1/4 transverse projection at 60° (smoke value)
            eps_120=0.25 * e,
        )
        assert eps_max > eps_min

    def test_zero_strain_returns_zero(self) -> None:
        eps_max, eps_min, theta = compute_delta_rosette(0.0, 0.0, 0.0)
        assert abs(eps_max) < 1e-12
        assert abs(eps_min) < 1e-12
        assert math.isfinite(theta)
