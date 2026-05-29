"""Async-discovery tests against :class:`FakeDtolBackend`."""

from __future__ import annotations

import pytest

from dtollib.system import find_devices, find_subsystems
from dtollib.testing import make_fake_backend


@pytest.mark.anyio
async def test_find_devices_against_fake_backend() -> None:
    backend = make_fake_backend(include_dt9805=True, include_dt9806=True)
    boards = await find_devices(backend=backend)
    names = [b.name for b in boards]
    assert "DT9805(00)" in names
    assert "DT9806(00)" in names


@pytest.mark.anyio
async def test_find_devices_empty_when_no_boards() -> None:
    backend = make_fake_backend()
    boards = await find_devices(backend=backend)
    assert boards == []


@pytest.mark.anyio
async def test_find_subsystems_by_name() -> None:
    backend = make_fake_backend(include_dt9805=True)
    subs = await find_subsystems("DT9805(00)", backend=backend)
    assert len(subs) == 1
    assert subs[0].type.value == "analog_input"
    # Real DT9805/06 A/D returns raw codes, not firmware-linearised floats.
    assert subs[0].returns_floats is False


@pytest.mark.anyio
async def test_find_subsystems_by_board_info() -> None:
    backend = make_fake_backend(include_dt9805=True)
    boards = await find_devices(backend=backend)
    subs = await find_subsystems(boards[0], backend=backend)
    assert len(subs) >= 1


@pytest.mark.anyio
async def test_find_devices_swallows_backend_errors() -> None:
    """:func:`find_devices` never raises — it logs and returns an empty list."""

    class _BrokenBackend:
        def enum_boards(self) -> list:  # type: ignore[type-arg]
            raise RuntimeError("simulated SDK failure")

    boards = await find_devices(backend=_BrokenBackend())  # type: ignore[arg-type]
    assert boards == []
