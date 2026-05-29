"""Unit tests for the continuous block-path conversion kernel (§A.2).

Exercises :func:`dtollib.capi.conversion.linearise_block` — the vectorised
counterpart of the single-value app-side TC path. Pure NumPy; no SDK, no
hardware. Verifies the wiring (CJC sourcing from a scan row, voltage vs TC
row dispatch, open-circuit + envelope masking), not the NIST polynomial
itself (that lives in test_utils.py).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from dtollib.capi.conversion import BlockConversion, Encoding, linearise_block
from dtollib.utils import convert_volts_to_temperature

# Offset-binary 16-bit on a ±10 V bipolar range — the DT9805/06 A/D.
_RES_BITS = 16
_MIDSCALE = 1 << (_RES_BITS - 1)  # 32768 -> 0 V
_RANGE = (-10.0, 10.0)


def _code_for_input_volts(input_volts: float, gain: float) -> int:
    """Inverse of code_to_input_volts for offset-binary: build a test code."""
    adc_volts = input_volts * gain
    full = 1 << _RES_BITS
    code = round((adc_volts - _RANGE[0]) / (_RANGE[1] - _RANGE[0]) * full)
    return int(np.clip(code, 0, full - 1))


def test_voltage_rows_convert_to_volts() -> None:
    """A plain voltage row scales code -> input volts; no masks emitted."""
    code = _code_for_input_volts(2.5, 1.0)
    codes = np.array([[code, code, code]], dtype=np.int32)
    plan = BlockConversion(
        encoding=Encoding.OFFSET_BINARY,
        resolution_bits=_RES_BITS,
        ranges=(_RANGE,),
        gains=(1.0,),
    )
    data, cjc, masks = linearise_block(codes, plan)

    assert data.shape == (1, 3)
    assert cjc is None
    assert masks == {}
    np.testing.assert_allclose(data[0], 2.5, atol=2e-3)


def test_cjc_row_emitted_as_degc() -> None:
    """The CJC row (10 mV/°C) surfaces as ambient °C, not raw volts."""
    # 0.25 V on the CJC sensor at unity gain == 25 °C.
    cjc_code = _code_for_input_volts(0.25, 1.0)
    codes = np.array([[cjc_code, cjc_code]], dtype=np.int32)
    plan = BlockConversion(
        encoding=Encoding.OFFSET_BINARY,
        resolution_bits=_RES_BITS,
        ranges=(_RANGE,),
        gains=(1.0,),
        tc_types=(None,),
        tc_envelopes=(None,),
        cjc_row=0,
        cjc_volts_per_degc=0.010,
    )
    data, cjc, _masks = linearise_block(codes, plan)

    assert cjc is not None
    assert cjc.shape == (1, 2)
    np.testing.assert_allclose(data[0], 25.0, atol=0.1)
    np.testing.assert_allclose(cjc[0], 25.0, atol=0.1)


def test_thermocouple_row_matches_scalar_path() -> None:
    """A K-type TC row equals the scalar ITS-90 path, CJC-corrected from row 0."""
    cjc_degc = 25.0
    emf_volts = 0.004  # ~4 mV differential thermo-emf
    cjc_code = _code_for_input_volts(cjc_degc * 0.010, 1.0)
    tc_code = _code_for_input_volts(emf_volts, 100.0)
    # Row 0 = CJC (gain 1); row 1 = K-type TC (gain 100).
    codes = np.array([[cjc_code, cjc_code], [tc_code, tc_code]], dtype=np.int32)
    plan = BlockConversion(
        encoding=Encoding.OFFSET_BINARY,
        resolution_bits=_RES_BITS,
        ranges=(_RANGE, _RANGE),
        gains=(1.0, 100.0),
        tc_types=(None, "K"),
        tc_envelopes=(None, (-200.0, 1300.0)),
        cjc_row=0,
        cjc_volts_per_degc=0.010,
    )
    data, _cjc, masks = linearise_block(codes, plan)

    expected = convert_volts_to_temperature("K", emf_volts, cjc_temperature_c=cjc_degc)
    # Loose tolerance: the code round-trip quantises emf at ~3 µV/LSB.
    np.testing.assert_allclose(data[1], expected, atol=0.5)
    assert 1 in masks
    assert np.all(masks[1] == 0)  # all OK


def test_open_circuit_flags_sensor_open_and_nan() -> None:
    """A rail-pegged TC emf becomes SENSOR_OPEN (mask==1) + NaN."""
    cjc_code = _code_for_input_volts(0.25, 1.0)
    open_code = (1 << _RES_BITS) - 1  # positive full scale
    codes = np.array([[cjc_code], [open_code]], dtype=np.int32)
    plan = BlockConversion(
        encoding=Encoding.OFFSET_BINARY,
        resolution_bits=_RES_BITS,
        ranges=(_RANGE, _RANGE),
        gains=(1.0, 100.0),
        tc_types=(None, "K"),
        tc_envelopes=(None, (-200.0, 1300.0)),
        cjc_row=0,
    )
    data, _cjc, masks = linearise_block(codes, plan)

    assert masks[1][0] == 1  # SENSOR_OPEN
    assert math.isnan(data[1][0])


def test_out_of_range_high_masked() -> None:
    """A TC reading above its envelope becomes TEMP_OUT_OF_RANGE_HIGH + NaN."""
    cjc_code = _code_for_input_volts(0.25, 1.0)
    # ~6 mV at gain 100 with a tiny envelope forces an over-range verdict
    # without pegging the rail (so SENSOR_OPEN does not pre-empt it).
    tc_code = _code_for_input_volts(0.006, 100.0)
    codes = np.array([[cjc_code], [tc_code]], dtype=np.int32)
    plan = BlockConversion(
        encoding=Encoding.OFFSET_BINARY,
        resolution_bits=_RES_BITS,
        ranges=(_RANGE, _RANGE),
        gains=(1.0, 100.0),
        tc_types=(None, "K"),
        tc_envelopes=(None, (-10.0, 40.0)),
        cjc_row=0,
    )
    data, _cjc, masks = linearise_block(codes, plan)

    assert masks[1][0] == 3  # TEMP_OUT_OF_RANGE_HIGH
    assert math.isnan(data[1][0])


def test_has_thermocouples_property() -> None:
    """``has_thermocouples`` reflects whether any row needs linearisation."""
    voltage_only = BlockConversion(
        encoding=Encoding.OFFSET_BINARY,
        resolution_bits=_RES_BITS,
        ranges=(_RANGE,),
        gains=(1.0,),
    )
    assert not voltage_only.has_thermocouples
    with_tc = BlockConversion(
        encoding=Encoding.OFFSET_BINARY,
        resolution_bits=_RES_BITS,
        ranges=(_RANGE, _RANGE),
        gains=(1.0, 100.0),
        tc_types=(None, "K"),
        tc_envelopes=(None, (-200.0, 1300.0)),
        cjc_row=0,
    )
    assert with_tc.has_thermocouples


def test_tc_types_length_mismatch_raises() -> None:
    """A plan whose tc_types disagrees with the code shape is rejected."""
    codes = np.zeros((2, 4), dtype=np.int32)
    plan = BlockConversion(
        encoding=Encoding.OFFSET_BINARY,
        resolution_bits=_RES_BITS,
        ranges=(_RANGE, _RANGE),
        gains=(1.0, 100.0),
        tc_types=("K",),  # length 1, not 2
        tc_envelopes=((-200.0, 1300.0),),
    )
    with pytest.raises(ValueError, match="tc_types"):
        linearise_block(codes, plan)
