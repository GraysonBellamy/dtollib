"""Hardware acceptance — synchronous block reads on the DT9805/06.

Validates the polled alternative to ``record()``: ``session.read_block()`` and
``session.read_inprocess()``. The critical bench question these answer is the
startup ordering — on the DT9805/06 the SDK only rotates buffers through the
Ready/Done queues once the notification window is wired and the second
``olDaConfig`` (``arm``) has run, *even for a polled consumer*. The read path
registers a no-op notification and arms before polling; if that ordering is
wrong on real silicon, ``read_block`` hangs (caught here by the bounded
``timeout``) the same way ``record()`` did before WS-A0.

Reads whatever sits on AI ch0/ch1 — no external stimulus required.
"""

from __future__ import annotations

import math
import os

import pytest

from dtollib import (
    AnalogInputVoltage,
    BufferPlan,
    DataFlow,
    TaskSpec,
    Timing,
    open_device,
)

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("DTOLLIB_ENABLE_HARDWARE_TESTS") != "1",
        reason="set DTOLLIB_ENABLE_HARDWARE_TESTS=1 with a DT9805/06 attached",
    ),
    pytest.mark.anyio,
]

_BOARD = os.environ.get("DTOLLIB_HW_BOARD", "DT9805(00)")
_RATE_HZ = 1000.0
_SAMPLES_PER_BUFFER = 100
_N_CHANNELS = 2


def _continuous_spec() -> TaskSpec:
    return TaskSpec(
        name="hw_read_block",
        board=_BOARD,
        channels=[
            AnalogInputVoltage(physical_channel=0, name="ch0"),
            AnalogInputVoltage(physical_channel=1, name="ch1"),
        ],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=_RATE_HZ),
        buffers=BufferPlan(buffers=4, samples_per_buffer=_SAMPLES_PER_BUFFER),
    )


async def test_read_block_returns_shaped_blocks() -> None:
    """``read_block`` primes the pool and returns sequential, correctly-shaped blocks."""
    async with await open_device(_continuous_spec(), autostart=False) as session:
        first = await session.read_block(_SAMPLES_PER_BUFFER, timeout=8.0)
        assert first.data.shape == (_N_CHANNELS, _SAMPLES_PER_BUFFER)
        assert first.block_index == 0
        assert first.is_linearised  # all-AI task → volts conversion plan applied

        second = await session.read_block(_SAMPLES_PER_BUFFER, timeout=8.0)
        assert second.block_index == 1
        assert second.first_sample_index == _SAMPLES_PER_BUFFER


async def test_read_inprocess_drains_partial_or_none() -> None:
    """``read_inprocess`` either drains a partial buffer or returns None — never hangs."""
    async with await open_device(_continuous_spec(), autostart=False) as session:
        # Prime + give the SDK a moment to begin filling the first buffer.
        block = await session.read_inprocess()
        if block is None:
            # Nothing accumulated yet on the first attempt — prime via a full
            # block, then the in-process buffer should yield partial data.
            await session.read_block(_SAMPLES_PER_BUFFER, timeout=8.0)
            block = await session.read_inprocess()
        if block is not None:
            assert block.data.shape[0] == _N_CHANNELS
            assert 0 < block.samples_per_channel <= _SAMPLES_PER_BUFFER
            assert math.isfinite(float(block.data[0, 0]))
