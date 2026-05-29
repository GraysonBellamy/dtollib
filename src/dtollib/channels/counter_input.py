"""Counter/timer, quadrature, and tachometer *input* channel specs.

These route through dedicated subsystems — ``OLSS_CT`` for the counter
modes, ``OLSS_QUAD`` for the quadrature decoder, ``OLSS_TACH`` for the
tachometer — not through the analog-input multi-sensor path. None of them
is a MULTI_SENSOR channel, so they inherit the base
:meth:`ChannelSpec.kind_to_multi_sensor_type` (which raises).

Each counter-input spec carries a ``counter_mode`` :class:`ClassVar` so the
:class:`~dtollib.tasks.TaskBuilder` can issue ``olDaSetCTMode`` without
branching on the concrete class.

Design reference: docs/design.md §8.12; docs/implementation-plan.md §7.1, §7.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from dtollib.channels.base import ChannelSpec
from dtollib.errors import DtolValidationError, ErrorContext
from dtollib.tasks.models import (
    ClockSource,
    CounterMode,
    Edge,
    GateType,
    QuadratureDecodeMode,
)

__all__ = [
    "CounterEdgeCount",
    "CounterEdgeToEdge",
    "CounterFrequency",
    "CounterInputBase",
    "QuadratureDecoder",
    "Tachometer",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class CounterInputBase(ChannelSpec):
    """Common knobs for ``OLSS_CT`` counter-input channels.

    Attributes:
        clock_source: Counter clock source (internal vs external).
        gate_type: Gate-enable logic applied via ``olDaSetGateType``.
    """

    clock_source: ClockSource = ClockSource.INTERNAL
    gate_type: GateType = GateType.SOFTWARE

    # Subclasses set the C/T mode they configure.
    counter_mode: ClassVar[CounterMode]


@dataclass(frozen=True, slots=True, kw_only=True)
class CounterEdgeCount(CounterInputBase):
    """Event counter — counts edges on the counter input (``OL_CTMODE_COUNT``).

    Read back via :meth:`~dtollib.tasks.DtolSession.read_events`.

    Attributes:
        count_edge: Edge that increments the counter.
        cascade: Cascade with the adjacent counter for a 32-bit count.
    """

    kind: ClassVar[str] = "counter_edge_count"
    counter_mode: ClassVar[CounterMode] = CounterMode.COUNT

    count_edge: Edge = Edge.RISING
    cascade: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class CounterFrequency(CounterInputBase):
    """Frequency measurement over a gated window (``OL_CTMODE_MEASURE``).

    Read back via :meth:`~dtollib.tasks.DtolSession.measure_frequency`.

    Attributes:
        gate_period_s: Measurement window in seconds. ``None`` uses the
            device default window.
    """

    kind: ClassVar[str] = "counter_frequency"
    counter_mode: ClassVar[CounterMode] = CounterMode.MEASURE

    gate_period_s: float | None = None

    def __post_init__(self) -> None:
        """Reject a non-positive measurement window."""
        super().__post_init__()
        if self.gate_period_s is not None and self.gate_period_s <= 0.0:
            raise DtolValidationError(
                f"CounterFrequency[{self.display_name}]: gate_period_s must be "
                f"positive (got {self.gate_period_s})",
                context=ErrorContext(
                    operation="CounterFrequency.__post_init__",
                    channel=self.physical_channel,
                    channel_name=self.name,
                ),
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class CounterEdgeToEdge(CounterInputBase):
    """Edge-to-edge interval / pulse-width measurement (``OL_CTMODE_MEASURE``).

    Read back via :meth:`~dtollib.tasks.DtolSession.read_events` (clock ticks
    between the start and stop edges).

    Attributes:
        start_edge: Edge that starts the interval timer.
        stop_edge: Edge that stops it.
    """

    kind: ClassVar[str] = "counter_edge_to_edge"
    counter_mode: ClassVar[CounterMode] = CounterMode.MEASURE

    start_edge: Edge = Edge.RISING
    stop_edge: Edge = Edge.FALLING


@dataclass(frozen=True, slots=True, kw_only=True)
class QuadratureDecoder(ChannelSpec):
    """Quadrature encoder decoder (``OLSS_QUAD``).

    Read back via :meth:`~dtollib.tasks.DtolSession.read_events` (accumulated
    position count).

    Attributes:
        decode_mode: Counts per encoder line (X1 / X2 / X4).
        index_reset: Reset the position count on the encoder's index/Z pulse.
    """

    kind: ClassVar[str] = "quadrature_decoder"

    decode_mode: QuadratureDecodeMode = QuadratureDecodeMode.X4
    index_reset: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class Tachometer(ChannelSpec):
    """Tachometer input (``OLSS_TACH``) — first-class, distinct from C/T.

    Read back via :meth:`~dtollib.tasks.DtolSession.measure_frequency`.

    Attributes:
        measure_edge: Edge used to time successive periods.
        stop_edge: Edge that ends a measurement window.
    """

    kind: ClassVar[str] = "tachometer"

    measure_edge: Edge = Edge.RISING
    stop_edge: Edge = Edge.FALLING
