"""Underrun routing for :func:`dtollib.streaming.play` (WS-AO / A2).

``OLDA_WM_UNDERRUN_ERROR`` → :class:`DtolBufferUnderrunError`, routed by
``ErrorPolicy`` exactly as the input bridge routes overrun: ``RAISE`` surfaces
a clean exception after the shielded teardown; ``RETURN`` / ``SKIP`` log + count
and keep streaming.
"""

from __future__ import annotations

import anyio
import numpy as np
import pytest

from dtollib import (
    AnalogOutputVoltage,
    BufferPlan,
    DataFlow,
    DtolBufferUnderrunError,
    ErrorPolicy,
    SdkEventKind,
    SubsystemType,
    TaskSpec,
    Timing,
    WrapMode,
    open_device,
    play,
)
from dtollib.testing import make_fake_backend

pytestmark = pytest.mark.anyio


def _ao_single_spec() -> TaskSpec:
    return TaskSpec(
        name="play-underrun",
        board="DT9806(00)",
        subsystem_type=SubsystemType.ANALOG_OUTPUT,
        channels=[AnalogOutputVoltage(physical_channel=0, name="ao0", requires_confirm=False)],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=1000.0),
        buffers=BufferPlan(buffers=3, samples_per_buffer=2, wrap_mode=WrapMode.SINGLE),
    )


# The fake fires synchronously; a short settle lets the drainer route the
# error before the block exits.
_SETTLE_S = 0.1


async def test_underrun_raise_surfaces_clean_exception() -> None:
    backend = make_fake_backend(include_dt9806=True)
    session = await open_device(_ao_single_spec(), backend=backend, autostart=False)
    source = np.zeros(6)

    async def run() -> None:
        async with play(session, source, error_policy=ErrorPolicy.RAISE):
            backend.fire_event(session.hdass, SdkEventKind.UNDERRUN_ERROR)
            await anyio.sleep(_SETTLE_S)  # let the drainer capture before exit

    try:
        with pytest.raises(DtolBufferUnderrunError):
            await run()
    finally:
        await session.close()


@pytest.mark.parametrize("policy", [ErrorPolicy.RETURN, ErrorPolicy.SKIP])
async def test_underrun_non_raise_counts_and_continues(policy: ErrorPolicy) -> None:
    backend = make_fake_backend(include_dt9806=True)
    session = await open_device(_ao_single_spec(), backend=backend, autostart=False)
    source = np.zeros(6)
    try:
        async with play(session, source, error_policy=policy) as summary:
            backend.fire_event(session.hdass, SdkEventKind.UNDERRUN_ERROR)
            await anyio.sleep(_SETTLE_S)
        assert summary.underruns_observed >= 1
    finally:
        await session.close()
