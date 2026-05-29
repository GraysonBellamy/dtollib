"""Tests for the output (fill) mode of :class:`BufferPool` (WS-AO / A1).

Exercise the Fill-before-Queue invariant, the fill → queue → done → refill →
free cycle, and the round-trip of filled bytes through the fake's
``copy_to_buffer`` / ``copy_buffer``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from dtollib import BufferPlan, DtolTaskStateError
from dtollib.backend._buffer_pool import BufferDirection, BufferPool
from dtollib.capi.constants import OL_DF_CONTINUOUS, OLSS_DA
from dtollib.tasks.models import BufferState
from dtollib.testing import make_fake_backend

if TYPE_CHECKING:
    from dtollib.backend.fake import FakeDtolBackend


def _open_da() -> tuple[FakeDtolBackend, int]:
    backend = make_fake_backend(include_dt9806=True)
    hdrvr = backend.initialize("DT9806(00)")
    hdass = backend.get_dass(hdrvr, OLSS_DA, 0)
    backend.set_data_flow(hdass, OL_DF_CONTINUOUS)
    backend.commit(hdass)
    return backend, hdass


def _chunk(value: int, n: int) -> bytes:
    return np.full(n, value, dtype=np.int16).tobytes()


def _notify_callback(_msg_id: int, _wparam: int, _lparam: int) -> int:
    return 0


def _output_pool(
    *, buffers: int = 3, samples_per_buffer: int = 4, n_channels: int = 1
) -> tuple[FakeDtolBackend, int, BufferPool]:
    backend, hdass = _open_da()
    pool = BufferPool(
        backend,
        hdass,
        BufferPlan(buffers=buffers, samples_per_buffer=samples_per_buffer),
        n_channels=n_channels,
        direction=BufferDirection.OUTPUT,
    )
    pool.allocate()
    return backend, hdass, pool


class TestFillBeforeQueue:
    def test_queue_all_before_fill_raises(self) -> None:
        _backend, _hdass, pool = _output_pool()
        with pytest.raises(DtolTaskStateError, match="Fill-before-Queue"):
            pool.queue_all()

    def test_seed_then_queue_marks_queued(self) -> None:
        _backend, _hdass, pool = _output_pool(samples_per_buffer=4)
        pool.seed_all([_chunk(i, 4) for i in range(3)])
        assert all(raw.state == BufferState.FILLED for raw in pool.buffers)
        pool.queue_all()
        assert all(raw.state == BufferState.QUEUED for raw in pool.buffers)

    def test_seed_all_wrong_count_raises(self) -> None:
        _backend, _hdass, pool = _output_pool()
        with pytest.raises(DtolTaskStateError, match="one chunk per HBUF"):
            pool.seed_all([_chunk(0, 4), _chunk(1, 4)])  # 2 chunks, 3 buffers

    def test_fill_on_input_pool_raises(self) -> None:
        backend, hdass = _open_da()
        pool = BufferPool(
            backend,
            hdass,
            BufferPlan(buffers=3, samples_per_buffer=4),
            n_channels=1,
            direction=BufferDirection.INPUT,
        )
        pool.allocate()
        with pytest.raises(DtolTaskStateError, match="INPUT pool"):
            pool.fill(pool.buffers[0], _chunk(0, 4))


class TestRefillCycle:
    def test_fill_queue_done_refill_free(self) -> None:
        backend, hdass, pool = _output_pool(samples_per_buffer=4)
        pool.seed_all([_chunk(i, 4) for i in range(3)])
        pool.queue_all()

        # Simulate the SDK emitting the head buffer.
        backend.register_notification(hdass, _notify_callback)
        moved = backend.fire_buffer_done(hdass)
        assert moved is not None

        raw = pool.get_done()
        assert raw is not None
        assert raw.hbuf == moved
        assert raw.state == BufferState.COMPLETED

        # Refill it with new data, requeue, then tear down.
        pool.fill(raw, _chunk(99, 4))
        assert raw.state.value == BufferState.FILLED.value
        pool.requeue(raw)
        assert raw.state.value == BufferState.QUEUED.value

        pool.flush()
        pool.free_all()
        assert all(raw.state == BufferState.RELEASED for raw in pool.buffers)

    def test_requeue_without_refill_raises(self) -> None:
        backend, hdass, pool = _output_pool(samples_per_buffer=4)
        pool.seed_all([_chunk(i, 4) for i in range(3)])
        pool.queue_all()
        backend.register_notification(hdass, _notify_callback)
        backend.fire_buffer_done(hdass)
        raw = pool.get_done()
        assert raw is not None
        with pytest.raises(DtolTaskStateError, match="Fill-before-Queue"):
            pool.requeue(raw)  # COMPLETED, never refilled

    def test_free_all_refuses_inprocess(self) -> None:
        _backend, _hdass, pool = _output_pool()
        pool.seed_all([_chunk(i, 4) for i in range(3)])
        pool.queue_all()
        pool.mark_inprocess(pool.buffers[0])
        with pytest.raises(DtolTaskStateError, match="INPROCESS"):
            pool.free_all()


class TestRoundTrip:
    def test_filled_bytes_round_trip_through_fake(self) -> None:
        backend, _hdass, pool = _output_pool(samples_per_buffer=4)
        payload = np.array([0, 32768, 65535, 12345], dtype=np.uint16).astype(np.int16)
        data = payload.tobytes()
        pool.fill(pool.buffers[0], data)
        out = backend.copy_buffer(pool.buffers[0].hbuf, 4, 2)
        assert out == data


class TestFakeFillBeforeQueueInvariant:
    def test_put_buffer_on_unfilled_da_hbuf_raises(self) -> None:
        backend, hdass = _open_da()
        hbuf = backend.alloc_buffer(4, 2)
        with pytest.raises(DtolTaskStateError, match="Fill-before-Queue"):
            backend.put_buffer(hdass, hbuf)

    def test_put_buffer_after_fill_succeeds(self) -> None:
        backend, hdass = _open_da()
        hbuf = backend.alloc_buffer(4, 2)
        backend.copy_to_buffer(hbuf, _chunk(7, 4), 4)
        backend.put_buffer(hdass, hbuf)  # filled — no raise
