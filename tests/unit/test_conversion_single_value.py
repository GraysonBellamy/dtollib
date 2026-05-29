"""Tests for the scalar single-value conversion helper.

Covers :func:`dtollib.capi.conversion.code_to_input_volts` - the offset-binary
code-to-volts math used by the application-side thermocouple read path (the
SDK's ``olDaCodeToVolts`` is unusable on the DT9805/06; see docs/decisions.md).
"""

from __future__ import annotations

import math

from dtollib.capi.conversion import code_to_input_volts


def _offset_binary_volts(code: int, gain: float) -> float:
    """DT9805/06 A/D: +/-10 V bipolar, 16-bit offset binary."""
    return code_to_input_volts(code, gain, vmin=-10.0, vmax=10.0, resolution_bits=16)


class TestOffsetBinary:
    def test_midscale_is_zero_volts(self) -> None:
        assert math.isclose(_offset_binary_volts(32768, 1.0), 0.0, abs_tol=2e-4)

    def test_min_and_max_codes_map_to_rails(self) -> None:
        assert math.isclose(_offset_binary_volts(0, 1.0), -10.0, abs_tol=1e-9)
        assert math.isclose(_offset_binary_volts(65535, 1.0), +10.0, abs_tol=1e-3)

    def test_gain_refers_reading_to_input(self) -> None:
        # Same code at gain 100 reads 1/100th the input voltage.
        v1 = _offset_binary_volts(40000, 1.0)
        v100 = _offset_binary_volts(40000, 100.0)
        assert math.isclose(v100, v1 / 100.0, rel_tol=1e-9)

    def test_open_rail_at_high_gain_is_small_positive(self) -> None:
        # An open input pegs the ADC at +full scale; at gain 100 that is
        # about +V_RAIL/gain = +0.1 V - the session's open-detection threshold.
        assert math.isclose(_offset_binary_volts(65535, 100.0), 0.1, abs_tol=1e-3)


class TestTwosComplement:
    def test_zero_code_is_zero_volts(self) -> None:
        # Two's-complement code 0 sits at mid-scale, or 0 V on a +/-10 V range.
        volts = code_to_input_volts(
            0, 1.0, vmin=-10.0, vmax=10.0, resolution_bits=16, twos_complement=True
        )
        assert math.isclose(volts, 0.0, abs_tol=2e-4)

    def test_matches_offset_binary_after_shift(self) -> None:
        # A two's-complement code C equals offset-binary code C + 2^(n-1).
        tc = code_to_input_volts(
            -100, 1.0, vmin=-10.0, vmax=10.0, resolution_bits=16, twos_complement=True
        )
        ob = _offset_binary_volts(32768 - 100, 1.0)
        assert math.isclose(tc, ob, rel_tol=1e-9)


class TestRoundTripTemperature:
    def test_zero_emf_reads_cold_junction(self) -> None:
        # The end-to-end identity the bench relies on: a thermocouple at the
        # cold-junction temperature emits about 0 V and reads back as CJC temp.
        from dtollib.utils import convert_volts_to_temperature

        emf = _offset_binary_volts(32768, 100.0)  # 0 V at the TC gain
        cjc_volts = _offset_binary_volts(33587, 1.0)  # CJC code for about 25 C
        cjc_degc = cjc_volts / 0.010
        temp = convert_volts_to_temperature("K", emf, cjc_temperature_c=cjc_degc)
        assert math.isclose(temp, 25.0, abs_tol=0.5)
