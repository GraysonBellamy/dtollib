"""SDK notification + enumeration callback typedefs.

This module re-exports the ``WINFUNCTYPE`` (or ``CFUNCTYPE`` on
non-Windows) enumeration function-pointer types declared in
:mod:`dtollib.capi.types` and adds:

- :class:`SdkEventKind` — enum naming the eleven ``OLDA_WM_*``
  notification messages the callback bridge demultiplexes.
- :func:`event_kind_from_message` — convert a raw ``UINT message_id``
  (the ``OLDA_WM_*`` window message posted via ``olDaSetWndHandle``) into
  an :class:`SdkEventKind`.
"""

from __future__ import annotations

from enum import IntEnum

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
from dtollib.capi.types import (
    BOARD_ENUM_EX_PROC,
    BOARD_ENUM_PROC,
    CHAN_CAP_ENUM_PROC,
    SS_CAP_ENUM_PROC,
    SS_ENUM_PROC,
)

__all__ = [
    "BOARD_ENUM_EX_PROC",
    "BOARD_ENUM_PROC",
    "CHAN_CAP_ENUM_PROC",
    "SS_CAP_ENUM_PROC",
    "SS_ENUM_PROC",
    "SdkEventKind",
    "event_kind_from_message",
]


class SdkEventKind(IntEnum):
    """SDK notification message kind (one of the eleven ``OLDA_WM_*``).

    The eleven ``OLDA_WM_*`` messages from dasdk_digest.md §6.  Values
    are the raw SDK message IDs declared in :mod:`dtollib.capi.constants`
    so an :class:`SdkEventKind` instance can be compared directly
    against the integer ``message_id`` the SDK posts to the message
    window.

    The callback bridge demultiplexes incoming messages into either
    ``DaqBlock`` emissions (``BUFFER_DONE``) or typed-exception
    routing (``OVERRUN_ERROR`` / ``UNDERRUN_ERROR`` /
    ``TRIGGER_ERROR``).
    """

    BUFFER_DONE = OLDA_WM_BUFFER_DONE
    BUFFER_REUSED = OLDA_WM_BUFFER_REUSED
    PRETRIGGER_BUFFER_DONE = OLDA_WM_PRETRIGGER_BUFFER_DONE
    QUEUE_DONE = OLDA_WM_QUEUE_DONE
    QUEUE_STOPPED = OLDA_WM_QUEUE_STOPPED
    IO_COMPLETE = OLDA_WM_IO_COMPLETE
    TRIGGER_ERROR = OLDA_WM_TRIGGER_ERROR
    OVERRUN_ERROR = OLDA_WM_OVERRUN_ERROR
    UNDERRUN_ERROR = OLDA_WM_UNDERRUN_ERROR
    EVENT_DONE = OLDA_WM_EVENT_DONE
    MEASURE_DONE = OLDA_WM_MEASURE_DONE


def event_kind_from_message(message_id: int) -> SdkEventKind | None:
    """Map a raw SDK ``message_id`` to :class:`SdkEventKind`.

    Args:
        message_id: ``UINT`` window-message ID the SDK posts to the
            message window via ``olDaSetWndHandle``.

    Returns:
        Matching :class:`SdkEventKind`, or ``None`` if the message ID
        is not one of the eleven documented events.  An unknown ID is
        not an error per se — newer SDK revisions may introduce
        messages we have not yet bound; the bridge logs and ignores
        them.
    """
    try:
        return SdkEventKind(message_id)
    except ValueError:
        return None
