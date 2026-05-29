"""Tests for :class:`dtollib.sync.SyncPortal`."""

from __future__ import annotations

import pytest

from dtollib.sync import SyncPortal, run_sync


def test_portal_runs_async_function() -> None:
    """A simple async function returns its result through the portal."""

    async def add(a: int, b: int) -> int:
        return a + b

    with SyncPortal() as portal:
        assert portal.running is True
        assert portal.call(add, 2, 3) == 5
    # Portal is no longer running after exit.


def test_portal_not_reusable() -> None:
    """A ``SyncPortal`` instance cannot be re-entered after exit."""
    portal = SyncPortal()
    with portal:
        pass
    with pytest.raises(RuntimeError, match="not reusable"), portal:
        pass


def test_call_outside_running_raises() -> None:
    """``portal.call`` without ``__enter__`` raises ``RuntimeError``."""

    async def noop() -> None:
        return None

    portal = SyncPortal()
    with pytest.raises(RuntimeError, match="not running"):
        portal.call(noop)


def test_run_sync_one_shot() -> None:
    """:func:`run_sync` runs a coroutine in a throwaway portal."""

    async def double(x: int) -> int:
        return x * 2

    assert run_sync(double, 21) == 42


def test_exception_propagates() -> None:
    """An exception raised inside the coroutine propagates through ``call``."""

    async def boom() -> None:
        raise ValueError("from coroutine")

    with SyncPortal() as portal, pytest.raises(ValueError, match="from coroutine"):
        portal.call(boom)
