"""``record_polled`` — software-timed scalar polling at a fixed rate.

Drives a :class:`~dtollib.tasks.session.DtolSession` (or a
:class:`~dtollib.manager.DtolManager`) at a fixed cadence and emits one
:class:`DaqReading` per tick. The polled loop uses absolute-target
scheduling so drift across cycles does not accumulate.

Software-timed only: use :func:`record` for hardware-clocked block
acquisition.

Design reference: docs/design.md §14.1 (recorder dispatch), §12.3.1
(software-timed path).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Union

import anyio

from dtollib.errors import (
    DtolError,
    DtolReadError,
    DtolTaskStateError,
    ErrorContext,
)
from dtollib.streaming._types import (
    AcquisitionSummary,
    ErrorPolicy,
    OverflowPolicy,
    Recording,
)
from dtollib.tasks.models import DaqReading

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping

    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

    from dtollib.manager import DeviceResult, DtolManager
    from dtollib.tasks.session import DtolSession


__all__ = ["record_polled"]


_PolledItem = Union["DaqReading", "Mapping[str, DeviceResult[DaqReading]]"]


@asynccontextmanager
async def record_polled(
    source: DtolSession | DtolManager,
    *,
    rate_hz: float,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
    overflow: OverflowPolicy = OverflowPolicy.BLOCK,
    buffer_size: int = 64,
) -> AsyncGenerator[Recording[_PolledItem]]:
    """Software-timed polling at ``rate_hz``.

    Args:
        source: A :class:`DtolSession` (yields :class:`DaqReading` per tick)
            or a :class:`DtolManager` (yields
            ``Mapping[str, DeviceResult[DaqReading]]`` per tick).
        rate_hz: Target poll rate, in Hz. Must be > 0.
        error_policy: :attr:`RAISE` cancels the recorder on a poll error;
            :attr:`RETURN` emits a payload with ``.error`` set;
            :attr:`SKIP` drops the failed payload silently (counter only).
        overflow: Consumer back-pressure policy. Default ``BLOCK`` —
            software-timed pollers can pause without SDK overrun.
        buffer_size: AnyIO send-stream capacity in payload slots.

    Yields:
        :class:`Recording[T]` whose ``stream`` is the async iterator of
        payloads and ``summary`` is the mutable :class:`AcquisitionSummary`.

    Raises:
        DtolTaskStateError: ``source`` is not in a pollable state.
        ValueError: ``rate_hz <= 0`` or ``buffer_size < 1``.
    """
    if rate_hz <= 0:
        raise ValueError(f"rate_hz must be > 0, got {rate_hz!r}")
    if buffer_size < 1:
        raise ValueError(f"buffer_size must be >= 1, got {buffer_size!r}")

    from dtollib.manager import DtolManager  # noqa: PLC0415

    if isinstance(source, DtolManager) and not source.names:
        raise DtolTaskStateError(
            "record_polled() requires a DtolManager with at least one task",
            context=ErrorContext(operation="record_polled"),
        )

    summary = AcquisitionSummary(started_at=datetime.now(UTC))
    period = 1.0 / rate_hz
    tx, rx = anyio.create_memory_object_stream[_PolledItem](max_buffer_size=buffer_size)
    drop_rx = rx.clone()

    async with anyio.create_task_group() as tg, rx, drop_rx:
        if isinstance(source, DtolManager):
            tg.start_soon(
                _manager_producer,
                source,
                tx,
                drop_rx,
                summary,
                period,
                error_policy,
                overflow,
            )
        else:
            tg.start_soon(
                _session_producer,
                source,
                tx,
                drop_rx,
                summary,
                period,
                error_policy,
                overflow,
            )
        try:
            yield Recording(stream=rx, summary=summary, rate_hz=rate_hz)
        finally:
            await tx.aclose()
            tg.cancel_scope.cancel()
    summary.finished_at = datetime.now(UTC)


async def _session_producer(
    source: DtolSession,
    tx: MemoryObjectSendStream[_PolledItem],
    drop_rx: MemoryObjectReceiveStream[_PolledItem],
    summary: AcquisitionSummary,
    period: float,
    error_policy: ErrorPolicy,
    overflow: OverflowPolicy,
) -> None:
    """Absolute-target poll loop for a single session."""
    start = anyio.current_time()
    tick = 0
    try:
        while True:
            target = start + tick * period
            now = anyio.current_time()
            if now > target + period:
                missed = int((now - target) / period)
                summary.payloads_dropped += missed
                tick += missed
                target = start + tick * period
            if anyio.current_time() < target:
                await anyio.sleep_until(target)
            try:
                reading = await source.poll()
            except (DtolReadError, DtolError) as exc:
                summary.errors_observed += 1
                if error_policy == ErrorPolicy.RAISE:
                    raise
                if error_policy == ErrorPolicy.SKIP:
                    tick += 1
                    continue
                reading = _error_reading(source, exc)
            await _emit(tx, drop_rx, reading, summary, overflow)
            tick += 1
    except (anyio.BrokenResourceError, anyio.ClosedResourceError, anyio.EndOfStream):
        return


async def _manager_producer(
    manager: DtolManager,
    tx: MemoryObjectSendStream[_PolledItem],
    drop_rx: MemoryObjectReceiveStream[_PolledItem],
    summary: AcquisitionSummary,
    period: float,
    error_policy: ErrorPolicy,
    overflow: OverflowPolicy,
) -> None:
    """Absolute-target poll loop for a multi-task manager."""
    start = anyio.current_time()
    tick = 0
    try:
        while True:
            target = start + tick * period
            now = anyio.current_time()
            if now > target + period:
                missed = int((now - target) / period)
                summary.payloads_dropped += missed
                tick += missed
                target = start + tick * period
            if anyio.current_time() < target:
                await anyio.sleep_until(target)
            results: Mapping[str, DeviceResult[DaqReading]]
            try:
                results = await manager.poll()
            except DtolError:
                summary.errors_observed += 1
                if error_policy == ErrorPolicy.RAISE:
                    raise
                if error_policy == ErrorPolicy.SKIP:
                    tick += 1
                    continue
                results = {}
            errors_this_tick = sum(
                1 for r in results.values() if getattr(r, "error", None) is not None
            )
            summary.errors_observed += errors_this_tick
            await _emit(tx, drop_rx, results, summary, overflow)
            tick += 1
    except (anyio.BrokenResourceError, anyio.ClosedResourceError, anyio.EndOfStream):
        return


def _error_reading(source: DtolSession, exc: BaseException) -> DaqReading:
    """Synthesise a :class:`DaqReading` carrying ``.error`` for RETURN policy."""
    import time as _time  # noqa: PLC0415

    now = datetime.now(UTC)
    return DaqReading(
        device=source.spec.name,
        task=source.spec.name,
        values={},
        units={},
        t_mono_ns=_time.monotonic_ns(),
        t_utc=now,
        requested_at=now,
        received_at=now,
        latency_s=0.0,
        error=exc if isinstance(exc, DtolError) else None,
    )


async def _emit(
    tx: MemoryObjectSendStream[_PolledItem],
    drop_rx: MemoryObjectReceiveStream[_PolledItem],
    payload: _PolledItem,
    summary: AcquisitionSummary,
    overflow: OverflowPolicy,
) -> None:
    """Apply the configured overflow policy to one outbound payload."""
    if overflow == OverflowPolicy.BLOCK:
        await tx.send(payload)
        summary.payloads_emitted += 1
        return
    try:
        tx.send_nowait(payload)
        summary.payloads_emitted += 1
        return
    except anyio.WouldBlock:
        pass
    if overflow == OverflowPolicy.DROP_NEWEST:
        summary.payloads_dropped += 1
        return
    # DROP_OLDEST — evict, retry.
    import contextlib  # noqa: PLC0415

    with contextlib.suppress(Exception):
        drop_rx.receive_nowait()
        summary.payloads_dropped += 1
    try:
        tx.send_nowait(payload)
        summary.payloads_emitted += 1
    except anyio.WouldBlock:
        summary.payloads_dropped += 1
