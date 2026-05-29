"""TEDS (Transducer Electronic Data Sheet) read helpers — §8.B5.

IEEE-1451.4 smart transducers store calibration metadata on the sensor
(hardware TEDS) or in a sidecar file (virtual TEDS).  The DataAcq SDK
exposes four readers — :func:`read_strain_gage_teds`,
:func:`read_strain_gage_virtual_teds`, :func:`read_bridge_sensor_teds`,
:func:`read_bridge_sensor_virtual_teds` — backed by ``olDaRead*Teds``.

Hardware reads are **capability-gated**: the owned DT9805/DT9806 report
``supports_multisensor=False`` and the SDK returns ECODE 36, so the
hardware variants raise :class:`~dtollib.errors.DtolCapabilityError`
before touching the SDK (mirroring the :class:`~dtollib.tasks.TaskBuilder`
gate).  Virtual reads parse a file and need no hardware, so they are not
gated.

Real-sensor verification is deferred until a multi-sensor DT module
(DT9828/9829/9837) is on the bench; until then the read path is exercised
against :class:`~dtollib.backend.fake.FakeDtolBackend` scripted payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dtollib.errors import DtolCapabilityError, ErrorContext

if TYPE_CHECKING:
    from dtollib.backend.base import DtolBackend

__all__ = [
    "BridgeSensorTeds",
    "StrainGageTeds",
    "read_bridge_sensor_teds",
    "read_bridge_sensor_virtual_teds",
    "read_strain_gage_teds",
    "read_strain_gage_virtual_teds",
]


def _f(raw: dict[str, object], key: str) -> float:
    value = raw.get(key, 0.0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _i(raw: dict[str, object], key: str) -> int:
    value = raw.get(key, 0)
    return int(value) if isinstance(value, (int, float)) else 0


def _s(raw: dict[str, object], key: str) -> str:
    value = raw.get(key, "")
    return value if isinstance(value, str) else str(value)


@dataclass(frozen=True, slots=True, kw_only=True)
class StrainGageTeds:
    """Decoded strain-gage TEDS (``STRAIN_GAGE_TEDS``, TedsApi.h).

    Carries the basic-TEDS identity block plus the strain-gage-specific
    calibration fields.  ``raw`` retains every field the SDK populated so
    callers can reach less-common members without a wrapper attribute.
    """

    manufacturer_id: int
    model_number: int
    version_letter: str
    version_number: int
    serial_number: int
    gage_factor: float
    gage_resistance_ohms: float
    poisson_coefficient: float
    min_physical_value: float
    max_physical_value: float
    raw: dict[str, object]

    @classmethod
    def from_raw(cls, raw: dict[str, object]) -> StrainGageTeds:
        """Build from the backend's flattened ``STRAIN_GAGE_TEDS`` dict."""
        return cls(
            manufacturer_id=_i(raw, "manufacturerId"),
            model_number=_i(raw, "modelNumber"),
            version_letter=_s(raw, "versionLetter"),
            version_number=_i(raw, "versionNumber"),
            serial_number=_i(raw, "serialNumber"),
            gage_factor=_f(raw, "gageFactor"),
            gage_resistance_ohms=_f(raw, "sensorImped"),
            poisson_coefficient=_f(raw, "poissonCoef"),
            min_physical_value=_f(raw, "minPhysicalValue"),
            max_physical_value=_f(raw, "maxPhysicalValue"),
            raw=dict(raw),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class BridgeSensorTeds:
    """Decoded bridge-sensor TEDS (``BRIDGE_SENSOR_TEDS``, TedsApi.h)."""

    manufacturer_id: int
    model_number: int
    version_letter: str
    version_number: int
    serial_number: int
    sensor_impedance_ohms: float
    excitation_nominal_v: float
    min_physical_value: float
    max_physical_value: float
    min_electrical_value: float
    max_electrical_value: float
    raw: dict[str, object]

    @classmethod
    def from_raw(cls, raw: dict[str, object]) -> BridgeSensorTeds:
        """Build from the backend's flattened ``BRIDGE_SENSOR_TEDS`` dict."""
        return cls(
            manufacturer_id=_i(raw, "manufacturerId"),
            model_number=_i(raw, "modelNumber"),
            version_letter=_s(raw, "versionLetter"),
            version_number=_i(raw, "versionNumber"),
            serial_number=_i(raw, "serialNumber"),
            sensor_impedance_ohms=_f(raw, "sensorImped"),
            excitation_nominal_v=_f(raw, "exciteAmplNom"),
            min_physical_value=_f(raw, "minPhysicalValue"),
            max_physical_value=_f(raw, "maxPhysicalValue"),
            min_electrical_value=_f(raw, "minElecVal"),
            max_electrical_value=_f(raw, "maxElecVal"),
            raw=dict(raw),
        )


def _require_teds_capability(backend: DtolBackend, hdass: int, op: str) -> None:
    """Raise ``DtolCapabilityError`` for a hardware-TEDS read on a plain A/D."""
    if not backend.query_capabilities(hdass).supports_multisensor:
        raise DtolCapabilityError(
            f"{op}: hardware TEDS requires an intelligent multi-sensor subsystem "
            f"(OLSSC_SUP_MULTISENSOR); this subsystem reports "
            f"supports_multisensor=False. The DT9805/DT9806 cannot read on-sensor "
            f"TEDS — use a virtual-TEDS file or a DT9828/9829/9837-class module.",
            context=ErrorContext(operation=op, ecode=None),
        )


def read_strain_gage_teds(backend: DtolBackend, hdass: int, channel: int) -> StrainGageTeds:
    """Read on-sensor strain-gage TEDS for ``channel`` (capability-gated)."""
    _require_teds_capability(backend, hdass, "read_strain_gage_teds")
    return StrainGageTeds.from_raw(backend.read_strain_gage_hardware_teds(hdass, channel))


def read_strain_gage_virtual_teds(backend: DtolBackend, path: str) -> StrainGageTeds:
    """Read a strain-gage virtual-TEDS file (no hardware, no capability gate)."""
    return StrainGageTeds.from_raw(backend.read_strain_gage_virtual_teds(path))


def read_bridge_sensor_teds(backend: DtolBackend, hdass: int, channel: int) -> BridgeSensorTeds:
    """Read on-sensor bridge TEDS for ``channel`` (capability-gated)."""
    _require_teds_capability(backend, hdass, "read_bridge_sensor_teds")
    return BridgeSensorTeds.from_raw(backend.read_bridge_sensor_hardware_teds(hdass, channel))


def read_bridge_sensor_virtual_teds(backend: DtolBackend, path: str) -> BridgeSensorTeds:
    """Read a bridge virtual-TEDS file (no hardware, no capability gate)."""
    return BridgeSensorTeds.from_raw(backend.read_bridge_sensor_virtual_teds(path))
