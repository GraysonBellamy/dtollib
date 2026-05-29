"""§8.B6 — strain/bridge volts→engineering helpers (fake backend)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from dtollib import (
    BridgeInput,
    StrainGageConfiguration,
    StrainInput,
    bridge_value_from_volts,
    strain_from_volts,
)
from dtollib.testing import make_fake_backend, make_fake_multisensor

if TYPE_CHECKING:
    from dtollib.backend.fake import FakeDtolBackend


def _backend() -> FakeDtolBackend:
    return make_fake_backend(boards=[make_fake_multisensor()])


def test_strain_from_volts_uses_spec_params() -> None:
    backend = _backend()
    spec = StrainInput(
        physical_channel=0,
        configuration=StrainGageConfiguration.QUARTER_BRIDGE,
        gage_factor=2.0,
        excitation_voltage=2.5,
    )
    # Fake formula: eps = -4 * Vr / GF, Vr = (Vs - Vu)/Vex.
    eps = strain_from_volts(backend, spec, v_unstrained=0.0, v_strained=0.0025)
    vr = 0.0025 / 2.5
    assert math.isclose(eps, -4.0 * vr / 2.0)


def test_strain_from_volts_excitation_override() -> None:
    backend = _backend()
    spec = StrainInput(physical_channel=0, gage_factor=2.0, excitation_voltage=2.5)
    # Overriding excitation changes Vr — the override must win over the spec.
    eps_default = strain_from_volts(backend, spec, v_unstrained=0.0, v_strained=0.001)
    eps_override = strain_from_volts(
        backend, spec, v_unstrained=0.0, v_strained=0.001, v_excitation=5.0
    )
    assert eps_override != eps_default


def test_strain_records_call_on_fake() -> None:
    backend = _backend()
    spec = StrainInput(physical_channel=0)
    strain_from_volts(backend, spec, v_unstrained=0.0, v_strained=0.001)
    assert any(op == "volts_to_strain" for op, _ in backend.operations)


def test_bridge_value_from_volts_uses_spec_params() -> None:
    backend = _backend()
    spec = BridgeInput(physical_channel=0, sensitivity_mv_per_v=2.0, excitation_voltage=10.0)
    # Fake formula: value = Vr_mV_per_V / rated, Vr_mV_per_V = (Vs-Vu)/Vex*1000.
    value = bridge_value_from_volts(backend, spec, v_unstrained=0.0, v_strained=0.01)
    vr_mv_per_v = 0.01 / 10.0 * 1000.0
    assert math.isclose(value, vr_mv_per_v / 2.0)


def test_zero_excitation_returns_zero() -> None:
    backend = _backend()
    spec = StrainInput(physical_channel=0, excitation_voltage=0.0)
    assert strain_from_volts(backend, spec, v_unstrained=0.0, v_strained=0.001) == 0.0
