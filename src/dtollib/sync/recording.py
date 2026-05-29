"""Sync wrappers for :func:`record` and :func:`record_polled`.

Each wrapper owns its own :class:`SyncPortal` and yields a sync
:class:`SyncRecording[T]` whose ``stream`` is a sync iterator of records.
Mirrors the sibling-library shape (``nidaqlib.sync.recording``).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from dtollib.streaming import (
    AcquisitionSummary,
    ErrorPolicy,
    OverflowPolicy,
)
from dtollib.streaming import (
    record as _async_record,
)
from dtollib.streaming import (
    record_polled as _async_record_polled,
)
from dtollib.sync.portal import SyncAsyncIterator, SyncPortal

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dtollib.sync.session import SyncDtolSession
    from dtollib.tasks.models import DaqBlock, DaqReading


__all__ = [
    "AcquisitionSummary",
    "ErrorPolicy",
    "OverflowPolicy",
    "SyncRecording",
    "record",
    "record_polled",
]


@dataclass(slots=True)
class SyncRecording[T]:
    """Sync mirror of :class:`dtollib.streaming.Recording`.

    The ``stream`` here is a :class:`SyncAsyncIterator` (iterate with a
    plain ``for`` loop); the rest of the shape matches the async handle.
    """

    stream: SyncAsyncIterator[T]
    summary: AcquisitionSummary
    rate_hz: float | None


@contextlib.contextmanager  # pyright: ignore[reportDeprecated]
def record(
    source: SyncDtolSession,
    *,
    timeout: float = 10.0,
    stream_buffer_size: int = 16,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
    overflow: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
) -> Iterator[SyncRecording[DaqBlock]]:
    """Sync wrapper around :func:`dtollib.streaming.record`.

    Yields a :class:`SyncRecording[DaqBlock]`. Iterate ``recording.stream``
    with a normal ``for`` loop. As with the async recorder, ``source`` must
    have been opened with ``autostart=False`` (the recorder owns the
    commit/arm/start sequence).

    Example::

        with (
            SyncDtolSession(spec, autostart=False) as session,
            record(session) as recording,
        ):
            for block in recording.stream:
                process(block)
    """
    with SyncPortal() as portal:
        acm = _async_record(
            source.raw_session,
            timeout=timeout,
            stream_buffer_size=stream_buffer_size,
            error_policy=error_policy,
            overflow=overflow,
        )
        with portal.wrap_async_context_manager(acm) as recording:
            sync_iter = portal.wrap_async_iter(recording.stream)
            try:
                yield SyncRecording(
                    stream=sync_iter,
                    summary=recording.summary,
                    rate_hz=recording.rate_hz,
                )
            finally:
                sync_iter.close()


@contextlib.contextmanager  # pyright: ignore[reportDeprecated]
def record_polled(
    source: SyncDtolSession,
    *,
    rate_hz: float,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
    overflow: OverflowPolicy = OverflowPolicy.BLOCK,
    buffer_size: int = 64,
) -> Iterator[SyncRecording[DaqReading]]:
    """Sync wrapper around :func:`dtollib.streaming.record_polled`.

    The sync facade only accepts a session source — the manager-mode
    fan-out belongs to async-only call sites — so the per-tick payload is
    always :class:`DaqReading`.
    """
    with SyncPortal() as portal:
        acm = _async_record_polled(
            source.raw_session,
            rate_hz=rate_hz,
            error_policy=error_policy,
            overflow=overflow,
            buffer_size=buffer_size,
        )
        with portal.wrap_async_context_manager(acm) as recording:
            # The session-source overload always emits DaqReading; the
            # async-side Union is widened only for manager-mode.
            reading_rx = cast(
                "SyncAsyncIterator[DaqReading]",
                portal.wrap_async_iter(recording.stream),
            )
            try:
                yield SyncRecording(
                    stream=reading_rx,
                    summary=recording.summary,
                    rate_hz=recording.rate_hz,
                )
            finally:
                reading_rx.close()
