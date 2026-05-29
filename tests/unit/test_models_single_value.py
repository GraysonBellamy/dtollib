"""Tests for :class:`DaqReading` and the sentinel detector."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dtollib import DaqReading, SensorStatus
from dtollib.capi.conversion import detect_thermocouple_sentinel


class TestDaqReading:
    def test_construction_with_defaults(self) -> None:
        now = datetime.now(UTC)
        reading = DaqReading(
            device="d",
            requested_at=now,
            received_at=now,
            t_utc=now,
            t_mono_ns=1,
            latency_s=0.0,
        )
        assert reading.values == {}
        assert reading.sensor_status == {}
        assert reading.metadata == {}
        assert reading.error is None

    def test_immutable_after_construction(self) -> None:
        now = datetime.now(UTC)
        reading = DaqReading(
            device="d",
            requested_at=now,
            received_at=now,
            t_utc=now,
            t_mono_ns=1,
            latency_s=0.0,
            values={"a": 1.0},
        )
        with pytest.raises(TypeError):
            reading.values["a"] = 2.0  # type: ignore[index]

    def test_to_dict_round_trips_sensor_status_value_strings(self) -> None:
        now = datetime.now(UTC)
        reading = DaqReading(
            device="d",
            requested_at=now,
            received_at=now,
            t_utc=now,
            t_mono_ns=1,
            latency_s=0.0,
            sensor_status={"ch0": SensorStatus.SENSOR_OPEN},
        )
        data = reading.to_dict()
        assert data["sensor_status"] == {"ch0": "sensor_open"}


class TestSentinelDetector:
    def test_known_sentinel_floats_map_to_status(self) -> None:
        assert detect_thermocouple_sentinel(-9999.0) == "sensor_open"
        assert detect_thermocouple_sentinel(-8888.0) == "temp_out_of_range_low"
        assert detect_thermocouple_sentinel(-7777.0) == "temp_out_of_range_high"

    def test_plausible_temperature_returns_none(self) -> None:
        assert detect_thermocouple_sentinel(25.5) is None
        assert detect_thermocouple_sentinel(0.0) is None
        assert detect_thermocouple_sentinel(-50.0) is None
