"""``DtolManager.start_synchronized`` tests."""

from __future__ import annotations

import pytest

from dtollib import (
    AnalogInputVoltage,
    CounterEdgeCount,
    DtolValidationError,
    TaskSpec,
)
from dtollib.manager import DtolManager
from dtollib.tasks.models import SubsystemState
from dtollib.testing import make_fake_backend, make_fake_dt9805

pytestmark = pytest.mark.anyio


def _ai_spec(name: str) -> TaskSpec:
    return TaskSpec(
        name=name,
        board="DT9806(00)",
        channels=[AnalogInputVoltage(physical_channel=0, min_val=-10.0, max_val=10.0)],
    )


def _ct_spec(name: str) -> TaskSpec:
    return TaskSpec(name=name, board="DT9806(00)", channels=[CounterEdgeCount(physical_channel=0)])


async def test_start_synchronized_runs_both() -> None:
    backend = make_fake_backend(include_dt9806=True)
    async with DtolManager() as mgr:
        ai = await mgr.add("ai", _ai_spec("ai"), backend=backend)
        ct = await mgr.add("ct", _ct_spec("ct"), backend=backend)
        await mgr.start_synchronized(["ai", "ct"])
        assert backend.state_of(ai.raw_hdass) == SubsystemState.RUNNING
        assert backend.state_of(ct.raw_hdass) == SubsystemState.RUNNING


async def test_start_synchronized_issues_four_step_sequence() -> None:
    backend = make_fake_backend(include_dt9806=True)
    async with DtolManager() as mgr:
        await mgr.add("ai", _ai_spec("ai"), backend=backend)
        await mgr.add("ct", _ct_spec("ct"), backend=backend)
        backend.operations.clear()
        await mgr.start_synchronized(["ai", "ct"])
    names = [op for op, _payload in backend.operations]
    # get_ss_list → put × 2 → pre_start → start → release, in order.
    assert names.index("get_ss_list") < names.index("put_dass_to_ss_list")
    assert names.index("simultaneous_pre_start") < names.index("simultaneous_start")
    assert names.index("simultaneous_start") < names.index("release_ss_list")
    assert names.count("put_dass_to_ss_list") == 2


async def test_releases_list_even_when_start_fails() -> None:
    backend = make_fake_backend(include_dt9806=True)
    async with DtolManager() as mgr:
        await mgr.add("ai", _ai_spec("ai"), backend=backend)
        await mgr.add("ct", _ct_spec("ct"), backend=backend)
        backend.fail_next("simultaneous_start", code=20)
        from dtollib.errors import DtolError

        with pytest.raises(DtolError):
            await mgr.start_synchronized(["ai", "ct"])
        # The list handle is released even though the start failed (before teardown).
        names = [op for op, _payload in backend.operations]
        assert "release_ss_list" in names
        assert names.index("simultaneous_start") < names.index("release_ss_list")


async def test_multi_board_rejected() -> None:
    backend = make_fake_backend(boards=[make_fake_dt9805(), make_fake_dt9805(name="DT9805(01)")])
    async with DtolManager() as mgr:
        await mgr.add(
            "a",
            TaskSpec(
                name="a",
                board="DT9805(00)",
                channels=[AnalogInputVoltage(physical_channel=0)],
            ),
            backend=backend,
        )
        await mgr.add(
            "b",
            TaskSpec(
                name="b",
                board="DT9805(01)",
                channels=[AnalogInputVoltage(physical_channel=0)],
            ),
            backend=backend,
        )
        with pytest.raises(DtolValidationError, match="one board"):
            await mgr.start_synchronized(["a", "b"])


async def test_separate_backends_rejected() -> None:
    backend_a = make_fake_backend(include_dt9806=True)
    backend_b = make_fake_backend(include_dt9806=True)
    async with DtolManager() as mgr:
        await mgr.add("ai", _ai_spec("ai"), backend=backend_a)
        await mgr.add("ct", _ct_spec("ct"), backend=backend_b)
        with pytest.raises(DtolValidationError, match="share one backend"):
            await mgr.start_synchronized(["ai", "ct"])
