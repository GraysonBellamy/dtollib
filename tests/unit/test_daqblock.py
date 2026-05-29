"""Tests for :class:`DaqBlock` / :class:`DaqSample` / :class:`SdkEventKind`."""

from __future__ import annotations

import pickle
from datetime import UTC, datetime

import numpy as np
import pytest

from dtollib import (
    DaqBlock,
    DaqSample,
    DtolValidationError,
    SdkEventKind,
    SensorStatus,
    block_to_long_rows,
)


def _make_block(
    *,
    n_channels: int = 2,
    samples_per_channel: int = 4,
    block_index: int = 0,
    first_sample_index: int = 0,
    rate_hz: float | None = 1000.0,
    sensor_status: dict[str, np.ndarray] | None = None,
    raw_codes: np.ndarray | None = None,
    cjc_data: np.ndarray | None = None,
    is_linearised: bool = False,
) -> DaqBlock:
    now = datetime.now(UTC)
    channels = tuple(f"ch{i}" for i in range(n_channels))
    data = np.zeros((n_channels, samples_per_channel), dtype=np.float64)
    return DaqBlock(
        device="dev",
        channels=channels,
        data=data,
        raw_codes=raw_codes,
        cjc_data=cjc_data,
        block_index=block_index,
        first_sample_index=first_sample_index,
        samples_per_channel=samples_per_channel,
        sample_rate_hz=rate_hz,
        block_period_ns=(None if rate_hz is None else round(1e9 / rate_hz)),
        task_started_at=now,
        t0=now,
        t_mono_ns=1_000_000_000,
        t_utc=now,
        read_started_at=now,
        read_finished_at=now,
        elapsed_s=0.001,
        units={f"ch{i}": "V" for i in range(n_channels)},
        sensor_status=sensor_status or {},
        is_linearised=is_linearised,
    )


class TestDaqBlockConstruction:
    def test_happy_path(self) -> None:
        block = _make_block()
        assert block.data.shape == (2, 4)
        assert block.n_channels == 2
        assert block.channels == ("ch0", "ch1")
        assert block.units["ch0"] == "V"

    def test_data_shape_mismatch_rejected(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(DtolValidationError, match="does not match"):
            DaqBlock(
                device="d",
                channels=("a", "b"),
                data=np.zeros((3, 4), dtype=np.float64),
                block_index=0,
                first_sample_index=0,
                samples_per_channel=4,
                task_started_at=now,
                t0=now,
                t_mono_ns=0,
                t_utc=now,
                read_started_at=now,
                read_finished_at=now,
                elapsed_s=0.0,
            )

    def test_raw_codes_shape_must_match_data(self) -> None:
        raw = np.zeros((2, 5), dtype=np.int16)
        with pytest.raises(DtolValidationError, match="raw_codes shape"):
            _make_block(raw_codes=raw)

    def test_cjc_data_shape_must_match_data(self) -> None:
        cjc = np.zeros((2, 5), dtype=np.float64)
        with pytest.raises(DtolValidationError, match="cjc_data shape"):
            _make_block(cjc_data=cjc)

    def test_sensor_status_mask_length_must_match_samples(self) -> None:
        mask = np.zeros(3, dtype=np.int8)
        with pytest.raises(DtolValidationError, match="sensor_status"):
            _make_block(sensor_status={"ch0": mask})

    def test_block_index_must_be_non_negative(self) -> None:
        with pytest.raises(DtolValidationError, match="block_index"):
            _make_block(block_index=-1)

    def test_first_sample_index_must_be_non_negative(self) -> None:
        with pytest.raises(DtolValidationError, match="first_sample_index"):
            _make_block(first_sample_index=-1)


class TestDaqBlockImmutability:
    def test_data_array_is_read_only(self) -> None:
        block = _make_block()
        with pytest.raises(ValueError, match=r"read-only|writeable"):
            block.data[0, 0] = 1.0

    def test_raw_codes_array_is_read_only(self) -> None:
        raw = np.zeros((2, 4), dtype=np.int16)
        block = _make_block(raw_codes=raw)
        assert block.raw_codes is not None
        with pytest.raises(ValueError, match=r"read-only|writeable"):
            block.raw_codes[0, 0] = 1

    def test_sensor_status_mask_is_read_only(self) -> None:
        mask = np.zeros(4, dtype=np.int8)
        block = _make_block(sensor_status={"ch0": mask})
        with pytest.raises(ValueError, match=r"read-only|writeable"):
            block.sensor_status["ch0"][0] = 1

    def test_units_mapping_is_immutable(self) -> None:
        block = _make_block()
        with pytest.raises(TypeError):
            block.units["ch0"] = "mV"  # type: ignore[index]


class TestDaqBlockPickle:
    def test_round_trip_preserves_data_and_metadata(self) -> None:
        mask = np.array([0, 1, 0, 0], dtype=np.int8)
        block = _make_block(sensor_status={"ch0": mask})
        restored = pickle.loads(pickle.dumps(block))
        assert restored.device == block.device
        assert restored.channels == block.channels
        np.testing.assert_array_equal(restored.data, block.data)
        np.testing.assert_array_equal(restored.sensor_status["ch0"], block.sensor_status["ch0"])


class TestBlockToLongRows:
    def test_row_count_matches_n_channels_times_samples(self) -> None:
        block = _make_block(n_channels=3, samples_per_channel=5)
        rows = list(block_to_long_rows(block))
        assert len(rows) == 3 * 5

    def test_emits_in_channel_major_sample_minor_order(self) -> None:
        block = _make_block(n_channels=2, samples_per_channel=3)
        rows = list(block_to_long_rows(block))
        assert [r.channel for r in rows] == ["ch0", "ch0", "ch0", "ch1", "ch1", "ch1"]
        assert [r.sample_index for r in rows] == [0, 1, 2, 0, 1, 2]

    def test_sample_index_accounts_for_first_sample_offset(self) -> None:
        block = _make_block(samples_per_channel=2, first_sample_index=10)
        rows = list(block_to_long_rows(block))
        assert [r.sample_index for r in rows] == [10, 11, 10, 11]

    def test_t_mono_ns_reconstructs_via_block_period_ns(self) -> None:
        block = _make_block(samples_per_channel=3, rate_hz=1000.0)
        rows = list(block_to_long_rows(block))
        # ch0 row spans 3 samples at 1 ms cadence — 1e6 ns increments.
        ch0_rows = [r for r in rows if r.channel == "ch0"]
        assert [r.t_mono_ns - ch0_rows[0].t_mono_ns for r in ch0_rows] == [
            0,
            1_000_000,
            2_000_000,
        ]

    def test_sensor_status_mask_decoded_per_sample(self) -> None:
        sensor_open = list(SensorStatus).index(SensorStatus.SENSOR_OPEN)
        mask = np.array([0, sensor_open, 0, 0], dtype=np.int8)
        block = _make_block(samples_per_channel=4, sensor_status={"ch0": mask})
        rows = list(block_to_long_rows(block))
        ch0_rows = [r for r in rows if r.channel == "ch0"]
        assert [r.sensor_status for r in ch0_rows] == [
            SensorStatus.OK,
            SensorStatus.SENSOR_OPEN,
            SensorStatus.OK,
            SensorStatus.OK,
        ]

    def test_channels_without_mask_default_to_ok(self) -> None:
        block = _make_block()
        rows = list(block_to_long_rows(block))
        assert all(r.sensor_status == SensorStatus.OK for r in rows)

    def test_is_linearised_propagates_from_block(self) -> None:
        block = _make_block(is_linearised=True)
        rows = list(block_to_long_rows(block))
        assert rows
        assert all(r.is_linearised for r in rows)

    def test_is_linearised_false_by_default(self) -> None:
        block = _make_block()
        rows = list(block_to_long_rows(block))
        assert all(not r.is_linearised for r in rows)


class TestDaqSample:
    def test_to_dict_round_trips_sensor_status_value(self) -> None:
        now = datetime.now(UTC)
        sample = DaqSample(
            device="d",
            channel="ch0",
            value=1.5,
            sample_index=42,
            block_index=3,
            t_mono_ns=1234,
            t_utc=now,
            sensor_status=SensorStatus.SENSOR_OPEN,
        )
        row = sample.to_dict()
        assert row["sensor_status"] == "sensor_open"
        assert row["value"] == 1.5
        assert row["sample_index"] == 42
        assert row["is_linearised"] is False

    def test_to_dict_carries_is_linearised(self) -> None:
        now = datetime.now(UTC)
        sample = DaqSample(
            device="d",
            channel="ch0",
            value=23.4,
            sample_index=0,
            block_index=0,
            t_mono_ns=0,
            t_utc=now,
            unit="degC",
            is_linearised=True,
        )
        assert sample.to_dict()["is_linearised"] is True

    def test_metadata_is_immutable(self) -> None:
        now = datetime.now(UTC)
        sample = DaqSample(
            device="d",
            channel="ch0",
            value=1.0,
            sample_index=0,
            block_index=0,
            t_mono_ns=0,
            t_utc=now,
            metadata={"run": "r1"},
        )
        with pytest.raises(TypeError):
            sample.metadata["run"] = "r2"  # type: ignore[index]


class TestSdkEventKind:
    def test_all_eleven_kinds_present(self) -> None:
        names = {k.value for k in SdkEventKind}
        assert names == {
            "buffer_done",
            "pretrigger_buffer_done",
            "buffer_reused",
            "queue_done",
            "queue_stopped",
            "io_complete",
            "event_done",
            "measure_done",
            "trigger_error",
            "overrun_error",
            "underrun_error",
        }

    def test_str_enum_value_is_lowercase_underscore(self) -> None:
        for kind in SdkEventKind:
            assert kind.value.islower()
            assert " " not in kind.value
