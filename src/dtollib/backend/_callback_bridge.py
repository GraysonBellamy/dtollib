"""§12.3.2 callback bridge — driver thread → asyncio.

The single most complex module in dtollib. The SDK delivers buffer-done
events on a driver-managed thread (no event loop, no asyncio). This
module bridges that thread to an AnyIO memory-object stream of
:class:`DaqBlock`s consumed by ``async for`` in user code.

Three threads matter:

- **Asyncio thread.** Runs the event loop, the ``async for block in rx``
  iteration, and the ``__aexit__`` shutdown.
- **Driver thread.** Owned by the DataAcq SDK. Calls the notification
  procedure with ``(msg_id, wparam, lparam)``. The only safe operation
  here is ``queue.SimpleQueue.put_nowait`` of a tiny event tuple.
- **Drainer thread.** A long-lived ``anyio.to_thread.run_sync`` worker.
  Pulls events off the SimpleQueue, calls ``pool.get_done()`` +
  ``backend.read_buffer_payload`` to copy data, constructs
  :class:`DaqBlock`s, sends on the memory-object stream, and recycles
  HBUFs via ``pool.requeue``.

The cardinal rule: the driver-thread callback only signals; the drainer
does the work.

Design reference: docs/design.md §12.3.2 (ordering invariants, callback
body rules), Appendix C (skeleton).
"""

from __future__ import annotations

import contextlib
import logging
import queue
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import anyio
import anyio.to_thread as anyio_to_thread

from dtollib.backend._bridge_common import (
    SENTINEL,
    DrainStop,
    DriverEvent,
    Sentinel,
    msg_id_to_kind,
)
from dtollib.errors import (
    DtolBufferOverrunError,
    DtolBufferUnderrunError,
    DtolError,
    DtolTriggerError,
    ErrorContext,
)
from dtollib.streaming._types import (
    AcquisitionSummary,
    ErrorPolicy,
    OverflowPolicy,
)
from dtollib.tasks.models import DaqBlock, SdkEventKind

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    import numpy.typing as npt
    from anyio.streams.memory import MemoryObjectReceiveStream

    from dtollib.backend._buffer_pool import BufferPool
    from dtollib.backend.base import DtolBackend
    from dtollib.capi.conversion import BlockConversion


__all__ = ["BridgeConfig", "callback_bridge"]


_log = logging.getLogger(__name__)


class BridgeConfig:
    """Configuration bundle for :func:`callback_bridge`.

    Plain attribute container — kept simple so the bridge doesn't take a
    long parameter list. Required fields are positional kw-args on the
    bridge function; this carries the per-task metadata needed to
    construct each :class:`DaqBlock`.
    """

    __slots__ = (
        "channels",
        "conversion",
        "device",
        "error_policy",
        "overflow_policy",
        "sample_rate_hz",
        "stream_buffer_size",
        "task",
        "task_started_at",
        "task_started_mono_ns",
        "units",
    )

    def __init__(
        self,
        *,
        device: str,
        task: str | None,
        channels: tuple[str, ...],
        sample_rate_hz: float | None,
        task_started_at: datetime,
        task_started_mono_ns: int,
        units: dict[str, str | None] | None = None,
        error_policy: ErrorPolicy = ErrorPolicy.RAISE,
        overflow_policy: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
        stream_buffer_size: int = 16,
        conversion: BlockConversion | None = None,
    ) -> None:
        self.device = device
        self.task = task
        self.channels = channels
        self.sample_rate_hz = sample_rate_hz
        self.task_started_at = task_started_at
        self.task_started_mono_ns = task_started_mono_ns
        self.units = units or dict.fromkeys(channels, None)
        self.error_policy = error_policy
        self.overflow_policy = overflow_policy
        self.stream_buffer_size = stream_buffer_size
        # Optional code→engineering-units plan. When None the drainer emits
        # raw codes cast to float (is_linearised=False) — the historical
        # behaviour. When set, the drainer converts and sets is_linearised.
        self.conversion = conversion


@asynccontextmanager
async def callback_bridge(  # noqa: PLR0915
    backend: DtolBackend,
    hdass: int,
    pool: BufferPool,
    config: BridgeConfig,
) -> AsyncGenerator[tuple[MemoryObjectReceiveStream[DaqBlock], AcquisitionSummary]]:
    """Drive the §12.3.2 driver-thread → asyncio bridge.

    Lifecycle on entry:

    1. Allocate the SimpleQueue + memory-object stream.
    2. Register the notification procedure on the backend (which pins
       the wrapper internally — see docs/design.md §12.3.2).
    3. Start the drainer task in an AnyIO task group.

    Lifecycle on exit (shielded against cancellation):

    1. ``backend.stop(hdass)`` — orderly stop; or ``abort`` if caller
       prefers (the bridge always uses stop; the outer recorder may
       call ``abort`` before entering ``__aexit__`` for emergency
       shutdown).
    2. Unregister the notification (drops the pinned wrapper).
    3. Put the sentinel on the queue to wake the drainer.
    4. Wait for ``drain_done``.
    5. Send-stream is closed inside the drainer ``finally`` block.

    The pool teardown (``flush + free_all``) is the caller's job — the
    session lifecycle owns the pool, not the bridge.
    """
    chunk_q: queue.SimpleQueue[DriverEvent | Sentinel] = queue.SimpleQueue()
    tx_send, tx_recv = anyio.create_memory_object_stream[DaqBlock](
        max_buffer_size=config.stream_buffer_size,
    )
    drain_done = anyio.Event()
    summary = AcquisitionSummary(started_at=datetime.now(UTC))
    # Holds a captured exception for ErrorPolicy.RAISE — re-raised to the
    # caller only after the ordered shutdown completes (see DrainStop).
    error_holder: list[BaseException] = []

    def _on_notify(msg_id: int, wparam: int, lparam: int) -> int:
        # DRIVER THREAD. Minimal work: timestamp + put_nowait.
        # No asyncio, no logging, no allocations beyond the tuple.
        chunk_q.put_nowait((msg_id, time.monotonic_ns(), wparam, lparam))
        return 0

    try:
        notification_handle = backend.register_notification(hdass, _on_notify)
    except BaseException:
        # Registration failed before the drainer started — the streams
        # allocated above would otherwise leak (deallocator warning) and
        # the caller would see only the registration error. Close them so
        # the failure is clean and loud, not a silent resource leak.
        tx_send.close()
        tx_recv.close()
        raise

    async def _drainer() -> None:
        """Long-lived worker: pull events, build blocks, emit + recycle."""
        block_index = 0
        first_sample_index = 0
        samples_per_buffer = pool.plan.samples_per_buffer
        n_channels = pool.n_channels
        period_ns: int | None = (
            None if not config.sample_rate_hz else round(1e9 / config.sample_rate_hz)
        )
        try:
            while True:
                item = await anyio_to_thread.run_sync(chunk_q.get)
                if isinstance(item, Sentinel):
                    return
                # Driver-thread payload tuple.
                msg_id, mono_ns, wparam, lparam = item
                del wparam, lparam
                kind = msg_id_to_kind(msg_id)
                if kind is None:
                    _log.warning("callback_bridge: unknown msg_id=0x%x; dropping", msg_id)
                    continue

                if kind == SdkEventKind.BUFFER_DONE:
                    await _handle_buffer_done(
                        mono_ns=mono_ns,
                        block_index=block_index,
                        first_sample_index=first_sample_index,
                        samples_per_buffer=samples_per_buffer,
                        n_channels=n_channels,
                        period_ns=period_ns,
                    )
                    block_index += 1
                    first_sample_index += samples_per_buffer
                elif kind == SdkEventKind.BUFFER_REUSED:
                    _log.warning(
                        "callback_bridge: BUFFER_REUSED — data overwritten in "
                        "WrapMode.MULTIPLE; consumer is falling behind"
                    )
                    summary.errors_observed += 1
                elif kind == SdkEventKind.OVERRUN_ERROR:
                    summary.overruns_observed += 1
                    summary.errors_observed += 1
                    await _route_sdk_error(
                        DtolBufferOverrunError(
                            "SDK reported OLDA_WM_OVERRUN_ERROR — consumer fell behind buffer pool",
                            context=ErrorContext(operation="callback_bridge"),
                        ),
                        mono_ns=mono_ns,
                        block_index=block_index,
                        first_sample_index=first_sample_index,
                        samples_per_buffer=samples_per_buffer,
                        n_channels=n_channels,
                        period_ns=period_ns,
                    )
                elif kind == SdkEventKind.UNDERRUN_ERROR:
                    summary.underruns_observed += 1
                    summary.errors_observed += 1
                    await _route_sdk_error(
                        DtolBufferUnderrunError(
                            "SDK reported OLDA_WM_UNDERRUN_ERROR — AO buffer pool starved",
                            context=ErrorContext(operation="callback_bridge"),
                        ),
                        mono_ns=mono_ns,
                        block_index=block_index,
                        first_sample_index=first_sample_index,
                        samples_per_buffer=samples_per_buffer,
                        n_channels=n_channels,
                        period_ns=period_ns,
                    )
                elif kind == SdkEventKind.TRIGGER_ERROR:
                    summary.errors_observed += 1
                    await _route_sdk_error(
                        DtolTriggerError(
                            "SDK reported OLDA_WM_TRIGGER_ERROR",
                            context=ErrorContext(operation="callback_bridge"),
                        ),
                        mono_ns=mono_ns,
                        block_index=block_index,
                        first_sample_index=first_sample_index,
                        samples_per_buffer=samples_per_buffer,
                        n_channels=n_channels,
                        period_ns=period_ns,
                    )
                elif kind in {
                    SdkEventKind.QUEUE_DONE,
                    SdkEventKind.QUEUE_STOPPED,
                    SdkEventKind.IO_COMPLETE,
                }:
                    # End-of-run signals — close the stream cleanly.
                    return
                else:
                    # PRETRIGGER_BUFFER_DONE / EVENT_DONE / MEASURE_DONE —
                    # counter/timer and other non-AI subsystems; log + drop.
                    _log.debug("callback_bridge: %s event ignored on AI stream", kind.value)
        except DrainStop:
            # ErrorPolicy.RAISE: exit the loop cleanly; the captured error
            # is re-raised by the bridge after the ordered shutdown.
            pass
        finally:
            drain_done.set()
            await tx_send.aclose()

    async def _handle_buffer_done(
        *,
        mono_ns: int,
        block_index: int,
        first_sample_index: int,
        samples_per_buffer: int,
        n_channels: int,
        period_ns: int | None,
    ) -> None:
        """Pull the Done HBUF, copy to ndarray, build DaqBlock, recycle."""
        import numpy as np  # noqa: PLC0415

        raw = await anyio_to_thread.run_sync(pool.get_done)
        if raw is None:
            return
        read_started = datetime.now(UTC)
        try:
            view: npt.NDArray[Any] = await anyio_to_thread.run_sync(pool.payload_view, raw)
        except Exception as exc:
            _log.exception("callback_bridge: payload_view raised")
            summary.errors_observed += 1
            if config.error_policy == ErrorPolicy.RAISE:
                # Recycle the buffer first so the SDK stop/free sequence is
                # clean, then signal an orderly drainer exit (see DrainStop).
                with contextlib.suppress(Exception):
                    await anyio_to_thread.run_sync(pool.requeue, raw)
                error_holder.append(exc)
                raise DrainStop from exc
            # Recycle anyway under SKIP / RETURN so the SDK keeps moving.
            await anyio_to_thread.run_sync(pool.requeue, raw)
            del exc
            return
        # Reshape the flat ndarray view into (n_channels, samples_per_buffer).
        # The drainer makes a hard copy so the HBUF can be requeued safely.
        sample_count = min(raw.valid_samples // max(n_channels, 1), samples_per_buffer)
        if sample_count == 0:
            await anyio_to_thread.run_sync(pool.requeue, raw)
            return
        flat = np.asarray(view)[: sample_count * n_channels].copy()
        data_int = flat.reshape(sample_count, n_channels).T  # (n_channels, n_samples)

        # Convert codes → engineering units when a plan is configured; else
        # fall through to the historical raw-codes-as-float behaviour. The
        # conversion is vectorised and runs on the drainer thread (off the
        # event loop), matching the design's hot-path placement.
        cjc_data: npt.NDArray[Any] | None = None
        sensor_status: dict[str, npt.NDArray[Any]] = {}
        is_linearised = False
        if config.conversion is not None and data_int.dtype.kind in {"i", "u"}:
            converted, cjc_data, row_masks = _linearise(config.conversion, data_int)
            data = converted
            is_linearised = True
            for row, mask in row_masks.items():
                if row < len(config.channels):
                    sensor_status[config.channels[row]] = mask
        else:
            data = data_int.astype(np.float64, copy=False)

        read_finished = datetime.now(UTC)
        elapsed_s = (read_finished - read_started).total_seconds()
        block = DaqBlock(
            device=config.device,
            task=config.task,
            channels=config.channels,
            data=data,
            raw_codes=data_int.astype(np.int32, copy=False)
            if data_int.dtype.kind in {"i", "u"}
            else None,
            cjc_data=cjc_data,
            block_index=block_index,
            first_sample_index=first_sample_index,
            samples_per_channel=sample_count,
            sample_rate_hz=config.sample_rate_hz,
            block_period_ns=period_ns,
            task_started_at=config.task_started_at,
            t0=_reconstruct_t0(
                config.task_started_at,
                config.task_started_mono_ns,
                mono_ns,
            ),
            t_mono_ns=mono_ns,
            t_utc=datetime.now(UTC),
            t_midpoint_mono_ns=(
                mono_ns + (sample_count * period_ns) // 2 if period_ns is not None else None
            ),
            read_started_at=read_started,
            read_finished_at=read_finished,
            elapsed_s=elapsed_s,
            units=config.units,
            is_linearised=is_linearised,
            sensor_status=sensor_status,
        )
        await _emit_block(block)
        # Recycle the HBUF AFTER the emit succeeds — under DROP_OLDEST we
        # drop the buffer's data but always recycle to keep the SDK moving.
        await anyio_to_thread.run_sync(pool.requeue, raw)

    async def _emit_block(block: DaqBlock) -> None:
        """Send ``block`` on the stream, honouring OverflowPolicy."""
        if config.overflow_policy == OverflowPolicy.BLOCK:
            await tx_send.send(block)
            summary.payloads_emitted += 1
            return
        try:
            tx_send.send_nowait(block)
            summary.payloads_emitted += 1
        except anyio.WouldBlock:
            if config.overflow_policy == OverflowPolicy.DROP_OLDEST:
                # Evict the oldest queued payload and retry — best-effort.
                with contextlib.suppress(Exception):
                    tx_recv.receive_nowait()
                summary.payloads_dropped += 1
                try:
                    tx_send.send_nowait(block)
                    summary.payloads_emitted += 1
                except anyio.WouldBlock:
                    summary.payloads_dropped += 1
            else:  # DROP_NEWEST
                summary.payloads_dropped += 1

    async def _route_sdk_error(
        exc: DtolError,
        *,
        mono_ns: int,
        block_index: int,
        first_sample_index: int,
        samples_per_buffer: int,
        n_channels: int,
        period_ns: int | None,
    ) -> None:
        """Route a wrapped SDK error per the configured ErrorPolicy."""
        import numpy as np  # noqa: PLC0415

        if config.error_policy == ErrorPolicy.RAISE:
            # Defer the raise: capture it and unwind the drainer cleanly so
            # the ordered SDK/pool teardown runs without a task-group cancel
            # race (segfault/deadlock on hardware). See DrainStop.
            error_holder.append(exc)
            raise DrainStop from exc
        if config.error_policy == ErrorPolicy.RETURN:
            now = datetime.now(UTC)
            zero_data = np.zeros((n_channels, samples_per_buffer), dtype=np.float64)
            block = DaqBlock(
                device=config.device,
                task=config.task,
                channels=config.channels,
                data=zero_data,
                block_index=block_index,
                first_sample_index=first_sample_index,
                samples_per_channel=samples_per_buffer,
                sample_rate_hz=config.sample_rate_hz,
                block_period_ns=period_ns,
                task_started_at=config.task_started_at,
                t0=now,
                t_mono_ns=mono_ns,
                t_utc=now,
                read_started_at=now,
                read_finished_at=now,
                elapsed_s=0.0,
                units=config.units,
                error=exc,
            )
            await _emit_block(block)
        # SKIP: count via summary.errors_observed (already incremented).
        _log.warning("callback_bridge: SDK error routed by ErrorPolicy: %s", exc)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_drainer)
        try:
            yield tx_recv, summary
        finally:
            # Shielded shutdown — §12.3.2 ordering.
            with anyio.CancelScope(shield=True):
                # 1. Stop the SDK subsystem (in-flight callbacks complete).
                with contextlib.suppress(Exception):
                    await anyio_to_thread.run_sync(backend.stop, hdass)
                # 2. Unregister notification (drops the pinned wrapper).
                with contextlib.suppress(Exception):
                    await anyio_to_thread.run_sync(
                        backend.unregister_notification,
                        hdass,
                        notification_handle,
                    )
                # 3. Wake the drainer.
                chunk_q.put_nowait(SENTINEL)
                # 4. Wait for it to exit.
                await drain_done.wait()
                # 5. Close the receive stream — the drainer closes tx_send,
                #    but tx_recv carries its own state that needs explicit
                #    closure to avoid a deallocator warning.
                await tx_recv.aclose()
                summary.finished_at = datetime.now(UTC)
        tg.cancel_scope.cancel()

    # ErrorPolicy.RAISE: surface the captured SDK error to the caller only
    # now — after the ordered shutdown above has fully completed, so the
    # exception never races the SDK driver thread or the pool teardown.
    if error_holder:
        raise error_holder[0]


def _linearise(
    plan: BlockConversion,
    codes: npt.NDArray[Any],
) -> tuple[npt.NDArray[Any], npt.NDArray[Any] | None, dict[int, npt.NDArray[Any]]]:
    """Drainer-thread wrapper over :func:`conversion.linearise_block`.

    Kept module-level (and importing lazily) so the hot-path branch in
    :func:`_handle_buffer_done` reads cleanly and the numpy/conversion import
    stays off the module-load path.
    """
    from dtollib.capi.conversion import linearise_block  # noqa: PLC0415

    return linearise_block(codes, plan)


def _reconstruct_t0(
    task_started_at: datetime,
    task_started_mono_ns: int,
    callback_mono_ns: int,
) -> datetime:
    """Reconstruct wall-clock-at-first-sample from the monotonic delta."""
    delta_ns = callback_mono_ns - task_started_mono_ns
    return task_started_at + _ns_to_timedelta(delta_ns)


def _ns_to_timedelta(ns: int) -> timedelta:
    """Lazy import to avoid circular module load."""
    from datetime import timedelta  # noqa: PLC0415

    return timedelta(microseconds=ns / 1000.0)
