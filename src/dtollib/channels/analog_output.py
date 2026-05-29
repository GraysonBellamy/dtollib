"""Analog-output channel spec and supporting safety metadata.

Provides :class:`AnalogOutputVoltage` for the DT9806 D/A subsystem.
Outputs carry operator-defined *safe-band* metadata (``safe_min`` /
``safe_max``) and a ``requires_confirm`` gate; :meth:`DtolSession.write`
enforces both before any SDK call (docs/design.md §18, never silently
clamp).

Design reference: docs/design.md §18.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from dtollib.channels.base import ChannelSpec
from dtollib.errors import DtolValidationError, ErrorContext
from dtollib.tasks.models import IOType

__all__ = ["AnalogOutputVoltage"]


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogOutputVoltage(ChannelSpec):
    """Voltage-mode analog output (``olDaPutSingleValue`` / waveform D/A).

    Two range layers:

    - ``[min_val, max_val]`` — the device electrical range. A write
      outside this is electrically impossible and always raises
      :class:`~dtollib.errors.DtolValidationError`; ``confirm`` does not
      override it.
    - ``[safe_min, safe_max]`` — an optional operator safe band, a subset
      of the device range. A write outside the safe band (or any write to
      a channel with ``requires_confirm=True``) needs ``confirm=True``,
      else :meth:`DtolSession.write` raises
      :class:`~dtollib.errors.DtolConfirmationRequiredError`
      (docs/design.md §18.1).

    Attributes:
        min_val: Lower output range, volts.
        max_val: Upper output range, volts.
        safe_min: Lower safe-band bound, volts. ``None`` = no lower gate.
        safe_max: Upper safe-band bound, volts. ``None`` = no upper gate.
        requires_confirm: When true, every write to this channel needs
            ``confirm=True`` regardless of the safe band.
        gain: Output-gain-list entry passed to the SDK write call.
    """

    kind: ClassVar[str] = "ao_voltage"

    min_val: float = -10.0
    max_val: float = 10.0
    safe_min: float | None = None
    safe_max: float | None = None
    requires_confirm: bool = True
    gain: float = 1.0

    def __post_init__(self) -> None:
        """Reject inconsistent ranges before the SDK ever sees them."""
        super().__post_init__()
        if self.min_val >= self.max_val:
            raise self._reject(
                f"min_val={self.min_val} must be strictly less than max_val={self.max_val}"
            )
        if self.safe_min is not None and self.safe_min < self.min_val:
            raise self._reject(
                f"safe_min={self.safe_min} is below the device range min_val={self.min_val}"
            )
        if self.safe_max is not None and self.safe_max > self.max_val:
            raise self._reject(
                f"safe_max={self.safe_max} is above the device range max_val={self.max_val}"
            )
        if (
            self.safe_min is not None
            and self.safe_max is not None
            and self.safe_min >= self.safe_max
        ):
            raise self._reject(
                f"safe_min={self.safe_min} must be strictly less than safe_max={self.safe_max}"
            )

    def _reject(self, detail: str) -> DtolValidationError:
        return DtolValidationError(
            f"AnalogOutputVoltage[{self.display_name}]: {detail}",
            context=ErrorContext(
                operation="AnalogOutputVoltage.__post_init__",
                channel=self.physical_channel,
                channel_name=self.name,
            ),
        )

    def in_device_range(self, value: float) -> bool:
        """True if ``value`` lies within the device electrical range."""
        return self.min_val <= value <= self.max_val

    def in_safe_band(self, value: float) -> bool:
        """True if ``value`` lies within the configured safe band.

        An unset bound (``None``) does not constrain that side. With both
        bounds unset, any in-range value is considered "in band".
        """
        if self.safe_min is not None and value < self.safe_min:
            return False
        return not (self.safe_max is not None and value > self.safe_max)

    def kind_to_multi_sensor_type(self) -> IOType:
        """``AnalogOutputVoltage`` → :attr:`IOType.VOLTAGE_OUT`."""
        return IOType.VOLTAGE_OUT
