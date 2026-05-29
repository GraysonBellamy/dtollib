"""Multi-sensor dispatch tests for :class:`DataAcqBackend.add_channel`.

The multi-sensor configure path issues a sequence of per-type setters on the
injected :class:`OpenLayersApi`.  These assert the dispatch forwards the right
calls (and tolerates ECODE 36) without loading the SDK.
"""

from __future__ import annotations

import threading
from typing import Any, cast

import pytest

from dtollib import (
    BridgeInput,
    CurrentInput,
    IepeInput,
    ResistanceInput,
    RtdInput,
    RtdType,
    StrainInput,
    ThermistorInput,
)
from dtollib.backend.dataacq import DataAcqBackend
from dtollib.capi.constants import (
    OL_COUPLING_AC,
    OL_EXCITATION_CURRENT_SRC_INTERNAL,
    OL_RTD_TYPE_CUSTOM,
    OL_RTD_TYPE_PT3850,
    OL_STRAIN_QUARTER_BRIDGE,
)
from dtollib.errors import DtolCapabilityError, ErrorContext


class _RecordingApi:
    """Stub OpenLayersApi recording call names + args; never touches the SDK."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str):
        def _record(*args: Any) -> Any:
            self.calls.append((name, args))
            return None

        return _record

    @property
    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


class _RejectingApi(_RecordingApi):
    """Like ``_RecordingApi`` but every multi-sensor setter returns ec=36."""

    def __getattr__(self, name: str):
        def _record(*args: Any) -> Any:
            self.calls.append((name, args))
            multi_sensor_prefixes = (
                "set_rtd",
                "set_thermistor",
                "set_strain",
                "set_bridge",
                "set_excitation",
                "set_coupling",
            )
            if name.startswith(multi_sensor_prefixes):
                raise DtolCapabilityError(
                    "not supported",
                    context=ErrorContext(operation=name, ecode=36),
                )
            return None

        return _record


def _backend_with_stub(api: _RecordingApi | None = None) -> tuple[DataAcqBackend, _RecordingApi]:
    backend = DataAcqBackend.__new__(DataAcqBackend)
    api = api or _RecordingApi()
    mutable = cast("Any", backend)
    mutable._api = api
    mutable._lock = threading.RLock()
    mutable._capability_cache = {}
    return backend, api


def test_rtd_standard_dispatch() -> None:
    backend, api = _backend_with_stub()
    backend.add_channel(0x10, 0, RtdInput(physical_channel=3, rtd_type=RtdType.PT3850))
    src = OL_EXCITATION_CURRENT_SRC_INTERNAL
    assert ("set_rtd_type", (0x10, 3, OL_RTD_TYPE_PT3850)) in api.calls
    assert ("set_rtd_r0", (0x10, 3, 100.0)) in api.calls
    assert ("set_excitation_current_source", (0x10, 3, src)) in api.calls
    # No CVD coefficients on a standard curve.
    assert "set_rtd_a" not in api.names
    # AnalogInputBase basics still issued.
    assert "set_channel_type" in api.names
    assert "set_gain_list_entry" in api.names


def test_rtd_custom_dispatch_emits_coefficients() -> None:
    backend, api = _backend_with_stub()
    backend.add_channel(
        0x10,
        0,
        RtdInput(physical_channel=0, rtd_type=RtdType.CUSTOM, a=3.9e-3, b=-5.8e-7, c=-4.2e-12),
    )
    assert ("set_rtd_type", (0x10, 0, OL_RTD_TYPE_CUSTOM)) in api.calls
    assert ("set_rtd_a", (0x10, 0, 3.9e-3)) in api.calls
    assert ("set_rtd_b", (0x10, 0, -5.8e-7)) in api.calls
    assert ("set_rtd_c", (0x10, 0, -4.2e-12)) in api.calls


def test_thermistor_dispatch() -> None:
    backend, api = _backend_with_stub()
    backend.add_channel(0x10, 0, ThermistorInput(physical_channel=2, a=1.4e-3, b=2.4e-4, c=1.0e-7))
    assert ("set_thermistor_a", (0x10, 2, 1.4e-3)) in api.calls
    assert ("set_thermistor_b", (0x10, 2, 2.4e-4)) in api.calls
    assert ("set_thermistor_c", (0x10, 2, 1.0e-7)) in api.calls


def test_iepe_dispatch_sets_ac_coupling_and_excitation() -> None:
    backend, api = _backend_with_stub()
    backend.add_channel(0x10, 0, IepeInput(physical_channel=1, excitation_current_a=0.002))
    src = OL_EXCITATION_CURRENT_SRC_INTERNAL
    assert ("set_coupling_type", (0x10, 1, OL_COUPLING_AC)) in api.calls
    assert ("set_excitation_current_source", (0x10, 1, src)) in api.calls
    assert ("set_excitation_current_value", (0x10, 1, 0.002)) in api.calls


def test_resistance_dispatch_excitation_only() -> None:
    backend, api = _backend_with_stub()
    backend.add_channel(0x10, 0, ResistanceInput(physical_channel=0, excitation_current_a=0.001))
    assert "set_excitation_current_source" in api.names
    assert ("set_excitation_current_value", (0x10, 0, 0.001)) in api.calls


def test_strain_dispatch() -> None:
    backend, api = _backend_with_stub()
    backend.add_channel(0x10, 0, StrainInput(physical_channel=4, excitation_voltage=2.5))
    assert ("set_strain_bridge_configuration", (0x10, 4, OL_STRAIN_QUARTER_BRIDGE)) in api.calls
    assert "set_strain_excitation_voltage_source" in api.names
    assert ("set_strain_excitation_voltage", (0x10, 2.5)) in api.calls
    assert ("set_strain_shunt_resistor", (0x10, 4, False)) in api.calls


def test_bridge_dispatch() -> None:
    backend, api = _backend_with_stub()
    backend.add_channel(0x10, 0, BridgeInput(physical_channel=5, excitation_voltage=5.0))
    assert "set_bridge_configuration" in api.names
    assert ("set_strain_excitation_voltage", (0x10, 5.0)) in api.calls


def test_current_dispatch_sets_range() -> None:
    backend, api = _backend_with_stub()
    backend.add_channel(0x10, 0, CurrentInput(physical_channel=0, min_val=0.004, max_val=0.02))
    # CurrentInput routes through the range helper like a voltage channel.
    assert "set_channel_range" in api.names


def test_dispatch_tolerates_ecode_36() -> None:
    """Every multi-sensor setter returning ec=36 must not crash add_channel."""
    backend, _ = _backend_with_stub(_RejectingApi())
    # Should complete without raising despite every setter returning ec=36.
    backend.add_channel(0x10, 0, RtdInput(physical_channel=0))
    backend.add_channel(0x10, 1, StrainInput(physical_channel=1))


def test_dispatch_reraises_non_36_errors() -> None:
    """A non-NOT_SUPPORTED SDK error propagates (the tolerance is ec=36 only)."""

    class _BoomApi(_RecordingApi):
        def __getattr__(self, name: str):
            def _record(*args: Any) -> Any:
                self.calls.append((name, args))
                if name == "set_rtd_type":
                    raise DtolCapabilityError(
                        "boom", context=ErrorContext(operation=name, ecode=12)
                    )
                return None

            return _record

    backend, _ = _backend_with_stub(_BoomApi())
    with pytest.raises(DtolCapabilityError):
        backend.add_channel(0x10, 0, RtdInput(physical_channel=0))
