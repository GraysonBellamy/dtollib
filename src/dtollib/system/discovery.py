"""Async discovery — :func:`find_devices`, :func:`find_subsystems`.

Both helpers wrap blocking SDK calls in :func:`anyio.to_thread.run_sync`
so they fit naturally into an :mod:`anyio`-flavoured event loop.

Failure mode: discovery never raises.  If the SDK call fails, the
result list is empty and the failure is logged.  Higher-level CLIs
(:mod:`dtollib.cli.diag`, :mod:`dtollib.cli.discover`) probe the SDK
load directly and report dependency failures distinctly from
"no boards installed".

Design reference: docs/design.md §20.1.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import anyio.to_thread  # pyright: ignore[reportMissingImports]

if TYPE_CHECKING:
    from dtollib.backend.base import DtolBackend
    from dtollib.system.models import BoardInfo, SubsystemInfo


__all__ = ["find_devices", "find_subsystems"]


_logger = logging.getLogger("dtollib.system.discovery")


async def find_devices(*, backend: DtolBackend | None = None) -> list[BoardInfo]:
    """Enumerate every installed DT-Open Layers board.

    Args:
        backend: Backend to query.  Defaults to a freshly-constructed
            :class:`~dtollib.backend.dataacq.DataAcqBackend` — the
            test suite injects a
            :class:`~dtollib.backend.fake.FakeDtolBackend` here.

    Returns:
        List of :class:`BoardInfo`.  Empty list if no boards are
        installed or the SDK is unavailable.
    """
    resolved = backend if backend is not None else await _default_backend()
    try:
        return await anyio.to_thread.run_sync(resolved.enum_boards)
    except Exception:
        _logger.exception("find_devices failed")
        return []


async def find_subsystems(
    board: BoardInfo | str,
    *,
    backend: DtolBackend | None = None,
) -> list[SubsystemInfo]:
    """Enumerate subsystems on a board.

    Args:
        board: A :class:`BoardInfo` from :func:`find_devices`, or a
            raw board name string.
        backend: Backend to query.  Defaults to a freshly-constructed
            :class:`~dtollib.backend.dataacq.DataAcqBackend`.

    Returns:
        List of :class:`SubsystemInfo`.  Empty list on any failure;
        the failure is logged.
    """
    resolved = backend if backend is not None else await _default_backend()
    name = board if isinstance(board, str) else board.name
    try:
        return await anyio.to_thread.run_sync(resolved.enum_subsystems, name)
    except Exception:
        _logger.exception("find_subsystems(%s) failed", name)
        return []


async def _default_backend() -> DtolBackend:
    """Construct a :class:`DataAcqBackend` on a worker thread.

    Importing :mod:`dtollib.backend.dataacq` is cheap, but the
    underlying ``load_openlayers`` call hits the filesystem; we route
    it through :mod:`anyio.to_thread` so an async caller never blocks
    the event loop on first SDK contact.
    """
    from dtollib.backend.dataacq import DataAcqBackend  # noqa: PLC0415

    return await anyio.to_thread.run_sync(DataAcqBackend)
