"""Multi-sensor fake-backend behaviour — MULTI_SENSOR ordering + spec recording."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dtollib import IOType, RtdInput, StrainInput
from dtollib.errors import DtolTaskStateError
from dtollib.testing import make_fake_backend, make_fake_dt9805, make_fake_multisensor

if TYPE_CHECKING:
    from dtollib.backend.fake import FakeDtolBackend


def _open_ms_hdass() -> tuple[FakeDtolBackend, int]:
    backend = make_fake_backend(boards=[make_fake_multisensor()])
    devices = backend.enum_boards()
    hdrvr = backend.initialize(devices[0].name)
    hdass = backend.get_dass(hdrvr, 0, 0)
    return backend, hdass


def test_add_channel_before_set_multi_sensor_type_raises() -> None:
    backend, hdass = _open_ms_hdass()
    with pytest.raises(DtolTaskStateError, match="before set_multi_sensor_type"):
        backend.add_channel(hdass, 0, RtdInput(physical_channel=0))


def test_set_multi_sensor_type_then_add_channel_ok() -> None:
    backend, hdass = _open_ms_hdass()
    backend.set_multi_sensor_type(hdass, 0, IOType.RTD)
    backend.add_channel(hdass, 0, RtdInput(physical_channel=0))
    assert backend.multi_sensor_types[(hdass, 0)] == IOType.RTD
    assert isinstance(backend.multi_sensor_specs[(hdass, 0)], RtdInput)


def test_operations_log_records_ordering() -> None:
    backend, hdass = _open_ms_hdass()
    backend.set_multi_sensor_type(hdass, 1, IOType.STRAIN_GAGE)
    backend.add_channel(hdass, 0, StrainInput(physical_channel=1))
    ops = [op for op, _ in backend.operations]
    # set_multi_sensor_type must precede add_channel for the channel.
    assert ops.index("set_multi_sensor_type") < ops.index("add_channel")


def test_plain_ad_board_does_not_enforce_ordering() -> None:
    # DT9805 reports supports_multisensor=False, so add_channel never gates.
    backend = make_fake_backend(boards=[make_fake_dt9805()])
    devices = backend.enum_boards()
    hdrvr = backend.initialize(devices[0].name)
    hdass = backend.get_dass(hdrvr, 0, 0)
    # No set_multi_sensor_type call, yet add_channel succeeds (voltage spec).
    from dtollib import AnalogInputVoltage

    backend.add_channel(hdass, 0, AnalogInputVoltage(physical_channel=0))
    assert (hdass, 0) not in backend.multi_sensor_specs
