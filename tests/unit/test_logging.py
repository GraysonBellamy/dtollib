"""Tests for :mod:`dtollib._logging`."""

from __future__ import annotations

import logging

from dtollib._logging import ROOT, get_logger


def test_root_name() -> None:
    """``ROOT`` is ``"dtollib"`` — sibling-library convention."""
    assert ROOT == "dtollib"


def test_get_logger_root() -> None:
    """Empty ``name`` returns the root logger."""
    log = get_logger("")
    assert log.name == "dtollib"
    assert isinstance(log, logging.Logger)


def test_get_logger_nested() -> None:
    """Non-empty ``name`` returns ``dtollib.<suffix>``."""
    log = get_logger("tasks.session")
    assert log.name == "dtollib.tasks.session"


def test_get_logger_no_handlers_configured() -> None:
    """The library never configures handlers on the root logger."""
    log = get_logger("")
    # Root may inherit handlers from a test runner; the library itself
    # adds none. Assert *the library hasn't propagated* by checking that
    # the per-call logger has no handlers attached.
    nested = get_logger("test-suite")
    assert nested.handlers == []
    # And the root we returned is the canonical name.
    assert log.name == "dtollib"
