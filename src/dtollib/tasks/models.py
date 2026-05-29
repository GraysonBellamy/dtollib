"""Public enums + payload dataclasses for task / channel / lifecycle modeling.

:class:`DaqBlock` is a full frozen dataclass, and :class:`DaqSample` +
:class:`SdkEventKind` back the continuous-bridge path.

Design reference: docs/design.md §8 (enums), §8.9 (DaqReading), §8.10
(DaqBlock), §8.11 (DaqSample), §12.3.2 (SdkEventKind).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from dtollib.errors import DtolValidationError, ErrorContext

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from datetime import datetime

    import numpy as np
    import numpy.typing as npt

    from dtollib.errors import DtolError


__all__ = [
    "BufferState",
    "ClockSource",
    "CounterMode",
    "DaqBlock",
    "DaqReading",
    "DaqSample",
    "DataFlow",
    "Edge",
    "GateType",
    "IOType",
    "PulseType",
    "QuadratureDecodeMode",
    "QueueStrategy",
    "RetriggerMode",
    "SdkEventKind",
    "SensorStatus",
    "SubsystemState",
    "SubsystemType",
    "WrapMode",
    "block_to_long_rows",
]


# ---------------------------------------------------------------------------
# Data-flow / subsystem-kind enums (docs/design.md §8.12)
# ---------------------------------------------------------------------------


class DataFlow(StrEnum):
    """SDK data-flow modes for a configured subsystem.

    ``FINITE`` is implemented on top of ``CONTINUOUS`` + ``WrapMode.NONE``
    plus a sample ceiling — the SDK has no dedicated finite mode, but the
    recorder stops when the cumulative sample count is reached.

    The two ``*_PRETRIGGER`` / ``*_ABOUT_TRIGGER`` modes are flagged
    "Legacy Devices" in the SDK manual and deferred past v0.1.
    """

    SINGLE_VALUE = "single_value"
    CONTINUOUS = "continuous"
    FINITE = "finite"
    CONTINUOUS_PRETRIGGER = "continuous_pretrigger"
    CONTINUOUS_ABOUT_TRIGGER = "continuous_about_trigger"


class SubsystemType(StrEnum):
    """SDK subsystem types — one ``HDASS`` is one subsystem of one type."""

    ANALOG_INPUT = "analog_input"
    ANALOG_OUTPUT = "analog_output"
    DIGITAL_INPUT = "digital_input"
    DIGITAL_OUTPUT = "digital_output"
    COUNTER_TIMER = "counter_timer"
    QUADRATURE = "quadrature"
    # Broken out from COUNTER_TIMER despite both flowing through C/T-like
    # configuration calls; the SDK treats them as distinct subsystem types
    # (OLSS_TACH != OLSS_CT).
    TACHOMETER = "tachometer"


# ---------------------------------------------------------------------------
# Lifecycle state enums (docs/design.md §8.13, §8.14)
# ---------------------------------------------------------------------------


class SubsystemState(StrEnum):
    """Canonical subsystem state — exposed via ``DtolSession.state``.

    Borrowed from the .NET API's ``SubsystemBase.State``. The SDK already
    tracks this; surfacing it instead of synthesising it from
    ``is_running()`` plus implicit flags lets tests assert exact
    transitions and lets error messages name the precise lifecycle phase.
    """

    INITIALIZED = "initialized"
    CONFIGURED_FOR_SINGLE_VALUE = "configured_for_single_value"
    CONFIGURED_FOR_CONTINUOUS = "configured_for_continuous"
    PRESTARTED = "prestarted"
    RUNNING = "running"
    STOPPING = "stopping"
    ABORTING = "aborting"
    IO_COMPLETE = "io_complete"


class BufferState(StrEnum):
    """Per-``HBUF`` lifecycle — tracked on the internal ``RawBuffer``.

    Borrowed from ``OIBuffer.State``. Promotes use-after-free into an
    explicit error and lets the pool refuse ``free_all()`` while any buffer
    is ``INPROCESS`` (the §12.3.2 shutdown invariant).

    ``FILLED`` is the output-pool addition: an HBUF that has been written
    with a waveform chunk (``olDmCopyToBuffer``) but not yet queued. It
    encodes the Fill-before-Queue invariant — an output buffer must be
    filled before ``put_buffer`` (see :class:`~dtollib.backend._buffer_pool.BufferPool`).
    """

    IDLE = "idle"
    FILLED = "filled"
    QUEUED = "queued"
    INPROCESS = "inprocess"
    COMPLETED = "completed"
    RELEASED = "released"


# ---------------------------------------------------------------------------
# Channel discriminators (docs/design.md §8.15, §13.1)
# ---------------------------------------------------------------------------


class IOType(StrEnum):
    """Channel measurement-kind discriminator from ``SupportedChannelInfo.IOType``.

    Carried on ``CapabilitySet.channel_caps[ch]["IOType"]`` so the wrapper
    can reject "configure channel 3 as RTD" when that channel reports
    ``VOLTAGE_IN`` only.

    ``MULTI_SENSOR`` is the DT9805 case: one physical channel that the SDK
    re-types at configure time based on what's wired to it.
    """

    VOLTAGE_IN = "voltage_in"
    VOLTAGE_OUT = "voltage_out"
    CURRENT = "current"
    THERMOCOUPLE = "thermocouple"
    RTD = "rtd"
    THERMISTOR = "thermistor"
    RESISTANCE = "resistance"
    STRAIN_GAGE = "strain_gage"
    BRIDGE = "bridge"
    ACCELEROMETER = "accelerometer"  # IEPE
    DIGITAL_INPUT = "digital_input"
    DIGITAL_OUTPUT = "digital_output"
    COUNTER_TIMER = "counter_timer"
    TACHOMETER = "tachometer"
    QUADRATURE_DECODER = "quadrature_decoder"
    MULTI_SENSOR = "multi_sensor"


class SensorStatus(StrEnum):
    """Per-channel sentinel status preserved through scalarisation.

    TC channels can produce sentinel float values that must NOT be coerced
    into plausible measurements. The recorder writes the sentinel to a
    ``sensor_status`` overlay on the reading / block and replaces the data
    cell with NaN, so downstream sinks never silently log "23.4 °C" for
    an open thermocouple.
    """

    OK = "ok"
    SENSOR_OPEN = "sensor_open"
    TEMP_OUT_OF_RANGE_LOW = "temp_out_of_range_low"
    TEMP_OUT_OF_RANGE_HIGH = "temp_out_of_range_high"


# ---------------------------------------------------------------------------
# Timing / trigger / buffer enums
# ---------------------------------------------------------------------------


class Edge(StrEnum):
    """Digital / threshold trigger slope."""

    RISING = "rising"
    FALLING = "falling"


class WrapMode(StrEnum):
    """``BufferPlan`` wrap mode.

    ``NONE`` = finite acquisition (stop after one fill of the queued
    buffers). ``SINGLE`` = DAC waveform (loop a single buffer). ``MULTIPLE``
    = standard continuous (re-queue completed buffers).
    """

    NONE = "none"
    SINGLE = "single"
    MULTIPLE = "multiple"


class QueueStrategy(StrEnum):
    """How completed HBUFs are returned to the Ready queue."""

    REQUEUE = "requeue"
    KEEP = "keep"
    FREE_ON_DONE = "free_on_done"


class ClockSource(StrEnum):
    """``Timing.clock_source`` discriminator."""

    INTERNAL = "internal"
    EXTERNAL = "external"


class RetriggerMode(StrEnum):
    """``RetriggerSpec.mode`` — triggered-scan acquisition mode."""

    SCAN_PER_TRIGGER = "scan_per_trigger"
    INTERNAL = "internal"
    EXTRA = "extra"


# ---------------------------------------------------------------------------
# Counter/timer enums (docs/design.md §8.12)
# ---------------------------------------------------------------------------


class CounterMode(StrEnum):
    """Counter/timer operation mode — maps to the SDK ``OL_CTMODE_*`` family.

    Carried as a ``ClassVar`` on each counter channel spec so the
    :class:`~dtollib.tasks.TaskBuilder` dispatches ``olDaSetCTMode`` without
    branching on the concrete spec class.
    """

    COUNT = "count"  # event counting (OL_CTMODE_COUNT)
    MEASURE = "measure"  # edge-to-edge / frequency (OL_CTMODE_MEASURE)
    RATE = "rate"  # rate generation / pulse train (OL_CTMODE_RATE)
    ONE_SHOT = "one_shot"  # single pulse (OL_CTMODE_ONESHOT)
    ONE_SHOT_REPEAT = "one_shot_repeat"  # repetitive one-shot (OL_CTMODE_ONESHOT_RPT)
    QUADRATURE = "quadrature"  # quadrature decode (OL_CTMODE_QUAD)
    TACHOMETER = "tachometer"  # tachometer (OL_CTMODE_TACH)


class GateType(StrEnum):
    """Counter gate-enable logic — maps to the SDK ``OL_GATE_*`` family."""

    SOFTWARE = "software"  # always enabled (OL_GATE_SWGATE)
    LOW_LEVEL = "low_level"  # OL_GATE_LOWLEVEL
    HIGH_LEVEL = "high_level"  # OL_GATE_HIGHLEVEL
    LOW_EDGE = "low_edge"  # OL_GATE_LOWEDGE
    HIGH_EDGE = "high_edge"  # OL_GATE_HIGHEDGE


class PulseType(StrEnum):
    """Pulse-output polarity — maps to the SDK ``OL_PULSETYPE_*`` family."""

    LOW_TO_HIGH = "low_to_high"  # OL_PULSETYPE_LOWTOHI
    HIGH_TO_LOW = "high_to_low"  # OL_PULSETYPE_HITOLOW


class QuadratureDecodeMode(StrEnum):
    """Quadrature decoder count multiplier (counts per encoder line)."""

    X1 = "x1"
    X2 = "x2"
    X4 = "x4"


class SdkEventKind(StrEnum):
    """SDK notification-procedure message kinds delivered to the callback bridge.

    Eleven distinct event types arrive on the same ``olDaSetNotificationProcedure``
    callback; the drainer dispatches on the kind. ``BUFFER_DONE`` is the happy
    path; ``OVERRUN_ERROR`` / ``UNDERRUN_ERROR`` / ``TRIGGER_ERROR`` are wrapped
    into typed exceptions (or routed per ``ErrorPolicy``); ``BUFFER_REUSED``
    means data was overwritten in ``WrapMode.MULTIPLE`` and is logged at WARNING;
    ``QUEUE_DONE`` / ``QUEUE_STOPPED`` / ``IO_COMPLETE`` signal end-of-run;
    ``PRETRIGGER_BUFFER_DONE`` / ``EVENT_DONE`` / ``MEASURE_DONE`` are
    subsystem-specific and routed to dedicated handlers.
    """

    BUFFER_DONE = "buffer_done"
    PRETRIGGER_BUFFER_DONE = "pretrigger_buffer_done"
    BUFFER_REUSED = "buffer_reused"
    QUEUE_DONE = "queue_done"
    QUEUE_STOPPED = "queue_stopped"
    IO_COMPLETE = "io_complete"
    EVENT_DONE = "event_done"
    MEASURE_DONE = "measure_done"
    TRIGGER_ERROR = "trigger_error"
    OVERRUN_ERROR = "overrun_error"
    UNDERRUN_ERROR = "underrun_error"


# ---------------------------------------------------------------------------
# DaqReading — scalar / cross-instrument bridge (docs/design.md §8.9)
# ---------------------------------------------------------------------------


def _empty_values() -> Mapping[str, float | int | bool]:
    return MappingProxyType({})


def _empty_units() -> Mapping[str, str | None]:
    return MappingProxyType({})


def _empty_sensor_status() -> Mapping[str, SensorStatus]:
    return MappingProxyType({})


def _empty_metadata() -> Mapping[str, str | int | float | bool]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True, kw_only=True)
class DaqReading:
    """One scalar reading across the channels of a single-value task.

    Field shape matches :class:`nidaqlib.DaqReading` /
    :class:`alicatlib.Sample` / :class:`sartoriuslib.Sample` for
    cross-instrument joinability: the canonical key is
    ``(device, t_mono_ns)``.

    Attributes:
        device: ``DtolManager.add(name=...)`` value, or the
            :class:`~dtollib.tasks.TaskSpec.name` for ad-hoc sessions.
            Join key with sibling-library samples.
        task: Underlying ``TaskSpec.name``.  Distinct from ``device``
            when one manager entry hosts a renamed task.
        values: Channel name → scalar value mapping.  TC sentinels
            appear as ``float("nan")`` here; the explanation lives in
            ``sensor_status`` (see docs/design.md §13.1).
        units: Channel name → engineering unit string (``"V"``,
            ``"degC"``, ...).  ``None`` for unit-less channels.
        requested_at: Wall clock at the start of the poll call.
        received_at: Wall clock at the end of the poll call.
        t_utc: Wall-clock midpoint of the integration window.  This is
            the timestamp downstream consumers should plot against.
        t_mono_ns: Monotonic nanoseconds at the start of the poll call.
            Canonical join key with sibling-library samples.
        t_midpoint_mono_ns: Optional monotonic-ns midpoint when the
            backend can report it.  This is ``None`` for single-value
            reads — the simultaneous-sample-and-hold devices we target
            return one value per channel from one acquisition window.
        latency_s: ``(received_at - requested_at).total_seconds()``.
        sensor_status: Channel name → :class:`SensorStatus`.  Only
            channels with non-``OK`` status appear; absent entries
            imply ``OK``.
        metadata: Free-form key/value metadata propagated by the
            session and the channel specs.
        error: ``None`` under :attr:`ErrorPolicy.RAISE`; otherwise the
            wrapped :class:`~dtollib.errors.DtolError` for the failed
            poll.
    """

    device: str
    task: str | None = None
    values: Mapping[str, float | int | bool] = field(default_factory=_empty_values)
    units: Mapping[str, str | None] = field(default_factory=_empty_units)
    requested_at: datetime
    received_at: datetime
    t_utc: datetime
    t_mono_ns: int
    t_midpoint_mono_ns: int | None = None
    latency_s: float
    sensor_status: Mapping[str, SensorStatus] = field(default_factory=_empty_sensor_status)
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=_empty_metadata)
    error: DtolError | None = None

    def __post_init__(self) -> None:
        """Freeze the mapping fields so callers cannot mutate them post-hoc."""
        for name in ("values", "units", "sensor_status", "metadata"):
            current = getattr(self, name)
            if not isinstance(current, MappingProxyType):
                object.__setattr__(self, name, MappingProxyType(dict(current)))

    def __getstate__(self) -> dict[str, Any]:
        """Unwrap MappingProxyType views — they are not pickle-friendly."""
        return _slots_to_picklable_state(self, _DAQ_READING_MAPPING_FIELDS)

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore slotted fields and re-wrap mappings via :meth:`__post_init__`."""
        _restore_slots_state(self, state)
        self.__post_init__()

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly mapping suitable for the row-oriented sinks."""
        return {
            "device": self.device,
            "task": self.task,
            "values": dict(self.values),
            "units": dict(self.units),
            "requested_at": self.requested_at.isoformat(),
            "received_at": self.received_at.isoformat(),
            "t_utc": self.t_utc.isoformat(),
            "t_mono_ns": self.t_mono_ns,
            "t_midpoint_mono_ns": self.t_midpoint_mono_ns,
            "latency_s": self.latency_s,
            "sensor_status": {k: v.value for k, v in self.sensor_status.items()},
            "metadata": dict(self.metadata),
            "error": None if self.error is None else str(self.error),
        }


# ---------------------------------------------------------------------------
# DaqBlock — hardware-clocked, rectangular (docs/design.md §8.10)
# ---------------------------------------------------------------------------


def _empty_block_sensor_status() -> Mapping[str, npt.NDArray[np.int8]]:
    return cast("Mapping[str, npt.NDArray[np.int8]]", MappingProxyType({}))


def _empty_block_units() -> Mapping[str, str | None]:
    return cast("Mapping[str, str | None]", MappingProxyType({}))


_DAQ_READING_MAPPING_FIELDS: tuple[str, ...] = (
    "values",
    "units",
    "sensor_status",
    "metadata",
)

_DAQ_BLOCK_MAPPING_FIELDS: tuple[str, ...] = ("units", "sensor_status")

_DAQ_SAMPLE_MAPPING_FIELDS: tuple[str, ...] = ("metadata",)


def _slots_to_picklable_state(obj: Any, mapping_fields: tuple[str, ...]) -> dict[str, Any]:
    """Return a dict snapshot of slotted fields, unwrapping MappingProxyType."""
    state: dict[str, Any] = {}
    for slot in obj.__slots__:
        value: Any = getattr(obj, slot)
        if slot in mapping_fields and isinstance(value, MappingProxyType):
            state[slot] = dict(cast("Mapping[Any, Any]", value))
        else:
            state[slot] = value
    return state


def _restore_slots_state(obj: Any, state: dict[str, Any]) -> None:
    """Repopulate slots on a fresh instance during unpickling."""
    for name, value in state.items():
        object.__setattr__(obj, name, value)


@dataclass(frozen=True, slots=True, kw_only=True)
class DaqBlock:
    """One hardware-clocked acquisition buffer copied off the SDK Done queue.

    Constructed by the §12.3.2 callback-bridge drainer thread after
    ``olDaGetBuffer`` + ``olDmGetBufferPtr``. The ``data`` array is a
    drainer-owned copy (the SDK ``HBUF`` is recycled before the block leaves
    the drainer); the array is marked read-only so downstream sinks cannot
    mutate it in place.

    Sample-time reconstruction: derive each sample's ``t_mono_ns`` from
    ``block.t_mono_ns + k * block.block_period_ns`` and ``t_utc`` analogously.
    Use ``first_sample_index + k`` as the absolute sample index across the
    whole run. Do not interpolate off ``t0`` — it carries scheduler jitter.

    Attributes:
        device: ``DtolManager.add(name=...)`` value; join key with siblings.
        task: Underlying ``TaskSpec.name``.
        channels: Channel display names in array-row order; ``data[i]`` is
            ``channels[i]``.
        data: Converted samples, shape ``(len(channels), samples_per_channel)``,
            dtype ``float64``. NaN at positions where ``sensor_status`` is non-OK.
        raw_codes: Original SDK codes, shape matches ``data``, dtype ``int16``
            or ``int32``. Populated when ``RawLogging`` is configured or the
            backend retains them for replay.
        cjc_data: CJC stream, shape matches ``data``, dtype ``float64``.
            Populated when ``olDaSetReturnCjcTemperatureInStream`` is enabled
            on a subsystem with ``OLSSC_SUP_INTERLEAVED_CJC_IN_STREAM``.
        block_index: 0-based monotonic per task.
        first_sample_index: Cumulative offset since ``task_started_at``.
        samples_per_channel: ``data.shape[1]`` — duplicated for ergonomic
            access without indexing into the ndarray shape.
        sample_rate_hz: Actual clock rate read back via
            ``olDaGetClockFrequency`` after configure (the SDK may quantise
            the requested rate).
        block_period_ns: ``round(1e9 / sample_rate_hz)`` — ns per sample.
        task_started_at: Wall-clock anchor for sample-time reconstruction.
        t0: Wall clock at the first sample of THIS block.
        t_mono_ns: Monotonic ns at callback receipt (drainer thread).
        t_utc: Wall clock at the block midpoint — the timestamp consumers
            should plot against.
        t_midpoint_mono_ns: Block-midpoint in monotonic ns.
        read_started_at: Drainer-thread wall clock at ``olDaGetBuffer`` start.
        read_finished_at: Drainer-thread wall clock after copy + requeue.
        elapsed_s: ``(read_finished_at - read_started_at).total_seconds()``.
        units: Channel name → engineering unit (``"V"`` / ``"degC"`` / ...).
        is_linearised: ``True`` when ``data`` holds engineering units
            (volts / °C) produced by the drainer's code→units conversion;
            ``False`` when ``data`` holds raw ADC codes cast to float (the
            unconverted fallback, e.g. ``RawCountsSink`` / replay). Sinks and
            the replay tool key off this rather than guessing from values.
        sensor_status: Channel name → ``int8`` mask, same length as
            ``samples_per_channel``, encoded with :class:`SensorStatus`
            ordinals. Absent channels imply all-OK; the matching positions
            in ``data`` are NaN.
        error: ``None`` under :attr:`ErrorPolicy.RAISE`; otherwise the
            wrapped :class:`~dtollib.errors.DtolError` for the failed read.
            When set, ``data`` is zero-filled to the expected shape.
    """

    device: str
    channels: tuple[str, ...]
    data: npt.NDArray[np.float64]
    task: str | None = None
    raw_codes: npt.NDArray[np.signedinteger[Any]] | None = None
    cjc_data: npt.NDArray[np.float64] | None = None
    block_index: int
    first_sample_index: int
    samples_per_channel: int
    sample_rate_hz: float | None = None
    block_period_ns: int | None = None
    task_started_at: datetime
    t0: datetime
    t_mono_ns: int
    t_utc: datetime
    t_midpoint_mono_ns: int | None = None
    read_started_at: datetime
    read_finished_at: datetime
    elapsed_s: float
    units: Mapping[str, str | None] = field(default_factory=_empty_block_units)
    is_linearised: bool = False
    sensor_status: Mapping[str, npt.NDArray[np.int8]] = field(
        default_factory=_empty_block_sensor_status,
    )
    error: DtolError | None = None

    def __post_init__(self) -> None:
        """Validate array shapes and freeze mapping + ndarray mutability."""
        n_channels = len(self.channels)
        expected_shape = (n_channels, self.samples_per_channel)
        if self.data.shape != expected_shape:
            raise DtolValidationError(
                f"DaqBlock.data shape {self.data.shape} does not match "
                f"(len(channels)={n_channels}, samples_per_channel="
                f"{self.samples_per_channel})",
                context=ErrorContext(operation="DaqBlock.__post_init__"),
            )
        if self.raw_codes is not None and self.raw_codes.shape != expected_shape:
            raise DtolValidationError(
                f"DaqBlock.raw_codes shape {self.raw_codes.shape} does not "
                f"match data shape {expected_shape}",
                context=ErrorContext(operation="DaqBlock.__post_init__"),
            )
        if self.cjc_data is not None and self.cjc_data.shape != expected_shape:
            raise DtolValidationError(
                f"DaqBlock.cjc_data shape {self.cjc_data.shape} does not "
                f"match data shape {expected_shape}",
                context=ErrorContext(operation="DaqBlock.__post_init__"),
            )
        for ch_name, mask in self.sensor_status.items():
            if mask.shape != (self.samples_per_channel,):
                raise DtolValidationError(
                    f"DaqBlock.sensor_status[{ch_name!r}] shape {mask.shape} "
                    f"does not match (samples_per_channel="
                    f"{self.samples_per_channel},)",
                    context=ErrorContext(operation="DaqBlock.__post_init__"),
                )
        if self.block_index < 0:
            raise DtolValidationError(
                f"DaqBlock.block_index must be >= 0 (got {self.block_index})",
                context=ErrorContext(operation="DaqBlock.__post_init__"),
            )
        if self.first_sample_index < 0:
            raise DtolValidationError(
                f"DaqBlock.first_sample_index must be >= 0 (got {self.first_sample_index})",
                context=ErrorContext(operation="DaqBlock.__post_init__"),
            )

        # Lock arrays read-only so sinks cannot mutate the drainer's copy.
        self.data.setflags(write=False)
        if self.raw_codes is not None:
            self.raw_codes.setflags(write=False)
        if self.cjc_data is not None:
            self.cjc_data.setflags(write=False)
        for mask in self.sensor_status.values():
            mask.setflags(write=False)

        # Freeze mappings.
        if not isinstance(self.units, MappingProxyType):
            object.__setattr__(self, "units", MappingProxyType(dict(self.units)))
        if not isinstance(self.sensor_status, MappingProxyType):
            object.__setattr__(
                self,
                "sensor_status",
                MappingProxyType(dict(self.sensor_status)),
            )

    def __getstate__(self) -> dict[str, Any]:
        """Unwrap MappingProxyType views so the block can be pickled."""
        return _slots_to_picklable_state(self, _DAQ_BLOCK_MAPPING_FIELDS)

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore slotted fields and re-run shape/freeze validation."""
        _restore_slots_state(self, state)
        self.__post_init__()

    @property
    def n_channels(self) -> int:
        """Number of channels — ``len(self.channels)``."""
        return len(self.channels)


# ---------------------------------------------------------------------------
# DaqSample — per-sample scalarisation (docs/design.md §8.11)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class DaqSample:
    """One (channel, sample) pair scalarised from a :class:`DaqBlock`.

    Produced explicitly via :func:`block_to_long_rows`. Useful for row-oriented
    sinks (CSV, JSONL, Postgres) that prefer one row per measurement over a
    blob column. Carries the same join contract as :class:`DaqReading` so
    long-form DAQ rows can be merged with sibling-library samples on
    ``(device, t_mono_ns)``.

    ``is_linearised`` is inherited from the source :class:`DaqBlock`: it is
    ``True`` when ``value`` is in engineering units (volts / °C) and
    ``False`` when it is a raw ADC code cast to float. Row-oriented sinks
    persist it so the on-disk row is self-describing.
    """

    device: str
    channel: str
    value: float
    sample_index: int
    block_index: int
    t_mono_ns: int
    t_utc: datetime
    task: str | None = None
    unit: str | None = None
    sensor_status: SensorStatus = SensorStatus.OK
    is_linearised: bool = False
    metadata: Mapping[str, str | int | float | bool] = field(
        default_factory=_empty_metadata,
    )

    def __post_init__(self) -> None:
        """Freeze the metadata mapping."""
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(
                self,
                "metadata",
                MappingProxyType(dict(self.metadata)),
            )

    def __getstate__(self) -> dict[str, Any]:
        """Unwrap MappingProxyType so the sample can be pickled."""
        return _slots_to_picklable_state(self, _DAQ_SAMPLE_MAPPING_FIELDS)

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore slotted fields and re-wrap the metadata mapping."""
        _restore_slots_state(self, state)
        self.__post_init__()

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly mapping suitable for row-oriented sinks."""
        return {
            "device": self.device,
            "task": self.task,
            "channel": self.channel,
            "value": self.value,
            "unit": self.unit,
            "sample_index": self.sample_index,
            "block_index": self.block_index,
            "t_mono_ns": self.t_mono_ns,
            "t_utc": self.t_utc.isoformat(),
            "sensor_status": self.sensor_status.value,
            "is_linearised": self.is_linearised,
            "metadata": dict(self.metadata),
        }


def block_to_long_rows(block: DaqBlock) -> Iterator[DaqSample]:
    """Yield one :class:`DaqSample` per (channel, sample) pair in ``block``.

    Reconstructs each sample's monotonic timestamp from
    ``block.t_mono_ns + k * block.block_period_ns`` (constant if
    ``block_period_ns`` is ``None`` — only the block-level timestamp is
    used). Sensor-status masks are decoded back into :class:`SensorStatus`
    values per sample.

    Yields ``n_channels * samples_per_channel`` samples in (channel-major,
    sample-minor) order.
    """
    period_ns = block.block_period_ns or 0
    # Mask values are SensorStatus ordinals — index into the declaration order.
    status_order = list(SensorStatus)
    for ch_index, ch_name in enumerate(block.channels):
        row = block.data[ch_index]
        mask = block.sensor_status.get(ch_name)
        unit = block.units.get(ch_name)
        for k in range(block.samples_per_channel):
            if mask is not None:
                status_ord = int(mask[k])
                status = (
                    status_order[status_ord]
                    if 0 <= status_ord < len(status_order)
                    else SensorStatus.OK
                )
            else:
                status = SensorStatus.OK
            yield DaqSample(
                device=block.device,
                task=block.task,
                channel=ch_name,
                value=float(row[k]),
                unit=unit,
                sample_index=block.first_sample_index + k,
                block_index=block.block_index,
                t_mono_ns=block.t_mono_ns + k * period_ns,
                t_utc=block.t_utc,
                sensor_status=status,
                is_linearised=block.is_linearised,
            )
