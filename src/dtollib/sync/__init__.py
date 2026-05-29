"""Sync facade — :class:`Dtol`, :class:`SyncDtolSession`, :class:`SyncPortal`.

Async is canonical; the sync facade wraps it through :class:`SyncPortal`
so scripts, notebooks, and REPL sessions can drive tasks without
``await``.

Provides:

- :class:`Dtol` — entry point (``Dtol.open_device(spec)``).
- :class:`SyncDtolSession` — blocking wrapper over
  :class:`~dtollib.tasks.DtolSession`.
- :func:`record` / :func:`record_polled` — blocking streaming wrappers
  yielding a :class:`SyncRecording`.
- :class:`SyncPortal` / :func:`run_sync`.
"""

from __future__ import annotations

from dtollib.streaming import ErrorPolicy, OverflowPolicy
from dtollib.sync.daq import Dtol
from dtollib.sync.portal import SyncAsyncIterator, SyncPortal, run_sync
from dtollib.sync.recording import (
    AcquisitionSummary,
    SyncRecording,
    record,
    record_polled,
)
from dtollib.sync.session import SyncDtolSession

__all__ = [
    "AcquisitionSummary",
    "Dtol",
    "ErrorPolicy",
    "OverflowPolicy",
    "SyncAsyncIterator",
    "SyncDtolSession",
    "SyncPortal",
    "SyncRecording",
    "record",
    "record_polled",
    "run_sync",
]
