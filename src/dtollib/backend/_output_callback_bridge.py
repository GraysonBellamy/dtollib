"""Output (waveform-playback) analog of the §12.3.2 callback bridge.

Where :mod:`dtollib.backend._callback_bridge` *drains* completed buffers and
emits :class:`~dtollib.tasks.models.DaqBlock`s, this bridge *refills* emptied
buffers from a waveform source and re-queues them. The shutdown discipline is
shared via :mod:`dtollib.backend._bridge_common`; only the data direction
inverts.

Three threads still matter (asyncio / driver / drainer), and the cardinal rule
is unchanged: the driver-thread callback only signals; the drainer does the
work. For output that work is *fill + requeue* instead of *copy + emit*.

Two wrap modes:

- ``WrapMode.SINGLE`` — the SDK loops the pre-filled buffer ring as one
  continuous waveform. The drainer performs **no refill**; it exists only to
  satisfy the register-before-arm invariant (it still registers a
  notification, exactly like the input bridge, so the SDK's buffer-rotation
  state machine is wired), to route ``UNDERRUN_ERROR`` / ``TRIGGER_ERROR`` per
  :class:`ErrorPolicy`, and to handle end-of-run.
- ``WrapMode.MULTIPLE`` — on each ``BUFFER_DONE`` the drainer pulls the next
  chunk from the waveform source, fills the emptied HBUF, and re-queues it.
  When the source is exhausted the playback stops cleanly (finite playback).

Shutdown (shielded, §12.3.2 order): ``mute`` (if the subsystem supports it —
avoids a DAC output transient) → ``stop`` → unregister → sentinel → drain-wait.
The caller frees the pool afterwards.

Design reference: docs/design.md §12.3.2, docs/plan-hardware-functional.md
§WS-AO.
"""

from __future__ import annotations

import logging
import queue
import time
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
    DtolBufferUnderrunError,
    DtolError,
    DtolTriggerError,
    ErrorContext,
)
from dtollib.streaming._types import AcquisitionSummary, ErrorPolicy
from dtollib.tasks.models import SdkEventKind, WrapMode

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from dtollib.backend._buffer_pool import BufferPool
    from dtollib.backend.base import DtolBackend


__all__ = ["OutputBridgeConfig", "output_callback_bridge"]


_log = logging.getLogger(__name__)


class OutputBridgeConfig:
    """Configuration bundle for :func:`output_callback_bridge`.

    Plain attribute container, mirroring
    :class:`~dtollib.backend._callback_bridge.BridgeConfig`.
    """

    __slots__ = (
        "device",
        "error_policy",
        "supports_mute",
        "task",
        "wrap_mode",
    )

    def __init__(
        self,
        *,
        device: str,
        task: str | None,
        wrap_mode: WrapMode,
        error_policy: ErrorPolicy = ErrorPolicy.RAISE,
        supports_mute: bool = False,
    ) -> None:
        self.device = device
        self.task = task
        self.wrap_mode = wrap_mode
        self.error_policy = error_policy
        self.supports_mute = supports_mute


_END_OF_RUN_KINDS = frozenset(
    {SdkEventKind.QUEUE_DONE, SdkEventKind.QUEUE_STOPPED, SdkEventKind.IO_COMPLETE}
)


@asynccontextmanager
async def output_callback_bridge(  # noqa: PLR0915
    backend: DtolBackend,
    hdass: int,
    pool: BufferPool,
    config: OutputBridgeConfig,
    *,
    pull: Callable[[], Awaitable[bytes | None]] | None = None,
) -> AsyncGenerator[AcquisitionSummary]:
    """Drive the output (waveform-playback) driver-thread → asyncio bridge.

    Args:
        backend: The backend owning ``hdass``.
        hdass: D/A subsystem handle.
        pool: An ``OUTPUT``-direction :class:`BufferPool`, allocated but not
            yet queued — the caller seeds + queues it *inside* this context so
            the notification is registered before ``arm`` (config #2).
        config: Per-task metadata + the resolved wrap mode / error policy.
        pull: ``async () -> bytes | None`` yielding the next code-domain chunk
            for ``WrapMode.MULTIPLE`` (``None`` = source exhausted → stop
            cleanly). Required for ``MULTIPLE``; unused for ``SINGLE``.

    Yields:
        The mutable :class:`AcquisitionSummary` for the run.

    Lifecycle on entry: register the notification (so the caller's subsequent
    ``arm`` is legal), then start the drainer.

    Lifecycle on exit (shielded): ``mute`` (if supported) → ``stop`` →
    unregister → sentinel → drain-wait. Pool teardown is the caller's job.
    """
    if config.wrap_mode is WrapMode.MULTIPLE and pull is None:
        raise DtolError(
            "output_callback_bridge: WrapMode.MULTIPLE requires a `pull` source",
            context=ErrorContext(operation="output_callback_bridge"),
        )

    chunk_q: queue.SimpleQueue[DriverEvent | Sentinel] = queue.SimpleQueue()
    drain_done = anyio.Event()
    summary = AcquisitionSummary(started_at=datetime.now(UTC))
    # Holds a captured exception for ErrorPolicy.RAISE — re-raised to the
    # caller only after the ordered shutdown completes (see DrainStop).
    error_holder: list[BaseException] = []

    def _on_notify(msg_id: int, wparam: int, lparam: int) -> int:
        # DRIVER THREAD. Minimal work: timestamp + put_nowait.
        chunk_q.put_nowait((msg_id, time.monotonic_ns(), wparam, lparam))
        return 0

    notification_handle = backend.register_notification(hdass, _on_notify)

    async def _refill_one() -> bool:
        """Refill + requeue one emptied HBUF. Returns False when source done."""
        raw = await anyio_to_thread.run_sync(pool.get_done)
        if raw is None or pull is None:
            # ``pull is None`` is unreachable for MULTIPLE (validated at entry);
            # treat it as "nothing to refill" rather than risk a None call.
            return raw is None
        chunk = await pull()
        if chunk is None:
            # Finite source exhausted — leave the buffer empty and stop.
            return False
        await anyio_to_thread.run_sync(pool.fill, raw, chunk)
        await anyio_to_thread.run_sync(pool.requeue, raw)
        summary.payloads_emitted += 1
        return True

    def _route_sdk_error(exc: DtolError) -> None:
        """Route a wrapped SDK error per the configured ErrorPolicy.

        ``RAISE`` defers the raise via :class:`DrainStop` so the shielded
        teardown runs without a task-group cancel race; ``RETURN`` / ``SKIP``
        have no consumer payload to annotate on output, so both log + count.
        """
        summary.errors_observed += 1
        if config.error_policy == ErrorPolicy.RAISE:
            error_holder.append(exc)
            raise DrainStop from exc
        _log.warning("output_callback_bridge: SDK error routed by ErrorPolicy: %s", exc)

    def _error_for(kind: SdkEventKind) -> DtolError | None:
        """Map an error event kind to its typed exception (or None)."""
        if kind == SdkEventKind.UNDERRUN_ERROR:
            summary.underruns_observed += 1
            return DtolBufferUnderrunError(
                "SDK reported OLDA_WM_UNDERRUN_ERROR — D/A buffer pool starved",
                context=ErrorContext(operation="output_callback_bridge"),
            )
        if kind == SdkEventKind.TRIGGER_ERROR:
            return DtolTriggerError(
                "SDK reported OLDA_WM_TRIGGER_ERROR",
                context=ErrorContext(operation="output_callback_bridge"),
            )
        return None

    async def _handle(kind: SdkEventKind, *, refilling: bool) -> bool:
        """Process one event. Returns False to end the run cleanly."""
        if kind == SdkEventKind.BUFFER_DONE:
            if refilling:
                try:
                    return await _refill_one()
                except DtolError as exc:
                    # A bad/over-range streamed chunk is a hard error regardless
                    # of ErrorPolicy. Defer it past the shielded teardown.
                    error_holder.append(exc)
                    raise DrainStop from exc
            return True
        if kind in _END_OF_RUN_KINDS:
            return False
        error = _error_for(kind)
        if error is not None:
            _route_sdk_error(error)
        else:
            # BUFFER_REUSED / PRETRIGGER_* / EVENT_DONE / MEASURE_DONE — not
            # meaningful for the output path; log + drop.
            _log.debug("output_callback_bridge: %s event ignored", kind.value)
        return True

    async def _drainer() -> None:
        """Long-lived worker: pull events, refill (MULTIPLE) or no-op (SINGLE)."""
        refilling = config.wrap_mode is WrapMode.MULTIPLE
        try:
            while True:
                item = await anyio_to_thread.run_sync(chunk_q.get)
                if isinstance(item, Sentinel):
                    return
                kind = msg_id_to_kind(item[0])
                if kind is None:
                    _log.warning("output_callback_bridge: unknown msg_id=0x%x; dropping", item[0])
                    continue
                if not await _handle(kind, refilling=refilling):
                    return
        except DrainStop:
            # ErrorPolicy.RAISE: exit cleanly; the captured error is re-raised
            # by the bridge after the ordered shutdown.
            pass
        finally:
            drain_done.set()

    async with anyio.create_task_group() as tg:
        _ = tg.start_soon(_drainer)
        try:
            yield summary
        finally:
            # Shielded shutdown — §12.3.2 ordering, mute-first to avoid a DAC
            # output transient before the subsystem actually stops.
            with anyio.CancelScope(shield=True):
                if config.supports_mute:
                    with suppress(Exception):
                        await anyio_to_thread.run_sync(backend.mute, hdass)
                with suppress(Exception):
                    await anyio_to_thread.run_sync(backend.stop, hdass)
                with suppress(Exception):
                    await anyio_to_thread.run_sync(
                        backend.unregister_notification,
                        hdass,
                        notification_handle,
                    )
                chunk_q.put_nowait(SENTINEL)
                await drain_done.wait()
                summary.finished_at = datetime.now(UTC)
        tg.cancel()

    # ErrorPolicy.RAISE: surface the captured SDK error now — after the ordered
    # shutdown above, so it never races the driver thread or pool teardown.
    if error_holder:
        raise error_holder[0]
