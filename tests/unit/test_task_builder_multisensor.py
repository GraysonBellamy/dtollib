"""``TaskBuilder`` multi-sensor dispatch + capability gate.

Two halves:

- On an intelligent multi-sensor subsystem the builder re-types each channel
  (``set_multi_sensor_type`` BEFORE ``add_channel``) and runs the full
  configure path.
- On the owned DT9805/06 (``supports_multisensor=False``) every multi-sensor
  spec is rejected at configure time with :class:`DtolCapabilityError` — the
  builder gate, not a raw backend ECODE 36.
"""

from __future__ import annotations

import pytest

from dtollib import (
    AnalogInputVoltage,
    BridgeInput,
    CurrentInput,
    DtolCapabilityError,
    IepeInput,
    IOType,
    ResistanceInput,
    RtdInput,
    StrainInput,
    ThermistorInput,
    ThermocoupleInput,
    ThermocoupleType,
)
from dtollib.capi.constants import OLSS_AD
from dtollib.tasks.builder import TaskBuilder
from dtollib.tasks.spec import TaskSpec
from dtollib.testing import make_fake_backend, make_fake_dt9805, make_fake_multisensor


def _configure(backend: object, board: str, channel: object) -> tuple[int, list[str]]:
    hdrvr = backend.initialize(board)  # type: ignore[attr-defined]
    hdass = backend.get_dass(hdrvr, OLSS_AD, 0)  # type: ignore[attr-defined]
    caps = backend.query_capabilities(hdass)  # type: ignore[attr-defined]
    spec = TaskSpec(name="ms", board=board, channels=[channel])  # type: ignore[list-item]
    backend.operations.clear()  # type: ignore[attr-defined]
    TaskBuilder(backend).configure_single_value(hdass, spec, caps)  # type: ignore[arg-type]
    return hdass, [name for name, _ in backend.operations]  # type: ignore[attr-defined]


_MULTI_SENSOR_SPECS = [
    (RtdInput(physical_channel=0), IOType.RTD),
    (ThermistorInput(physical_channel=0, a=1e-3, b=2e-4, c=1e-7), IOType.THERMISTOR),
    (ResistanceInput(physical_channel=0), IOType.RESISTANCE),
    (CurrentInput(physical_channel=0), IOType.CURRENT),
    (IepeInput(physical_channel=0), IOType.ACCELEROMETER),
    (StrainInput(physical_channel=0), IOType.STRAIN_GAGE),
    (BridgeInput(physical_channel=0), IOType.BRIDGE),
]


class TestMultiSensorBoard:
    @pytest.mark.parametrize(("spec", "io_type"), _MULTI_SENSOR_SPECS)
    def test_retypes_then_adds(self, spec: object, io_type: IOType) -> None:
        backend = make_fake_backend(boards=[make_fake_multisensor()])
        hdass, names = _configure(backend, "DT9829(00)", spec)
        # Ordering invariant — re-type precedes add_channel (design.md §8.5a).
        assert names.index("set_multi_sensor_type") < names.index("add_channel")
        assert backend.multi_sensor_types[(hdass, 0)] == io_type
        assert "commit" in names


class TestOwnedHardwareGate:
    @pytest.mark.parametrize(("spec", "_io_type"), _MULTI_SENSOR_SPECS)
    def test_multi_sensor_spec_rejected_on_plain_ad(self, spec: object, _io_type: IOType) -> None:
        backend = make_fake_backend(boards=[make_fake_dt9805()])
        with pytest.raises(DtolCapabilityError, match="requires an intelligent multi-sensor"):
            _configure(backend, "DT9805(00)", spec)

    def test_voltage_still_allowed_on_plain_ad(self) -> None:
        backend = make_fake_backend(boards=[make_fake_dt9805()])
        _hdass, names = _configure(backend, "DT9805(00)", AnalogInputVoltage(physical_channel=0))
        assert "add_channel" in names

    def test_thermocouple_still_allowed_on_plain_ad(self) -> None:
        backend = make_fake_backend(boards=[make_fake_dt9805()])
        spec = ThermocoupleInput(
            physical_channel=0,
            thermocouple_type=ThermocoupleType.K,
            min_val_degc=-50.0,
            max_val_degc=200.0,
        )
        _hdass, names = _configure(backend, "DT9805(00)", spec)
        assert "add_channel" in names
