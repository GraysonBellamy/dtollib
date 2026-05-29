"""Tests for the §12.3.2 callback bridge against ``FakeDtolBackend``."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anyio
import numpy as np
import pytest

from dtollib import (
    BufferPlan,
    DtolBufferOverrunError,
    DtolTriggerError,
    SdkEventKind,
)
from dtollib.backend._buffer_pool import BufferPool
from dtollib.backend._callback_bridge import BridgeConfig, callback_bridge
from dtollib.capi.constants import OL_DF_CONTINUOUS, OLSS_AD
from dtollib.streaming._types import ErrorPolicy, OverflowPolicy
from dtollib.testing import make_fake_backend

if TYPE_CHECKING:
    from dtollib.backend.fake import FakeDtolBackend


def _setup_bridge(
    *,
    n_channels: int = 2,
    samples_per_buffer: int = 10,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
    overflow_policy: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
    stream_buffer_size: int = 16,
) -> tuple[FakeDtolBackend, int, BufferPool, BridgeConfig]:
    backend = make_fake_backend(include_dt9805=True)
    hdrvr = backend.initialize("DT9805(00)")
    hdass = backend.get_dass(hdrvr, OLSS_AD, 0)
    backend.set_data_flow(hdass, OL_DF_CONTINUOUS)
    # Config #1 (olDaConfig) runs before the notification window is wired,
    # matching record()'s bench-proven ordering. The tests below enter the
    # bridge (register), queue, then arm (config #2), then start.
    backend.commit(hdass)
    pool = BufferPool(
        backend,
        hdass,
        BufferPlan(buffers=4, samples_per_buffer=samples_per_buffer),
        n_channels=n_channels,
    )
    pool.allocate()
    config = BridgeConfig(
        device="dev",
        task="t",
        channels=tuple(f"ch{i}" for i in range(n_channels)),
        sample_rate_hz=1000.0,
        task_started_at=datetime.now(UTC),
        task_started_mono_ns=time.monotonic_ns(),
        units={f"ch{i}": "V" for i in range(n_channels)},
        error_policy=error_policy,
        overflow_policy=overflow_policy,
        stream_buffer_size=stream_buffer_size,
    )
    return backend, hdass, pool, config


class TestCallbackBridgeHappyPath:
    @pytest.mark.anyio
    async def test_buffer_done_produces_block(self) -> None:
        backend, hdass, pool, config = _setup_bridge()
        async with callback_bridge(backend, hdass, pool, config) as (rx, summary):
            # bridge has registered notification; now queue + arm + start.
            pool.queue_all()
            backend.arm(hdass)
            backend.start(hdass)
            # Synthesise one BUFFER_DONE.
            backend.fire_buffer_done(hdass, fill=np.arange(20, dtype=np.int16))
            block = await rx.receive()
            assert block.data.shape == (2, 10)
            assert block.block_index == 0
            assert summary.payloads_emitted == 1

    @pytest.mark.anyio
    async def test_multiple_buffers_yield_blocks_in_order(self) -> None:
        backend, hdass, pool, config = _setup_bridge()
        async with callback_bridge(backend, hdass, pool, config) as (rx, summary):
            pool.queue_all()
            backend.arm(hdass)
            backend.start(hdass)
            for i in range(3):
                fill = np.full(20, i, dtype=np.int16)
                backend.fire_buffer_done(hdass, fill=fill)
            blocks = [await rx.receive() for _ in range(3)]
            assert [b.block_index for b in blocks] == [0, 1, 2]
            assert [b.first_sample_index for b in blocks] == [0, 10, 20]
            assert summary.payloads_emitted == 3


class TestCallbackBridgeErrorPolicies:
    @pytest.mark.anyio
    async def test_raise_propagates_overrun(self) -> None:
        backend, hdass, pool, config = _setup_bridge(error_policy=ErrorPolicy.RAISE)

        async def run_bridge_until_error() -> None:
            async with callback_bridge(backend, hdass, pool, config) as (rx, _):
                pool.queue_all()
                backend.arm(hdass)
                backend.start(hdass)
                backend.fire_event(hdass, SdkEventKind.OVERRUN_ERROR)
                with anyio.move_on_after(0.5):
                    async for _ in rx:
                        pass

        # ErrorPolicy.RAISE surfaces the SDK error as a clean, direct
        # exception — NOT wrapped in a task-group BaseExceptionGroup. The
        # bridge captures it in the drainer, runs the ordered shutdown, then
        # re-raises (the pre-fix code raised inside the task group, which
        # both wrapped the exception and raced the teardown into a
        # segfault/deadlock on real hardware under sustained overrun).
        with pytest.raises(DtolBufferOverrunError):
            await run_bridge_until_error()

    @pytest.mark.anyio
    async def test_return_emits_error_block(self) -> None:
        backend, hdass, pool, config = _setup_bridge(error_policy=ErrorPolicy.RETURN)
        async with callback_bridge(backend, hdass, pool, config) as (rx, summary):
            pool.queue_all()
            backend.arm(hdass)
            backend.start(hdass)
            backend.fire_event(hdass, SdkEventKind.OVERRUN_ERROR)
            block = await rx.receive()
            assert block.error is not None
            assert isinstance(block.error, DtolBufferOverrunError)
            assert np.all(block.data == 0.0)
            assert summary.overruns_observed == 1

    @pytest.mark.anyio
    async def test_skip_increments_counter_only(self) -> None:
        backend, hdass, pool, config = _setup_bridge(error_policy=ErrorPolicy.SKIP)
        async with callback_bridge(backend, hdass, pool, config) as (rx, summary):
            pool.queue_all()
            backend.arm(hdass)
            backend.start(hdass)
            backend.fire_event(hdass, SdkEventKind.OVERRUN_ERROR)
            # Send a follow-up valid block to confirm the bridge keeps running.
            backend.fire_buffer_done(hdass, fill=np.zeros(20, dtype=np.int16))
            block = await rx.receive()
            assert block.error is None
            assert summary.overruns_observed == 1
            assert summary.payloads_emitted == 1

    @pytest.mark.anyio
    async def test_trigger_error_routes(self) -> None:
        backend, hdass, pool, config = _setup_bridge(error_policy=ErrorPolicy.RETURN)
        async with callback_bridge(backend, hdass, pool, config) as (rx, summary):
            pool.queue_all()
            backend.arm(hdass)
            backend.start(hdass)
            backend.fire_event(hdass, SdkEventKind.TRIGGER_ERROR)
            block = await rx.receive()
            assert isinstance(block.error, DtolTriggerError)
            assert summary.errors_observed == 1


class TestCallbackBridgeEndOfRun:
    @pytest.mark.anyio
    async def test_queue_done_closes_stream(self) -> None:
        backend, hdass, pool, config = _setup_bridge()
        async with callback_bridge(backend, hdass, pool, config) as (rx, _):
            pool.queue_all()
            backend.arm(hdass)
            backend.start(hdass)
            backend.fire_event(hdass, SdkEventKind.QUEUE_DONE)
            # Stream closes; receiving raises EndOfStream.
            with pytest.raises(anyio.EndOfStream):
                with anyio.fail_after(1.0):
                    await rx.receive()


class TestCallbackBridgeShutdown:
    @pytest.mark.anyio
    async def test_shutdown_completes_cleanly(self) -> None:
        backend, hdass, pool, config = _setup_bridge()
        async with callback_bridge(backend, hdass, pool, config) as (rx, _):
            pool.queue_all()
            backend.arm(hdass)
            backend.start(hdass)
            backend.fire_buffer_done(hdass, fill=np.zeros(20, dtype=np.int16))
            block = await rx.receive()
            assert block is not None
        # Bridge exit succeeded: backend state is no longer RUNNING; pool can be freed.
        pool.free_all()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
