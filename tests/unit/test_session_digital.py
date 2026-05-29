"""Digital-input port read path — byte decomposition into per-line bools."""

from __future__ import annotations

import pytest

from dtollib import (
    DataFlow,
    DigitalInputPort,
    DigitalLine,
    SubsystemType,
    TaskSpec,
    open_device,
)
from dtollib.errors import DtolValidationError
from dtollib.testing import make_fake_backend

pytestmark = pytest.mark.anyio


def _din_spec(*, lines: tuple[DigitalLine, ...]) -> TaskSpec:
    return TaskSpec(
        name="din-read",
        board="DT9806(00)",
        subsystem_type=SubsystemType.DIGITAL_INPUT,
        channels=[DigitalInputPort(physical_channel=0, name="din", lines=lines)],
        data_flow=DataFlow.SINGLE_VALUE,
    )


async def test_din_poll_decomposes_byte_into_lines() -> None:
    backend = make_fake_backend(include_dt9806=True)
    spec = _din_spec(
        lines=(
            DigitalLine(bit=0, name="d0"),
            DigitalLine(bit=1, name="d1"),
            DigitalLine(bit=7, name="d7"),
        )
    )
    session = await open_device(spec, backend=backend, autostart=False)
    try:
        await session.configure()
        backend.scalar_values[(session.hdass, 0)] = 0b1000_0010
        reading = await session.poll()
        # Raw byte surfaced under the port name.
        assert reading.values["din"] == 0b1000_0010
        # Per-line bools decomposed from the byte.
        assert reading.values["d0"] is False
        assert reading.values["d1"] is True
        assert reading.values["d7"] is True
    finally:
        await session.close()


async def test_din_port_index_past_single_port_rejected_at_configure() -> None:
    # A port index past num_channels (the old per-line model produced these)
    # must be rejected at configure, before any SDK read.
    backend = make_fake_backend(include_dt9806=True)
    bad = TaskSpec(
        name="din-bad",
        board="DT9806(00)",
        subsystem_type=SubsystemType.DIGITAL_INPUT,
        channels=[DigitalInputPort(physical_channel=2, name="din")],
        data_flow=DataFlow.SINGLE_VALUE,
    )
    with pytest.raises(DtolValidationError, match="out of range"):
        await open_device(bad, backend=backend, autostart=False)
