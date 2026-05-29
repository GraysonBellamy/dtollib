"""``DtolSession`` counter read-path tests."""

from __future__ import annotations

import pytest

from dtollib import (
    CounterEdgeCount,
    CounterFrequency,
    DtolTaskStateError,
    QuadratureDecoder,
    Tachometer,
    TaskSpec,
    ThermocoupleInput,
    ThermocoupleType,
)
from dtollib.tasks.session import DtolSession
from dtollib.testing import make_fake_backend

pytestmark = pytest.mark.anyio


async def test_read_events_counter() -> None:
    backend = make_fake_backend(include_dt9806=True)
    spec = TaskSpec(
        name="counts", board="DT9806(00)", channels=[CounterEdgeCount(physical_channel=0)]
    )
    async with DtolSession(spec, backend) as session:
        backend.script_count(session.raw_hdass, 0, 12345)
        reading = await session.read_events()
    assert reading.values["ch0"] == 12345
    assert reading.units["ch0"] == "counts"
    assert reading.device == "counts"


async def test_measure_frequency_counter() -> None:
    backend = make_fake_backend(include_dt9806=True)
    spec = TaskSpec(
        name="freq", board="DT9806(00)", channels=[CounterFrequency(physical_channel=0)]
    )
    async with DtolSession(spec, backend) as session:
        backend.script_frequency(session.raw_hdass, 0, 2500.0)
        reading = await session.measure_frequency()
    assert reading.values["ch0"] == 2500.0
    assert reading.units["ch0"] == "Hz"


async def test_quadrature_position_via_read_events() -> None:
    backend = make_fake_backend(include_dt9806=True)
    spec = TaskSpec(
        name="pos", board="DT9806(00)", channels=[QuadratureDecoder(physical_channel=0)]
    )
    async with DtolSession(spec, backend) as session:
        backend.script_count(session.raw_hdass, 0, -42 & 0xFFFFFFFF)
        reading = await session.read_events()
    assert "ch0" in reading.values


async def test_tachometer_via_measure_frequency() -> None:
    backend = make_fake_backend(include_dt9806=True)
    spec = TaskSpec(name="rpm", board="DT9806(00)", channels=[Tachometer(physical_channel=0)])
    async with DtolSession(spec, backend) as session:
        backend.script_frequency(session.raw_hdass, 0, 60.0)
        reading = await session.measure_frequency()
    assert reading.values["ch0"] == 60.0


async def test_read_events_rejects_non_counter_task() -> None:
    backend = make_fake_backend(include_dt9806=True)
    spec = TaskSpec(
        name="tc",
        board="DT9806(00)",
        channels=[
            ThermocoupleInput(
                physical_channel=1,
                thermocouple_type=ThermocoupleType.K,
                min_val_degc=-50.0,
                max_val_degc=200.0,
            )
        ],
    )
    async with DtolSession(spec, backend) as session:
        with pytest.raises(DtolTaskStateError, match="not a counter subsystem"):
            await session.read_events()


async def test_quadrature_and_tachometer_route_to_distinct_subsystems() -> None:
    """Quadrature and tachometer tasks bind their own subsystems concurrently."""
    backend = make_fake_backend(include_dt9806=True)
    quad_spec = TaskSpec(
        name="q", board="DT9806(00)", channels=[QuadratureDecoder(physical_channel=0)]
    )
    tach_spec = TaskSpec(name="t", board="DT9806(00)", channels=[Tachometer(physical_channel=0)])
    async with DtolSession(quad_spec, backend) as q, DtolSession(tach_spec, backend) as t:
        backend.script_count(q.raw_hdass, 0, 7)
        backend.script_frequency(t.raw_hdass, 0, 3.0)
        assert (await q.read_events()).values["ch0"] == 7
        assert (await t.measure_frequency()).values["ch0"] == 3.0
