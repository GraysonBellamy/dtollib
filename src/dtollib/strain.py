"""Strain / bridge volts→engineering conversion helpers — §8.B6.

The strain/bridge read path is two stages: a normal voltage acquisition
(``poll`` / ``record``) yields the bridge output voltage, then these
helpers turn that voltage into engineering units (strain ε, or the
bridge transducer's rated quantity) via the SDK's
``olDaVoltsToStrain`` / ``olDaVoltsToBridgeBasedSensor``.

The helpers read the gage/bridge parameters straight off the channel
spec, so a caller only supplies the measured unstrained/strained
voltages.  The conversions are pure SDK math (no HDASS, no capability
gate) — the capability gate lives upstream at configure time
(:func:`dtollib.tasks.builder._require_io_type_supported`).

Pure application-side rosette transforms (rectangular / delta) live in
:mod:`dtollib.utils` for callers who prefer not to round-trip the SDK.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dtollib.capi.constants import (
    OL_STRAIN_FULL_BRIDGE_AXIAL,
    OL_STRAIN_FULL_BRIDGE_BENDING,
    OL_STRAIN_FULL_BRIDGE_BENDING_POISSON,
    OL_STRAIN_HALF_BRIDGE_BENDING,
    OL_STRAIN_HALF_BRIDGE_POISSON,
    OL_STRAIN_QUARTER_BRIDGE,
    OL_STRAIN_QUARTER_BRIDGE_TEMP_COMPENSATION,
)
from dtollib.channels.analog_input import StrainGageConfiguration

if TYPE_CHECKING:
    from dtollib.backend.base import DtolBackend
    from dtollib.channels.analog_input import BridgeInput, StrainInput

__all__ = ["bridge_value_from_volts", "strain_from_volts"]


_STRAIN_CONFIG_TO_OL: dict[StrainGageConfiguration, int] = {
    StrainGageConfiguration.FULL_BRIDGE_BENDING: OL_STRAIN_FULL_BRIDGE_BENDING,
    StrainGageConfiguration.FULL_BRIDGE_BENDING_POISSON: OL_STRAIN_FULL_BRIDGE_BENDING_POISSON,
    StrainGageConfiguration.FULL_BRIDGE_AXIAL: OL_STRAIN_FULL_BRIDGE_AXIAL,
    StrainGageConfiguration.HALF_BRIDGE_POISSON: OL_STRAIN_HALF_BRIDGE_POISSON,
    StrainGageConfiguration.HALF_BRIDGE_BENDING: OL_STRAIN_HALF_BRIDGE_BENDING,
    StrainGageConfiguration.QUARTER_BRIDGE: OL_STRAIN_QUARTER_BRIDGE,
    StrainGageConfiguration.QUARTER_BRIDGE_TEMP_COMPENSATION: (
        OL_STRAIN_QUARTER_BRIDGE_TEMP_COMPENSATION
    ),
}


def strain_from_volts(
    backend: DtolBackend,
    spec: StrainInput,
    *,
    v_unstrained: float,
    v_strained: float,
    v_excitation: float | None = None,
) -> float:
    """Convert a measured strain-bridge voltage to strain (ε).

    Reads the gage parameters (configuration, gage factor, resistance,
    lead resistance, Poisson ratio) from ``spec``; only the two measured
    voltages are supplied per reading.  ``v_excitation`` defaults to the
    spec's configured excitation voltage.
    """
    return backend.volts_to_strain(
        _STRAIN_CONFIG_TO_OL[spec.configuration],
        v_unstrained,
        v_strained,
        spec.excitation_voltage if v_excitation is None else v_excitation,
        spec.gage_factor,
        spec.gage_resistance_ohms,
        spec.lead_resistance_ohms,
        spec.poisson_ratio,
        0.0,
    )


def bridge_value_from_volts(
    backend: DtolBackend,
    spec: BridgeInput,
    *,
    v_unstrained: float,
    v_strained: float,
    v_excitation: float | None = None,
    temperature_coefficient: float = 0.0,
) -> float:
    """Convert a measured bridge-transducer voltage to its engineering value.

    Reads the bridge parameters (nominal resistance, lead resistance,
    rated sensitivity) from ``spec``; ``v_excitation`` defaults to the
    spec's configured excitation voltage.
    """
    return backend.volts_to_bridge_based_sensor(
        v_unstrained,
        v_strained,
        spec.excitation_voltage if v_excitation is None else v_excitation,
        temperature_coefficient,
        spec.nominal_resistance_ohms,
        spec.lead_resistance_ohms,
        spec.sensitivity_mv_per_v,
        0.0,
    )
