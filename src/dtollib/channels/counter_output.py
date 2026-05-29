"""Counter/timer *output* channel specs — pulse train and one-shot.

All route through the ``OLSS_CT`` subsystem. They are output channels with
no read-back; :meth:`~dtollib.tasks.DtolSession.start` begins generation and
:meth:`~dtollib.tasks.DtolSession.stop` ends it. None is a MULTI_SENSOR
channel, so they inherit the base
:meth:`ChannelSpec.kind_to_multi_sensor_type` (which raises).

Design reference: docs/design.md §8.12; docs/implementation-plan.md §7.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from dtollib.channels.base import ChannelSpec
from dtollib.errors import DtolValidationError, ErrorContext
from dtollib.tasks.models import ClockSource, CounterMode, GateType, PulseType

__all__ = [
    "CounterOutputBase",
    "OneShotOutput",
    "PulseTrainOutput",
    "RepetitiveOneShotOutput",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class CounterOutputBase(ChannelSpec):
    """Common knobs for ``OLSS_CT`` counter-output channels.

    Attributes:
        clock_source: Counter clock source.
        pulse_type: Output pulse polarity.
        gate_type: Gate-enable logic.
    """

    clock_source: ClockSource = ClockSource.INTERNAL
    pulse_type: PulseType = PulseType.HIGH_TO_LOW
    gate_type: GateType = GateType.SOFTWARE

    counter_mode: ClassVar[CounterMode]


@dataclass(frozen=True, slots=True, kw_only=True)
class PulseTrainOutput(CounterOutputBase):
    """Continuous pulse-train (rate) generation (``OL_CTMODE_RATE``).

    Attributes:
        frequency_hz: Output pulse frequency in hertz (> 0).
        duty_cycle: Fraction of each period the output is in its active
            level, in ``(0, 1)``.
    """

    kind: ClassVar[str] = "pulse_train_output"
    counter_mode: ClassVar[CounterMode] = CounterMode.RATE

    frequency_hz: float
    duty_cycle: float = 0.5

    def __post_init__(self) -> None:
        """Reject a non-positive frequency or an out-of-range duty cycle."""
        super().__post_init__()
        if self.frequency_hz <= 0.0:
            raise self._reject(f"frequency_hz must be positive (got {self.frequency_hz})")
        if not (0.0 < self.duty_cycle < 1.0):
            raise self._reject(f"duty_cycle must be in (0, 1) (got {self.duty_cycle})")

    def _reject(self, detail: str) -> DtolValidationError:
        return DtolValidationError(
            f"PulseTrainOutput[{self.display_name}]: {detail}",
            context=ErrorContext(
                operation="PulseTrainOutput.__post_init__",
                channel=self.physical_channel,
                channel_name=self.name,
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class OneShotOutput(CounterOutputBase):
    """Single output pulse on trigger (``OL_CTMODE_ONESHOT``).

    Attributes:
        pulse_width_s: Width of the generated pulse in seconds (> 0).
    """

    kind: ClassVar[str] = "one_shot_output"
    counter_mode: ClassVar[CounterMode] = CounterMode.ONE_SHOT

    pulse_width_s: float

    def __post_init__(self) -> None:
        """Reject a non-positive pulse width."""
        super().__post_init__()
        if self.pulse_width_s <= 0.0:
            raise DtolValidationError(
                f"{type(self).__name__}[{self.display_name}]: pulse_width_s must be "
                f"positive (got {self.pulse_width_s})",
                context=ErrorContext(
                    operation=f"{type(self).__name__}.__post_init__",
                    channel=self.physical_channel,
                    channel_name=self.name,
                ),
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class RepetitiveOneShotOutput(OneShotOutput):
    """Repetitive one-shot pulse on each trigger (``OL_CTMODE_ONESHOT_RPT``)."""

    kind: ClassVar[str] = "repetitive_one_shot_output"
    counter_mode: ClassVar[CounterMode] = CounterMode.ONE_SHOT_REPEAT
