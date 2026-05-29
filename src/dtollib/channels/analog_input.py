"""Analog-input channel specs and supporting enums.

Provides :class:`AnalogInputVoltage` and :class:`ThermocoupleInput`.
The multi-sensor subclasses :class:`RtdInput`,
:class:`ThermistorInput`, :class:`ResistanceInput`, :class:`CurrentInput`,
:class:`IepeInput`, :class:`StrainInput`, and :class:`BridgeInput` — all
sharing :class:`AnalogInputBase` — target intelligent multi-sensor
DT modules (DT9828/9829/9837); the owned DT9805/DT9806 reject them with
ECODE 36 and the :class:`~dtollib.tasks.TaskBuilder` capability gate
raises :class:`~dtollib.errors.DtolCapabilityError` first.

Design reference: docs/design.md §8.3–§8.6, docs/implementation-plan.md §8.B1.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from dtollib.channels.base import ChannelSpec
from dtollib.errors import DtolValidationError, ErrorContext
from dtollib.tasks.models import IOType
from dtollib.utils import get_thermocouple_range

__all__ = [
    "AnalogInputBase",
    "AnalogInputVoltage",
    "BridgeConfiguration",
    "BridgeInput",
    "ChannelType",
    "CjcSource",
    "CouplingType",
    "CurrentInput",
    "Encoding",
    "ExcitationSource",
    "FilterType",
    "IepeInput",
    "ResistanceInput",
    "RtdInput",
    "RtdType",
    "StrainExcitationSource",
    "StrainGageConfiguration",
    "StrainInput",
    "TemperatureUnit",
    "ThermistorInput",
    "ThermocoupleInput",
    "ThermocoupleType",
]


# ---------------------------------------------------------------------------
# Enums shared by AI subclasses
# ---------------------------------------------------------------------------


class ChannelType(StrEnum):
    """Channel-wiring discriminator (``olDaSetChannelType``)."""

    SINGLE_ENDED = "single_ended"
    DIFFERENTIAL = "differential"
    PSEUDO_DIFFERENTIAL = "pseudo_differential"


class FilterType(StrEnum):
    """Per-channel filter selection (``olDaSetChannelFilter``)."""

    NONE = "none"
    LOW_PASS = "low_pass"  # noqa: S105 - filter names, not credentials.
    HIGH_PASS = "high_pass"  # noqa: S105 - filter names, not credentials.
    BAND_PASS = "band_pass"  # noqa: S105 - filter names, not credentials.


class Encoding(StrEnum):
    """Sample-code encoding (``olDaSetEncoding``)."""

    BINARY = "binary"
    TWOS_COMPLEMENT = "twos_complement"
    OFFSET_BINARY = "offset_binary"


class CouplingType(StrEnum):
    """AC vs DC coupling (``olDaSetCouplingType``)."""

    AC = "ac"
    DC = "dc"


class ThermocoupleType(StrEnum):
    """Thermocouple letter designation (``olDaSetThermocoupleType``).

    Lock string values to single uppercase letters so they round-trip
    cleanly with NIST coefficient lookups in :mod:`dtollib.utils`.
    """

    J = "J"
    K = "K"
    T = "T"
    E = "E"
    R = "R"
    S = "S"
    B = "B"
    N = "N"


class CjcSource(StrEnum):
    """Cold-junction-compensation source (``olDaSetCjcSource``)."""

    INTERNAL = "internal"
    EXTERNAL = "external"
    NONE = "none"


class TemperatureUnit(StrEnum):
    """Temperature unit emitted by the subsystem.

    Maps to ``olDaSetTemperatureFilter`` / unit-selection setters on
    SDK builds that support per-channel temperature units.
    """

    DEG_C = "deg_c"
    DEG_F = "deg_f"
    KELVIN = "kelvin"


# ---------------------------------------------------------------------------
# Multi-sensor enums (see docs/implementation-plan.md §8.B1)
#
# String values are dtollib's stable public API; the numeric SDK
# constants they map to live in :mod:`dtollib.capi.constants` and the
# backend dispatch tables, transcribed from ``OLDADEFS.H`` /
# ``OLDAAPI.H`` per the §1.4a constant-verification gate.
# ---------------------------------------------------------------------------


class RtdType(StrEnum):
    """RTD curve selection (``olDaSetRtdType``).

    Mirrors the ``OL_RTD_TYPE_*`` family in ``OLDADEFS.H``.  The numeric
    suffix is the platinum temperature coefficient (α × 10⁴): ``PT3850``
    is the DIN/IEC 60751 standard.  :attr:`CUSTOM` defers the curve to
    explicit Callendar–Van Dusen coefficients (``r0`` / ``a`` / ``b`` /
    ``c`` on :class:`RtdInput`).
    """

    PT3750 = "pt3750"
    PT3850 = "pt3850"
    PT3911 = "pt3911"
    PT3916 = "pt3916"
    PT3920 = "pt3920"
    PT3928 = "pt3928"
    CUSTOM = "custom"


class ExcitationSource(StrEnum):
    """Excitation-current source (``olDaSetExcitationCurrentSource``).

    Mirrors ``EXCITATION_CURRENT_SRC`` in ``OLDADEFS.H``.  Used by IEPE,
    resistance, RTD, and thermistor channels that need a driven
    measurement current.
    """

    INTERNAL = "internal"
    EXTERNAL = "external"
    DISABLED = "disabled"


class StrainExcitationSource(StrEnum):
    """Strain-gage excitation-voltage source (``olDaSetStrainExcitationVoltageSource``).

    Mirrors ``STRAIN_EXCITATION_VOLTAGE_SRC`` in ``OLDADEFS.H``.  Unlike
    :class:`ExcitationSource` there is no ``DISABLED`` member — a strain
    bridge always needs an excitation voltage.
    """

    INTERNAL = "internal"
    EXTERNAL = "external"


class StrainGageConfiguration(StrEnum):
    """Strain-gage bridge wiring (``olDaSetStrainBridgeConfiguration``).

    Mirrors ``STRAIN_GAGE_CONFIGURATION`` in ``OLDADEFS.H`` — the full
    seven-way wiring set the SDK distinguishes for a strain gage.
    """

    FULL_BRIDGE_BENDING = "full_bridge_bending"
    FULL_BRIDGE_BENDING_POISSON = "full_bridge_bending_poisson"
    FULL_BRIDGE_AXIAL = "full_bridge_axial"
    HALF_BRIDGE_POISSON = "half_bridge_poisson"
    HALF_BRIDGE_BENDING = "half_bridge_bending"
    QUARTER_BRIDGE = "quarter_bridge"
    QUARTER_BRIDGE_TEMP_COMPENSATION = "quarter_bridge_temp_compensation"


class BridgeConfiguration(StrEnum):
    """Generic bridge-sensor wiring (``olDaSetBridgeConfiguration``).

    Mirrors ``BRIDGE_CONFIGURATION`` in ``OLDADEFS.H`` — the three-way
    full/half/quarter subset used for non-strain bridge transducers
    (load cells, pressure sensors).
    """

    FULL = "full"
    HALF = "half"
    QUARTER = "quarter"


# ---------------------------------------------------------------------------
# Analog-input base + concrete subclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogInputBase(ChannelSpec):
    """Common AI knobs shared by every analog-input subclass.

    Maps to ``olDaSetChannelType`` / ``olDaSetGainListEntry`` /
    ``olDaSetChannelFilter`` / ``olDaSetEncoding`` /
    ``olDaSetCouplingType``. The backend issues these after the channel
    is added to the channel list.

    Attributes:
        channel_type: Wiring (single-ended / differential).
        gain: Programmable-gain-amplifier setting. ``1.0`` = unity.
        filter: Optional analog filter selection.
        encoding: Optional sample-code encoding override.
        coupling: Optional AC/DC coupling. ``None`` defers to subsystem
            default.
    """

    channel_type: ChannelType = ChannelType.SINGLE_ENDED
    gain: float = 1.0
    filter: FilterType | None = None
    encoding: Encoding | None = None
    coupling: CouplingType | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogInputVoltage(AnalogInputBase):
    """Voltage-mode analog input.

    Attributes:
        min_val: Lower input voltage range (``olDaSetChannelRange``).
        max_val: Upper input voltage range.
    """

    kind: ClassVar[str] = "ai_voltage"

    min_val: float = -10.0
    max_val: float = 10.0

    def __post_init__(self) -> None:
        """Reject ``min_val >= max_val`` before the SDK does."""
        super().__post_init__()
        if self.min_val >= self.max_val:
            raise DtolValidationError(
                f"AnalogInputVoltage: min_val={self.min_val} must be "
                f"strictly less than max_val={self.max_val}",
                context=ErrorContext(
                    operation="AnalogInputVoltage.__post_init__",
                    channel=self.physical_channel,
                    channel_name=self.name,
                ),
            )

    def kind_to_multi_sensor_type(self) -> IOType:
        """``AnalogInputVoltage`` → :attr:`IOType.VOLTAGE_IN`."""
        return IOType.VOLTAGE_IN


@dataclass(frozen=True, slots=True, kw_only=True)
class ThermocoupleInput(AnalogInputBase):
    """Thermocouple analog input — engineering units are degrees C/F/K.

    Two read paths, selected by the subsystem's capabilities:

    - **Firmware-linearised** (``OLSSC_RETURNS_FLOATS`` true): the device
      emits temperature directly via ``olDaGetSingleFloat``.
    - **Application-linearised** (DT9805/DT9806 A/D — ``returns_floats``
      false, ``supports_thermocouples`` true): the device returns raw
      codes. The wrapper reads the differential thermo-emf plus the CJC
      sensor on :attr:`cjc_channel`, then applies NIST ITS-90 polynomials
      (:func:`dtollib.utils.convert_volts_to_temperature`). This requires
      **differential** wiring and a high gain to resolve the µV-level emf —
      hence the defaults below differ from :class:`AnalogInputBase`.

    Defaults are tuned for the DT9805/DT9806: differential wiring,
    ``gain=100`` (≈3 µV/LSB on the ±10 V/16-bit A/D), CJC on channel 0.

    Attributes:
        thermocouple_type: NIST letter designation (J/K/T/E/R/S/B/N).
        min_val_degc: Lower temperature limit. Validated against the
            type's NIST operating range in ``__post_init__``.
        max_val_degc: Upper temperature limit.
        cjc_source: Cold-junction-compensation source.
        cjc_channel: Channel carrying the cold-junction sensor on the
            application-linearised path (channel 0 at 10 mV/°C on the
            DT9805/DT9806). Ignored on firmware-linearised subsystems.
        units: Reporting unit (deg C today; multi-sensor builds wire up conversion).
        channel_type: Overrides the base default to ``DIFFERENTIAL`` —
            mandatory for thermocouple/low-level measurement (UM9800 p.36).
        gain: Overrides the base default to ``100.0`` for emf resolution.
    """

    kind: ClassVar[str] = "thermocouple"

    thermocouple_type: ThermocoupleType
    min_val_degc: float
    max_val_degc: float
    cjc_source: CjcSource = CjcSource.INTERNAL
    cjc_channel: int = 0
    units: TemperatureUnit = TemperatureUnit.DEG_C
    channel_type: ChannelType = ChannelType.DIFFERENTIAL
    gain: float = 100.0

    def __post_init__(self) -> None:
        """Reject ranges outside NIST operating envelope, before SDK does."""
        super().__post_init__()
        if self.min_val_degc >= self.max_val_degc:
            raise DtolValidationError(
                f"ThermocoupleInput: min_val_degc={self.min_val_degc} must be "
                f"strictly less than max_val_degc={self.max_val_degc}",
                context=ErrorContext(
                    operation="ThermocoupleInput.__post_init__",
                    channel=self.physical_channel,
                    channel_name=self.name,
                ),
            )
        envelope_lo, envelope_hi = get_thermocouple_range(self.thermocouple_type.value)
        if self.min_val_degc < envelope_lo or self.max_val_degc > envelope_hi:
            raise DtolValidationError(
                f"ThermocoupleInput Type {self.thermocouple_type.value}: "
                f"range [{self.min_val_degc}, {self.max_val_degc}] °C "
                f"falls outside the NIST envelope "
                f"[{envelope_lo}, {envelope_hi}] °C",
                context=ErrorContext(
                    operation="ThermocoupleInput.__post_init__",
                    channel=self.physical_channel,
                    channel_name=self.name,
                    extra={"tc_type": self.thermocouple_type.value},
                ),
            )

    def kind_to_multi_sensor_type(self) -> IOType:
        """``ThermocoupleInput`` → :attr:`IOType.THERMOCOUPLE`."""
        return IOType.THERMOCOUPLE


# ---------------------------------------------------------------------------
# Multi-sensor input specs
#
# These target *intelligent* multi-sensor DT modules (DT9828/9829/9837).
# The owned DT9805/DT9806 report ``supports_multisensor=False`` and reject
# every multi-sensor setter with ECODE 36, so the :class:`TaskBuilder`
# capability gate raises :class:`~dtollib.errors.DtolCapabilityError`
# before the SDK is ever called (docs/implementation-plan.md §8.B4).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class RtdInput(AnalogInputBase):
    """Resistance-temperature-detector input — engineering units are °C.

    Maps to ``olDaSetRtdType`` plus, for :attr:`RtdType.CUSTOM`, the
    Callendar–Van Dusen setters ``olDaSetRtdR0`` / ``olDaSetRtdA`` /
    ``olDaSetRtdB`` / ``olDaSetRtdC``.

    Attributes:
        rtd_type: Standard RTD curve, or :attr:`RtdType.CUSTOM` to supply
            explicit coefficients.
        r0: Resistance at 0 °C in ohms (PT100 → ``100.0``).
        a, b: Callendar–Van Dusen coefficients. Required when
            ``rtd_type is RtdType.CUSTOM``; forbidden otherwise (the
            standard curve already fixes them).
        c: Sub-zero Callendar–Van Dusen coefficient (optional even for
            custom curves; only affects T < 0 °C).
        excitation_source: Measurement-current source.
        excitation_current_a: Driven current in amps, or ``None`` for the
            subsystem default.
    """

    kind: ClassVar[str] = "rtd"

    rtd_type: RtdType = RtdType.PT3850
    r0: float = 100.0
    a: float | None = None
    b: float | None = None
    c: float | None = None
    excitation_source: ExcitationSource = ExcitationSource.INTERNAL
    excitation_current_a: float | None = None

    def __post_init__(self) -> None:
        """Enforce the custom-vs-standard coefficient contract."""
        super().__post_init__()
        is_custom = self.rtd_type is RtdType.CUSTOM
        has_ab = self.a is not None and self.b is not None
        if is_custom and not has_ab:
            raise DtolValidationError(
                "RtdInput(rtd_type=CUSTOM) requires both 'a' and 'b' "
                "Callendar-Van Dusen coefficients",
                context=self._ctx("RtdInput.__post_init__"),
            )
        if not is_custom and (self.a is not None or self.b is not None or self.c is not None):
            raise DtolValidationError(
                f"RtdInput(rtd_type={self.rtd_type.value}): a/b/c coefficients "
                "are only valid with rtd_type=CUSTOM (the standard curve fixes them)",
                context=self._ctx("RtdInput.__post_init__"),
            )
        if self.r0 <= 0.0:
            raise DtolValidationError(
                f"RtdInput: r0 must be positive (got {self.r0})",
                context=self._ctx("RtdInput.__post_init__"),
            )

    def kind_to_multi_sensor_type(self) -> IOType:
        """``RtdInput`` → :attr:`IOType.RTD`."""
        return IOType.RTD

    def _ctx(self, operation: str) -> ErrorContext:
        return ErrorContext(
            operation=operation,
            channel=self.physical_channel,
            channel_name=self.name,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ThermistorInput(AnalogInputBase):
    """Thermistor input — engineering units are °C.

    Maps to ``olDaSetThermistorA`` / ``olDaSetThermistorB`` /
    ``olDaSetThermistorC`` (the Steinhart–Hart coefficients).

    Attributes:
        a, b, c: Steinhart–Hart coefficients. All three are required —
            a thermistor has no standard curve the SDK can assume.
        excitation_source: Measurement-current source.
        excitation_current_a: Driven current in amps, or ``None`` for the
            subsystem default.
    """

    kind: ClassVar[str] = "thermistor"

    a: float
    b: float
    c: float
    excitation_source: ExcitationSource = ExcitationSource.INTERNAL
    excitation_current_a: float | None = None

    def kind_to_multi_sensor_type(self) -> IOType:
        """``ThermistorInput`` → :attr:`IOType.THERMISTOR`."""
        return IOType.THERMISTOR


@dataclass(frozen=True, slots=True, kw_only=True)
class ResistanceInput(AnalogInputBase):
    """Direct resistance measurement — engineering units are ohms.

    Configured via the excitation-current setters; the SDK reports the
    measured resistance directly.

    Attributes:
        excitation_source: Measurement-current source.
        excitation_current_a: Driven current in amps, or ``None`` for the
            subsystem default.
    """

    kind: ClassVar[str] = "resistance"

    excitation_source: ExcitationSource = ExcitationSource.INTERNAL
    excitation_current_a: float | None = None

    def kind_to_multi_sensor_type(self) -> IOType:
        """``ResistanceInput`` → :attr:`IOType.RESISTANCE`."""
        return IOType.RESISTANCE


@dataclass(frozen=True, slots=True, kw_only=True)
class CurrentInput(AnalogInputBase):
    """Process-current input (e.g. 4–20 mA) — engineering units are amps.

    Attributes:
        min_val: Lower current range in amps (``olDaSetChannelRange``).
        max_val: Upper current range in amps.
    """

    kind: ClassVar[str] = "current"

    min_val: float = 0.0
    max_val: float = 0.02

    def __post_init__(self) -> None:
        """Reject ``min_val >= max_val`` before the SDK does."""
        super().__post_init__()
        if self.min_val >= self.max_val:
            raise DtolValidationError(
                f"CurrentInput: min_val={self.min_val} must be strictly less "
                f"than max_val={self.max_val}",
                context=ErrorContext(
                    operation="CurrentInput.__post_init__",
                    channel=self.physical_channel,
                    channel_name=self.name,
                ),
            )

    def kind_to_multi_sensor_type(self) -> IOType:
        """``CurrentInput`` → :attr:`IOType.CURRENT`."""
        return IOType.CURRENT


@dataclass(frozen=True, slots=True, kw_only=True)
class IepeInput(AnalogInputBase):
    """IEPE / ICP accelerometer input (constant-current-driven, AC-coupled).

    Maps to ``olDaSetCouplingType`` + ``olDaSetExcitationCurrentSource`` +
    ``olDaSetExcitationCurrentValue`` (the SDK has no single
    ``olDaSetIEPE`` on this build).

    Attributes:
        coupling: Forced to :attr:`CouplingType.AC` — IEPE sensors ride a
            DC bias that must be blocked. DC coupling is rejected.
        excitation_source: Constant-current source. :attr:`ExcitationSource.DISABLED`
            is rejected — an IEPE sensor needs drive current.
        excitation_current_a: Drive current in amps (typically 0.002–0.004 A).
        sensitivity_v_per_unit: Optional sensor sensitivity (V per
            engineering unit) carried as metadata for downstream scaling.
    """

    kind: ClassVar[str] = "iepe"

    coupling: CouplingType | None = CouplingType.AC
    excitation_source: ExcitationSource = ExcitationSource.INTERNAL
    excitation_current_a: float = 0.004
    sensitivity_v_per_unit: float | None = None

    def __post_init__(self) -> None:
        """Reject DC coupling and disabled excitation — invalid for IEPE."""
        super().__post_init__()
        if self.coupling is CouplingType.DC:
            raise DtolValidationError(
                "IepeInput requires AC coupling (an IEPE sensor rides a DC bias "
                "that must be blocked); got coupling=DC",
                context=self._ctx("IepeInput.__post_init__"),
            )
        if self.excitation_source is ExcitationSource.DISABLED:
            raise DtolValidationError(
                "IepeInput requires a drive current; excitation_source=DISABLED is invalid",
                context=self._ctx("IepeInput.__post_init__"),
            )
        if self.excitation_current_a <= 0.0:
            raise DtolValidationError(
                f"IepeInput: excitation_current_a must be positive "
                f"(got {self.excitation_current_a})",
                context=self._ctx("IepeInput.__post_init__"),
            )

    def kind_to_multi_sensor_type(self) -> IOType:
        """``IepeInput`` → :attr:`IOType.ACCELEROMETER`."""
        return IOType.ACCELEROMETER

    def _ctx(self, operation: str) -> ErrorContext:
        return ErrorContext(
            operation=operation,
            channel=self.physical_channel,
            channel_name=self.name,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class StrainInput(AnalogInputBase):
    """Strain-gage input — engineering units are strain (ε, dimensionless).

    Maps to ``olDaSetStrainBridgeConfiguration`` +
    ``olDaSetStrainExcitationVoltageSource`` +
    ``olDaSetStrainExcitationVoltage`` + ``olDaSetStrainShuntResistor``,
    with the volts→strain transform applied via
    :func:`dtollib.utils` / ``olDaVoltsToStrain``.

    Attributes:
        configuration: Bridge wiring (quarter / half / full).
        gage_factor: Gage factor (sensitivity); must be positive.
        gage_resistance_ohms: Nominal gage resistance (e.g. 120 / 350 Ω).
        poisson_ratio: Poisson's ratio of the specimen (Poisson bridges).
        lead_resistance_ohms: Lead-wire resistance for quarter-bridge
            correction.
        excitation_source: Excitation-voltage source.
        excitation_voltage: Bridge excitation voltage in volts.
        shunt_enabled: Engage the internal shunt-calibration resistor.
    """

    kind: ClassVar[str] = "strain"

    configuration: StrainGageConfiguration = StrainGageConfiguration.QUARTER_BRIDGE
    gage_factor: float = 2.0
    gage_resistance_ohms: float = 350.0
    poisson_ratio: float = 0.3
    lead_resistance_ohms: float = 0.0
    excitation_source: StrainExcitationSource = StrainExcitationSource.INTERNAL
    excitation_voltage: float = 0.0
    shunt_enabled: bool = False

    def __post_init__(self) -> None:
        """Reject non-physical gage factor / resistance."""
        super().__post_init__()
        if self.gage_factor <= 0.0:
            raise DtolValidationError(
                f"StrainInput: gage_factor must be positive (got {self.gage_factor})",
                context=self._ctx("StrainInput.__post_init__"),
            )
        if self.gage_resistance_ohms <= 0.0:
            raise DtolValidationError(
                f"StrainInput: gage_resistance_ohms must be positive "
                f"(got {self.gage_resistance_ohms})",
                context=self._ctx("StrainInput.__post_init__"),
            )

    def kind_to_multi_sensor_type(self) -> IOType:
        """``StrainInput`` → :attr:`IOType.STRAIN_GAGE`."""
        return IOType.STRAIN_GAGE

    def _ctx(self, operation: str) -> ErrorContext:
        return ErrorContext(
            operation=operation,
            channel=self.physical_channel,
            channel_name=self.name,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class BridgeInput(AnalogInputBase):
    """Generic bridge-transducer input (load cell, pressure sensor).

    Maps to ``olDaSetBridgeConfiguration`` + the strain excitation
    setters, with the volts→engineering transform applied via
    ``olDaVoltsToBridgeBasedSensor``.

    Attributes:
        configuration: Bridge wiring (full / half / quarter).
        nominal_resistance_ohms: Nominal bridge resistance.
        sensitivity_mv_per_v: Rated sensitivity (mV/V at full scale).
        excitation_source: Excitation-voltage source.
        excitation_voltage: Bridge excitation voltage in volts.
        lead_resistance_ohms: Lead-wire resistance for quarter-bridge
            correction.
        shunt_enabled: Engage the internal shunt-calibration resistor.
    """

    kind: ClassVar[str] = "bridge"

    configuration: BridgeConfiguration = BridgeConfiguration.FULL
    nominal_resistance_ohms: float = 350.0
    sensitivity_mv_per_v: float = 2.0
    excitation_source: StrainExcitationSource = StrainExcitationSource.INTERNAL
    excitation_voltage: float = 0.0
    lead_resistance_ohms: float = 0.0
    shunt_enabled: bool = False

    def __post_init__(self) -> None:
        """Reject non-physical resistance."""
        super().__post_init__()
        if self.nominal_resistance_ohms <= 0.0:
            raise DtolValidationError(
                f"BridgeInput: nominal_resistance_ohms must be positive "
                f"(got {self.nominal_resistance_ohms})",
                context=ErrorContext(
                    operation="BridgeInput.__post_init__",
                    channel=self.physical_channel,
                    channel_name=self.name,
                ),
            )

    def kind_to_multi_sensor_type(self) -> IOType:
        """``BridgeInput`` → :attr:`IOType.BRIDGE`."""
        return IOType.BRIDGE
