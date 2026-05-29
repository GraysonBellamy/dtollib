"""Lock the public surface across all implemented capabilities.

Adding a top-level export updates ``dtollib.__all__`` AND
updates this test in the same commit. The test exists to make
unintentional surface drift visible in code review.
"""

from __future__ import annotations

import dtollib

_PUBLIC_BASE: frozenset[str] = frozenset(
    {
        # config
        "DEFAULT_ENV_PREFIX",
        "DtolConfig",
        "config_from_env",
        # version
        "__version__",
        # errors — full docs/design.md §17.3 tree
        "DtolBackendError",
        "DtolBufferOverrunError",
        "DtolBufferUnderrunError",
        "DtolCapabilityError",
        "DtolCapiError",
        "DtolConfigurationError",
        "DtolConfirmationRequiredError",
        "DtolConnectionError",
        "DtolDependencyError",
        "DtolError",
        "DtolReadError",
        "DtolResourceError",
        "DtolSinkDependencyError",
        "DtolSinkError",
        "DtolSinkSchemaError",
        "DtolSinkWriteError",
        "DtolTaskStateError",
        "DtolTimeoutError",
        "DtolTransientError",
        "DtolTriggerError",
        "DtolValidationError",
        "DtolWriteError",
        "ErrorContext",
        # enums — docs/design.md §8.12-§8.15
        "BufferState",
        "ClockSource",
        "DataFlow",
        "Edge",
        "IOType",
        "QueueStrategy",
        "RetriggerMode",
        "SensorStatus",
        "SubsystemState",
        "SubsystemType",
        "WrapMode",
        # utilities
        "to_pint",
    }
)

_DISCOVERY_ADDITIONS: frozenset[str] = frozenset(
    {
        # system/ discovery + capabilities (docs/design.md §20)
        "BoardInfo",
        "CapabilitySet",
        "DeviceInfo",
        "SubsystemInfo",
        "find_devices",
        "find_subsystems",
        # utils.py — NIST TC math + rosette transforms (docs/design.md §15.4)
        "compute_delta_rosette",
        "compute_rectangular_rosette",
        "convert_temperature_to_volts",
        "convert_volts_to_temperature",
        "get_thermocouple_range",
    }
)

_SINGLE_VALUE_ADDITIONS: frozenset[str] = frozenset(
    {
        # channels (docs/design.md §8.2–§8.6)
        "AnalogInputBase",
        "AnalogInputVoltage",
        "ChannelSpec",
        "ChannelType",
        "CjcSource",
        "CouplingType",
        "Encoding",
        "FilterType",
        "TemperatureUnit",
        "ThermocoupleInput",
        "ThermocoupleType",
        # multi-sensor channels (docs/implementation-plan.md §8.B1)
        "BridgeConfiguration",
        "BridgeInput",
        "CurrentInput",
        "ExcitationSource",
        "IepeInput",
        "ResistanceInput",
        "RtdInput",
        "RtdType",
        "StrainExcitationSource",
        "StrainGageConfiguration",
        "StrainInput",
        "ThermistorInput",
        # TEDS helpers (multi-sensor)
        "BridgeSensorTeds",
        "StrainGageTeds",
        "read_bridge_sensor_teds",
        "read_bridge_sensor_virtual_teds",
        "read_strain_gage_teds",
        "read_strain_gage_virtual_teds",
        # strain/bridge volts→engineering helpers (multi-sensor)
        "bridge_value_from_volts",
        "strain_from_volts",
        # tasks (docs/design.md §8.1, §8.7, §8.8, §8.10)
        "BufferPlan",
        "DaqBlock",
        "DaqReading",
        "DtolSession",
        "RawLogging",
        "RetriggerSpec",
        "SoftwareStart",
        "TaskBuilder",
        "TaskSpec",
        "Timing",
        "TriggerSpec",
        # manager + factory (docs/design.md §9.3, §16)
        "DeviceResult",
        "DtolManager",
        "open_device",
    }
)

_CONTINUOUS_ADDITIONS: frozenset[str] = frozenset(
    {
        # tasks/models.py — full DaqBlock + per-sample scalarisation + event kinds
        "DaqSample",
        "SdkEventKind",
        "block_to_long_rows",
        # sinks/base.py — row-flattening helpers (mirror nidaqlib's exports)
        "block_to_rows",
        "reading_to_row",
        # tasks/triggers.py — full start-trigger hierarchy
        "AnalogThresholdStart",
        "ExternalDigitalStart",
        "ReferenceTrigger",
        "SyncBusStart",
        # streaming — recorders + policy types
        "AcquisitionSummary",
        "ErrorPolicy",
        "OverflowPolicy",
        "Recording",
        "record",
        "record_polled",
    }
)


_OUTPUT_ADDITIONS: frozenset[str] = frozenset(
    {
        # channels — DT9806 output surface (docs/design.md §18.3)
        "AnalogOutputVoltage",
        "DigitalInputPort",
        "DigitalLine",
        "DigitalOutputPort",
        # channel serialisation registry
        "channel_from_dict",
    }
)


_COUNTER_ADDITIONS: frozenset[str] = frozenset(
    {
        # counter/timer + quadrature + tachometer channel specs
        "CounterEdgeCount",
        "CounterEdgeToEdge",
        "CounterFrequency",
        "QuadratureDecoder",
        "Tachometer",
        "PulseTrainOutput",
        "OneShotOutput",
        "RepetitiveOneShotOutput",
        # counter/timer public enums
        "CounterMode",
        "GateType",
        "PulseType",
        "QuadratureDecodeMode",
    }
)


_WS_AO_ADDITIONS: frozenset[str] = frozenset(
    {
        # streaming — continuous analog-output waveform playback (WS-AO)
        "play",
        "PlaybackSource",
    }
)


_EXPECTED_PUBLIC: frozenset[str] = (
    _PUBLIC_BASE
    | _DISCOVERY_ADDITIONS
    | _SINGLE_VALUE_ADDITIONS
    | _CONTINUOUS_ADDITIONS
    | _OUTPUT_ADDITIONS
    | _COUNTER_ADDITIONS
    | _WS_AO_ADDITIONS
)


def test_all_matches_documented_surface() -> None:
    """``dtollib.__all__`` exactly matches the documented public surface."""
    actual = frozenset(dtollib.__all__)
    missing = _EXPECTED_PUBLIC - actual
    extra = actual - _EXPECTED_PUBLIC
    assert not missing, f"Expected names missing from __all__: {sorted(missing)}"
    assert not extra, f"Unexpected names in __all__: {sorted(extra)}"


def test_every_all_name_resolves() -> None:
    """Every name in ``__all__`` resolves to an attribute on the package."""
    for name in dtollib.__all__:
        assert hasattr(dtollib, name), f"__all__ contains {name!r} but module lacks it"


def test_reserved_names_absent() -> None:
    """Names reserved for deliberately non-top-level surfaces are not present at top level."""
    reserved_names = [
        "Dtol",  # sync facade entry — lives under dtollib.sync deliberately.
        "RawCountsSink",
        "ParquetSink",
        "CsvSink",
        "JsonlSink",
        "SqliteSink",
        "PostgresSink",
        # FakeDtolBackend lives under dtollib.backend deliberately.
    ]
    for name in reserved_names:
        assert not hasattr(dtollib, name), (
            f"Reserved name {name!r} present already — update the public "
            f"surface set in tests/unit/test_public_api.py if intentional."
        )
