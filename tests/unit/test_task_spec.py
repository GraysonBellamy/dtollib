"""Tests for :class:`dtollib.TaskSpec` and supporting dataclasses."""

from __future__ import annotations

from pathlib import Path

import pytest

from dtollib import (
    AnalogInputVoltage,
    BufferPlan,
    ClockSource,
    DataFlow,
    DtolValidationError,
    RawLogging,
    SoftwareStart,
    SubsystemType,
    TaskSpec,
    ThermocoupleInput,
    ThermocoupleType,
    Timing,
    WrapMode,
)


def _voltage(physical_channel: int = 0) -> AnalogInputVoltage:
    return AnalogInputVoltage(physical_channel=physical_channel)


def _tc(physical_channel: int = 0) -> ThermocoupleInput:
    return ThermocoupleInput(
        physical_channel=physical_channel,
        thermocouple_type=ThermocoupleType.K,
        min_val_degc=-50.0,
        max_val_degc=200.0,
    )


class TestTimingValidation:
    def test_rate_must_be_positive(self) -> None:
        with pytest.raises(DtolValidationError, match="rate_hz must be positive"):
            Timing(rate_hz=0.0)

    def test_external_clock_requires_divider(self) -> None:
        with pytest.raises(DtolValidationError, match="external_divider is required"):
            Timing(rate_hz=1000.0, clock_source=ClockSource.EXTERNAL)

    def test_internal_clock_forbids_divider(self) -> None:
        with pytest.raises(DtolValidationError, match="forbidden when clock_source"):
            Timing(rate_hz=1000.0, external_divider=4)

    def test_valid_internal_timing(self) -> None:
        timing = Timing(rate_hz=1000.0)
        assert timing.clock_source == ClockSource.INTERNAL


class TestBufferPlanValidation:
    def test_buffers_below_minimum_rejected(self) -> None:
        with pytest.raises(DtolValidationError, match="must be >= 3"):
            BufferPlan(buffers=2, samples_per_buffer=100)

    def test_samples_per_buffer_must_be_positive(self) -> None:
        with pytest.raises(DtolValidationError, match="samples_per_buffer"):
            BufferPlan(buffers=4, samples_per_buffer=0)

    def test_default_buffer_plan(self) -> None:
        plan = BufferPlan(samples_per_buffer=1000)
        assert plan.buffers == 4
        assert plan.wrap_mode == WrapMode.MULTIPLE


class TestTaskSpecValidation:
    def test_empty_channels_rejected(self) -> None:
        with pytest.raises(DtolValidationError, match="channels is empty"):
            TaskSpec(name="t", channels=[])

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(DtolValidationError, match="non-empty string"):
            TaskSpec(name="", channels=[_voltage()])

    def test_mixed_channel_kinds_rejected(self) -> None:
        # All voltage / all TC is fine; mixing them is fine too (both AI).
        # The validation here is on subsystem-kind level — single-value mode
        # only has AI subclasses, so mixing voltage + TC is allowed.  This test
        # documents that.
        TaskSpec(name="t", channels=[_voltage(0), _tc(1)])

    def test_default_trigger_is_software_start(self) -> None:
        spec = TaskSpec(name="t", channels=[_voltage()])
        assert isinstance(spec.trigger, SoftwareStart)

    def test_subsystem_type_inferred(self) -> None:
        spec = TaskSpec(name="t", channels=[_voltage()])
        assert spec.infer_subsystem_type() == SubsystemType.ANALOG_INPUT

    def test_explicit_subsystem_type_conflict_rejected(self) -> None:
        with pytest.raises(DtolValidationError, match="conflicts"):
            TaskSpec(
                name="t",
                channels=[_voltage()],
                subsystem_type=SubsystemType.ANALOG_OUTPUT,
            )


class TestTaskSpecDataFlowMatrix:
    """Single-value / §4.2 validation matrix.

    | data_flow      | timing    | buffers   |
    |----------------|-----------|-----------|
    | SINGLE_VALUE   | forbidden | forbidden |
    | CONTINUOUS     | required  | required  |
    """

    def test_single_value_forbids_timing(self) -> None:
        with pytest.raises(DtolValidationError, match="timing is forbidden"):
            TaskSpec(name="t", channels=[_voltage()], timing=Timing(rate_hz=1000.0))

    def test_single_value_forbids_buffers(self) -> None:
        with pytest.raises(DtolValidationError, match="buffers is forbidden"):
            TaskSpec(
                name="t",
                channels=[_voltage()],
                buffers=BufferPlan(samples_per_buffer=100),
            )

    def test_continuous_requires_timing(self) -> None:
        with pytest.raises(DtolValidationError, match="timing is required"):
            TaskSpec(
                name="t",
                channels=[_voltage()],
                data_flow=DataFlow.CONTINUOUS,
                buffers=BufferPlan(samples_per_buffer=100),
            )

    def test_continuous_requires_buffers(self) -> None:
        with pytest.raises(DtolValidationError, match="buffers is required"):
            TaskSpec(
                name="t",
                channels=[_voltage()],
                data_flow=DataFlow.CONTINUOUS,
                timing=Timing(rate_hz=1000.0),
            )

    def test_valid_continuous_task(self) -> None:
        spec = TaskSpec(
            name="t",
            channels=[_voltage()],
            data_flow=DataFlow.CONTINUOUS,
            timing=Timing(rate_hz=1000.0),
            buffers=BufferPlan(samples_per_buffer=100),
        )
        assert spec.timing is not None
        assert spec.timing.rate_hz == 1000.0
        assert spec.buffers is not None
        assert spec.buffers.buffers == 4


class TestRawLogging:
    def test_construction(self) -> None:
        log = RawLogging(path=Path("run.dt-raw"))
        assert log.path == Path("run.dt-raw")
        assert log.include_metadata is True


class TestTaskSpecFiniteMatrix:
    """Finite-mode validation — `samples_per_channel` + `WrapMode.NONE`."""

    def test_finite_requires_samples_per_channel(self) -> None:
        with pytest.raises(DtolValidationError, match="samples_per_channel is required"):
            TaskSpec(
                name="t",
                channels=[_voltage()],
                data_flow=DataFlow.FINITE,
                timing=Timing(rate_hz=1000.0),
                buffers=BufferPlan(samples_per_buffer=100, wrap_mode=WrapMode.NONE),
            )

    def test_finite_requires_wrap_mode_none(self) -> None:
        with pytest.raises(DtolValidationError, match=r"wrap_mode must be WrapMode\.NONE"):
            TaskSpec(
                name="t",
                channels=[_voltage()],
                data_flow=DataFlow.FINITE,
                timing=Timing(rate_hz=1000.0, samples_per_channel=5000),
                buffers=BufferPlan(samples_per_buffer=100),
            )

    def test_valid_finite_task(self) -> None:
        spec = TaskSpec(
            name="t",
            channels=[_voltage()],
            data_flow=DataFlow.FINITE,
            timing=Timing(rate_hz=1000.0, samples_per_channel=5000),
            buffers=BufferPlan(samples_per_buffer=100, wrap_mode=WrapMode.NONE),
        )
        assert spec.timing is not None
        assert spec.timing.samples_per_channel == 5000

    def test_continuous_allows_samples_per_channel_none(self) -> None:
        spec = TaskSpec(
            name="t",
            channels=[_voltage()],
            data_flow=DataFlow.CONTINUOUS,
            timing=Timing(rate_hz=1000.0),
            buffers=BufferPlan(samples_per_buffer=100),
        )
        assert spec.timing is not None
        assert spec.timing.samples_per_channel is None

    def test_timing_samples_per_channel_must_be_positive(self) -> None:
        with pytest.raises(DtolValidationError, match="samples_per_channel must be positive"):
            Timing(rate_hz=1000.0, samples_per_channel=0)
