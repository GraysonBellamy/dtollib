"""Trigger specifications — full start-trigger hierarchy.

Beyond :class:`SoftwareStart`, this module provides the other start-trigger
kinds (``ExternalDigitalStart``, ``AnalogThresholdStart``, ``SyncBusStart``)
plus :class:`ReferenceTrigger` for pre-/about-trigger acquisition.
:class:`RetriggerSpec` (triggered-scan retrigger) lives in ``tasks/spec.py``.

Design reference: docs/design.md §8.8.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from dtollib.errors import DtolValidationError, ErrorContext
from dtollib.tasks.models import Edge

__all__ = [
    "AnalogThresholdStart",
    "ExternalDigitalStart",
    "ReferenceTrigger",
    "SoftwareStart",
    "SyncBusStart",
    "TriggerSpec",
]


class TriggerSpec:
    """Marker base class for every trigger specification.

    Sealed-ish via the ``kind`` ClassVar discriminator; concrete subclasses
    override. :class:`SoftwareStart` and the rest of the start-trigger
    hierarchy subclass this base.
    """

    kind: ClassVar[str] = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class SoftwareStart(TriggerSpec):
    """Software-triggered start (``OL_TRG_SOFT``).

    The default trigger for every :class:`~dtollib.tasks.TaskSpec`. The SDK
    accepts the configured task on ``olDaStart`` immediately without waiting
    for an external edge.
    """

    kind: ClassVar[str] = "software_start"


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalDigitalStart(TriggerSpec):
    """External digital edge start (``OL_TRG_EXTERN``).

    Acquisition starts on the configured edge of the board's external
    trigger input. Use to synchronise capture with an external event (laser
    shutter, mechanical contact, sibling-board sync line).
    """

    kind: ClassVar[str] = "external_digital_start"

    edge: Edge = Edge.RISING


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalogThresholdStart(TriggerSpec):
    """Analog threshold start (``OL_TRG_THRESHPOS`` / ``OL_TRG_THRESHNEG``).

    Acquisition starts when the configured monitor channel's voltage crosses
    ``level`` in the direction given by ``slope``. ``Edge.RISING`` selects
    ``OL_TRG_THRESHPOS``; ``Edge.FALLING`` selects ``OL_TRG_THRESHNEG``.

    Attributes:
        channel: Physical channel index to monitor for the threshold crossing.
        level: Threshold voltage (volts).
        slope: ``RISING`` for cross-from-below; ``FALLING`` for cross-from-above.
    """

    kind: ClassVar[str] = "analog_threshold_start"

    channel: int
    level: float
    slope: Edge = Edge.RISING

    def __post_init__(self) -> None:
        """Validate the channel index is non-negative."""
        if self.channel < 0:
            raise DtolValidationError(
                f"AnalogThresholdStart.channel must be >= 0 (got {self.channel})",
                context=ErrorContext(
                    operation="AnalogThresholdStart.__post_init__",
                    channel=self.channel,
                ),
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class SyncBusStart(TriggerSpec):
    """Sync-bus start (``OL_TRG_SYNCBUS``).

    Acquisition starts on a sync-bus edge — used to coordinate multiple
    boards sharing a common back-plane sync line. Multi-board coordination
    via ``olDaSetSyncMode`` (not yet supported) builds on top of this
    trigger kind.
    """

    kind: ClassVar[str] = "sync_bus_start"


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceTrigger:
    """Reference-trigger composition — pre/about-trigger acquisition.

    Composes onto a :class:`TaskSpec` via ``TaskSpec.reference`` (not yet
    supported).
    The ``source`` trigger establishes the reference event; the SDK then
    collects ``post_scan_count`` samples after that event before stopping.

    Attributes:
        source: The trigger kind that marks the reference event.
        post_scan_count: Samples to collect after the reference event.
    """

    source: TriggerSpec
    post_scan_count: int

    def __post_init__(self) -> None:
        """Validate ``post_scan_count`` is positive."""
        if self.post_scan_count <= 0:
            raise DtolValidationError(
                f"ReferenceTrigger.post_scan_count must be positive (got {self.post_scan_count})",
                context=ErrorContext(operation="ReferenceTrigger.__post_init__"),
            )
