"""Tests for the continuous-mode ordering invariants enforced by the fake.

The bench-proven continuous startup sequence has two ``olDaConfig`` calls
(docs/decisions.md): config #1 (``commit``) after channel/clock/wrap setup,
then ``olDaSetWndHandle`` (``register_notification``), then the Ready queue,
then config #2 (``arm``), then ``start``. ``FakeDtolBackend`` enforces:

- ``commit`` (config #1) needs neither a notification nor a queued buffer.
- ``register_notification`` happens after config #1 and before ``arm``.
- ``arm`` (config #2) requires a registered notification AND a queued buffer.
- ``start`` requires the continuous task to be armed.
- Stop BEFORE unregister.
- ``free_all()`` refuses while INPROCESS.

The callback bridge asserts the same invariants from its perspective; the
fake-level tests catch violations earlier in the pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from dtollib import DtolTaskStateError, SdkEventKind
from dtollib.capi.constants import OL_DF_CONTINUOUS, OLSS_AD
from dtollib.tasks.models import BufferState, SubsystemState
from dtollib.testing import make_fake_backend

if TYPE_CHECKING:
    from dtollib.backend.fake import FakeDtolBackend


def _open() -> tuple[FakeDtolBackend, int]:
    backend = make_fake_backend(include_dt9805=True)
    hdrvr = backend.initialize("DT9805(00)")
    hdass = backend.get_dass(hdrvr, OLSS_AD, 0)
    backend.set_data_flow(hdass, OL_DF_CONTINUOUS)
    return backend, hdass


def _noop_notification(_msg: int, _wparam: int, _lparam: int) -> int:
    return 0


class TestRegisterBeforeArmInvariant:
    def test_arm_without_register_raises(self) -> None:
        backend, hdass = _open()
        backend.commit(hdass)  # config #1 is fine without a notification
        hbuf = backend.alloc_buffer(100, 2)
        backend.put_buffer(hdass, hbuf)
        with pytest.raises(DtolTaskStateError, match="BEFORE register_notification"):
            backend.arm(hdass)

    def test_commit_without_register_is_allowed(self) -> None:
        backend, hdass = _open()
        # config #1 runs before the notification window exists — no error.
        backend.commit(hdass)
        assert backend.state_of(hdass) == SubsystemState.CONFIGURED_FOR_CONTINUOUS

    def test_register_after_arm_raises(self) -> None:
        backend, hdass = _open()
        backend.commit(hdass)
        backend.register_notification(hdass, _noop_notification)
        hbuf = backend.alloc_buffer(100, 2)
        backend.put_buffer(hdass, hbuf)
        backend.arm(hdass)
        with pytest.raises(DtolTaskStateError, match="AFTER arm"):
            backend.register_notification(hdass, _noop_notification)


class TestQueueBeforeArmInvariant:
    def test_arm_with_empty_ready_queue_raises(self) -> None:
        backend, hdass = _open()
        backend.commit(hdass)
        backend.register_notification(hdass, _noop_notification)
        with pytest.raises(DtolTaskStateError, match="BEFORE queueing buffers"):
            backend.arm(hdass)


class TestArmBeforeStartInvariant:
    def test_start_without_arm_raises(self) -> None:
        backend, hdass = _open()
        backend.commit(hdass)
        backend.register_notification(hdass, _noop_notification)
        hbuf = backend.alloc_buffer(100, 2)
        backend.put_buffer(hdass, hbuf)
        with pytest.raises(DtolTaskStateError, match="start BEFORE arm"):
            backend.start(hdass)


class TestStopBeforeUnregisterInvariant:
    def test_unregister_while_running_raises(self) -> None:
        backend, hdass = _open()
        backend.commit(hdass)
        backend.register_notification(hdass, _noop_notification)
        hbuf = backend.alloc_buffer(100, 2)
        backend.put_buffer(hdass, hbuf)
        backend.arm(hdass)
        backend.start(hdass)
        with pytest.raises(DtolTaskStateError, match="stop-before-unregister"):
            backend.unregister_notification(hdass, None)


class TestStateTransitionsContinuous:
    def test_full_lifecycle(self) -> None:
        backend, hdass = _open()
        assert backend.state_of(hdass) == SubsystemState.INITIALIZED
        backend.commit(hdass)
        assert backend.state_of(hdass) == SubsystemState.CONFIGURED_FOR_CONTINUOUS
        backend.register_notification(hdass, _noop_notification)
        hbuf = backend.alloc_buffer(100, 2)
        backend.put_buffer(hdass, hbuf)
        backend.arm(hdass)
        backend.start(hdass)
        assert backend.state_of(hdass) == SubsystemState.RUNNING
        backend.stop(hdass)
        assert backend.state_of(hdass) == SubsystemState.IO_COMPLETE


class TestSyntheticBufferDone:
    def test_fire_buffer_done_routes_to_callback(self) -> None:
        backend, hdass = _open()
        events: list[int] = []

        def callback(msg: int, _wparam: int, _lparam: int) -> int:
            events.append(msg)
            return 0

        backend.commit(hdass)
        backend.register_notification(hdass, callback)
        hbuf = backend.alloc_buffer(100, 2)
        backend.put_buffer(hdass, hbuf)
        backend.arm(hdass)
        backend.start(hdass)
        backend.fire_buffer_done(hdass, fill=np.zeros(100, dtype=np.int16))
        assert len(events) == 1
        # OLDA_WM_BUFFER_DONE is the OL message ID; the fake routed it
        # through the registered callback.
        assert events[0] != 0

    def test_fire_event_overrun(self) -> None:
        backend, hdass = _open()
        events: list[SdkEventKind] = []

        def callback(msg: int, _w: int, _l: int) -> int:
            from dtollib.capi.constants import (
                OLDA_WM_BUFFER_DONE,
                OLDA_WM_OVERRUN_ERROR,
            )

            mapping = {
                OLDA_WM_BUFFER_DONE: SdkEventKind.BUFFER_DONE,
                OLDA_WM_OVERRUN_ERROR: SdkEventKind.OVERRUN_ERROR,
            }
            events.append(mapping.get(msg, SdkEventKind.BUFFER_DONE))
            return 0

        backend.commit(hdass)
        backend.register_notification(hdass, callback)
        hbuf = backend.alloc_buffer(100, 2)
        backend.put_buffer(hdass, hbuf)
        backend.arm(hdass)
        backend.start(hdass)
        backend.fire_event(hdass, SdkEventKind.OVERRUN_ERROR)
        assert events == [SdkEventKind.OVERRUN_ERROR]


class TestBufferStateInvariants:
    def test_free_buffer_while_inprocess_raises(self) -> None:
        backend, hdass = _open()
        hbuf = backend.alloc_buffer(100, 2)
        backend.put_buffer(hdass, hbuf)
        # Force the buffer into INPROCESS state on the fake.
        backend.force_hbuf_state(hbuf, BufferState.INPROCESS)
        with pytest.raises(DtolTaskStateError, match="INPROCESS"):
            backend.free_buffer(hbuf)
