"""ECODE → typed-exception classification + the single ``check`` seam.

Three responsibilities, in dependency order:

1. :func:`oldaapi_error_string` / :func:`olmem_error_string` — call the
   matching DLL's error-string function to decode a raw ``ECODE``.
2. :func:`classify` — map ``ECODE`` to a
   :class:`~dtollib.errors.DtolError` subclass.  Per-code table
   preferred; range-based fallback for unknown codes
   (docs/design.md §17.4).
3. :func:`check` — single error-wrapping point.  Every
   :class:`~dtollib.capi.api.OpenLayersApi` method routes through
   this function; an AST-level test in the binding suite
   asserts the gate is never bypassed.

The classification table grows monotonically — each
new SDK function bound brings its documented ``OLERR_*`` returns into
:data:`_PER_CODE_TABLE`.
"""

from __future__ import annotations

import ctypes
from typing import TYPE_CHECKING, Any, Final, Literal

from dtollib.capi.constants import (
    OLDA_WM_OVERRUN_ERROR,
    OLDA_WM_TRIGGER_ERROR,
    OLDA_WM_UNDERRUN_ERROR,
    OLNOERROR,
)
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
    ErrorContext,
)

if TYPE_CHECKING:
    from dtollib.capi.loader import OpenLayersDlls


__all__ = [
    "check",
    "classify",
    "oldaapi_error_string",
    "olmem_error_string",
]


# --- Error-string helpers ---------------------------------------------------


_ERROR_STRING_BUFFER_SIZE: Final[int] = 256


def oldaapi_error_string(dlls: OpenLayersDlls, ecode: int) -> str:
    """Decode an ``oldaapi`` ECODE via ``olDaGetErrorString``.

    Args:
        dlls: Loaded DataAcq SDK handle pair from
            :func:`~dtollib.capi.loader.load_openlayers`.
        ecode: Raw ``OLSTATUS`` value (non-zero).

    Returns:
        Decoded human-readable error string.  Empty string if the SDK
        does not recognise the code or the decoder itself fails — the
        caller logs whatever string it gets without re-raising.
    """
    buf = ctypes.create_string_buffer(_ERROR_STRING_BUFFER_SIZE)
    try:
        dlls.oldaapi.olDaGetErrorString(ecode, buf, _ERROR_STRING_BUFFER_SIZE)
    except (OSError, AttributeError):
        return ""
    return buf.value.decode("ascii", errors="replace")


def olmem_error_string(dlls: OpenLayersDlls, ecode: int) -> str:
    """Decode an ``olmem`` ECODE via ``olDmGetErrorString``."""
    buf = ctypes.create_string_buffer(_ERROR_STRING_BUFFER_SIZE)
    try:
        dlls.olmem.olDmGetErrorString(ecode, buf, _ERROR_STRING_BUFFER_SIZE)
    except (OSError, AttributeError):
        return ""
    return buf.value.decode("ascii", errors="replace")


# --- ECODE → DtolError subclass classification -----------------------------
#
# Two-stage lookup per docs/design.md §17.4:
#
# 1. Exact-match per-code table for documented ``OLERR_*`` codes that
#    have a precise typed-exception home (the buffer-error / trigger-error
#    / capability-error subclasses).
# 2. Range-based fallback for ECODEs that match a known category but
#    are not individually documented.  Catches new-firmware codes
#    that the SDK adds within a category without breaking the
#    binding.
#
# Asynchronous-error sentinels (OLDA_WM_OVERRUN_ERROR etc.) are
# included here because the continuous callback bridge classifies them
# through the same function.

# Per-code table — extend monotonically as new SDK functions land.
# Numeric values mirror the asynchronous-error sentinels defined in
# OLWIN.H; per-code OLERR_* additions are added alongside their owning SDK function.
_PER_CODE_TABLE: Final[dict[int, type[DtolError]]] = {
    OLDA_WM_OVERRUN_ERROR: DtolBufferOverrunError,
    OLDA_WM_UNDERRUN_ERROR: DtolBufferUnderrunError,
    OLDA_WM_TRIGGER_ERROR: DtolTriggerError,
}


# Range-based fallback per design.md §17.4 and dasdk_digest.md §8.
#
# Ranges are *half-open*: ``(low, high)`` matches ``low <= ecode < high``.
# The first matching range wins; ordering matches dasdk_digest.md §8
# (Device → Init → Config → Operation → Buffer → Memory).
_RANGE_TABLE: Final[tuple[tuple[int, int, type[DtolError]], ...]] = (
    (0x0001, 0x0100, DtolBackendError),  # Device errors
    (0x0100, 0x0200, DtolResourceError),  # Initialization errors
    (0x0200, 0x0300, DtolConfigurationError),  # Configuration errors
    (0x0300, 0x0400, DtolTaskStateError),  # Operation errors
    (0x0400, 0x0500, DtolCapabilityError),  # Buffer errors
    (0x0500, 0x0600, DtolBackendError),  # Memory errors
)


def classify(ecode: int) -> type[DtolError]:
    """Return the :class:`DtolError` subclass for an SDK status code.

    Two-pass lookup:

    1. Per-code table (exact match).
    2. Range-based fallback (the SDK groups codes by category).

    Anything that matches no range falls through to
    :class:`~dtollib.errors.DtolCapiError`.

    Args:
        ecode: Raw ``OLSTATUS`` value (non-zero).

    Returns:
        Subclass of :class:`~dtollib.errors.DtolError`.
    """
    if ecode == OLNOERROR:
        # Caller should not classify success; return a generic
        # subclass rather than raising — keeps :func:`classify`
        # side-effect-free.
        return DtolCapiError
    specific = _PER_CODE_TABLE.get(ecode)
    if specific is not None:
        return specific
    for low, high, cls in _RANGE_TABLE:
        if low <= ecode < high:
            return cls
    # SDK has documented codes well below 0x0001 and above 0x05FF in
    # some revisions; ``DtolValidationError`` would be wrong (no
    # client-side validation happened); a generic capi error is
    # accurate.
    if ecode < 0:
        # Negative codes are not part of the SDK convention — but
        # safer to surface them as validation failures than as raw
        # SDK errors.
        return DtolValidationError
    return DtolCapiError


# --- The single ``check`` seam ---------------------------------------------


def check(
    dlls: OpenLayersDlls,
    status: int,
    *,
    op: str,
    source: Literal["oldaapi", "olmem"] = "oldaapi",
    context: ErrorContext | None = None,
    **ctx: Any,
) -> None:
    """Raise the typed exception for a non-zero ``status``; no-op on success.

    The single entry point through which every SDK call flows.  The
    invariant ``status == OLNOERROR or this function raises`` is
    asserted by the AST-level test
    ``tests/unit/test_capi_api_check_invariant.py``.

    Args:
        dlls: Loaded DataAcq SDK handle pair, used to decode the error
            string via the matching DLL.
        status: Raw ``OLSTATUS`` returned by the SDK call.
        op: Logical operation name for :class:`ErrorContext`
            (e.g. ``"olDaConfig"`` or ``"poll"``).  Pass the
            SDK function name when wrapping a single SDK call.
        source: Which DLL emitted ``status`` — controls which
            error-string decoder is consulted.
        context: Pre-built :class:`ErrorContext` to enrich.  If
            ``None`` an empty context is constructed.
        **ctx: Extra fields merged into :class:`ErrorContext` via
            :meth:`ErrorContext.merged` — typical use is to pass
            ``board=...``, ``channel=...``, ``element=...`` from
            the calling backend method.

    Raises:
        DtolError: Subclass selected by :func:`classify` for the
            given ``status``.  Exception message includes the decoded
            error string and the SDK operation name.
    """
    if status == OLNOERROR:
        return

    cls = classify(status)
    if source == "oldaapi":
        message = oldaapi_error_string(dlls, status)
    else:
        message = olmem_error_string(dlls, status)

    base_ctx = context if context is not None else ErrorContext()
    enriched = base_ctx.merged(
        operation=op,
        ecode=status,
        ecode_source=source,
        ecode_message=message or None,
        **ctx,
    )
    summary = f"{op} failed with ECODE={status} ({source})"
    if message:
        summary = f"{summary}: {message}"
    raise cls(summary, context=enriched)
