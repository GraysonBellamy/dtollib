"""Tests for the error hierarchy.

The docs/design.md §17.3 tree is fully defined — every class must
be importable and the subclass relationships must hold so code
can raise them without breaking ecosystem consumers that catch the
parents.
"""

from __future__ import annotations

import pytest

from dtollib import (
    DtolBackendError,
    DtolBufferOverrunError,
    DtolBufferUnderrunError,
    DtolCapabilityError,
    DtolCapiError,
    DtolConfigurationError,
    DtolConfirmationRequiredError,
    DtolDependencyError,
    DtolError,
    DtolReadError,
    DtolResourceError,
    DtolSinkDependencyError,
    DtolSinkError,
    DtolSinkSchemaError,
    DtolSinkWriteError,
    DtolTaskStateError,
    DtolTimeoutError,
    DtolTriggerError,
    DtolValidationError,
    DtolWriteError,
    ErrorContext,
    SubsystemType,
)

_DIRECT_CHILDREN_OF_ROOT: list[type[DtolError]] = [
    DtolConfigurationError,
    DtolValidationError,
    DtolTaskStateError,
    DtolReadError,
    DtolWriteError,
    DtolTimeoutError,
    DtolResourceError,
    DtolCapabilityError,
    DtolCapiError,
    DtolBackendError,
    DtolDependencyError,
    DtolConfirmationRequiredError,
    DtolSinkError,
]


@pytest.mark.parametrize("cls", _DIRECT_CHILDREN_OF_ROOT)
def test_subclass_of_root(cls: type[DtolError]) -> None:
    """Every documented direct-child class inherits from :class:`DtolError`."""
    assert issubclass(cls, DtolError)


def test_capi_tree() -> None:
    """``DtolCapiError`` is the parent for buffer / trigger flavours."""
    assert issubclass(DtolBufferOverrunError, DtolCapiError)
    assert issubclass(DtolBufferUnderrunError, DtolCapiError)
    assert issubclass(DtolTriggerError, DtolCapiError)
    # And transitively under DtolError.
    assert issubclass(DtolBufferOverrunError, DtolError)


def test_sink_tree() -> None:
    """``DtolSinkError`` is the parent for the sink flavours."""
    assert issubclass(DtolSinkSchemaError, DtolSinkError)
    assert issubclass(DtolSinkWriteError, DtolSinkError)
    assert issubclass(DtolSinkDependencyError, DtolSinkError)
    assert issubclass(DtolSinkError, DtolError)


@pytest.mark.parametrize(
    "cls",
    [
        *_DIRECT_CHILDREN_OF_ROOT,
        DtolBufferOverrunError,
        DtolBufferUnderrunError,
        DtolTriggerError,
        DtolSinkSchemaError,
        DtolSinkWriteError,
        DtolSinkDependencyError,
    ],
)
def test_message_only_construction(cls: type[DtolError]) -> None:
    """Every subclass constructs from a message string alone."""
    err = cls("boom")
    assert isinstance(err, DtolError)
    assert err.context is not None
    assert "boom" in str(err)


def test_error_context_is_frozen() -> None:
    """``ErrorContext`` is a frozen dataclass — mutation must fail."""
    ctx = ErrorContext(task_name="t1")
    with pytest.raises(AttributeError):
        ctx.task_name = "other"  # type: ignore[misc]


def test_error_context_extra_is_immutable() -> None:
    """The ``extra`` mapping is read-only after construction."""
    ctx = ErrorContext(extra={"k": "v"})
    with pytest.raises(TypeError):
        ctx.extra["k"] = "tampered"  # type: ignore[index]


def test_error_context_with_full_fields() -> None:
    """Every documented ``ErrorContext`` field accepts its expected type."""
    ctx = ErrorContext(
        task_name="heat_flux",
        board="DT9805(00)",
        subsystem_type=SubsystemType.ANALOG_INPUT,
        element=0,
        channel_name="surface_tc_K",
        channel=3,
        operation="poll",
        ecode=200800,
        ecode_source="oldaapi",
        ecode_message="OL_GENERAL_FAILURE",
        extra={"hint": "check cable"},
    )
    assert ctx.task_name == "heat_flux"
    assert ctx.board == "DT9805(00)"
    assert ctx.subsystem_type is SubsystemType.ANALOG_INPUT
    assert ctx.ecode_source == "oldaapi"


def test_error_context_merged() -> None:
    """``merged(**updates)`` overlays known fields; unknown keys go to ``extra``."""
    base = ErrorContext(task_name="t1", extra={"first": 1})
    merged = base.merged(board="DT9805(00)", new_hint="check cable")
    assert merged.task_name == "t1"
    assert merged.board == "DT9805(00)"
    assert merged.extra["first"] == 1
    assert merged.extra["new_hint"] == "check cable"


def test_with_context_preserves_message() -> None:
    """``err.with_context(**)`` returns a new error of the same class."""
    err = DtolReadError("read failed", context=ErrorContext(task_name="t1"))
    enriched = err.with_context(operation="poll")
    assert isinstance(enriched, DtolReadError)
    assert enriched.context.task_name == "t1"
    assert enriched.context.operation == "poll"
    assert "read failed" in str(enriched)
