"""Tests for ECODE → typed-exception classification (:mod:`dtollib.capi.errors`)."""

from __future__ import annotations

from typing import Any

import pytest

from dtollib.capi.constants import (
    OLDA_WM_OVERRUN_ERROR,
    OLDA_WM_TRIGGER_ERROR,
    OLDA_WM_UNDERRUN_ERROR,
    OLNOERROR,
)
from dtollib.capi.errors import check, classify
from dtollib.errors import (
    DtolBackendError,
    DtolBufferOverrunError,
    DtolBufferUnderrunError,
    DtolCapabilityError,
    DtolCapiError,
    DtolConfigurationError,
    DtolError,
    DtolResourceError,
    DtolTaskStateError,
    DtolTriggerError,
    DtolValidationError,
)

# ``check`` only dereferences ``dlls`` when it needs to decode the
# error string; the helpers handle ``AttributeError`` and ``OSError``
# silently, so passing ``None`` is safe in test context.
_NULL_DLLS: Any = None


class TestClassify:
    @pytest.mark.parametrize(
        ("ecode", "expected"),
        [
            (OLDA_WM_OVERRUN_ERROR, DtolBufferOverrunError),
            (OLDA_WM_UNDERRUN_ERROR, DtolBufferUnderrunError),
            (OLDA_WM_TRIGGER_ERROR, DtolTriggerError),
        ],
    )
    def test_per_code_table(self, ecode: int, expected: type[DtolError]) -> None:
        """Documented async-error sentinels map to dedicated subclasses."""
        assert classify(ecode) is expected

    @pytest.mark.parametrize(
        ("ecode", "expected"),
        [
            (0x0050, DtolBackendError),  # device-error range
            (0x0150, DtolResourceError),  # initialization-error range
            (0x0250, DtolConfigurationError),  # configuration-error range
            (0x0350, DtolTaskStateError),  # operation-error range
            (0x0450, DtolCapabilityError),  # buffer-error range
            (0x0550, DtolBackendError),  # memory-error range
        ],
    )
    def test_range_fallback(self, ecode: int, expected: type[DtolError]) -> None:
        """Each documented SDK error-code range maps to a subclass."""
        # Skip codes that happen to be in the per-code table.
        if ecode in (OLDA_WM_OVERRUN_ERROR, OLDA_WM_UNDERRUN_ERROR, OLDA_WM_TRIGGER_ERROR):
            pytest.skip("ecode is also in per-code table")
        assert classify(ecode) is expected

    def test_negative_ecode_routes_to_validation(self) -> None:
        """Negative codes are not part of the SDK convention — treat as validation."""
        assert classify(-1) is DtolValidationError

    def test_unknown_high_ecode_falls_through(self) -> None:
        """An ECODE outside every documented range falls through to ``DtolCapiError``."""
        assert classify(0xFFFF) is DtolCapiError

    def test_no_error_does_not_raise(self) -> None:
        """``classify(OLNOERROR)`` does not raise (caller should not classify success)."""
        # Returns *some* class but the contract is "doesn't raise".
        assert issubclass(classify(OLNOERROR), DtolError)


class TestCheck:
    def test_success_is_noop(self) -> None:
        """``check(status=0)`` returns ``None`` without raising."""
        check(_NULL_DLLS, OLNOERROR, op="test")

    def test_failure_raises_typed(self) -> None:
        """``check`` raises the typed exception selected by :func:`classify`."""
        with pytest.raises(DtolBufferOverrunError) as exc_info:
            check(_NULL_DLLS, OLDA_WM_OVERRUN_ERROR, op="olDaGetBuffer")
        ctx = exc_info.value.context
        assert ctx.operation == "olDaGetBuffer"
        assert ctx.ecode == OLDA_WM_OVERRUN_ERROR
        assert ctx.ecode_source == "oldaapi"

    def test_olmem_source_routes_correctly(self) -> None:
        """``source="olmem"`` propagates to the error context."""
        with pytest.raises(DtolError) as exc_info:
            check(_NULL_DLLS, 0x0550, op="olDmAllocBuffer", source="olmem")
        assert exc_info.value.context.ecode_source == "olmem"

    def test_extra_ctx_kwargs_appear_in_context(self) -> None:
        """Extra ``ctx`` kwargs land in :class:`ErrorContext`."""
        with pytest.raises(DtolError) as exc_info:
            check(
                _NULL_DLLS,
                0x0250,
                op="olDaConfig",
                board="DT9805(00)",
                channel=3,
            )
        ctx = exc_info.value.context
        assert ctx.board == "DT9805(00)"
        assert ctx.channel == 3
