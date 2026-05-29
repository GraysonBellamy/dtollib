"""Shared §18 analog-output safety gate + volts→code encoding.

One home for the output-write validation and the offset-binary conversion, so
:meth:`dtollib.tasks.session.DtolSession.write` (scalar) and
:func:`dtollib.streaming.play` (vectorised waveform chunks) gate and encode
identically. The DT9805/06 family's ``olDaCodeToVolts`` / ``olDaVoltsToCode``
are unreliable on these boards (docs/decisions.md), so dtollib scales in
software, matching the read path.

Design reference: docs/design.md §18.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dtollib.errors import DtolConfirmationRequiredError, DtolValidationError, ErrorContext

if TYPE_CHECKING:
    import numpy.typing as npt


__all__ = [
    "AO_RESOLUTION_BITS",
    "ao_volts_to_code",
    "ao_volts_to_codes",
    "gate_ao_samples",
]


# DT9806 D/A is 16-bit; AO codes are offset-binary across [min_val, max_val].
AO_RESOLUTION_BITS = 16


def gate_ao_samples(
    channel: Any,
    *,
    lo: float,
    hi: float,
    confirm: bool,
    ctx: ErrorContext,
    op: str,
) -> None:
    """Apply the §18 AO safety gate to a value or a value *range* ``[lo, hi]``.

    The single validator shared by :meth:`DtolSession.write` (scalar; passes
    ``lo == hi``) and :func:`dtollib.streaming.play` (a waveform chunk; passes
    the chunk's ``min`` / ``max``). The gate is monotone in its bounds, so
    checking the extremes is equivalent to checking every sample:

    - ``[lo, hi]`` outside the device ``[min_val, max_val]`` → always
      :class:`DtolValidationError` (electrically impossible; ``confirm`` does
      not override).
    - any sample outside the safe band, **or** ``requires_confirm`` set,
      without ``confirm=True`` → :class:`DtolConfirmationRequiredError`.

    Raises on a gate failure; returns ``None`` when the range is admissible.
    """
    if lo < channel.min_val or hi > channel.max_val:
        raise DtolValidationError(
            f"{op}: value(s) on {channel.display_name} outside the device range "
            f"[{channel.min_val}, {channel.max_val}] V (observed [{lo}, {hi}])",
            context=ctx,
        )
    out_of_band = not channel.in_safe_band(lo) or not channel.in_safe_band(hi)
    if (out_of_band or channel.requires_confirm) and not confirm:
        raise DtolConfirmationRequiredError(
            f"{op}: value(s) on {channel.display_name} require confirm=True "
            f"(safe band [{channel.safe_min}, {channel.safe_max}], "
            f"requires_confirm={channel.requires_confirm})",
            context=ctx,
        )


def ao_volts_to_code(volts: float, min_val: float, max_val: float) -> int:
    """Map one AO voltage to a 16-bit offset-binary device code.

    The caller has already gated ``min_val <= volts <= max_val``, so the result
    is in ``[0, 2**bits - 1]``.
    """
    full_scale = (1 << AO_RESOLUTION_BITS) - 1
    span = max_val - min_val
    frac = 0.0 if span == 0 else (volts - min_val) / span
    code = round(frac * full_scale)
    return max(0, min(full_scale, code))


def ao_volts_to_codes(
    volts: npt.NDArray[Any],
    min_val: float,
    max_val: float,
) -> npt.NDArray[Any]:
    """Vectorised twin of :func:`ao_volts_to_code` — returns ``uint16`` codes."""
    import numpy as np  # noqa: PLC0415

    full_scale = (1 << AO_RESOLUTION_BITS) - 1
    span = max_val - min_val
    frac = np.zeros_like(volts) if span == 0 else (volts - min_val) / span
    codes = np.rint(frac * full_scale)
    return np.clip(codes, 0, full_scale).astype(np.uint16)
