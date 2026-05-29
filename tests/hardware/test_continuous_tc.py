"""Hardware acceptance — continuous thermocouple linearisation (§A.4).

Proves the production ``record()`` path delivers **linearised °C** blocks on a
real DT9806, not raw codes: the drainer scales each scan's TC rows through the
NIST ITS-90 inverse, cold-junction-corrected from channel 0 carried in the scan
list (the interleaved-CJC stream is unsupported on these boards — docs/
decisions.md). Mirrors the bench rig from the single-value TC validation: K-type
TCs on ch4/ch6, CJC on ch0.

Two assertions matter: blocks arrive with ``is_linearised`` set and data in a
plausible ambient °C band, and an unconnected channel reports ``SENSOR_OPEN``.
The reference-thermometer agreement check is a maintainer step (set
``DTOLLIB_TC_REFERENCE_C`` to enable the tolerance assertion).
"""

from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING, cast

import anyio
import numpy as np
import pytest

from dtollib import (
    AnalogInputVoltage,
    BufferPlan,
    DataFlow,
    TaskSpec,
    ThermocoupleInput,
    Timing,
    open_device,
    record,
)
from dtollib.channels.analog_input import ThermocoupleType
from dtollib.tasks.models import SensorStatus

if TYPE_CHECKING:
    from dtollib.tasks.models import DaqBlock

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("DTOLLIB_ENABLE_HARDWARE_TESTS") != "1",
        reason="set DTOLLIB_ENABLE_HARDWARE_TESTS=1 with a DT9806 + K-type TCs attached",
    ),
    pytest.mark.anyio,
]

_BOARD = "DT9806(00)"
_RATE_HZ = 200.0  # aggregate A/D clock; 3 channels -> ~67 scans/s/channel
_SAMPLES_PER_BUFFER = 50
# Bench rig: K-type TCs on ch4 & ch6, CJC sensor on ch0 (10 mV/°C, gain 1).
_CONNECTED_TC = 4
_OPEN_TC = 6


def _tc_spec() -> TaskSpec:
    return TaskSpec(
        name="hw_continuous_tc",
        board=_BOARD,
        channels=[
            AnalogInputVoltage(physical_channel=0, name="cjc", gain=1.0),
            ThermocoupleInput(
                physical_channel=_CONNECTED_TC,
                name="tc_connected",
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=-50.0,
                max_val_degc=150.0,
                gain=100.0,
                cjc_channel=0,
            ),
            ThermocoupleInput(
                physical_channel=_OPEN_TC,
                name="tc_open",
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=-50.0,
                max_val_degc=150.0,
                gain=100.0,
                cjc_channel=0,
            ),
        ],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=_RATE_HZ),
        buffers=BufferPlan(buffers=4, samples_per_buffer=_SAMPLES_PER_BUFFER),
    )


async def test_continuous_tc_blocks_are_linearised_degc() -> None:
    """``record()`` yields linearised °C blocks; open TC -> SENSOR_OPEN."""
    blocks: list[DaqBlock] = []
    async with (
        await open_device(_tc_spec(), autostart=False) as session,
        record(session) as recording,
    ):
        with anyio.fail_after(10.0):
            async for block in recording.stream:
                blocks.append(block)
                if len(blocks) >= 3:
                    break

    assert blocks, "no DaqBlock arrived — continuous TC path did not flow"
    reference_c = os.environ.get("DTOLLIB_TC_REFERENCE_C")
    for block in blocks:
        assert block.is_linearised, "drainer did not linearise — data is raw codes"
        assert block.data.shape == (3, _SAMPLES_PER_BUFFER)
        # Row 0 = CJC ambient °C; row 1 = connected TC; row 2 = open TC.
        ambient = cast("float", np.nanmean(block.data[0]))
        assert 0.0 < ambient < 50.0, f"CJC ambient {ambient} °C implausible"

        connected = block.data[1]
        finite = connected[np.isfinite(connected)]
        assert finite.size, "connected TC produced no finite samples"
        assert np.all((finite >= -50.0) & (finite <= 150.0))
        if reference_c is not None:
            # Maintainer step: agreement with a reference thermometer within
            # K-type tolerance (±2.2 °C class 2) plus CJC slack.
            assert np.all(np.abs(finite - float(reference_c)) <= 3.0)

        # The unconnected TC pegs the rail -> SENSOR_OPEN + NaN.
        open_mask = block.sensor_status.get("tc_open")
        assert open_mask is not None
        assert np.any(open_mask == int(_SENSOR_OPEN_ORDINAL))
        assert np.all(np.isnan(block.data[2][open_mask == int(_SENSOR_OPEN_ORDINAL)]))


# SensorStatus ordinal for SENSOR_OPEN (declaration order: OK=0, OPEN=1, ...).
_SENSOR_OPEN_ORDINAL = list(SensorStatus).index(SensorStatus.SENSOR_OPEN)


def test_sensor_open_ordinal_is_one() -> None:
    """Guard the mask encoding the drainer relies on (OK=0, SENSOR_OPEN=1)."""
    assert _SENSOR_OPEN_ORDINAL == 1
    assert not math.isnan(_SENSOR_OPEN_ORDINAL)
