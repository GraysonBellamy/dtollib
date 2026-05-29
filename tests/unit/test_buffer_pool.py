"""Tests for :class:`dtollib.backend._buffer_pool.BufferPool`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from dtollib import BufferPlan, DtolTaskStateError, WrapMode
from dtollib.backend._buffer_pool import BufferPool
from dtollib.capi.constants import OLSS_AD
from dtollib.tasks.models import BufferState
from dtollib.testing import make_fake_backend

if TYPE_CHECKING:
    from dtollib.backend.fake import FakeDtolBackend


def _open_fake_subsystem() -> tuple[FakeDtolBackend, int]:
    backend = make_fake_backend(include_dt9805=True)
    hdrvr = backend.initialize("DT9805(00)")
    hdass = backend.get_dass(hdrvr, OLSS_AD, 0)
    return backend, hdass


class TestBufferPoolAllocate:
    def test_allocate_creates_n_buffers(self) -> None:
        backend, hdass = _open_fake_subsystem()
        plan = BufferPlan(buffers=4, samples_per_buffer=100)
        pool = BufferPool(backend, hdass, plan, n_channels=2)
        pool.allocate()
        assert len(pool.buffers) == 4
        for raw in pool.buffers:
            assert raw.state == BufferState.IDLE
            assert raw.capacity_samples == 100 * 2

    def test_allocate_rejects_second_call(self) -> None:
        backend, hdass = _open_fake_subsystem()
        pool = BufferPool(backend, hdass, BufferPlan(samples_per_buffer=100), n_channels=1)
        pool.allocate()
        with pytest.raises(DtolTaskStateError, match="already ran"):
            pool.allocate()


class TestBufferPoolQueueAll:
    def test_queue_all_pushes_every_buffer(self) -> None:
        backend, hdass = _open_fake_subsystem()
        pool = BufferPool(backend, hdass, BufferPlan(samples_per_buffer=100), n_channels=1)
        pool.allocate()
        pool.queue_all()
        for raw in pool.buffers:
            assert raw.state == BufferState.QUEUED
        assert backend.get_queue_size(hdass, 0) == 4

    def test_queue_all_one_shot(self) -> None:
        backend, hdass = _open_fake_subsystem()
        pool = BufferPool(backend, hdass, BufferPlan(samples_per_buffer=100), n_channels=1)
        pool.allocate()
        pool.queue_all()
        with pytest.raises(DtolTaskStateError, match="already ran"):
            pool.queue_all()

    def test_queue_all_before_allocate_rejected(self) -> None:
        backend, hdass = _open_fake_subsystem()
        pool = BufferPool(backend, hdass, BufferPlan(samples_per_buffer=100), n_channels=1)
        with pytest.raises(DtolTaskStateError, match="before allocate"):
            pool.queue_all()


class TestBufferPoolGetDone:
    def test_get_done_returns_completed_buffer(self) -> None:
        backend, hdass = _open_fake_subsystem()
        pool = BufferPool(backend, hdass, BufferPlan(samples_per_buffer=100), n_channels=1)
        pool.allocate()
        # fire_buffer_done requires a notification registered — register a
        # no-op callback to satisfy that invariant for this isolated pool test.
        backend.register_notification(hdass, lambda msg, w, _lparam: 0)
        pool.queue_all()
        # Synthesise a completed buffer.
        payload = np.arange(100, dtype=np.int16)
        backend.fire_buffer_done(hdass, fill=payload)
        raw = pool.get_done()
        assert raw is not None
        assert raw.state == BufferState.COMPLETED
        assert raw.valid_samples == 100

    def test_get_done_returns_none_when_empty(self) -> None:
        backend, hdass = _open_fake_subsystem()
        pool = BufferPool(backend, hdass, BufferPlan(samples_per_buffer=100), n_channels=1)
        pool.allocate()
        pool.queue_all()
        assert pool.get_done() is None


class TestBufferPoolRequeue:
    def test_requeue_moves_back_to_queued(self) -> None:
        backend, hdass = _open_fake_subsystem()
        pool = BufferPool(backend, hdass, BufferPlan(samples_per_buffer=100), n_channels=1)
        pool.allocate()
        backend.register_notification(hdass, lambda msg, w, _lparam: 0)
        pool.queue_all()
        backend.fire_buffer_done(hdass, fill=np.zeros(100, dtype=np.int16))
        raw = pool.get_done()
        assert raw is not None
        pool.requeue(raw)
        assert raw.state == BufferState.QUEUED

    def test_requeue_released_buffer_rejected(self) -> None:
        backend, hdass = _open_fake_subsystem()
        pool = BufferPool(backend, hdass, BufferPlan(samples_per_buffer=100), n_channels=1)
        pool.allocate()
        raw = pool.buffers[0]
        raw.state = BufferState.RELEASED
        with pytest.raises(DtolTaskStateError, match="RELEASED"):
            pool.requeue(raw)


class TestBufferPoolFreeAll:
    def test_free_all_releases_every_buffer(self) -> None:
        backend, hdass = _open_fake_subsystem()
        pool = BufferPool(backend, hdass, BufferPlan(samples_per_buffer=100), n_channels=1)
        pool.allocate()
        pool.free_all()
        for raw in pool.buffers:
            assert raw.state == BufferState.RELEASED

    def test_free_all_refuses_while_inprocess(self) -> None:
        backend, hdass = _open_fake_subsystem()
        pool = BufferPool(backend, hdass, BufferPlan(samples_per_buffer=100), n_channels=1)
        pool.allocate()
        # Force one into INPROCESS state.
        pool.buffers[0].state = BufferState.INPROCESS
        with pytest.raises(DtolTaskStateError, match="INPROCESS"):
            pool.free_all()

    def test_double_free_detected(self) -> None:
        backend, hdass = _open_fake_subsystem()
        pool = BufferPool(backend, hdass, BufferPlan(samples_per_buffer=100), n_channels=1)
        pool.allocate()
        pool.free_all()
        with pytest.raises(DtolTaskStateError, match="double-frees"):
            pool.free_all()


class TestBufferPoolStateCounts:
    def test_state_counts_reports_distribution(self) -> None:
        backend, hdass = _open_fake_subsystem()
        pool = BufferPool(backend, hdass, BufferPlan(samples_per_buffer=100), n_channels=1)
        pool.allocate()
        counts = pool.state_counts
        assert counts[BufferState.IDLE] == 4
        assert counts[BufferState.QUEUED] == 0
        pool.queue_all()
        counts = pool.state_counts
        assert counts[BufferState.QUEUED] == 4
        assert counts[BufferState.IDLE] == 0


class TestBufferPoolFlush:
    def test_flush_empties_queues_and_resets_buffers(self) -> None:
        backend, hdass = _open_fake_subsystem()
        pool = BufferPool(backend, hdass, BufferPlan(samples_per_buffer=100), n_channels=1)
        pool.allocate()
        pool.queue_all()
        pool.flush()
        assert backend.get_queue_size(hdass, 0) == 0
        for raw in pool.buffers:
            assert raw.state == BufferState.IDLE


class TestBufferPoolWithChannelMultiplier:
    def test_capacity_samples_multiplies_by_n_channels(self) -> None:
        backend, hdass = _open_fake_subsystem()
        plan = BufferPlan(samples_per_buffer=500, wrap_mode=WrapMode.MULTIPLE)
        pool = BufferPool(backend, hdass, plan, n_channels=4)
        pool.allocate()
        assert pool.buffers[0].capacity_samples == 500 * 4
