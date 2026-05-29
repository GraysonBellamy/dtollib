"""Tests for the trigger hierarchy."""

from __future__ import annotations

import pickle

import pytest

from dtollib import (
    AnalogThresholdStart,
    DtolValidationError,
    Edge,
    ExternalDigitalStart,
    ReferenceTrigger,
    SoftwareStart,
    SyncBusStart,
    TriggerSpec,
)


class TestTriggerDiscriminators:
    def test_each_kind_carries_unique_class_var(self) -> None:
        kinds = {
            SoftwareStart.kind,
            ExternalDigitalStart.kind,
            AnalogThresholdStart.kind,
            SyncBusStart.kind,
        }
        assert len(kinds) == 4

    def test_all_concrete_kinds_are_triggerspec_subclasses(self) -> None:
        for cls in (SoftwareStart, ExternalDigitalStart, AnalogThresholdStart, SyncBusStart):
            assert issubclass(cls, TriggerSpec)

    def test_reference_trigger_is_not_a_triggerspec(self) -> None:
        # ReferenceTrigger composes a TriggerSpec; it isn't one itself.
        assert not issubclass(ReferenceTrigger, TriggerSpec)


class TestExternalDigitalStart:
    def test_defaults_to_rising_edge(self) -> None:
        trigger = ExternalDigitalStart()
        assert trigger.edge == Edge.RISING

    def test_falling_edge_accepted(self) -> None:
        trigger = ExternalDigitalStart(edge=Edge.FALLING)
        assert trigger.edge == Edge.FALLING

    def test_frozen(self) -> None:
        trigger = ExternalDigitalStart()
        with pytest.raises(AttributeError):
            trigger.edge = Edge.FALLING  # type: ignore[misc]


class TestAnalogThresholdStart:
    def test_channel_level_slope_required(self) -> None:
        trigger = AnalogThresholdStart(channel=2, level=1.5)
        assert trigger.channel == 2
        assert trigger.level == 1.5
        assert trigger.slope == Edge.RISING

    def test_negative_channel_rejected(self) -> None:
        with pytest.raises(DtolValidationError, match="channel must be >= 0"):
            AnalogThresholdStart(channel=-1, level=0.0)


class TestSyncBusStart:
    def test_kind_present_and_constructs(self) -> None:
        trigger = SyncBusStart()
        assert trigger.kind == "sync_bus_start"


class TestReferenceTrigger:
    def test_post_scan_count_must_be_positive(self) -> None:
        with pytest.raises(DtolValidationError, match="post_scan_count must be positive"):
            ReferenceTrigger(source=SoftwareStart(), post_scan_count=0)

    def test_composes_a_triggerspec(self) -> None:
        ref = ReferenceTrigger(source=ExternalDigitalStart(), post_scan_count=500)
        assert isinstance(ref.source, ExternalDigitalStart)
        assert ref.post_scan_count == 500


class TestTriggerPickle:
    def test_external_digital_round_trips(self) -> None:
        original = ExternalDigitalStart(edge=Edge.FALLING)
        restored = pickle.loads(pickle.dumps(original))
        assert restored == original

    def test_analog_threshold_round_trips(self) -> None:
        original = AnalogThresholdStart(channel=3, level=2.5, slope=Edge.FALLING)
        restored = pickle.loads(pickle.dumps(original))
        assert restored == original

    def test_reference_trigger_round_trips(self) -> None:
        original = ReferenceTrigger(
            source=AnalogThresholdStart(channel=0, level=1.0),
            post_scan_count=100,
        )
        restored = pickle.loads(pickle.dumps(original))
        assert restored == original
