"""Typed view over an HDASS's reported capabilities.

:class:`CapabilitySet` is the consolidated result of the four SDK
capability-query functions (``olDaGetSSCaps``, ``olDaGetSSCapsEx``,
``olDaEnumSSCaps``, ``olDaEnumChannelCaps``).  Downstream code never
inspects raw flag bitmasks — it asks the typed view.

:func:`query_capabilities` is the SDK-binding-facing factory.  It
composes the four queries in the order documented by the SDK manual
(``olDaGetSSCaps`` first; it establishes which downstream queries are
valid for the subsystem) and returns an immutable
:class:`CapabilitySet`.

Design reference: docs/design.md §20.2, §11.4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dtollib.capi.constants import (
    OL_ENUM_GAINS,
    OL_ENUM_RANGES,
    OLSSC_CGLDEPTH,
    OLSSC_CURRENT_OUTPUTS,
    OLSSC_MAX_DIGITALIOLIST_VALUE,
    OLSSC_NUMCHANNELS,
    OLSSC_RETURNS_FLOATS,
    OLSSC_SUP_AUTOCAL,
    OLSSC_SUP_CONTINUOUS,
    OLSSC_SUP_CTMODE_MEASURE,
    OLSSC_SUP_DMA,
    OLSSC_SUP_INPROCESSFLUSH,
    OLSSC_SUP_INTERLEAVED_CJC_IN_STREAM,
    OLSSC_SUP_MULTISENSOR,
    OLSSC_SUP_MUTE,
    OLSSC_SUP_PUT_SINGLE_VALUES,
    OLSSC_SUP_QUADRATURE_DECODER,
    OLSSC_SUP_SIMULTANEOUS_DA,
    OLSSC_SUP_SIMULTANEOUS_SH,
    OLSSC_SUP_SINGLEENDED,
    OLSSC_SUP_SINGLEVALUE,
    OLSSC_SUP_SINGLEVALUE_AUTORANGE,
    OLSSC_SUP_SYNCHRONOUS_DIGITALIO,
    OLSSC_SUP_THERMOCOUPLES,
    OLSSC_SUP_WRPWAVEFORM,
    OLSSCE_MAX_THROUGHPUT,
)

if TYPE_CHECKING:
    from dtollib.capi.api import OpenLayersApi


__all__ = ["CapabilitySet", "query_capabilities"]


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilitySet:
    """Consolidated capability view for one HDASS.

    All fields are populated at construction time from the SDK; the
    typed view is then used in lieu of raw flag queries throughout
    the codebase.

    Attributes:
        supports_singlevalue: ``OLSSC_SUP_SINGLEVALUE``.
        supports_continuous: ``OLSSC_SUP_CONTINUOUS``.
        supports_simultaneous_sh: ``OLSSC_SUP_SIMULTANEOUS_SH``.
        supports_simultaneous_da: ``OLSSC_SUP_SIMULTANEOUS_DA``.
        supports_multisensor: ``OLSSC_SUP_MULTISENSOR``.
        supports_singleended: ``OLSSC_SUP_SINGLEENDED``.
        supports_dma: ``OLSSC_SUP_DMA``.
        supports_autocal: ``OLSSC_SUP_AUTOCAL``.
        supports_singlevalue_autorange: ``OLSSC_SUP_SINGLEVALUE_AUTORANGE``.
        supports_inprocess_flush: ``OLSSC_SUP_INPROCESSFLUSH``.
        supports_interleaved_cjc_in_stream:
            ``OLSSC_SUP_INTERLEAVED_CJC_IN_STREAM``.
        returns_floats: ``OLSSC_RETURNS_FLOATS`` — true only on
            subsystems that linearise in firmware and return engineering
            units. The DT9805/DT9806 A/D reports **false**: it returns raw
            codes and the wrapper applies NIST ITS-90 itself (see
            :attr:`supports_thermocouples`).
        supports_thermocouples: ``OLSSC_SUP_THERMOCOUPLES`` — the
            subsystem has a thermocouple front-end + CJC channel. On the
            DT9805/DT9806 this is true while ``returns_floats`` is false,
            which selects the application-side linearisation read path
            (differential emf + CJC channel + ITS-90 polynomials).
        supports_ctmode_measure: ``OLSSC_SUP_CTMODE_MEASURE`` — the C/T
            subsystem supports edge-to-edge / frequency MEASURE mode. The
            DT9805/DT9806 C/T reports **false**; the builder gates
            ``CounterMode.MEASURE`` / ``.TACHOMETER`` off with a
            :class:`~dtollib.errors.DtolCapabilityError` when this is false.
        supports_quadrature_decoder: ``OLSSC_SUP_QUADRATURE_DECODER`` — the
            C/T subsystem supports quadrature decoding. The DT9805/DT9806
            report **false**; the builder gates ``CounterMode.QUADRATURE`` off
            when this is false.
        supports_mute: ``OLSSC_SUP_MUTE`` — the (D/A) subsystem can mute /
            unmute its output. ``play()`` mutes before stop to avoid a DAC
            transient; when false the bridge skips the mute step.
            **Position header-verified, bench read-back pending (WS-B).**
        supports_wrp_waveform: ``OLSSC_SUP_WRPWAVEFORM`` — the subsystem
            supports continuous (refilled) waveform output. Gates the
            ``WrapMode.MULTIPLE`` AO path. **Bench read-back pending (WS-B).**
        supports_put_single_values: ``OLSSC_SUP_PUT_SINGLE_VALUES`` — the
            subsystem supports the simultaneous multi-channel single-value
            write (``olDaPutSingleValues``). **Bench read-back pending (WS-B).**
        supports_synchronous_digitalio: ``OLSSC_SUP_SYNCHRONOUS_DIGITALIO`` —
            the subsystem supports synchronous digital output interleaved with
            the analog stream. **Bench read-back pending (WS-B).**
        current_outputs: ``OLSSC_CURRENT_OUTPUTS`` — the D/A drives current
            (not voltage) outputs. **Bench read-back pending (WS-B).**
        max_digitaliolist_value: ``OLSSC_MAX_DIGITALIOLIST_VALUE`` — max value
            for a synchronous-DIO list entry. **Bench read-back pending (WS-B).**
        resolution: ``olDaGetResolution`` — bits per sample. For an A/D this is
            the ADC resolution; for a **digital** subsystem it is the **port
            width** (number of lines per port; 8 on the DT9805/06 DIN/DOUT).
            ``0`` if the subsystem does not report it.
        num_channels: ``OLSSC_NUMCHANNELS`` — for digital subsystems this is the
            number of **ports**, not lines (1 on the DT9805/06).
        cgl_depth: ``OLSSC_CGLDEPTH`` — maximum channel-list size.
        max_throughput_hz: ``OLSSCE_MAX_THROUGHPUT`` — float Hz.
            ``None`` if not reported by the subsystem.
        ranges: Supported input ranges as ``(max_volts, min_volts)``
            pairs from ``olDaEnumSSCaps(OL_ENUM_RANGES)``.  Empty if the
            subsystem does not enumerate ranges.
        gains: Supported programmable-gain values from
            ``olDaEnumSSCaps(OL_ENUM_GAINS)`` (e.g. ``(1.0, 10.0, 100.0,
            500.0)`` on the DT9805/06 A/D).  Empty if not enumerated.
    """

    supports_singlevalue: bool
    supports_continuous: bool
    supports_simultaneous_sh: bool
    supports_simultaneous_da: bool
    supports_multisensor: bool
    supports_singleended: bool
    supports_dma: bool
    supports_autocal: bool
    supports_singlevalue_autorange: bool
    supports_inprocess_flush: bool
    supports_interleaved_cjc_in_stream: bool
    returns_floats: bool
    supports_thermocouples: bool = False
    supports_ctmode_measure: bool = False
    supports_quadrature_decoder: bool = False
    # Output / synchronous-DIO caps (WS-B — header-verified, bench read-back
    # pending). Default False/0 so the AO path degrades gracefully until a
    # live olDaGetSSCaps read-back confirms each position.
    supports_mute: bool = False
    supports_wrp_waveform: bool = False
    supports_put_single_values: bool = False
    supports_synchronous_digitalio: bool = False
    current_outputs: bool = False
    max_digitaliolist_value: int = 0
    resolution: int = 0
    num_channels: int
    cgl_depth: int
    max_throughput_hz: float | None
    ranges: tuple[tuple[float, float], ...] = field(default_factory=tuple)
    gains: tuple[float, ...] = field(default_factory=tuple)

    def supports_simultaneous(self) -> bool:
        """True if either AI or AO simultaneous-sample-hold is supported."""
        return self.supports_simultaneous_sh or self.supports_simultaneous_da


def _bool_cap(api: OpenLayersApi, hdass: int, cap_id: int) -> bool:
    """Query a boolean capability via ``olDaGetSSCaps``; default ``False``."""
    try:
        return bool(api.get_ss_caps(hdass, cap_id))
    except Exception:
        return False


def _int_cap(api: OpenLayersApi, hdass: int, cap_id: int, *, default: int = 0) -> int:
    """Query an integer capability via ``olDaGetSSCaps``; default ``0``."""
    try:
        return api.get_ss_caps(hdass, cap_id)
    except Exception:
        return default


def _float_cap(api: OpenLayersApi, hdass: int, cap_id: int) -> float | None:
    """Query a float capability via ``olDaGetSSCapsEx``; ``None`` on absence."""
    try:
        return api.get_ss_caps_ex(hdass, cap_id)
    except Exception:
        return None


def _resolution(api: OpenLayersApi, hdass: int) -> int:
    """Query ``olDaGetResolution`` (bits per sample / port width); ``0`` on absence."""
    try:
        return api.get_resolution(hdass)
    except Exception:
        return 0


def _enum_cap(api: OpenLayersApi, hdass: int, cap_id: int) -> list[tuple[float, float]]:
    """Enumerate a list capability via ``olDaEnumSSCaps``; ``[]`` on absence.

    Subsystems that don't support the enumeration (e.g. a digital
    subsystem queried for gains) return an error, which we map to the
    empty list rather than propagating.
    """
    try:
        return api.enum_ss_caps(hdass, cap_id)
    except Exception:
        return []


def query_capabilities(api: OpenLayersApi, hdass: int) -> CapabilitySet:
    """Build a :class:`CapabilitySet` for ``hdass`` from SDK queries.

    Composition order matters per the SDK manual: ``olDaGetSSCaps``
    is queried first to establish which downstream queries are valid.
    The boolean / integer / float caps below are each queried
    individually; subsystems that don't support a particular cap
    return an error from ``olDaGetSSCaps`` which we map to the
    "feature absent" default.

    Args:
        api: Bound :class:`OpenLayersApi`.
        hdass: Subsystem handle.

    Returns:
        Immutable :class:`CapabilitySet` populated from live SDK
        queries.
    """
    return CapabilitySet(
        supports_singlevalue=_bool_cap(api, hdass, OLSSC_SUP_SINGLEVALUE),
        supports_continuous=_bool_cap(api, hdass, OLSSC_SUP_CONTINUOUS),
        supports_simultaneous_sh=_bool_cap(api, hdass, OLSSC_SUP_SIMULTANEOUS_SH),
        supports_simultaneous_da=_bool_cap(api, hdass, OLSSC_SUP_SIMULTANEOUS_DA),
        supports_multisensor=_bool_cap(api, hdass, OLSSC_SUP_MULTISENSOR),
        supports_singleended=_bool_cap(api, hdass, OLSSC_SUP_SINGLEENDED),
        supports_dma=_bool_cap(api, hdass, OLSSC_SUP_DMA),
        supports_autocal=_bool_cap(api, hdass, OLSSC_SUP_AUTOCAL),
        supports_singlevalue_autorange=_bool_cap(api, hdass, OLSSC_SUP_SINGLEVALUE_AUTORANGE),
        supports_inprocess_flush=_bool_cap(api, hdass, OLSSC_SUP_INPROCESSFLUSH),
        supports_interleaved_cjc_in_stream=_bool_cap(
            api, hdass, OLSSC_SUP_INTERLEAVED_CJC_IN_STREAM
        ),
        returns_floats=_bool_cap(api, hdass, OLSSC_RETURNS_FLOATS),
        supports_thermocouples=_bool_cap(api, hdass, OLSSC_SUP_THERMOCOUPLES),
        supports_ctmode_measure=_bool_cap(api, hdass, OLSSC_SUP_CTMODE_MEASURE),
        supports_quadrature_decoder=_bool_cap(api, hdass, OLSSC_SUP_QUADRATURE_DECODER),
        supports_mute=_bool_cap(api, hdass, OLSSC_SUP_MUTE),
        supports_wrp_waveform=_bool_cap(api, hdass, OLSSC_SUP_WRPWAVEFORM),
        supports_put_single_values=_bool_cap(api, hdass, OLSSC_SUP_PUT_SINGLE_VALUES),
        supports_synchronous_digitalio=_bool_cap(api, hdass, OLSSC_SUP_SYNCHRONOUS_DIGITALIO),
        current_outputs=_bool_cap(api, hdass, OLSSC_CURRENT_OUTPUTS),
        max_digitaliolist_value=_int_cap(api, hdass, OLSSC_MAX_DIGITALIOLIST_VALUE),
        resolution=_resolution(api, hdass),
        num_channels=_int_cap(api, hdass, OLSSC_NUMCHANNELS),
        cgl_depth=_int_cap(api, hdass, OLSSC_CGLDEPTH),
        max_throughput_hz=_float_cap(api, hdass, OLSSCE_MAX_THROUGHPUT),
        ranges=tuple(_enum_cap(api, hdass, OL_ENUM_RANGES)),
        gains=tuple(g[0] for g in _enum_cap(api, hdass, OL_ENUM_GAINS)),
    )
