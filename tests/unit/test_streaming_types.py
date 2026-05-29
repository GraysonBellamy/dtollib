"""Tests for the streaming types."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import anyio
import pytest

from dtollib.streaming import (
    AcquisitionSummary,
    ErrorPolicy,
    OverflowPolicy,
    Recording,
)


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        (ErrorPolicy.RAISE, "raise"),
        (ErrorPolicy.RETURN, "return"),
        (ErrorPolicy.SKIP, "skip"),
    ],
)
def test_error_policy_values(member: ErrorPolicy, expected: str) -> None:
    """``ErrorPolicy`` member values are locked."""
    assert member.value == expected
    assert json.loads(json.dumps(member)) == expected


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        (OverflowPolicy.BLOCK, "block"),
        (OverflowPolicy.DROP_OLDEST, "drop_oldest"),
        (OverflowPolicy.DROP_NEWEST, "drop_newest"),
    ],
)
def test_overflow_policy_values(member: OverflowPolicy, expected: str) -> None:
    """``OverflowPolicy`` member values are locked."""
    assert member.value == expected
    assert json.loads(json.dumps(member)) == expected


def test_acquisition_summary_defaults() -> None:
    """``AcquisitionSummary`` constructs with sensible counter defaults."""
    started = datetime.now(UTC)
    summary = AcquisitionSummary(started_at=started)
    assert summary.finished_at is None
    assert summary.payloads_emitted == 0
    assert summary.payloads_dropped == 0
    assert summary.errors_observed == 0
    assert summary.extra == {}


def test_acquisition_summary_mutates_in_place() -> None:
    """The summary is intentionally mutable so the recorder can update it."""
    summary = AcquisitionSummary(started_at=datetime.now(UTC))
    summary.payloads_emitted += 1
    summary.errors_observed += 2
    summary.extra["board"] = "DT9805(00)"
    assert summary.payloads_emitted == 1
    assert summary.errors_observed == 2
    assert summary.extra["board"] == "DT9805(00)"


def test_recording_holds_handle_fields() -> None:
    """:class:`Recording` carries stream + summary + rate."""
    summary = AcquisitionSummary(started_at=datetime.now(UTC))
    send, stream = anyio.create_memory_object_stream[object](max_buffer_size=1)
    with send, stream:
        handle: Recording[object] = Recording(stream=stream, summary=summary, rate_hz=1000.0)
        assert handle.summary is summary
        assert handle.rate_hz == 1000.0
