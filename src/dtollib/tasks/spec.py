"""``TaskSpec`` + supporting dataclasses — the configuration entry-point.

A single spec describes single-value, continuous, and finite tasks;
``Timing`` / :class:`BufferPlan` / :class:`RawLogging` carry the
continuous-mode configuration consumed by :func:`dtollib.streaming.record`.

Design reference: docs/design.md §8.1 (TaskSpec), §8.7 (Timing),
§8.7a (BufferPlan).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from dtollib.errors import DtolValidationError, ErrorContext
from dtollib.tasks.models import (
    ClockSource,
    DataFlow,
    QueueStrategy,
    RetriggerMode,
    SubsystemType,
    WrapMode,
)
from dtollib.tasks.triggers import SoftwareStart, TriggerSpec

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from dtollib.channels.base import ChannelSpec

__all__ = [
    "BufferPlan",
    "RawLogging",
    "RetriggerSpec",
    "TaskSpec",
    "Timing",
]


# ---------------------------------------------------------------------------
# Timing (docs/design.md §8.7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class RetriggerSpec:
    """Triggered-scan retrigger specification.

    Wired by the :class:`~dtollib.tasks.TaskBuilder` into
    ``olDaSetTriggeredScanUsage`` + ``olDaSetMultiscanCount`` +
    ``olDaSetRetriggerMode`` (+ ``olDaSetRetriggerFrequency`` for INTERNAL,
    ``olDaSetRetrigger`` for EXTRA).

    Attributes:
        mode: Retrigger mode.  Defaults to ``EXTRA`` per the SDK doc
            recommendation when both INTERNAL and EXTRA are supported.
        multiscan_count: Channel-list scans collected per trigger (>= 1).
        frequency_hz: Internal retrigger rate; required for
            ``RetriggerMode.INTERNAL``, forbidden otherwise.
        source: Retrigger source trigger; required for
            ``RetriggerMode.EXTRA``, forbidden otherwise.
    """

    mode: RetriggerMode = RetriggerMode.EXTRA
    multiscan_count: int = 1
    frequency_hz: float | None = None
    source: TriggerSpec | None = None

    def __post_init__(self) -> None:
        """Validate mode-specific field requirements."""
        if self.multiscan_count < 1:
            raise DtolValidationError(
                f"RetriggerSpec.multiscan_count must be >= 1 (got {self.multiscan_count})",
                context=ErrorContext(operation="RetriggerSpec.__post_init__"),
            )
        if self.mode == RetriggerMode.INTERNAL and self.frequency_hz is None:
            raise DtolValidationError(
                "RetriggerSpec.frequency_hz is required when mode is INTERNAL",
                context=ErrorContext(operation="RetriggerSpec.__post_init__"),
            )
        if self.mode != RetriggerMode.INTERNAL and self.frequency_hz is not None:
            raise DtolValidationError(
                "RetriggerSpec.frequency_hz is only valid when mode is INTERNAL",
                context=ErrorContext(operation="RetriggerSpec.__post_init__"),
            )
        if self.mode == RetriggerMode.EXTRA and self.source is None:
            raise DtolValidationError(
                "RetriggerSpec.source is required when mode is EXTRA",
                context=ErrorContext(operation="RetriggerSpec.__post_init__"),
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class Timing:
    """Sample-clock + retrigger configuration for a continuous task.

    Required when ``TaskSpec.data_flow != SINGLE_VALUE``; forbidden
    otherwise (single-value reads ignore the timing engine).

    Attributes:
        rate_hz: Configured sample rate, in hertz.  Driven via
            ``olDaSetClockFrequency`` for ``ClockSource.INTERNAL``;
            interpreted as the external-clock divider's input rate
            otherwise.
        clock_source: Internal vs external clock selection.
        external_divider: Required when ``clock_source == EXTERNAL``;
            forbidden otherwise.
        retrigger: Optional triggered-scan retrigger specification.
            ``None`` = no retrigger.
    """

    rate_hz: float
    clock_source: ClockSource = ClockSource.INTERNAL
    external_divider: int | None = None
    retrigger: RetriggerSpec | None = None
    samples_per_channel: int | None = None
    """Optional sample ceiling. Required for ``DataFlow.FINITE``; the recorder
    stops when the cumulative emitted sample count reaches this value.
    Forbidden (None) for ``CONTINUOUS`` — that mode runs until ``stop()``."""

    def __post_init__(self) -> None:
        """Validate the external-divider / clock-source / samples-ceiling combos."""
        if self.rate_hz <= 0.0:
            raise DtolValidationError(
                f"Timing.rate_hz must be positive, got {self.rate_hz}",
                context=ErrorContext(operation="Timing.__post_init__"),
            )
        if self.clock_source == ClockSource.EXTERNAL and self.external_divider is None:
            raise DtolValidationError(
                "Timing.external_divider is required when clock_source is EXTERNAL",
                context=ErrorContext(operation="Timing.__post_init__"),
            )
        if self.clock_source == ClockSource.INTERNAL and self.external_divider is not None:
            raise DtolValidationError(
                "Timing.external_divider is forbidden when clock_source is INTERNAL",
                context=ErrorContext(operation="Timing.__post_init__"),
            )
        if self.samples_per_channel is not None and self.samples_per_channel <= 0:
            raise DtolValidationError(
                f"Timing.samples_per_channel must be positive (got {self.samples_per_channel})",
                context=ErrorContext(operation="Timing.__post_init__"),
            )


# ---------------------------------------------------------------------------
# BufferPlan + RawLogging (docs/design.md §8.7a)
# ---------------------------------------------------------------------------


_MIN_BUFFERS: int = 3
"""Minimum buffer count enforced by the SDK and §8.7a."""


@dataclass(frozen=True, slots=True, kw_only=True)
class BufferPlan:
    """SDK Ready / Inprocess / Done buffer plan for continuous tasks.

    Consumed by the buffer pool behind :func:`dtollib.streaming.record`.
    Required when ``TaskSpec.data_flow in {CONTINUOUS, FINITE,
    *_PRETRIGGER, *_ABOUT_TRIGGER}``; forbidden for ``SINGLE_VALUE``.

    Attributes:
        buffers: Number of HBUFs in the Ready/Inprocess/Done cycle.
            Minimum 3; default 4 (matches QuickDAQ default).
        samples_per_buffer: Samples per channel per HBUF.
        sample_width_bytes: ``None`` → backend auto-detects from
            ``OLSSC_RETURNS_FLOATS`` + resolution.
        wrap_mode: ``MULTIPLE`` (continuous reuse) / ``SINGLE`` (DAC
            waveform) / ``NONE`` (finite).
        queue_strategy: How completed HBUFs return to the Ready queue.
    """

    buffers: int = 4
    samples_per_buffer: int = 1000
    sample_width_bytes: int | None = None
    wrap_mode: WrapMode = WrapMode.MULTIPLE
    queue_strategy: QueueStrategy = QueueStrategy.REQUEUE

    def __post_init__(self) -> None:
        """Enforce the hard minimum-3 floor."""
        if self.buffers < _MIN_BUFFERS:
            raise DtolValidationError(
                f"BufferPlan.buffers must be >= {_MIN_BUFFERS} (got {self.buffers}); "
                "see docs/design.md §8.7a for the minimum-3 rationale",
                context=ErrorContext(operation="BufferPlan.__post_init__"),
            )
        if self.samples_per_buffer <= 0:
            raise DtolValidationError(
                f"BufferPlan.samples_per_buffer must be positive (got {self.samples_per_buffer})",
                context=ErrorContext(operation="BufferPlan.__post_init__"),
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class RawLogging:
    """Driver-side raw-counts logging configuration.

    Wired into :class:`~dtollib.sinks.RawCountsSink`.

    Attributes:
        path: Output ``.dt-raw`` file path.
        include_metadata: Embed task metadata in the file header.
        compression: Compression algorithm (currently always ``None``).
    """

    path: Path
    include_metadata: bool = True
    compression: None = None


# ---------------------------------------------------------------------------
# TaskSpec (docs/design.md §8.1)
# ---------------------------------------------------------------------------


def _empty_metadata() -> Mapping[str, str | int | float | bool]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskSpec:
    """One DT-Open Layers subsystem configured for one data-flow mode.

    A ``TaskSpec`` is a typed declaration; the
    :class:`~dtollib.tasks.TaskBuilder` translates it into the actual
    SDK call sequence.

    Attributes:
        name: Human-readable task identifier.  Propagated to error
            contexts, log lines, and sink rows.
        board: DT-Open Layers board name (e.g. ``"DT9805(00)"``).
            ``None`` = first discovered board.
        subsystem_type: Explicit override for the subsystem to bind.
            Usually inferred from the channel kinds — see
            :meth:`infer_subsystem_type`.
        element: Subsystem element index on the board.  ``0`` for the
            first AI subsystem; non-zero on boards with multiple
            subsystems of the same type.
        channels: Ordered channel specs.  Empty is rejected.  All
            channels must share a subsystem type — mixing voltage and
            DO in one task is a validation error.
        data_flow: One of :class:`DataFlow`.
        timing: Required for non-``SINGLE_VALUE``; forbidden for it.
        trigger: Defaults to :class:`SoftwareStart`; the full trigger
            hierarchy is supported.
        buffers: Required for non-``SINGLE_VALUE``; forbidden for it.
        logging: Optional driver-side raw-counts logging.
        stop_on_error: SDK-level ``olDaSetStopOnError``.  Orthogonal to
            recorder-level :class:`ErrorPolicy`; see docs/design.md
            §14.3.
        metadata: Free-form task-level metadata.
    """

    name: str
    channels: Sequence[ChannelSpec]
    board: str | None = None
    subsystem_type: SubsystemType | None = None
    element: int = 0
    data_flow: DataFlow = DataFlow.SINGLE_VALUE
    timing: Timing | None = None
    trigger: TriggerSpec = field(default_factory=SoftwareStart)
    buffers: BufferPlan | None = None
    logging: RawLogging | None = None
    stop_on_error: bool = True
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        """Validate the spec against the §8.1 / §4.2 matrix."""
        if not self.name:
            raise DtolValidationError(
                "TaskSpec.name must be a non-empty string",
                context=ErrorContext(operation="TaskSpec.__post_init__"),
            )
        if not self.channels:
            raise DtolValidationError(
                f"TaskSpec(name={self.name!r}).channels is empty; at least one channel is required",
                context=ErrorContext(
                    operation="TaskSpec.__post_init__",
                    task_name=self.name,
                ),
            )

        # Channel-kind homogeneity: one HDASS = one subsystem type.
        inferred = self.infer_subsystem_type()
        if self.subsystem_type is not None and self.subsystem_type != inferred:
            raise DtolValidationError(
                f"TaskSpec.subsystem_type={self.subsystem_type.value} conflicts "
                f"with inferred type {inferred.value} from the channel kinds",
                context=ErrorContext(
                    operation="TaskSpec.__post_init__",
                    task_name=self.name,
                ),
            )

        # data_flow / timing / buffers matrix — see implementation-plan.md §4.2.
        self._validate_data_flow_matrix()

        # Wrap metadata to enforce immutability.
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def _validate_data_flow_matrix(self) -> None:
        """Enforce timing / buffers / trigger combinations per data_flow."""
        single_value = self.data_flow == DataFlow.SINGLE_VALUE
        if single_value:
            if self.timing is not None:
                raise DtolValidationError(
                    "TaskSpec.timing is forbidden when data_flow=SINGLE_VALUE",
                    context=ErrorContext(
                        operation="TaskSpec.__post_init__",
                        task_name=self.name,
                    ),
                )
            if self.buffers is not None:
                raise DtolValidationError(
                    "TaskSpec.buffers is forbidden when data_flow=SINGLE_VALUE",
                    context=ErrorContext(
                        operation="TaskSpec.__post_init__",
                        task_name=self.name,
                    ),
                )
            return

        # Continuous / finite / pre-trigger / about-trigger require timing + buffers.
        if self.timing is None:
            raise DtolValidationError(
                f"TaskSpec.timing is required when data_flow={self.data_flow.value}",
                context=ErrorContext(
                    operation="TaskSpec.__post_init__",
                    task_name=self.name,
                ),
            )
        if self.buffers is None:
            raise DtolValidationError(
                f"TaskSpec.buffers is required when data_flow={self.data_flow.value}",
                context=ErrorContext(
                    operation="TaskSpec.__post_init__",
                    task_name=self.name,
                ),
            )

        # FINITE requires a sample ceiling so the recorder knows when to stop.
        if self.data_flow == DataFlow.FINITE and self.timing.samples_per_channel is None:
            raise DtolValidationError(
                "TaskSpec.timing.samples_per_channel is required when data_flow=FINITE",
                context=ErrorContext(
                    operation="TaskSpec.__post_init__",
                    task_name=self.name,
                ),
            )

        # FINITE pairs with WrapMode.NONE — the SDK reuses buffers in MULTIPLE,
        # which contradicts the linear-acquisition contract of finite mode.
        if self.data_flow == DataFlow.FINITE and self.buffers.wrap_mode != WrapMode.NONE:
            raise DtolValidationError(
                "TaskSpec.buffers.wrap_mode must be WrapMode.NONE for "
                f"data_flow=FINITE (got {self.buffers.wrap_mode.value})",
                context=ErrorContext(
                    operation="TaskSpec.__post_init__",
                    task_name=self.name,
                ),
            )

    def infer_subsystem_type(self) -> SubsystemType:
        """Derive the subsystem kind from the channel kinds.

        Returns:
            The implied :class:`SubsystemType`.

        Raises:
            DtolValidationError: Channels span multiple subsystem kinds
                or include an unrecognised kind.  One HDASS is one
                subsystem of one type — mixing is illegal.
        """
        inferred: SubsystemType | None = None
        for channel in self.channels:
            kind_type = _channel_subsystem_type(channel)
            if inferred is None:
                inferred = kind_type
            elif inferred != kind_type:
                raise DtolValidationError(
                    f"TaskSpec(name={self.name!r}).channels mixes subsystem "
                    f"kinds ({inferred.value} vs {kind_type.value}); "
                    "split into separate TaskSpecs (one HDASS per type).",
                    context=ErrorContext(
                        operation="TaskSpec.infer_subsystem_type",
                        task_name=self.name,
                    ),
                )
        if inferred is None:
            raise DtolValidationError(
                f"TaskSpec(name={self.name!r}).channels is empty; at least one channel is required",
                context=ErrorContext(
                    operation="TaskSpec.infer_subsystem_type",
                    task_name=self.name,
                ),
            )
        return inferred


def _channel_subsystem_type(channel: ChannelSpec) -> SubsystemType:  # noqa: PLR0911
    """Map a concrete :class:`ChannelSpec` to its owning subsystem kind."""
    # Lazy import — channels.analog_input depends on tasks.models which
    # would pull this package back through tasks/__init__.py mid-load.
    from dtollib.channels.analog_input import (  # noqa: PLC0415
        AnalogInputBase,
        AnalogInputVoltage,
        ThermocoupleInput,
    )
    from dtollib.channels.analog_output import AnalogOutputVoltage  # noqa: PLC0415
    from dtollib.channels.counter_input import (  # noqa: PLC0415
        CounterInputBase,
        QuadratureDecoder,
        Tachometer,
    )
    from dtollib.channels.counter_output import CounterOutputBase  # noqa: PLC0415
    from dtollib.channels.digital import (  # noqa: PLC0415
        DigitalInputPort,
        DigitalOutputPort,
    )

    if isinstance(channel, AnalogInputBase | AnalogInputVoltage | ThermocoupleInput):
        return SubsystemType.ANALOG_INPUT
    if isinstance(channel, AnalogOutputVoltage):
        return SubsystemType.ANALOG_OUTPUT
    if isinstance(channel, DigitalInputPort):
        return SubsystemType.DIGITAL_INPUT
    if isinstance(channel, DigitalOutputPort):
        return SubsystemType.DIGITAL_OUTPUT
    # Counter/timer inputs + outputs share the OLSS_CT subsystem.  Quadrature
    # and tachometer route through their own first-class subsystems.
    if isinstance(channel, QuadratureDecoder):
        return SubsystemType.QUADRATURE
    if isinstance(channel, Tachometer):
        return SubsystemType.TACHOMETER
    if isinstance(channel, CounterInputBase | CounterOutputBase):
        return SubsystemType.COUNTER_TIMER
    raise DtolValidationError(
        f"Unknown channel kind {type(channel).__name__}; "
        "subsystem inference is implemented for analog-input, analog-output, "
        "digital-I/O, counter/timer, quadrature, and tachometer subclasses.",
        context=ErrorContext(
            operation="TaskSpec._channel_subsystem_type",
            channel=channel.physical_channel,
        ),
    )
