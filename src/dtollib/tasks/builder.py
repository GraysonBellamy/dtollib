"""``TaskBuilder`` — translates a :class:`TaskSpec` into ordered backend calls.

The builder is the single place that knows the legal ordering of SDK
configuration calls.  It provides the single-value sequence and the
continuous-mode sequence with channel-list + timing + trigger +
buffer-pool setup.

Critical invariant (docs/design.md §8.5a): on ``IOType.MULTI_SENSOR``
channels, ``set_multi_sensor_type`` MUST be called BEFORE any
per-type setter on that channel.  The builder enforces this
unconditionally; the fake backend rejects out-of-order calls.

Design reference: docs/implementation-plan.md §4.3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dtollib.backend.dataacq import DATA_FLOW_TO_OL
from dtollib.capi.constants import (
    OL_CLK_EXTERNAL,
    OL_CLK_INTERNAL,
    OL_CTMODE_COUNT,
    OL_CTMODE_MEASURE,
    OL_CTMODE_ONESHOT,
    OL_CTMODE_ONESHOT_RPT,
    OL_CTMODE_QUAD,
    OL_CTMODE_RATE,
    OL_CTMODE_TACH,
    OL_EDGE_FALLING,
    OL_EDGE_RISING,
    OL_GATE_HIGH_EDGE,
    OL_GATE_HIGH_LEVEL,
    OL_GATE_LOW_EDGE,
    OL_GATE_LOW_LEVEL,
    OL_GATE_NONE,
    OL_PLS_HIGH2LOW,
    OL_PLS_LOW2HIGH,
    OL_RETRIG_EXTRA,
    OL_RETRIG_INTERNAL,
    OL_RETRIG_SCANPERTRIG,
    OL_TRG_EXTERN,
    OL_TRG_SOFT,
    OL_TRG_SYNCBUS,
    OL_TRG_THRESHNEG,
    OL_TRG_THRESHPOS,
    OL_WRP_MULTIPLE,
    OL_WRP_NONE,
    OL_WRP_SINGLE,
)
from dtollib.tasks.models import (
    ClockSource,
    CounterMode,
    DataFlow,
    Edge,
    GateType,
    PulseType,
    RetriggerMode,
    WrapMode,
)
from dtollib.tasks.triggers import (
    AnalogThresholdStart,
    ExternalDigitalStart,
    SoftwareStart,
    SyncBusStart,
)

if TYPE_CHECKING:
    from dtollib.backend.base import DtolBackend
    from dtollib.channels.base import ChannelSpec
    from dtollib.system.capabilities import CapabilitySet
    from dtollib.tasks.spec import RetriggerSpec, TaskSpec


__all__ = ["TaskBuilder"]


class TaskBuilder:
    """Translate a :class:`TaskSpec` into ordered backend calls.

    The builder is stateless other than the references it captures.
    It does not own the HDASS; callers (typically
    :class:`~dtollib.tasks.DtolSession`) keep the handle and pass it
    in.
    """

    def __init__(self, backend: DtolBackend) -> None:
        self._backend = backend

    def configure_single_value(
        self,
        hdass: int,
        spec: TaskSpec,
        capabilities: CapabilitySet,
    ) -> None:
        """Run the single-value configuration sequence.

        Sequence (docs/implementation-plan.md §4.3):

        1. ``set_data_flow(OL_DF_SINGLEVALUE)``.
        2. For each channel:
           a. If the channel is MULTI_SENSOR per the capability set,
              ``set_multi_sensor_type(...)`` FIRST.
           b. ``add_channel(...)`` — drives
              ``olDaSetChannelType`` + ``olDaSetChannelRange`` +
              ``olDaSetGainListEntry`` (+ ``olDaSetThermocoupleType``).
        3. ``set_stop_on_error(spec.stop_on_error)``.
        4. ``commit()`` — ``olDaConfig``.

        Args:
            hdass: Subsystem handle from
                :meth:`~dtollib.backend.DtolBackend.get_dass`.
            spec: Task specification.  Must have
                ``data_flow == DataFlow.SINGLE_VALUE`` — validated by
                ``TaskSpec.__post_init__`` plus an explicit assert
                here for the type narrower.
            capabilities: Live capability snapshot for the
                subsystem.  Drives the MULTI_SENSOR dispatch.
        """
        if spec.data_flow != DataFlow.SINGLE_VALUE:
            # TaskSpec validation should have caught this.  Belt + braces.
            from dtollib.errors import (  # noqa: PLC0415
                DtolTaskStateError,
                ErrorContext,
            )

            raise DtolTaskStateError(
                f"configure_single_value: data_flow={spec.data_flow.value}; "
                "use configure_continuous() for non-single-value tasks",
                context=ErrorContext(
                    operation="TaskBuilder.configure_single_value",
                    task_name=spec.name,
                ),
            )

        self._backend.set_data_flow(hdass, DATA_FLOW_TO_OL[spec.data_flow])

        for list_index, channel in enumerate(spec.channels):
            # MULTI_SENSOR ordering — docs/design.md §8.5a.  On
            # subsystems that report supports_multisensor, every
            # channel is re-typed at configure time; the spec's
            # ``kind_to_multi_sensor_type`` returns the SDK-facing
            # IOType discriminator.
            _require_io_type_supported(channel, capabilities)
            _validate_digital_port(channel, capabilities)
            if capabilities.supports_multisensor:
                io_type = channel.kind_to_multi_sensor_type()
                self._backend.set_multi_sensor_type(hdass, channel.physical_channel, io_type)

            self._backend.add_channel(hdass, list_index, channel)

        self._backend.set_stop_on_error(hdass, spec.stop_on_error)
        self._backend.commit(hdass)

    def configure_continuous(
        self,
        hdass: int,
        spec: TaskSpec,
        capabilities: CapabilitySet,
    ) -> None:
        """Run the continuous-mode pre-commit configuration sequence.

        Stops short of ``commit()`` — the §12.3.2 ordering requires
        notification registration and buffer queueing BEFORE
        ``olDaConfig``. The callback bridge / ``record()`` drives the
        commit step after wiring its bridge.

        Sequence (docs/implementation-plan.md §5.7):

        1. ``set_data_flow(OL_DF_CONTINUOUS)`` (or ``OL_DF_CONTINUOUS_*``).
        2. For each channel: MULTI_SENSOR retype if needed, then
           ``add_channel(...)``.
        3. ``set_channel_list([phys, ...])`` — drives the continuous-mode
           channel list separately from the gain-list entries.
        4. ``set_clock(...)``.
        5. ``set_trigger(...)``.
        6. ``set_wrap_mode(...)``.
        7. ``set_stop_on_error(...)``.

        Args:
            hdass: Subsystem handle.
            spec: Task spec with ``data_flow`` in {CONTINUOUS, FINITE}.
            capabilities: Subsystem capability snapshot — drives the
                MULTI_SENSOR dispatch.

        Raises:
            DtolTaskStateError: If ``spec`` is not in a continuous mode.
        """
        if spec.data_flow == DataFlow.SINGLE_VALUE:
            from dtollib.errors import (  # noqa: PLC0415
                DtolTaskStateError,
                ErrorContext,
            )

            raise DtolTaskStateError(
                "configure_continuous: data_flow=single_value; "
                "use configure_single_value() instead",
                context=ErrorContext(
                    operation="TaskBuilder.configure_continuous",
                    task_name=spec.name,
                ),
            )
        if spec.timing is None or spec.buffers is None:
            from dtollib.errors import (  # noqa: PLC0415
                DtolTaskStateError,
                ErrorContext,
            )

            raise DtolTaskStateError(
                "configure_continuous requires both Timing and BufferPlan",
                context=ErrorContext(
                    operation="TaskBuilder.configure_continuous",
                    task_name=spec.name,
                ),
            )

        self._backend.set_data_flow(hdass, DATA_FLOW_TO_OL[spec.data_flow])

        # Per-channel configuration (mirrors single-value path).
        for list_index, channel in enumerate(spec.channels):
            _require_io_type_supported(channel, capabilities)
            if capabilities.supports_multisensor:
                io_type = channel.kind_to_multi_sensor_type()
                self._backend.set_multi_sensor_type(hdass, channel.physical_channel, io_type)
            self._backend.add_channel(hdass, list_index, channel)

        # Continuous channel-list: flat list of physical channel indices.
        self._backend.set_channel_list(hdass, [c.physical_channel for c in spec.channels])

        # Clock.
        clock_source = (
            OL_CLK_INTERNAL if spec.timing.clock_source == ClockSource.INTERNAL else OL_CLK_EXTERNAL
        )
        self._backend.set_clock(
            hdass,
            rate_hz=spec.timing.rate_hz,
            clock_source=clock_source,
            external_divider=spec.timing.external_divider,
        )

        # Trigger.
        kind, threshold_channel, threshold_level = _trigger_to_sdk(spec.trigger)
        self._backend.set_trigger(
            hdass,
            kind=kind,
            threshold_channel=threshold_channel,
            threshold_level=threshold_level,
        )

        # Triggered-scan retrigger.  When the timing carries a
        # RetriggerSpec the SDK collects ``multiscan_count`` scans per trigger
        # at the configured retrigger source/rate.
        if spec.timing.retrigger is not None:
            self._configure_retrigger(hdass, spec.timing.retrigger)

        # Wrap mode (NONE for FINITE; MULTIPLE for continuous; SINGLE for DAC).
        wrap = _wrap_mode_to_sdk(spec.buffers.wrap_mode)
        self._backend.set_wrap_mode(hdass, wrap)

        # DMA usage. The SDK requires olDaSetDmaUsage(min(1, NUMDMACHANS))
        # for continuous mode even when the subsystem reports zero DMA
        # channels (the DT9805/06 report NUMDMACHANS==0 yet still need the
        # call — docs/decisions.md). Pass 1 when DMA is available, else 0,
        # and always make the call.
        self._backend.set_dma_usage(hdass, 1 if capabilities.supports_dma else 0)

        self._backend.set_stop_on_error(hdass, spec.stop_on_error)
        # Deliberately do NOT call commit() here — the §12.3.2 ordering
        # requires register + queue BEFORE commit; the recorder does it.

    def configure_counter(
        self,
        hdass: int,
        spec: TaskSpec,
        capabilities: CapabilitySet,
    ) -> None:
        """Configure a counter/timer, quadrature, or tachometer subsystem.

        Counter subsystems are read on demand after :meth:`start`; there is
        no channel/gain list or sample clock to set up.  The critical
        ordering invariant is **C/T mode first** — ``olDaSetCTMode`` re-types
        the counter and must precede gate / pulse / edge setters.  The fake
        backend rejects out-of-order calls.

        Args:
            hdass: Subsystem handle.
            spec: Task spec whose channels are counter/quadrature/tachometer.
            capabilities: Subsystem capability snapshot (unused today; kept
                for signature parity with the AI/continuous paths).
        """
        for channel in spec.channels:
            _require_counter_mode_supported(_counter_mode_for(channel), capabilities, channel)
            self._configure_counter_channel(hdass, channel)
        self._backend.set_stop_on_error(hdass, spec.stop_on_error)
        self._backend.commit(hdass)

    def _configure_counter_channel(self, hdass: int, channel: ChannelSpec) -> None:
        """Issue the ordered SDK calls for one counter-family channel."""
        from dtollib.channels.counter_input import (  # noqa: PLC0415
            CounterEdgeCount,
            CounterEdgeToEdge,
            CounterInputBase,
            QuadratureDecoder,
            Tachometer,
        )
        from dtollib.channels.counter_output import (  # noqa: PLC0415
            CounterOutputBase,
            OneShotOutput,
            PulseTrainOutput,
        )

        # 1. C/T mode FIRST — re-types the counter (docs/implementation-plan §7.6).
        mode = _counter_mode_for(channel)
        self._backend.set_ct_mode(hdass, _COUNTER_MODE_TO_OL[mode])

        # 2. Per-kind setters.
        if isinstance(channel, CounterInputBase):
            self._backend.set_gate_type(hdass, _GATE_TYPE_TO_OL[channel.gate_type])
            if isinstance(channel, CounterEdgeCount) and channel.cascade:
                self._backend.set_cascade_mode(hdass, cascade=True)
            if isinstance(channel, CounterEdgeToEdge):
                self._backend.set_measure_edges(
                    hdass,
                    start_edge=_EDGE_TO_OL[channel.start_edge],
                    stop_edge=_EDGE_TO_OL[channel.stop_edge],
                )
        elif isinstance(channel, CounterOutputBase):
            duty_or_width = (
                channel.duty_cycle
                if isinstance(channel, PulseTrainOutput)
                else channel.pulse_width_s
                if isinstance(channel, OneShotOutput)
                else 0.0
            )
            self._backend.set_pulse(
                hdass,
                pulse_type=_PULSE_TYPE_TO_OL[channel.pulse_type],
                duty_or_width=duty_or_width,
            )
            if isinstance(channel, PulseTrainOutput):
                self._backend.set_ct_clock(
                    hdass,
                    rate_hz=channel.frequency_hz,
                    clock_source=_clock_source_to_sdk(channel.clock_source),
                )
        elif isinstance(channel, Tachometer):
            self._backend.set_measure_edges(
                hdass,
                start_edge=_EDGE_TO_OL[channel.measure_edge],
                stop_edge=_EDGE_TO_OL[channel.stop_edge],
            )
        elif isinstance(channel, QuadratureDecoder):
            # OLSS_QUAD: mode set above is sufficient for the bound surface.
            # decode_mode / index_reset are recorded on the spec; dedicated
            # SDK setters for them are not yet bound (documented gap).
            pass

    def _configure_retrigger(self, hdass: int, retrigger: RetriggerSpec) -> None:
        """Wire a :class:`RetriggerSpec` into the triggered-scan SDK calls."""
        source: int | None = None
        if retrigger.source is not None:
            source, _threshold_channel, _threshold_level = _trigger_to_sdk(retrigger.source)
        self._backend.set_triggered_scan(
            hdass,
            multiscan_count=retrigger.multiscan_count,
            retrigger_mode=_RETRIG_MODE_TO_OL[retrigger.mode],
            frequency_hz=retrigger.frequency_hz,
            source=source,
        )


def _counter_mode_for(channel: ChannelSpec) -> CounterMode:
    """Resolve the :class:`CounterMode` a counter-family channel configures."""
    from dtollib.channels.counter_input import (  # noqa: PLC0415
        CounterInputBase,
        QuadratureDecoder,
        Tachometer,
    )
    from dtollib.channels.counter_output import CounterOutputBase  # noqa: PLC0415

    if isinstance(channel, QuadratureDecoder):
        return CounterMode.QUADRATURE
    if isinstance(channel, Tachometer):
        return CounterMode.TACHOMETER
    if isinstance(channel, CounterInputBase | CounterOutputBase):
        # Both base classes declare ``counter_mode`` as a ClassVar[CounterMode].
        return channel.counter_mode
    from dtollib.errors import DtolValidationError, ErrorContext  # noqa: PLC0415

    raise DtolValidationError(
        f"_counter_mode_for: {type(channel).__name__} is not a counter-family channel",
        context=ErrorContext(operation="_counter_mode_for", channel=channel.physical_channel),
    )


def _require_counter_mode_supported(
    mode: CounterMode,
    capabilities: CapabilitySet,
    channel: ChannelSpec,
) -> None:
    """Raise ``DtolCapabilityError`` if the C/T subsystem lacks ``mode``.

    Honours the "runtime capability query is the only authority" rule: the
    DT9805/06 advertise neither MEASURE nor quadrature support, so a task that
    asks for those modes fails cleanly at configure time (before any SDK call)
    rather than surfacing the SDK's generic NOT_SUPPORTED as a backend error.
    """
    cap_attr = _COUNTER_MODE_REQUIRED_CAP.get(mode)
    if cap_attr is not None and not getattr(capabilities, cap_attr):
        from dtollib.errors import DtolCapabilityError, ErrorContext  # noqa: PLC0415

        raise DtolCapabilityError(
            f"counter mode {mode.value!r} is not supported by this subsystem "
            f"(capability {cap_attr} is false); the attached hardware does not "
            f"expose it.",
            context=ErrorContext(
                operation="configure_counter",
                channel=channel.physical_channel,
            ),
        )


# Channel ``kind`` discriminators that require an intelligent multi-sensor
# subsystem (``supports_multisensor``).  Voltage + thermocouple work on the
# plain DT9805/06 A/D, so they are deliberately absent.
_MULTI_SENSOR_ONLY_KINDS: frozenset[str] = frozenset(
    {"rtd", "thermistor", "resistance", "current", "iepe", "strain", "bridge"},
)


def _require_io_type_supported(
    channel: ChannelSpec,
    capabilities: CapabilitySet,
) -> None:
    """Raise ``DtolCapabilityError`` for a multi-sensor spec on a plain A/D.

    Mirrors :func:`_require_counter_mode_supported`.  The owned DT9805/06
    report ``supports_multisensor=False`` and reject every multi-sensor
    setter with ECODE 36; this gate turns that into a clean, configure-time
    :class:`~dtollib.errors.DtolCapabilityError` naming the sensor kind,
    rather than letting a raw backend NOT_SUPPORTED surface mid-configure.
    """
    if type(channel).kind in _MULTI_SENSOR_ONLY_KINDS and not capabilities.supports_multisensor:
        from dtollib.errors import DtolCapabilityError, ErrorContext  # noqa: PLC0415

        raise DtolCapabilityError(
            f"channel kind {type(channel).kind!r} requires an intelligent "
            f"multi-sensor subsystem (OLSSC_SUP_MULTISENSOR); this subsystem "
            f"reports supports_multisensor=False. The DT9805/DT9806 do not "
            f"support RTD/thermistor/strain/bridge/IEPE/current/resistance "
            f"inputs — those need a DT9828/9829/9837-class module.",
            context=ErrorContext(
                operation="configure_analog_input",
                channel=channel.physical_channel,
                channel_name=channel.name,
                extra={"kind": type(channel).kind},
            ),
        )


def _validate_digital_port(
    channel: ChannelSpec,
    capabilities: CapabilitySet,
) -> None:
    """Validate a digital port spec against the live subsystem shape.

    A digital subsystem exposes ``num_channels`` *ports*, each
    ``resolution`` bits (lines) wide. The per-line model the library used to
    ship addressed each line as its own SDK channel, which the DT9805/06 (one
    8-bit port at channel 0) rejected with ECODE 7. This gate turns an
    out-of-range port index or bit into a clean configure-time error.
    """
    from dtollib.channels.digital import (  # noqa: PLC0415
        DigitalInputPort,
        DigitalOutputPort,
    )

    if not isinstance(channel, DigitalInputPort | DigitalOutputPort):
        return

    from dtollib.errors import DtolValidationError, ErrorContext  # noqa: PLC0415

    ctx = ErrorContext(
        operation="configure_single_value",
        channel=channel.physical_channel,
        channel_name=channel.name,
    )

    if capabilities.num_channels and channel.physical_channel >= capabilities.num_channels:
        raise DtolValidationError(
            f"digital port index {channel.physical_channel} is out of range; the "
            f"subsystem exposes {capabilities.num_channels} port(s) "
            f"[0, {capabilities.num_channels - 1}]. Each port is a "
            f"{capabilities.resolution}-bit byte — address individual lines via "
            f"DigitalLine(bit=...), not a per-line channel index.",
            context=ctx,
        )

    width = channel.width if channel.width is not None else capabilities.resolution
    if (
        channel.width is not None
        and capabilities.resolution
        and channel.width != capabilities.resolution
    ):
        raise DtolValidationError(
            f"digital port {channel.display_name} declares width={channel.width} but the "
            f"subsystem reports resolution={capabilities.resolution} bits; omit width to "
            f"use the live value or correct the spec.",
            context=ctx,
        )
    if width:
        for line in channel.lines:
            if line.bit >= width:
                raise DtolValidationError(
                    f"digital line bit {line.bit} is outside the {width}-bit port "
                    f"{channel.display_name} [0, {width - 1}].",
                    context=ctx,
                )


_COUNTER_MODE_TO_OL: dict[CounterMode, int] = {
    CounterMode.COUNT: OL_CTMODE_COUNT,
    CounterMode.MEASURE: OL_CTMODE_MEASURE,
    CounterMode.RATE: OL_CTMODE_RATE,
    CounterMode.ONE_SHOT: OL_CTMODE_ONESHOT,
    CounterMode.ONE_SHOT_REPEAT: OL_CTMODE_ONESHOT_RPT,
    CounterMode.QUADRATURE: OL_CTMODE_QUAD,
    CounterMode.TACHOMETER: OL_CTMODE_TACH,
}

_GATE_TYPE_TO_OL: dict[GateType, int] = {
    GateType.SOFTWARE: OL_GATE_NONE,
    GateType.LOW_LEVEL: OL_GATE_LOW_LEVEL,
    GateType.HIGH_LEVEL: OL_GATE_HIGH_LEVEL,
    GateType.LOW_EDGE: OL_GATE_LOW_EDGE,
    GateType.HIGH_EDGE: OL_GATE_HIGH_EDGE,
}

_PULSE_TYPE_TO_OL: dict[PulseType, int] = {
    PulseType.LOW_TO_HIGH: OL_PLS_LOW2HIGH,
    PulseType.HIGH_TO_LOW: OL_PLS_HIGH2LOW,
}

# Counter modes the SDK exposes only when the C/T subsystem advertises the
# matching capability.  COUNT / RATE / ONESHOT / ONESHOT_RPT are intrinsic to
# every C/T subsystem and need no gate.  MEASURE-family modes (incl.
# tachometer, which measures frequency) require OLSSC_SUP_CTMODE_MEASURE;
# quadrature requires OLSSC_SUP_QUADRATURE_DECODER.  The DT9805/06 expose
# neither (bench 2026-05-28) so these raise DtolCapabilityError at configure
# time.  The fake reports both true so the software path stays unit-tested.
_COUNTER_MODE_REQUIRED_CAP: dict[CounterMode, str] = {
    CounterMode.MEASURE: "supports_ctmode_measure",
    CounterMode.TACHOMETER: "supports_ctmode_measure",
    CounterMode.QUADRATURE: "supports_quadrature_decoder",
}

_EDGE_TO_OL: dict[Edge, int] = {
    Edge.RISING: OL_EDGE_RISING,
    Edge.FALLING: OL_EDGE_FALLING,
}

_RETRIG_MODE_TO_OL: dict[RetriggerMode, int] = {
    RetriggerMode.SCAN_PER_TRIGGER: OL_RETRIG_SCANPERTRIG,
    RetriggerMode.INTERNAL: OL_RETRIG_INTERNAL,
    RetriggerMode.EXTRA: OL_RETRIG_EXTRA,
}


def _clock_source_to_sdk(clock_source: ClockSource) -> int:
    """Map :class:`ClockSource` to the SDK clock selector."""
    return OL_CLK_INTERNAL if clock_source == ClockSource.INTERNAL else OL_CLK_EXTERNAL


def _trigger_to_sdk(trigger: object) -> tuple[int, int | None, float | None]:
    """Map a typed :class:`TriggerSpec` onto SDK selector + threshold args."""
    if isinstance(trigger, SoftwareStart):
        return OL_TRG_SOFT, None, None
    if isinstance(trigger, ExternalDigitalStart):
        return OL_TRG_EXTERN, None, None
    if isinstance(trigger, AnalogThresholdStart):
        kind = OL_TRG_THRESHPOS if trigger.slope == Edge.RISING else OL_TRG_THRESHNEG
        return kind, trigger.channel, trigger.level
    if isinstance(trigger, SyncBusStart):
        return OL_TRG_SYNCBUS, None, None
    # Fallback — unknown trigger kinds default to software-start.
    return OL_TRG_SOFT, None, None


def _wrap_mode_to_sdk(mode: WrapMode) -> int:
    """Map :class:`WrapMode` to the SDK selector integer."""
    if mode == WrapMode.NONE:
        return OL_WRP_NONE
    if mode == WrapMode.SINGLE:
        return OL_WRP_SINGLE
    return OL_WRP_MULTIPLE
