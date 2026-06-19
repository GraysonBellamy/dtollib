"""Tests for the synchronous block-read path: ``read_block`` / ``read_inprocess``.

These exercise the polled alternative to :func:`record` against
``FakeDtolBackend``. Hardware acceptance of the buffer-rotation startup
ordering is bench-gated (Tier E); the fake enforces the same
register → queue → arm → start invariant the DT9805/06 require.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import anyio
import numpy as np
import numpy.typing as npt
import pytest

from dtollib import (
    AnalogInputVoltage,
    BufferPlan,
    DataFlow,
    DtolCapabilityError,
    DtolTaskStateError,
    DtolTimeoutError,
    DtolValidationError,
    TaskSpec,
    Timing,
    open_device,
)
from dtollib.backend.fake import FakeBoard, FakeDtolBackend, FakeSubsystem
from dtollib.capi.constants import OLSS_AD
from dtollib.testing import make_dt9805_capabilities, make_fake_backend

if TYPE_CHECKING:
    from dtollib.tasks.models import DaqBlock
    from dtollib.tasks.session import DtolSession

pytestmark = pytest.mark.anyio


def _continuous_spec(name: str = "t") -> TaskSpec:
    return TaskSpec(
        name=name,
        board="DT9805(00)",
        channels=[
            AnalogInputVoltage(physical_channel=0, name="ch0"),
            AnalogInputVoltage(physical_channel=1, name="ch1"),
        ],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=1000.0),
        buffers=BufferPlan(buffers=4, samples_per_buffer=10),
    )


def _single_value_spec() -> TaskSpec:
    return TaskSpec(
        name="sv",
        board="DT9805(00)",
        channels=[AnalogInputVoltage(physical_channel=0, name="ch0")],
        data_flow=DataFlow.SINGLE_VALUE,
    )


async def _read_one(
    session: DtolSession,
    backend: FakeDtolBackend,
    hdass: int,
    fill: npt.NDArray[Any],
) -> DaqBlock:
    """Drive one ``read_block`` to completion, firing a synthetic buffer-done.

    Retries ``fire_buffer_done`` until it lands (it returns ``None`` while
    ``read_block`` priming has not yet queued buffers).
    """
    holder: list[DaqBlock] = []
    async with anyio.create_task_group() as tg:

        async def _run() -> None:
            holder.append(await session.read_block(10, timeout=5.0))

        _ = tg.start_soon(_run)
        for _ in range(5000):
            if backend.fire_buffer_done(hdass, fill=fill) is not None:
                break
            await anyio.sleep(0.001)
    return holder[0]


class TestReadBlock:
    async def test_returns_one_buffer(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(
            _continuous_spec(), backend=backend, autostart=False
        ) as session:
            hdass = session.hdass
            block = await _read_one(session, backend, hdass, np.arange(20, dtype=np.int16))
        assert block.data.shape == (2, 10)
        assert block.block_index == 0
        assert block.samples_per_channel == 10
        assert block.raw_codes is not None
        assert block.is_linearised  # all-AI task → volts conversion plan applied

    async def test_block_index_increments(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(
            _continuous_spec(), backend=backend, autostart=False
        ) as session:
            hdass = session.hdass
            b0 = await _read_one(session, backend, hdass, np.arange(20, dtype=np.int16))
            b1 = await _read_one(session, backend, hdass, np.arange(20, 40, dtype=np.int16))
        assert b0.block_index == 0
        assert b1.block_index == 1
        assert b1.first_sample_index == 10

    async def test_timeout_when_no_buffer_completes(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(
            _continuous_spec(), backend=backend, autostart=False
        ) as session:
            with pytest.raises(DtolTimeoutError, match="no buffer completed"):
                await session.read_block(10, timeout=0.05)

    async def test_rejects_single_value_spec(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(_single_value_spec(), backend=backend) as session:
            with pytest.raises(DtolTaskStateError, match="CONTINUOUS"):
                await session.read_block(10)

    async def test_rejects_zero_samples(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(
            _continuous_spec(), backend=backend, autostart=False
        ) as session:
            with pytest.raises(DtolValidationError, match="samples_per_channel"):
                await session.read_block(0)

    async def test_depth_mismatch_rejected(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(
            _continuous_spec(), backend=backend, autostart=False
        ) as session:
            # First call primes the pool at depth 10 (then times out, no fire).
            with pytest.raises(DtolTimeoutError):
                await session.read_block(10, timeout=0.02)
            with pytest.raises(DtolTaskStateError, match="already primed"):
                await session.read_block(20, timeout=0.02)


class TestReadInprocess:
    async def test_drains_partial_buffer(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(
            _continuous_spec(), backend=backend, autostart=False
        ) as session:
            hdass = session.hdass
            # First call primes the pool; the in-process buffer is still empty.
            assert await session.read_inprocess() is None
            # Now give the currently-filling buffer a partial payload.
            backend.set_inprocess_payload(hdass, np.arange(20, dtype=np.int16))
            block = await session.read_inprocess()
        assert block is not None
        assert block.data.shape == (2, 10)
        assert block.samples_per_channel == 10

    async def test_returns_none_when_empty(self) -> None:
        backend = make_fake_backend(include_dt9805=True)
        async with await open_device(
            _continuous_spec(), backend=backend, autostart=False
        ) as session:
            assert await session.read_inprocess() is None

    async def test_capability_gate(self) -> None:
        caps = replace(make_dt9805_capabilities(), supports_inprocess_flush=False)
        board = FakeBoard(
            name="DT9805(00)",
            model="DT9805",
            subsystems=[FakeSubsystem(type=OLSS_AD, element=0, capabilities=caps)],
        )
        backend = FakeDtolBackend([board])
        async with await open_device(
            _continuous_spec(), backend=backend, autostart=False
        ) as session:
            with pytest.raises(DtolCapabilityError, match="OLSSC_SUP_INPROCESSFLUSH"):
                await session.read_inprocess()
