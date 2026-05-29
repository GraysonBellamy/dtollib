"""Regression: DtolSession.close() must release the subsystem even when
the surrounding scope is cancelled.

A cancelled or timed-out session (e.g. ``move_on_after`` wrapping
``record()``) previously left the HDASS reserved because close()'s
awaited ``release_dass`` / ``terminate`` were cancelled mid-flight —
observed on real hardware as ECODE 20 "Subsystem in use". close() now
shields its teardown.
"""

from __future__ import annotations

import anyio
import pytest

from dtollib import AnalogInputVoltage, DataFlow, TaskSpec, open_device
from dtollib.testing import make_fake_backend


@pytest.mark.anyio
async def test_close_releases_subsystem_under_cancellation() -> None:
    backend = make_fake_backend(include_dt9805=True)
    spec = TaskSpec(
        name="t",
        board="DT9805(00)",
        channels=[AnalogInputVoltage(physical_channel=0, name="v0")],
        data_flow=DataFlow.SINGLE_VALUE,
    )
    session = await open_device(spec, backend=backend)
    backend.operations.clear()

    # Cancel the scope, then close — without the shield the awaited
    # release_dass/terminate would be cancelled before running.
    with anyio.CancelScope() as scope:
        scope.cancel()
        await session.close()

    ops = [op for op, _ in backend.operations]
    assert "release_dass" in ops, f"release_dass not called under cancellation; got {ops}"
    assert "terminate" in ops, f"terminate not called under cancellation; got {ops}"
    assert session.closed is True


@pytest.mark.anyio
async def test_close_is_idempotent_after_cancelled_close() -> None:
    backend = make_fake_backend(include_dt9805=True)
    spec = TaskSpec(
        name="t",
        board="DT9805(00)",
        channels=[AnalogInputVoltage(physical_channel=0, name="v0")],
        data_flow=DataFlow.SINGLE_VALUE,
    )
    session = await open_device(spec, backend=backend)
    with anyio.CancelScope() as scope:
        scope.cancel()
        await session.close()
    backend.operations.clear()
    await session.close()  # second close: no-op, no extra release/terminate
    assert backend.operations == []
