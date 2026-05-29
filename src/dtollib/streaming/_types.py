"""Streaming types shared across the recorder modules.

Provides :class:`ErrorPolicy`, :class:`OverflowPolicy`, and
:class:`AcquisitionSummary`. The :class:`Recording[T]` handle and the
recorders themselves (:func:`record`, :func:`record_polled`) live
alongside the :class:`DaqReading` / :class:`DaqBlock` payloads
they yield.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from anyio.streams.memory import MemoryObjectReceiveStream


__all__ = [
    "AcquisitionSummary",
    "ErrorPolicy",
    "OverflowPolicy",
    "Recording",
]


def _empty_extra() -> dict[str, object]:
    return {}


class ErrorPolicy(StrEnum):
    """How a recorder reacts to a backend error mid-stream.

    Attributes:
        RAISE: Cancel the recorder; the exception propagates out of the
            ``async with record(...)`` block.
        RETURN: Emit a payload with ``error=...`` set and continue the
            stream. Suitable for long unattended runs where a single bad
            poll shouldn't kill the recording.
        SKIP: Drop the failed payload silently. Increments
            ``AcquisitionSummary.errors_observed`` so the run report still
            reflects the failure count.
    """

    RAISE = "raise"
    RETURN = "return"
    SKIP = "skip"


class OverflowPolicy(StrEnum):
    """How a recorder reacts when its outgoing stream buffer fills.

    Attributes:
        BLOCK: Backpressure — the producer awaits buffer space. Default;
            preserves every payload at the cost of stalling the upstream
            buffer pool. Slow consumers risk SDK-level OVERRUN.
        DROP_OLDEST: Discard the head of the buffer to make room. Trades
            payload completeness for producer liveness. Increments
            ``AcquisitionSummary.payloads_dropped``.
        DROP_NEWEST: Discard the incoming payload. Same trade-off as
            ``DROP_OLDEST`` but the consumer sees only payloads from
            before the overflow started.
    """

    BLOCK = "block"
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"


@dataclass(slots=True)
class AcquisitionSummary:
    """Mutable summary of one recording session.

    Updated in place by the recorder; ``finished_at`` is set on context exit.
    The :class:`Recording[T]` handle exposes this so consumers can read
    progress without poking at the recorder's internals.

    ``overruns_observed`` and ``underruns_observed`` are SDK-level,
    distinguishable from ``payloads_dropped`` (which counts
    consumer-side losses under ``DROP_*`` overflow policies). See
    docs/design.md §14.1 for the rationale.
    """

    started_at: datetime
    finished_at: datetime | None = None
    payloads_emitted: int = 0
    payloads_dropped: int = 0
    errors_observed: int = 0
    overruns_observed: int = 0
    underruns_observed: int = 0
    extra: dict[str, object] = field(default_factory=_empty_extra)


@dataclass(slots=True)
class Recording[T]:
    """Active-recording handle returned by :func:`record` / :func:`record_polled`.

    Attributes:
        stream: AnyIO receive stream of payloads. Closes when the recorder
            context manager exits.
        summary: Mutable :class:`AcquisitionSummary` updated in place during
            the run. ``summary.finished_at`` is set on context exit.
        rate_hz: Configured cadence of the active recording. ``None`` for
            on-demand mode.
    """

    stream: MemoryObjectReceiveStream[T]
    summary: AcquisitionSummary
    rate_hz: float | None
