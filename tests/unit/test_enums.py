"""Tests for the public StrEnum surface.

Locks the value strings so a rename isn't silent — every
downstream consumer matches on these exact spellings.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from enum import StrEnum

from dtollib import (
    BufferState,
    ClockSource,
    DataFlow,
    Edge,
    IOType,
    QueueStrategy,
    RetriggerMode,
    SensorStatus,
    SubsystemState,
    SubsystemType,
    WrapMode,
)

_ALL_ENUMS: list[type[StrEnum]] = [
    DataFlow,
    SubsystemType,
    SubsystemState,
    BufferState,
    IOType,
    SensorStatus,
    Edge,
    WrapMode,
    QueueStrategy,
    ClockSource,
    RetriggerMode,
]


@pytest.mark.parametrize("cls", _ALL_ENUMS)
def test_enum_inherits_str(cls: type[StrEnum]) -> None:
    """Every public enum is a ``StrEnum`` (so ``str(e) == e.value``)."""
    member = next(iter(cls))
    assert isinstance(member, str)


@pytest.mark.parametrize("cls", _ALL_ENUMS)
def test_enum_json_round_trip(cls: type[StrEnum]) -> None:
    """Every member round-trips through ``json.dumps`` / ``json.loads``."""
    for member in cls:
        encoded = json.dumps(member)
        decoded = json.loads(encoded)
        assert decoded == member.value
        assert cls(decoded) is member


def test_data_flow_canonical_values() -> None:
    """Lock the ``DataFlow`` string values — docs/design.md §8.12."""
    assert DataFlow.SINGLE_VALUE.value == "single_value"
    assert DataFlow.CONTINUOUS.value == "continuous"
    assert DataFlow.FINITE.value == "finite"
    assert DataFlow.CONTINUOUS_PRETRIGGER.value == "continuous_pretrigger"
    assert DataFlow.CONTINUOUS_ABOUT_TRIGGER.value == "continuous_about_trigger"


def test_subsystem_state_canonical_values() -> None:
    """Lock the ``SubsystemState`` string values — docs/design.md §8.13."""
    expected = {
        SubsystemState.INITIALIZED: "initialized",
        SubsystemState.CONFIGURED_FOR_SINGLE_VALUE: "configured_for_single_value",
        SubsystemState.CONFIGURED_FOR_CONTINUOUS: "configured_for_continuous",
        SubsystemState.PRESTARTED: "prestarted",
        SubsystemState.RUNNING: "running",
        SubsystemState.STOPPING: "stopping",
        SubsystemState.ABORTING: "aborting",
        SubsystemState.IO_COMPLETE: "io_complete",
    }
    for member, expected_value in expected.items():
        assert member.value == expected_value


def test_buffer_state_canonical_values() -> None:
    """Lock the ``BufferState`` string values — docs/design.md §8.14."""
    assert BufferState.IDLE.value == "idle"
    assert BufferState.QUEUED.value == "queued"
    assert BufferState.INPROCESS.value == "inprocess"
    assert BufferState.COMPLETED.value == "completed"
    assert BufferState.RELEASED.value == "released"


def test_io_type_canonical_values() -> None:
    """Lock the ``IOType`` string values — docs/design.md §8.15."""
    assert IOType.MULTI_SENSOR.value == "multi_sensor"
    assert IOType.THERMOCOUPLE.value == "thermocouple"
    assert IOType.VOLTAGE_IN.value == "voltage_in"
    assert IOType.ACCELEROMETER.value == "accelerometer"


def test_sensor_status_canonical_values() -> None:
    """Lock the ``SensorStatus`` sentinel values — docs/design.md §13.1."""
    assert SensorStatus.OK.value == "ok"
    assert SensorStatus.SENSOR_OPEN.value == "sensor_open"
    assert SensorStatus.TEMP_OUT_OF_RANGE_LOW.value == "temp_out_of_range_low"
    assert SensorStatus.TEMP_OUT_OF_RANGE_HIGH.value == "temp_out_of_range_high"


def test_edge_values() -> None:
    """``Edge`` lock."""
    assert Edge.RISING.value == "rising"
    assert Edge.FALLING.value == "falling"


def test_wrap_mode_values() -> None:
    """``WrapMode`` lock."""
    assert WrapMode.NONE.value == "none"
    assert WrapMode.SINGLE.value == "single"
    assert WrapMode.MULTIPLE.value == "multiple"


def test_clock_source_values() -> None:
    """``ClockSource`` lock."""
    assert ClockSource.INTERNAL.value == "internal"
    assert ClockSource.EXTERNAL.value == "external"


def test_queue_strategy_values() -> None:
    """``QueueStrategy`` lock."""
    assert QueueStrategy.REQUEUE.value == "requeue"
    assert QueueStrategy.KEEP.value == "keep"
    assert QueueStrategy.FREE_ON_DONE.value == "free_on_done"


def test_retrigger_mode_values() -> None:
    """``RetriggerMode`` lock."""
    assert RetriggerMode.SCAN_PER_TRIGGER.value == "scan_per_trigger"
    assert RetriggerMode.INTERNAL.value == "internal"
    assert RetriggerMode.EXTRA.value == "extra"
