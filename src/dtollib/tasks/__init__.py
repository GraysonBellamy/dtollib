"""Task specifications, sessions, channels — single-value surface.

Provides :class:`TaskSpec`, :class:`Timing`, :class:`BufferPlan`,
:class:`RawLogging`, :class:`SoftwareStart`, :class:`DtolSession`, and
:class:`TaskBuilder`, alongside the public enum surface.  Continuous-mode
triggers and the §12.3.2 callback bridge are also wired in.
"""

from __future__ import annotations

from dtollib.tasks.builder import TaskBuilder
from dtollib.tasks.models import (
    BufferState,
    ClockSource,
    CounterMode,
    DaqBlock,
    DaqReading,
    DaqSample,
    DataFlow,
    Edge,
    GateType,
    IOType,
    PulseType,
    QuadratureDecodeMode,
    QueueStrategy,
    RetriggerMode,
    SdkEventKind,
    SensorStatus,
    SubsystemState,
    SubsystemType,
    WrapMode,
    block_to_long_rows,
)
from dtollib.tasks.session import DtolSession
from dtollib.tasks.spec import (
    BufferPlan,
    RawLogging,
    RetriggerSpec,
    TaskSpec,
    Timing,
)
from dtollib.tasks.triggers import (
    AnalogThresholdStart,
    ExternalDigitalStart,
    ReferenceTrigger,
    SoftwareStart,
    SyncBusStart,
    TriggerSpec,
)

__all__ = [
    "AnalogThresholdStart",
    "BufferPlan",
    "BufferState",
    "ClockSource",
    "CounterMode",
    "DaqBlock",
    "DaqReading",
    "DaqSample",
    "DataFlow",
    "DtolSession",
    "Edge",
    "ExternalDigitalStart",
    "GateType",
    "IOType",
    "PulseType",
    "QuadratureDecodeMode",
    "QueueStrategy",
    "RawLogging",
    "ReferenceTrigger",
    "RetriggerMode",
    "RetriggerSpec",
    "SdkEventKind",
    "SensorStatus",
    "SoftwareStart",
    "SubsystemState",
    "SubsystemType",
    "SyncBusStart",
    "TaskBuilder",
    "TaskSpec",
    "Timing",
    "TriggerSpec",
    "WrapMode",
    "block_to_long_rows",
]
