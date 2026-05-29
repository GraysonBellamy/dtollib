"""Continuous startup ordering — the bench-proven two-config sequence.

``record()`` must drive the DT9805/06 continuous sequence in the exact order
that delivers buffer-done events on hardware (docs/decisions.md):

    commit (olDaConfig #1) → register_notification (olDaSetWndHandle) →
    queue buffers → arm (olDaConfig #2) → start

and ``set_dma_usage`` must be called even on a subsystem that reports zero
DMA channels (the DT9805/06 report NUMDMACHANS==0 yet still need the call).
"""

from __future__ import annotations

import numpy as np
import pytest

from dtollib import (
    AnalogInputVoltage,
    BufferPlan,
    DataFlow,
    TaskSpec,
    Timing,
    open_device,
    record,
)
from dtollib.testing import make_fake_backend


def _continuous_spec() -> TaskSpec:
    return TaskSpec(
        name="t",
        channels=[
            AnalogInputVoltage(physical_channel=0, name="ch0"),
            AnalogInputVoltage(physical_channel=1, name="ch1"),
        ],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=1000.0),
        buffers=BufferPlan(buffers=4, samples_per_buffer=10),
    )


def _first_index(ops: list[str], name: str) -> int:
    return ops.index(name)


class TestContinuousStartupOrdering:
    @pytest.mark.anyio
    async def test_record_drives_two_config_sequence(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with (
            await open_device(_continuous_spec(), backend=backend, autostart=False) as session,
            record(session) as recording,
        ):
            hdass = session.raw_hdass
            backend.fire_buffer_done(hdass, fill=np.arange(20, dtype=np.int16))
            await recording.stream.receive()

        ops = [name for name, _payload in backend.operations]
        # The defining ordering: commit (#1) → register → queue → arm (#2) → start.
        assert (
            _first_index(ops, "commit")
            < _first_index(ops, "register_notification")
            < _first_index(ops, "put_buffer")
            < _first_index(ops, "arm")
            < _first_index(ops, "start")
        )

    @pytest.mark.anyio
    async def test_set_dma_usage_called_even_with_zero_dma_channels(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(
            _continuous_spec(), backend=backend, autostart=False
        ) as session:
            # The DT9805 reports NUMDMACHANS == 0, so set_dma_usage must still
            # be called with 0 (the SDK requires the call regardless).
            dma_calls = [payload for name, payload in backend.operations if name == "set_dma_usage"]
            assert dma_calls, "set_dma_usage was not called for a continuous task"
            hdass = session.raw_hdass
            assert (hdass, 0) in dma_calls


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
