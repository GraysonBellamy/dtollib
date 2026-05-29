"""Shared driver-thread → asyncio bridge machinery.

Two bridges sit on top of the §12.3.2 callback seam:

- :mod:`dtollib.backend._callback_bridge` (input) copies completed buffers
  out and emits :class:`~dtollib.tasks.models.DaqBlock`s.
- :mod:`dtollib.backend._output_callback_bridge` (output) refills emptied
  buffers from a waveform source and re-queues them.

The *data direction* inverts between them, but the *shutdown discipline* does
not: both pull tiny event tuples off a :class:`queue.SimpleQueue` filled by the
driver thread, both wake their drainer with a sentinel, both defer an
``ErrorPolicy.RAISE`` until after an ordered, shielded teardown, and both map
the same ``OLDA_WM_*`` message ids to :class:`SdkEventKind`. That common core
lives here so neither bridge copy-pastes it.

Design reference: docs/design.md §12.3.2.
"""

from __future__ import annotations

from dtollib.capi.constants import (
    OLDA_WM_BUFFER_DONE,
    OLDA_WM_BUFFER_REUSED,
    OLDA_WM_EVENT_DONE,
    OLDA_WM_IO_COMPLETE,
    OLDA_WM_MEASURE_DONE,
    OLDA_WM_OVERRUN_ERROR,
    OLDA_WM_PRETRIGGER_BUFFER_DONE,
    OLDA_WM_QUEUE_DONE,
    OLDA_WM_QUEUE_STOPPED,
    OLDA_WM_TRIGGER_ERROR,
    OLDA_WM_UNDERRUN_ERROR,
)
from dtollib.tasks.models import SdkEventKind

__all__ = [
    "SENTINEL",
    "DrainStop",
    "DriverEvent",
    "Sentinel",
    "msg_id_to_kind",
]


class Sentinel:
    """Drainer wake-up sentinel type."""


SENTINEL = Sentinel()
"""Singleton placed on the event queue to signal an orderly drainer exit."""


class DrainStop(Exception):  # noqa: N818 - internal control-flow signal, not an error
    """Internal: unwinds a drainer loop cleanly for an ``ErrorPolicy.RAISE``.

    Raising the user-facing exception directly inside the drainer task
    triggers an abrupt anyio task-group cancellation that races the shielded
    SDK/pool teardown (observed as a segfault *or* a deadlock on real hardware
    under sustained overrun/underrun). Instead the drainer captures the
    exception, raises this sentinel to exit its loop the same way a clean
    end-of-run does, and the bridge re-raises the captured exception only
    *after* the ordered shutdown (stop → unregister → drain → free) completes.
    """


type DriverEvent = tuple[int, int, int, int]
"""``(msg_id, monotonic_ns, wparam, lparam)`` — the tuple the driver-thread
callback places on the queue. The callback does nothing else."""


_MSG_ID_TO_KIND: dict[int, SdkEventKind] = {
    OLDA_WM_BUFFER_DONE: SdkEventKind.BUFFER_DONE,
    OLDA_WM_PRETRIGGER_BUFFER_DONE: SdkEventKind.PRETRIGGER_BUFFER_DONE,
    OLDA_WM_BUFFER_REUSED: SdkEventKind.BUFFER_REUSED,
    OLDA_WM_QUEUE_DONE: SdkEventKind.QUEUE_DONE,
    OLDA_WM_QUEUE_STOPPED: SdkEventKind.QUEUE_STOPPED,
    OLDA_WM_IO_COMPLETE: SdkEventKind.IO_COMPLETE,
    OLDA_WM_EVENT_DONE: SdkEventKind.EVENT_DONE,
    OLDA_WM_MEASURE_DONE: SdkEventKind.MEASURE_DONE,
    OLDA_WM_TRIGGER_ERROR: SdkEventKind.TRIGGER_ERROR,
    OLDA_WM_OVERRUN_ERROR: SdkEventKind.OVERRUN_ERROR,
    OLDA_WM_UNDERRUN_ERROR: SdkEventKind.UNDERRUN_ERROR,
}


def msg_id_to_kind(msg_id: int) -> SdkEventKind | None:
    """Map an ``OLDA_WM_*`` message id to its :class:`SdkEventKind` (or None)."""
    return _MSG_ID_TO_KIND.get(msg_id)
