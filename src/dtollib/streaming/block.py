"""``record()`` — hardware-clocked block acquisition.

User-facing facade over the §12.3.2 callback bridge. Owns the buffer
pool lifecycle and the prepare → register → queue → commit → start
ordering. Yields a :class:`~dtollib.streaming.Recording` whose ``stream``
is an ``AsyncIterator[DaqBlock]``.

Design reference: docs/design.md §14.1 (recorder dispatch), §14.2
(invariants), §12.3.2 (startup + shutdown ordering).
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anyio.to_thread as anyio_to_thread

from dtollib.backend._buffer_pool import BufferPool
from dtollib.backend._callback_bridge import BridgeConfig, callback_bridge
from dtollib.errors import DtolTaskStateError, ErrorContext
from dtollib.streaming._types import ErrorPolicy, OverflowPolicy, Recording
from dtollib.tasks.models import DataFlow

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from dtollib.capi.conversion import BlockConversion
    from dtollib.tasks.models import DaqBlock
    from dtollib.tasks.session import DtolSession


__all__ = ["record"]


@asynccontextmanager
async def record(
    session: DtolSession,
    *,
    timeout: float = 10.0,  # noqa: ASYNC109 - public API placeholder name.
    stream_buffer_size: int = 16,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
    overflow: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
) -> AsyncGenerator[Recording[DaqBlock]]:
    """Hardware-clocked continuous block acquisition.

    Drives the §12.3.2 callback bridge. The session MUST be opened with
    ``autostart=False`` — the bridge needs to register notification and
    queue buffers BEFORE ``olDaConfig`` (the recorder calls
    ``session.commit()`` internally after wiring the bridge).

    Args:
        session: Session opened via ``open_device(spec, autostart=False)``.
            ``spec.data_flow`` must be ``CONTINUOUS`` or ``FINITE``;
            ``spec.buffers`` must be non-None.
        timeout: Reserved for shutdown timeout in a future revision.
            Currently informational.
        stream_buffer_size: AnyIO memory-object-stream size — the
            consumer-side back-pressure window. Distinct from
            ``spec.buffers.buffers``.
        error_policy: How to surface SDK errors that reach the producer
            loop. See docs/design.md §14.3.
        overflow: How to react when the consumer stream is full. Default
            ``DROP_OLDEST`` — keeps the SDK queue moving even with a
            slow consumer (see docs/design.md §14.4).

    Yields:
        :class:`~dtollib.streaming.Recording` whose ``stream`` is an
        ``AsyncIterator[DaqBlock]`` and ``summary`` is the mutable
        :class:`AcquisitionSummary`.

    Raises:
        DtolTaskStateError: If ``spec.data_flow`` is not continuous or
            ``spec.buffers`` is None.
    """
    del timeout  # not yet wired — placeholder for §12.3.2 shutdown timeout
    spec = session.spec
    if spec.data_flow not in {DataFlow.CONTINUOUS, DataFlow.FINITE}:
        raise DtolTaskStateError(
            f"record() requires data_flow in {{CONTINUOUS, FINITE}}; got {spec.data_flow.value}",
            context=ErrorContext(
                operation="record",
                task_name=spec.name,
            ),
        )
    if spec.buffers is None:
        raise DtolTaskStateError(
            "record() requires TaskSpec.buffers to be configured",
            context=ErrorContext(operation="record", task_name=spec.name),
        )

    backend = session.backend
    hdass = session.raw_hdass

    # Build the pool — n_channels comes from the configured channel list.
    n_channels = len(spec.channels)
    pool = BufferPool(
        backend,
        hdass,
        spec.buffers,
        n_channels=n_channels,
    )
    pool.allocate()

    task_started_at = datetime.now(UTC)
    task_started_mono_ns = time.monotonic_ns()
    config = BridgeConfig(
        device=spec.name,
        task=spec.name,
        channels=tuple((c.name or f"ch{c.physical_channel}") for c in spec.channels),
        sample_rate_hz=spec.timing.rate_hz if spec.timing else None,
        task_started_at=task_started_at,
        task_started_mono_ns=task_started_mono_ns,
        units={
            (c.name or f"ch{c.physical_channel}"): getattr(c, "unit", None) for c in spec.channels
        },
        error_policy=error_policy,
        overflow_policy=overflow,
        stream_buffer_size=stream_buffer_size,
    )

    try:
        # Bench-proven continuous startup ordering (docs/decisions.md):
        #   commit (olDaConfig #1, after the builder's channel/clock/wrap
        #   setup) → register (olDaSetWndHandle) → queue → arm (olDaConfig
        #   #2, wires the window into buffer rotation) → start.
        await anyio_to_thread.run_sync(backend.commit, hdass)
        # Build the code→engineering-units plan AFTER commit (scaling is only
        # queryable once the subsystem is configured). On AI subsystems this
        # turns the drainer's raw-code blocks into volts / °C; on other
        # subsystem kinds the plan is None and raw codes pass through.
        config.conversion = build_conversion_plan(session, hdass)
        rate_hz = spec.timing.rate_hz if spec.timing else None
        async with callback_bridge(backend, hdass, pool, config) as (rx, summary):
            # callback_bridge has registered the notification window on entry.
            pool.queue_all()
            await anyio_to_thread.run_sync(backend.arm, hdass)
            await anyio_to_thread.run_sync(backend.start, hdass)
            yield Recording(stream=rx, summary=summary, rate_hz=rate_hz)
    finally:
        # Bridge shutdown is shielded inside callback_bridge.  Pool free
        # runs here, AFTER drain-wait completed in the bridge's __aexit__.
        with suppress(Exception):
            pool.flush()
        with suppress(Exception):
            pool.free_all()


def build_conversion_plan(session: DtolSession, hdass: int) -> BlockConversion | None:
    """Build the drainer's code→engineering-units plan for an AI block task.

    Returns ``None`` for non-analog-input subsystems (raw codes pass through
    unchanged, preserving historical behaviour). For analog input it returns a
    :class:`~dtollib.capi.conversion.BlockConversion` that scales each scan row
    to volts, and — on application-linearising thermocouple subsystems
    (``supports_thermocouples and not returns_floats``, i.e. the DT9805/06) —
    linearises thermocouple rows to °C using the cold-junction sensor carried
    in the scan list.

    Raises:
        DtolValidationError: A continuous thermocouple task on an
            application-linearising subsystem whose channel list omits the
            cold-junction channel (it must be present so the drainer can
            CJC-correct each scan — there is no usable interleaved-CJC path on
            these boards; docs/decisions.md).
    """
    from dtollib.capi.conversion import BlockConversion, Encoding  # noqa: PLC0415
    from dtollib.channels.analog_input import (  # noqa: PLC0415
        AnalogInputBase,
        ThermocoupleInput,
    )

    channels = session.spec.channels
    if not channels or not all(isinstance(c, AnalogInputBase) for c in channels):
        return None

    vmin, vmax, resolution_bits, twos_complement = session.backend.get_scaling(hdass)
    encoding = Encoding.TWOS_COMPLEMENT if twos_complement else Encoding.OFFSET_BINARY
    ranges = tuple((vmin, vmax) for _ in channels)
    gains = tuple(getattr(c, "gain", 1.0) for c in channels)

    caps = session.capabilities
    app_side_tc = caps.supports_thermocouples and not caps.returns_floats
    tc_channels = [c for c in channels if isinstance(c, ThermocoupleInput)]

    tc_types: list[str | None] = [None] * len(channels)
    tc_envelopes: list[tuple[float, float] | None] = [None] * len(channels)
    cjc_row: int | None = None

    if app_side_tc and tc_channels:
        cjc_physical = tc_channels[0].cjc_channel
        physical_to_row = {c.physical_channel: i for i, c in enumerate(channels)}
        cjc_row = physical_to_row.get(cjc_physical)
        if cjc_row is None:
            raise DtolTaskStateError(
                f"record(): continuous thermocouple task {session.spec.name!r} must "
                f"include its cold-junction channel (physical_channel={cjc_physical}) "
                f"in the channel list so the drainer can CJC-correct each scan. Add a "
                f"unity-gain channel for it (e.g. AnalogInputVoltage(physical_channel="
                f"{cjc_physical}, gain=1.0)); the interleaved-CJC stream is unsupported "
                f"on this subsystem (docs/decisions.md).",
                context=ErrorContext(
                    operation="build_conversion_plan", task_name=session.spec.name
                ),
            )
        for i, c in enumerate(channels):
            if isinstance(c, ThermocoupleInput):
                if c.physical_channel == cjc_physical:
                    raise DtolTaskStateError(
                        f"record(): thermocouple channel {c.physical_channel} is also "
                        f"the cold-junction channel — a channel cannot be both the CJC "
                        f"reference and a TC measurement on this subsystem. The CJC "
                        f"sensor (channel {cjc_physical}) is a linear 10 mV/°C device, "
                        f"not a thermocouple.",
                        context=ErrorContext(
                            operation="build_conversion_plan",
                            channel=c.physical_channel,
                            task_name=session.spec.name,
                        ),
                    )
                tc_types[i] = c.thermocouple_type.value
                tc_envelopes[i] = (c.min_val_degc, c.max_val_degc)

    return BlockConversion(
        encoding=encoding,
        resolution_bits=resolution_bits,
        ranges=ranges,
        gains=gains,
        tc_types=tuple(tc_types),
        tc_envelopes=tuple(tc_envelopes),
        cjc_row=cjc_row,
    )
