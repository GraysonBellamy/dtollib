"""Continuous thermocouple linearisation through ``record()`` (§A.2).

End-to-end over ``FakeDtolBackend`` configured as a DT9805/06 A/D
(``returns_floats=False``, ``supports_thermocouples=True``): a continuous TC
task must yield ``DaqBlock``s whose ``data`` is °C (not raw codes), CJC-
corrected from the cold-junction channel carried in the scan list, with
``is_linearised`` set. Also locks the fail-loud contract when the CJC channel
is omitted, and the reject-ch-as-its-own-CJC guard.
"""

from __future__ import annotations

import numpy as np
import pytest

from dtollib import (
    AnalogInputVoltage,
    BufferPlan,
    DataFlow,
    DtolTaskStateError,
    TaskSpec,
    ThermocoupleInput,
    Timing,
    open_device,
    record,
)
from dtollib.capi.conversion import code_to_input_volts
from dtollib.channels.analog_input import ThermocoupleType
from dtollib.testing import make_fake_backend
from dtollib.utils import convert_volts_to_temperature

_RES = 16


def _code(input_volts: float, gain: float) -> int:
    adc = input_volts * gain
    return round((adc - (-10.0)) / 20.0 * (1 << _RES))


def _tc_spec() -> TaskSpec:
    return TaskSpec(
        name="tc",
        channels=[
            # CJC sensor on ch0, unity gain — required in the scan list.
            AnalogInputVoltage(physical_channel=0, name="cjc", gain=1.0),
            ThermocoupleInput(
                physical_channel=4,
                name="tc4",
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=-200.0,
                max_val_degc=1300.0,
                gain=100.0,
                cjc_channel=0,
            ),
        ],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=1000.0),
        buffers=BufferPlan(buffers=4, samples_per_buffer=5),
    )


@pytest.mark.anyio
async def test_continuous_tc_block_is_linearised_degc() -> None:
    backend = make_fake_backend(include_dt9805=True)
    cjc_volts = 0.25  # -> 25 °C at 10 mV/°C
    emf_volts = 0.004  # ~4 mV thermo-emf
    cjc_code = _code(cjc_volts, 1.0)
    tc_code = _code(emf_volts, 100.0)
    # Scan-major fill: 5 scans of (cjc, tc).
    fill = np.array([cjc_code, tc_code] * 5, dtype=np.uint16)

    async with (
        await open_device(_tc_spec(), backend=backend, autostart=False) as session,
        record(session) as recording,
    ):
        backend.fire_buffer_done(session.raw_hdass, fill=fill)
        block = await recording.stream.receive()

    assert block.is_linearised
    assert block.data.shape == (2, 5)
    # Raw codes preserved for replay; cjc stream populated.
    assert block.raw_codes is not None
    assert block.cjc_data is not None

    # Row 0 (CJC) emitted as ambient °C.
    cjc_actual = code_to_input_volts(cjc_code, 1.0, vmin=-10.0, vmax=10.0, resolution_bits=_RES)
    np.testing.assert_allclose(block.data[0], cjc_actual / 0.010, atol=0.1)

    # Row 1 (TC) matches the scalar ITS-90 path with the same round-tripped emf.
    emf_actual = code_to_input_volts(tc_code, 100.0, vmin=-10.0, vmax=10.0, resolution_bits=_RES)
    expected = convert_volts_to_temperature("K", emf_actual, cjc_temperature_c=cjc_actual / 0.010)
    np.testing.assert_allclose(block.data[1], expected, atol=1e-6)
    # All samples OK — no sensor_status overlay entries with non-zero mask.
    assert all(np.all(mask == 0) for mask in block.sensor_status.values())


@pytest.mark.anyio
async def test_continuous_tc_requires_cjc_channel_in_scan_list() -> None:
    """Omitting the CJC channel fails loud rather than silently mis-reading."""
    backend = make_fake_backend(include_dt9805=True)
    spec = TaskSpec(
        name="tc_no_cjc",
        channels=[
            ThermocoupleInput(
                physical_channel=4,
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=-200.0,
                max_val_degc=1300.0,
                cjc_channel=0,
            ),
        ],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=1000.0),
        buffers=BufferPlan(buffers=4, samples_per_buffer=5),
    )
    async with await open_device(spec, backend=backend, autostart=False) as session:
        with pytest.raises(DtolTaskStateError, match="cold-junction channel"):
            async with record(session):
                pass


@pytest.mark.anyio
async def test_continuous_tc_rejects_tc_on_cjc_channel() -> None:
    """A TC sitting on its own CJC channel is rejected at record() time."""
    backend = make_fake_backend(include_dt9805=True)
    spec = TaskSpec(
        name="tc_on_cjc",
        channels=[
            ThermocoupleInput(
                physical_channel=0,
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=-200.0,
                max_val_degc=1300.0,
                cjc_channel=0,
            ),
        ],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=1000.0),
        buffers=BufferPlan(buffers=4, samples_per_buffer=5),
    )
    async with await open_device(spec, backend=backend, autostart=False) as session:
        with pytest.raises(DtolTaskStateError, match="cold-junction"):
            async with record(session):
                pass
