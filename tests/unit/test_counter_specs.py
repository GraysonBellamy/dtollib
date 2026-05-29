"""Counter/timer / quadrature / tachometer channel-spec tests."""

from __future__ import annotations

import pytest

from dtollib import (
    CounterEdgeCount,
    CounterEdgeToEdge,
    CounterFrequency,
    DtolValidationError,
    Edge,
    GateType,
    OneShotOutput,
    PulseTrainOutput,
    PulseType,
    QuadratureDecodeMode,
    QuadratureDecoder,
    RepetitiveOneShotOutput,
    Tachometer,
    TaskSpec,
)
from dtollib.channels import channel_from_dict
from dtollib.tasks.models import CounterMode, SubsystemType


class TestConstruction:
    def test_edge_count_defaults(self) -> None:
        ch = CounterEdgeCount(physical_channel=0)
        assert ch.count_edge == Edge.RISING
        assert ch.cascade is False
        assert ch.gate_type == GateType.SOFTWARE
        assert ch.counter_mode == CounterMode.COUNT

    def test_kw_only_enforced(self) -> None:
        with pytest.raises(TypeError):
            CounterEdgeCount(0)  # type: ignore[misc]

    def test_pulse_train_modes(self) -> None:
        ch = PulseTrainOutput(physical_channel=0, frequency_hz=1000.0, duty_cycle=0.25)
        assert ch.counter_mode == CounterMode.RATE
        assert ch.pulse_type == PulseType.HIGH_TO_LOW

    def test_one_shot_modes(self) -> None:
        assert OneShotOutput(physical_channel=0, pulse_width_s=1e-3).counter_mode == (
            CounterMode.ONE_SHOT
        )
        assert RepetitiveOneShotOutput(physical_channel=0, pulse_width_s=1e-3).counter_mode == (
            CounterMode.ONE_SHOT_REPEAT
        )

    def test_quadrature_defaults(self) -> None:
        ch = QuadratureDecoder(physical_channel=0)
        assert ch.decode_mode == QuadratureDecodeMode.X4
        assert ch.index_reset is False


class TestValidation:
    def test_pulse_train_rejects_bad_duty(self) -> None:
        with pytest.raises(DtolValidationError, match="duty_cycle"):
            PulseTrainOutput(physical_channel=0, frequency_hz=1000.0, duty_cycle=1.5)

    def test_pulse_train_rejects_nonpositive_frequency(self) -> None:
        with pytest.raises(DtolValidationError, match="frequency_hz"):
            PulseTrainOutput(physical_channel=0, frequency_hz=0.0)

    def test_one_shot_rejects_nonpositive_width(self) -> None:
        with pytest.raises(DtolValidationError, match="pulse_width_s"):
            OneShotOutput(physical_channel=0, pulse_width_s=0.0)

    def test_frequency_rejects_nonpositive_window(self) -> None:
        with pytest.raises(DtolValidationError, match="gate_period_s"):
            CounterFrequency(physical_channel=0, gate_period_s=-1.0)


class TestSubsystemInference:
    @pytest.mark.parametrize(
        ("channel", "expected"),
        [
            (CounterEdgeCount(physical_channel=0), SubsystemType.COUNTER_TIMER),
            (CounterFrequency(physical_channel=0), SubsystemType.COUNTER_TIMER),
            (CounterEdgeToEdge(physical_channel=0), SubsystemType.COUNTER_TIMER),
            (PulseTrainOutput(physical_channel=0, frequency_hz=1.0), SubsystemType.COUNTER_TIMER),
            (QuadratureDecoder(physical_channel=0), SubsystemType.QUADRATURE),
            (Tachometer(physical_channel=0), SubsystemType.TACHOMETER),
        ],
    )
    def test_infers_subsystem(self, channel: object, expected: SubsystemType) -> None:
        spec = TaskSpec(name="t", channels=[channel])  # type: ignore[list-item]
        assert spec.infer_subsystem_type() == expected

    def test_mixing_quad_and_ct_rejected(self) -> None:
        with pytest.raises(DtolValidationError, match="mixes subsystem"):
            TaskSpec(
                name="t",
                channels=[
                    CounterEdgeCount(physical_channel=0),
                    QuadratureDecoder(physical_channel=1),
                ],
            )


class TestSerialisation:
    @pytest.mark.parametrize(
        "channel",
        [
            CounterEdgeCount(physical_channel=0, cascade=True),
            CounterFrequency(physical_channel=1, gate_period_s=0.1),
            CounterEdgeToEdge(physical_channel=0, start_edge=Edge.FALLING),
            QuadratureDecoder(physical_channel=0, decode_mode=QuadratureDecodeMode.X2),
            Tachometer(physical_channel=0),
            PulseTrainOutput(physical_channel=0, frequency_hz=500.0, duty_cycle=0.3),
            OneShotOutput(physical_channel=0, pulse_width_s=2e-3),
            RepetitiveOneShotOutput(physical_channel=0, pulse_width_s=2e-3),
        ],
    )
    def test_round_trip(self, channel: object) -> None:
        restored = channel_from_dict(channel.to_dict())  # type: ignore[attr-defined]
        assert restored == channel
