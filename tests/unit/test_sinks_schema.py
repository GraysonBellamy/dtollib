"""Tests for :class:`dtollib.sinks._schema.SchemaLock`."""

from __future__ import annotations

import logging

import pytest

from dtollib.sinks._schema import ColumnSpec, SchemaLock


def _make_lock(name: str = "test") -> SchemaLock:
    return SchemaLock(sink_name=name, logger=logging.getLogger(f"test.schema.{name}"))


def test_first_batch_locks_columns() -> None:
    """The column set is locked from the first batch's union of keys."""
    lock = _make_lock()
    columns = lock.lock([{"a": 1, "b": "x"}, {"a": 2, "c": True}])
    names = [c.name for c in columns]
    assert names == ["a", "b", "c"]
    assert lock.is_locked is True


def test_double_lock_raises() -> None:
    """Locking twice is a programming error."""
    lock = _make_lock()
    lock.lock([{"a": 1}])
    with pytest.raises(RuntimeError, match="twice"):
        lock.lock([{"a": 2}])


def test_lock_empty_batch_raises() -> None:
    """``lock`` requires a non-empty first batch."""
    lock = _make_lock()
    with pytest.raises(ValueError, match="non-empty"):
        lock.lock([])


def test_project_drops_unknown_columns_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown columns on later batches are dropped with a one-shot WARN."""
    lock = _make_lock()
    lock.lock([{"a": 1}])
    with caplog.at_level(logging.WARNING):
        out = lock.project({"a": 5, "unexpected": "drop"})
    assert out == {"a": 5}
    # Second time the same unknown column appears, no new warning.
    initial_count = sum(1 for r in caplog.records if r.levelno == logging.WARNING)
    lock.project({"a": 6, "unexpected": "drop again"})
    final_count = sum(1 for r in caplog.records if r.levelno == logging.WARNING)
    assert final_count == initial_count


def test_project_fills_missing_columns_with_none() -> None:
    """Missing columns on later batches are filled with ``None``."""
    lock = _make_lock()
    lock.lock([{"a": 1, "b": "x"}])
    out = lock.project({"a": 5})
    assert out == {"a": 5, "b": None}


def test_lock_to_externally_supplied_specs() -> None:
    """``lock_to`` accepts a hand-rolled spec list."""
    lock = _make_lock()
    specs = (
        ColumnSpec(name="x", python_type=int, nullable=False),
        ColumnSpec(name="y", python_type=float, nullable=True),
    )
    columns = lock.lock_to(specs)
    assert columns == specs


def test_project_before_lock_raises() -> None:
    """``project`` before ``lock`` is a programming error."""
    lock = _make_lock()
    with pytest.raises(RuntimeError, match="before lock"):
        lock.project({"a": 1})
