"""Tests for the vectorised conversion kernels."""

from __future__ import annotations

import numpy as np
import pytest

from dtollib.capi.conversion import (
    Encoding,
    codes_to_volts_vectorised,
    deinterleave_cjc,
    detect_thermocouple_sentinel_vectorised,
)


class TestCodesToVoltsTwosComplement:
    def test_zero_code_yields_midpoint(self) -> None:
        codes = np.zeros((2, 4), dtype=np.int16)
        volts = codes_to_volts_vectorised(
            codes,
            ranges=[(-10.0, 10.0), (-5.0, 5.0)],
            gains=[1.0, 1.0],
            resolution_bits=16,
            encoding=Encoding.TWOS_COMPLEMENT,
        )
        # Symmetric bipolar: midpoint is 0 V.
        np.testing.assert_array_equal(volts, np.zeros((2, 4), dtype=np.float64))

    def test_positive_full_scale_yields_high_range(self) -> None:
        codes = np.full((1, 1), 2**15, dtype=np.int32)
        volts = codes_to_volts_vectorised(
            codes,
            ranges=[(-10.0, 10.0)],
            gains=[1.0],
            resolution_bits=16,
            encoding=Encoding.TWOS_COMPLEMENT,
        )
        # 2^15 / 2^15 = 1.0, * 10 V (half-span) + 0 (mid) = 10.0 V.
        np.testing.assert_allclose(volts[0, 0], 10.0)

    def test_negative_full_scale_yields_low_range(self) -> None:
        codes = np.full((1, 1), -(2**15), dtype=np.int32)
        volts = codes_to_volts_vectorised(
            codes,
            ranges=[(-10.0, 10.0)],
            gains=[1.0],
            resolution_bits=16,
            encoding=Encoding.TWOS_COMPLEMENT,
        )
        np.testing.assert_allclose(volts[0, 0], -10.0)

    def test_gain_divides_output(self) -> None:
        codes = np.full((1, 1), 2**15, dtype=np.int32)
        volts = codes_to_volts_vectorised(
            codes,
            ranges=[(-10.0, 10.0)],
            gains=[2.0],
            resolution_bits=16,
            encoding=Encoding.TWOS_COMPLEMENT,
        )
        np.testing.assert_allclose(volts[0, 0], 5.0)


class TestCodesToVoltsOffsetBinary:
    def test_mid_code_yields_midpoint(self) -> None:
        codes = np.full((1, 1), 2**15, dtype=np.int32)
        volts = codes_to_volts_vectorised(
            codes,
            ranges=[(-10.0, 10.0)],
            gains=[1.0],
            resolution_bits=16,
            encoding=Encoding.OFFSET_BINARY,
        )
        np.testing.assert_allclose(volts[0, 0], 0.0)

    def test_zero_code_yields_low_range(self) -> None:
        codes = np.zeros((1, 1), dtype=np.int32)
        volts = codes_to_volts_vectorised(
            codes,
            ranges=[(-10.0, 10.0)],
            gains=[1.0],
            resolution_bits=16,
            encoding=Encoding.OFFSET_BINARY,
        )
        np.testing.assert_allclose(volts[0, 0], -10.0)


class TestCodesToVoltsBinary:
    def test_zero_code_yields_low_range(self) -> None:
        codes = np.zeros((1, 1), dtype=np.int32)
        volts = codes_to_volts_vectorised(
            codes,
            ranges=[(0.0, 10.0)],
            gains=[1.0],
            resolution_bits=16,
            encoding=Encoding.BINARY,
        )
        np.testing.assert_allclose(volts[0, 0], 0.0)

    def test_max_code_yields_high_range(self) -> None:
        codes = np.full((1, 1), 2**16 - 1, dtype=np.int32)
        volts = codes_to_volts_vectorised(
            codes,
            ranges=[(0.0, 10.0)],
            gains=[1.0],
            resolution_bits=16,
            encoding=Encoding.BINARY,
        )
        np.testing.assert_allclose(volts[0, 0], 10.0)


class TestCodesToVoltsShapeValidation:
    def test_one_d_codes_rejected(self) -> None:
        codes = np.zeros(4, dtype=np.int16)
        with pytest.raises(ValueError, match="2-D"):
            codes_to_volts_vectorised(
                codes,
                ranges=[(-10.0, 10.0)],
                gains=[1.0],
                resolution_bits=16,
                encoding=Encoding.TWOS_COMPLEMENT,
            )

    def test_ranges_length_must_match_n_channels(self) -> None:
        codes = np.zeros((2, 4), dtype=np.int16)
        with pytest.raises(ValueError, match=r"ranges has length"):
            codes_to_volts_vectorised(
                codes,
                ranges=[(-10.0, 10.0)],
                gains=[1.0, 1.0],
                resolution_bits=16,
                encoding=Encoding.TWOS_COMPLEMENT,
            )

    def test_gains_length_must_match_n_channels(self) -> None:
        codes = np.zeros((2, 4), dtype=np.int16)
        with pytest.raises(ValueError, match=r"gains has length"):
            codes_to_volts_vectorised(
                codes,
                ranges=[(-10.0, 10.0), (-5.0, 5.0)],
                gains=[1.0],
                resolution_bits=16,
                encoding=Encoding.TWOS_COMPLEMENT,
            )

    def test_resolution_bits_out_of_range_rejected(self) -> None:
        codes = np.zeros((1, 1), dtype=np.int16)
        with pytest.raises(ValueError, match="resolution_bits"):
            codes_to_volts_vectorised(
                codes,
                ranges=[(-10.0, 10.0)],
                gains=[1.0],
                resolution_bits=0,
                encoding=Encoding.TWOS_COMPLEMENT,
            )


class TestDeinterleaveCjc:
    def test_known_pattern_round_trips(self) -> None:
        # 2 channels, 3 scans -> 12 elements.
        # Layout: [v00, c00, v10, c10, v01, c01, v11, c11, v02, c02, v12, c12]
        raw = np.array(
            [1.0, 100.0, 2.0, 200.0, 1.5, 101.0, 2.5, 201.0, 1.7, 102.0, 2.7, 202.0],
            dtype=np.float64,
        )
        measurement, cjc = deinterleave_cjc(raw, n_channels=2, n_samples=3)
        assert measurement.shape == (2, 3)
        assert cjc.shape == (2, 3)
        np.testing.assert_array_equal(measurement[0], [1.0, 1.5, 1.7])
        np.testing.assert_array_equal(measurement[1], [2.0, 2.5, 2.7])
        np.testing.assert_array_equal(cjc[0], [100.0, 101.0, 102.0])
        np.testing.assert_array_equal(cjc[1], [200.0, 201.0, 202.0])

    def test_wrong_size_rejected(self) -> None:
        raw = np.zeros(10, dtype=np.float64)
        with pytest.raises(ValueError, match="!="):
            deinterleave_cjc(raw, n_channels=2, n_samples=3)

    def test_caught_value_cjc_stride_bug(self) -> None:
        """Regression for the 'value, cjc, value, cjc' stride bug.

        If a future refactor accidentally treats the buffer as channel-major
        instead of scan-major, the measurement row would be alternating
        value/cjc values. This test pins the scan-major contract.
        """
        # Construct a buffer where every cjc is exactly +1000 above the value.
        # If the stride is wrong, the measurement row will contain values
        # like (v, v+1000, v', v'+1000, ...) — the test catches it.
        n_channels, n_samples = 1, 4
        scans: list[float] = []
        for i in range(n_samples):
            scans.extend([float(i), float(i) + 1000.0])
        raw = np.array(scans, dtype=np.float64)
        measurement, cjc = deinterleave_cjc(raw, n_channels=n_channels, n_samples=n_samples)
        np.testing.assert_array_equal(measurement[0], [0.0, 1.0, 2.0, 3.0])
        np.testing.assert_array_equal(cjc[0], [1000.0, 1001.0, 1002.0, 1003.0])


class TestThermocoupleSentinelVectorised:
    def test_plausible_values_yield_zero_mask(self) -> None:
        values = np.array([23.5, 100.0, -50.0], dtype=np.float64)
        mask = detect_thermocouple_sentinel_vectorised(values)
        np.testing.assert_array_equal(mask, np.zeros(3, dtype=np.int8))

    def test_sentinel_floats_yield_ordinals(self) -> None:
        values = np.array([-9999.0, -8888.0, -7777.0, 25.0], dtype=np.float64)
        mask = detect_thermocouple_sentinel_vectorised(values)
        np.testing.assert_array_equal(mask, np.array([1, 2, 3, 0], dtype=np.int8))

    def test_mask_dtype_is_int8(self) -> None:
        values = np.array([25.0], dtype=np.float64)
        mask = detect_thermocouple_sentinel_vectorised(values)
        assert mask.dtype == np.int8
