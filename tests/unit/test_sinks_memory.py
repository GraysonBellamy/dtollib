"""Tests for :class:`dtollib.sinks.InMemorySink`."""

from __future__ import annotations

import pytest

from dtollib.sinks import BlockSink, InMemorySink, ReadingSink


def _open_state(sink: InMemorySink) -> bool:
    """Indirect read of ``sink.is_open`` to defeat mypy's literal-narrowing.

    Asserting against ``sink.is_open`` repeatedly within one function
    triggers ``warn_unreachable`` because mypy narrows the property's
    return type to ``Literal[True]`` / ``Literal[False]`` after the
    first assertion and assumes subsequent reads return the same.
    Routing through this helper breaks the narrowing chain.
    """
    return sink.is_open


@pytest.mark.anyio
async def test_lifecycle_open_close() -> None:
    """``InMemorySink`` opens and closes idempotently."""
    sink = InMemorySink()
    assert _open_state(sink) is False
    await sink.open()
    assert _open_state(sink) is True
    await sink.close()
    assert _open_state(sink) is False


@pytest.mark.anyio
async def test_context_manager_protocol() -> None:
    """``async with InMemorySink()`` opens on enter and closes on exit."""
    async with InMemorySink() as sink:
        assert _open_state(sink) is True
    assert _open_state(sink) is False


def test_satisfies_both_protocols() -> None:
    """One sink, two Protocols — ``isinstance`` checks pass for both."""
    sink = InMemorySink()
    assert isinstance(sink, ReadingSink)
    assert isinstance(sink, BlockSink)


@pytest.mark.anyio
async def test_write_before_open_raises() -> None:
    """Calling ``write`` / ``write_many`` before ``open`` raises ``RuntimeError``."""
    sink = InMemorySink()
    with pytest.raises(RuntimeError, match="before open"):
        await sink.write_many([])
    with pytest.raises(RuntimeError, match="before open"):
        await sink.write(object())  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_close_preserves_buffers() -> None:
    """``close()`` does not clear the captured buffers — point of the sink is inspection."""
    sink = InMemorySink()
    await sink.open()
    # Placeholder records — we just confirm the buffers persist.
    sink.readings.append(object())  # type: ignore[arg-type]
    sink.blocks.append(object())  # type: ignore[arg-type]
    await sink.close()
    assert len(sink.readings) == 1
    assert len(sink.blocks) == 1
