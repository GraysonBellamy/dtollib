"""Raw-counts → engineering-units conversion helpers.

The single point that converts raw SDK codes (``olDmGetBufferPtr`` /
``olDaGetSingleValue``) into engineering-unit floats. Pure NumPy — works on
Linux/macOS CI without the SDK; the Windows binding lane compares the
vectorised output against the SDK's ``olDaCodeToVolts`` on representative
codes.

Design reference: docs/design.md §11.6 (vectorised conversion), §13.1
(thermocouple sentinels), §13.2 (CJC-interleaved buffers).
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from dtollib.capi.constants import OLNOERROR, OLSSC_RETURNS_FLOATS

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy as np
    import numpy.typing as npt

    from dtollib.capi.loader import OpenLayersDlls


__all__ = [
    "BlockConversion",
    "Encoding",
    "code_to_input_volts",
    "codes_to_volts_vectorised",
    "deinterleave_cjc",
    "detect_returns_floats",
    "detect_thermocouple_sentinel",
    "detect_thermocouple_sentinel_vectorised",
    "linearise_block",
]

_EXPECTED_CODE_DIMS = 2
_MAX_RESOLUTION_BITS = 32


class Encoding(StrEnum):
    """ADC code encoding — drives the code→volts formula.

    Mirrors the channel-spec :class:`~dtollib.channels.analog_input.Encoding`;
    duplicated here so the conversion kernel has no dependency on channel
    specs (it operates on plain ndarrays + parameters).
    """

    BINARY = "binary"
    TWOS_COMPLEMENT = "twos_complement"
    OFFSET_BINARY = "offset_binary"


def detect_returns_floats(dlls: OpenLayersDlls, hdass: int) -> bool:
    """Query the ``OLSSC_RETURNS_FLOATS`` capability on a subsystem.

    DT9805/DT9806 multi-sensor subsystems return engineering-unit
    floats directly; conventional A/D subsystems return raw integer
    codes that need scaling via :func:`codes_to_volts_vectorised`.

    Args:
        dlls: Loaded DataAcq SDK handle pair.
        hdass: Subsystem handle from :func:`olDaGetDASS`.

    Returns:
        ``True`` if the subsystem populates ``olDaGetSingleValues``
        with float engineering units; ``False`` if integer codes.
    """
    value = ctypes.c_ulong(0)
    status = dlls.oldaapi.olDaGetSSCaps(hdass, OLSSC_RETURNS_FLOATS, ctypes.byref(value))
    if status != OLNOERROR:
        return False
    return bool(value.value)


def codes_to_volts_vectorised(
    codes: npt.NDArray[np.signedinteger | np.unsignedinteger],
    *,
    ranges: Sequence[tuple[float, float]],
    gains: Sequence[float],
    resolution_bits: int,
    encoding: Encoding,
) -> npt.NDArray[np.float64]:
    """Convert raw SDK codes to engineering-unit volts in a vectorised pass.

    Operates on a ``(n_channels, n_samples)`` code array. Each channel's row
    is scaled using that channel's range and gain. The formula matches
    ``olDaCodeToVolts`` per SDK documentation:

    - ``TWOS_COMPLEMENT``: ``v = code / 2^(n-1) * (span / 2) / gain + mid``
    - ``OFFSET_BINARY``:    ``v = (code - 2^(n-1)) / 2^(n-1) * (span / 2) / gain + mid``
    - ``BINARY``:           ``v = code / (2^n - 1) * span / gain + lo``

    where ``span = hi - lo``, ``mid = (hi + lo) / 2`` per channel.

    Args:
        codes: Code array, shape ``(n_channels, n_samples)``. ``int16`` or
            ``int32`` depending on the subsystem's resolution.
        ranges: Per-channel ``(low_volts, high_volts)`` tuples; must have
            length ``n_channels``.
        gains: Per-channel gain values; must have length ``n_channels``.
        resolution_bits: ADC resolution in bits (e.g. ``16`` / ``24``).
        encoding: Code encoding kind.

    Returns:
        Float64 array, same shape as ``codes``, with values in volts.

    Raises:
        ValueError: If shapes mismatch or ``resolution_bits`` is unreasonable.
    """
    import numpy as np  # noqa: PLC0415

    if codes.ndim != _EXPECTED_CODE_DIMS:
        raise ValueError(f"codes must be 2-D (n_channels, n_samples), got shape {codes.shape}")
    n_channels, _ = codes.shape
    if len(ranges) != n_channels:
        raise ValueError(f"ranges has length {len(ranges)} but codes has {n_channels} channels")
    if len(gains) != n_channels:
        raise ValueError(f"gains has length {len(gains)} but codes has {n_channels} channels")
    if resolution_bits <= 1 or resolution_bits > _MAX_RESOLUTION_BITS:
        raise ValueError(f"resolution_bits must be in [2, 32]; got {resolution_bits}")

    lows = np.asarray([r[0] for r in ranges], dtype=np.float64)
    highs = np.asarray([r[1] for r in ranges], dtype=np.float64)
    gain_arr = np.asarray(list(gains), dtype=np.float64)
    span = highs - lows
    mid = (highs + lows) / 2.0

    code_f = codes.astype(np.float64, copy=False)
    if encoding == Encoding.TWOS_COMPLEMENT:
        scale = 2 ** (resolution_bits - 1)
        normalized = code_f / float(scale)
        volts = normalized * (span / 2.0)[:, None] / gain_arr[:, None] + mid[:, None]
    elif encoding == Encoding.OFFSET_BINARY:
        half = 2 ** (resolution_bits - 1)
        normalized = (code_f - float(half)) / float(half)
        volts = normalized * (span / 2.0)[:, None] / gain_arr[:, None] + mid[:, None]
    elif encoding == Encoding.BINARY:
        full = (2**resolution_bits) - 1
        normalized = code_f / float(full)
        volts = normalized * span[:, None] / gain_arr[:, None] + lows[:, None]
    else:
        raise ValueError(f"Unknown encoding: {encoding!r}")

    return volts.astype(np.float64, copy=False)


def code_to_input_volts(
    code: int,
    gain: float,
    *,
    vmin: float,
    vmax: float,
    resolution_bits: int,
    twos_complement: bool = False,
) -> float:
    """Convert one raw single-value code to the real-world input voltage.

    Scalar counterpart of :func:`codes_to_volts_vectorised` for the
    single-value app-side path (thermocouple linearisation on the
    DT9805/DT9806). Those A/D subsystems report ``OL_ENC_BINARY`` on a
    bipolar ±10 V range — i.e. offset-binary semantics: code ``0`` maps to
    ``vmin``, mid-scale (``2^(n-1)``) to 0 V, and ``2^n - 1`` to ``vmax``.
    Two's-complement codes are shifted into the unsigned range first. The
    result is divided by ``gain`` to refer the reading back to the input.

    ``olDaCodeToVolts`` is deliberately NOT used: it returns ECODE=9
    ("Invalid Encoding") on these boards (bench-verified 2026-05-28; see
    docs/decisions.md), so we do the conversion ourselves.

    Args:
        code: Raw count from ``olDaGetSingleValue``.
        gain: Programmable-gain-amplifier setting applied to the read.
        vmin: Configured minimum of the subsystem range (volts).
        vmax: Configured maximum of the subsystem range (volts).
        resolution_bits: ADC resolution in bits (16 on the DT9805/06).
        twos_complement: ``True`` when the subsystem encoding is
            ``OL_ENC_2SCOMP``; ``False`` for offset/straight binary.

    Returns:
        Input voltage in volts (already referred through the gain).
    """
    full = 1 << resolution_bits
    c = code
    if twos_complement:
        c = (c + (full >> 1)) % full
    adc_volts = (vmax - vmin) / full * c + vmin
    return adc_volts / gain


@dataclass(frozen=True, slots=True, kw_only=True)
class BlockConversion:
    """Per-block code → engineering-units conversion plan for the drainer.

    Built by :func:`dtollib.streaming.record` from the ``TaskSpec`` +
    ``CapabilitySet`` and consumed by the §12.3.2 drainer
    (:func:`dtollib.backend._callback_bridge.callback_bridge`) to turn a raw
    ``(n_channels, n_samples)`` code array into engineering units — replacing
    the earlier raw-codes-as-float fallback.

    The plan describes one entry per **scan-list row** (array row), in row
    order:

    - Voltage rows: code → volts via :func:`codes_to_volts_vectorised`.
    - Thermocouple rows (``tc_types[row]`` set): code → differential emf →
      NIST ITS-90 °C, cold-junction-corrected from the :attr:`cjc_row` sensor.

    Cold-junction sourcing on the DT9805/06 (``returns_floats`` False) is the
    bench-confirmed path: the CJC sensor (channel 0 at 10 mV/°C, gain 1) rides
    in the scan list as :attr:`cjc_row` — *not* the unsupported interleaved
    stream (``olDaSetReturnCjcTemperatureInStream`` returns ECODE 36 on these
    boards; see docs/decisions.md). The CJC row's own ``data`` is emitted as
    °C so it reads as an ambient-temperature channel.

    Attributes:
        encoding: ADC code encoding (offset-binary on the DT9805/06).
        resolution_bits: ADC resolution (16 on the DT9805/06).
        ranges: Per-row ``(low_volts, high_volts)`` subsystem range.
        gains: Per-row programmable-gain setting.
        tc_types: Per-row NIST TC letter (``"K"`` / ``"J"`` / ...), or
            ``None`` for non-TC rows.
        tc_envelopes: Per-row ``(min_degc, max_degc)`` operating envelope,
            or ``None`` for non-TC rows. Out-of-envelope samples become
            ``TEMP_OUT_OF_RANGE_{LOW,HIGH}`` + NaN.
        cjc_row: Scan-list row carrying the cold-junction sensor, or ``None``
            when no TC linearisation is required.
        cjc_volts_per_degc: CJC sensor scale (10 mV/°C on the DT9805/06).
        open_rail_volts: Bipolar rail magnitude — an open differential input
            pegs near ``+open_rail_volts / gain`` referred to input.
        open_rail_fraction: Fraction of the rail above which a TC row is
            treated as ``SENSOR_OPEN``.
    """

    encoding: Encoding
    resolution_bits: int
    ranges: tuple[tuple[float, float], ...]
    gains: tuple[float, ...]
    tc_types: tuple[str | None, ...] = field(default=())
    tc_envelopes: tuple[tuple[float, float] | None, ...] = field(default=())
    cjc_row: int | None = None
    cjc_volts_per_degc: float = 0.010
    open_rail_volts: float = 10.0
    open_rail_fraction: float = 0.95

    @property
    def has_thermocouples(self) -> bool:
        """``True`` when any row needs application-side TC linearisation."""
        return any(t is not None for t in self.tc_types)


def linearise_block(
    codes: npt.NDArray[np.signedinteger | np.unsignedinteger],
    plan: BlockConversion,
) -> tuple[
    npt.NDArray[np.float64],
    npt.NDArray[np.float64] | None,
    dict[int, npt.NDArray[np.int8]],
]:
    """Convert a raw code block to engineering units per ``plan``.

    The vectorised counterpart of
    :meth:`dtollib.tasks.session.DtolSession._read_all_channels_app_side_tc`
    for the continuous/block path. Voltage rows are scaled to volts;
    thermocouple rows are scaled to differential emf, cold-junction-corrected
    from :attr:`BlockConversion.cjc_row`, and linearised with the NIST ITS-90
    inverse polynomials (:func:`dtollib.utils.convert_volts_to_temperature`).

    Args:
        codes: Code array, shape ``(n_channels, n_samples)``.
        plan: Conversion plan; ``len(plan.ranges) == n_channels``.

    Returns:
        ``(data, cjc_data, sensor_masks)`` where ``data`` is the
        engineering-unit ``float64`` array (NaN at non-OK TC positions),
        ``cjc_data`` is the per-sample cold-junction °C broadcast to
        ``data``'s shape (``None`` when no CJC row), and ``sensor_masks`` maps
        each TC row index to its ``int8`` :class:`~dtollib.tasks.SensorStatus`
        mask. Rows absent from ``sensor_masks`` are all-OK.

    Raises:
        ValueError: If ``codes`` shape or ``plan`` lengths are inconsistent.
    """
    import numpy as np  # noqa: PLC0415

    volts = codes_to_volts_vectorised(
        codes,
        ranges=plan.ranges,
        gains=plan.gains,
        resolution_bits=plan.resolution_bits,
        encoding=plan.encoding,
    )
    n_channels = volts.shape[0]
    if plan.tc_types and len(plan.tc_types) != n_channels:
        raise ValueError(
            f"plan.tc_types has length {len(plan.tc_types)} but codes has {n_channels} channels"
        )

    data = volts.copy()
    cjc_data: npt.NDArray[np.float64] | None = None
    cjc_degc: npt.NDArray[np.float64] | None = None
    if plan.cjc_row is not None:
        cjc_degc = np.asarray(volts[plan.cjc_row] / plan.cjc_volts_per_degc, dtype=np.float64)
        cjc_data = np.ascontiguousarray(np.broadcast_to(cjc_degc, volts.shape))
        # The CJC row is a temperature sensor — emit it as °C, not raw volts.
        data[plan.cjc_row] = cjc_degc

    masks: dict[int, npt.NDArray[np.int8]] = {}
    for row, tc_type in enumerate(plan.tc_types):
        if tc_type is None:
            continue
        emf = volts[row]
        ref = cjc_degc if cjc_degc is not None else np.zeros_like(emf)
        temps = _tc_volts_to_temp_array(tc_type, emf, ref)
        mask = np.zeros(emf.shape, dtype=np.int8)
        # Envelope first; SENSOR_OPEN (rail-pegged) wins over an out-of-range
        # reading derived from the same pegged emf.
        envelope = plan.tc_envelopes[row] if row < len(plan.tc_envelopes) else None
        if envelope is not None:
            lo, hi = envelope
            mask = np.where(temps < lo, np.int8(2), mask)
            mask = np.where(temps > hi, np.int8(3), mask)
        open_threshold = plan.open_rail_fraction * (plan.open_rail_volts / plan.gains[row])
        mask = np.where(emf >= open_threshold, np.int8(1), mask)
        data[row] = np.where(mask != 0, np.nan, temps)
        masks[row] = mask

    return data, cjc_data, masks


def _tc_volts_to_temp_array(
    tc_type: str,
    volts: npt.NDArray[np.float64],
    cjc_degc: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """Per-sample NIST ITS-90 inverse over an emf array (non-raising).

    Wraps the scalar :func:`dtollib.utils.convert_volts_to_temperature`
    element-wise; out-of-range samples (e.g. an open-circuit rail emf) map to
    NaN rather than raising, so the caller masks them by sensor status. A
    Python loop is acceptable at bench scan rates; a fully-vectorised
    polynomial kernel is a future performance item (docs/design.md §26).
    """
    import numpy as np  # noqa: PLC0415

    from dtollib.errors import DtolValidationError  # noqa: PLC0415
    from dtollib.utils import convert_volts_to_temperature  # noqa: PLC0415

    flat_v = volts.ravel()
    flat_c = cjc_degc.ravel()
    out = np.empty(flat_v.shape, dtype=np.float64)
    for i in range(flat_v.size):
        try:
            out[i] = convert_volts_to_temperature(
                tc_type, float(flat_v[i]), cjc_temperature_c=float(flat_c[i])
            )
        except DtolValidationError:
            out[i] = np.nan
    return out.reshape(volts.shape)


def deinterleave_cjc(
    raw: npt.NDArray[np.floating | np.signedinteger],
    *,
    n_channels: int,
    n_samples: int,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Split an interleaved (value, cjc) buffer into separate ndarrays.

    Per design.md §13.2, when ``olDaSetReturnCjcTemperatureInStream(TRUE)``
    is set on a subsystem with ``OLSSC_SUP_INTERLEAVED_CJC_IN_STREAM``, each
    scan produces ``2 * n_channels`` values in the order::

        scan 0: ch0_v, ch0_cjc, ch1_v, ch1_cjc, ...
        scan 1: ch0_v, ch0_cjc, ch1_v, ch1_cjc, ...

    Total length: ``2 * n_channels * n_samples``.

    Args:
        raw: Flat 1-D buffer of length ``2 * n_channels * n_samples``, or a
            2-D array of shape ``(n_samples, 2 * n_channels)`` (both layouts
            handled).
        n_channels: Number of measurement channels (excluding the CJC
            interleave).
        n_samples: Samples per channel.

    Returns:
        ``(measurement, cjc)`` — both shape ``(n_channels, n_samples)``,
        dtype float64.

    Raises:
        ValueError: If ``raw.size != 2 * n_channels * n_samples``.
    """
    import numpy as np  # noqa: PLC0415

    expected = 2 * n_channels * n_samples
    if raw.size != expected:
        raise ValueError(
            f"deinterleave_cjc: raw buffer size {raw.size} != "
            f"2 * n_channels({n_channels}) * n_samples({n_samples}) = {expected}"
        )
    arr = np.asarray(raw, dtype=np.float64).reshape(n_samples, n_channels, 2)
    measurement = np.ascontiguousarray(arr[..., 0].T)
    cjc = np.ascontiguousarray(arr[..., 1].T)
    return measurement, cjc


def detect_thermocouple_sentinel(value: float) -> str | None:
    """Map a single SDK-style TC sentinel float to a :class:`SensorStatus` value.

    Single-value path — used by :class:`~dtollib.tasks.DaqReading`.
    See :func:`detect_thermocouple_sentinel_vectorised` for the array path.

    Args:
        value: The float returned by ``olDaGetSingleFloat`` /
            ``olDaGetSingleFloats``.

    Returns:
        The matching ``SensorStatus`` value string (``"sensor_open"`` /
        ``"temp_out_of_range_low"`` / ``"temp_out_of_range_high"``), or
        ``None`` if ``value`` is a plausible measurement.
    """
    return _SENTINEL_FLOAT_TO_STATUS.get(value)


def detect_thermocouple_sentinel_vectorised(
    values: npt.NDArray[np.floating],
) -> npt.NDArray[np.int8]:
    """Vectorised sentinel detection — for the continuous-mode drainer path.

    Returns an ``int8`` mask of the same shape as ``values``, encoded with
    :class:`~dtollib.tasks.SensorStatus` ordinals:

    - ``0``: OK (plausible measurement)
    - ``1``: SENSOR_OPEN
    - ``2``: TEMP_OUT_OF_RANGE_LOW
    - ``3``: TEMP_OUT_OF_RANGE_HIGH

    The drainer fills the masked positions in ``DaqBlock.data`` with NaN so
    downstream consumers that ignore ``sensor_status`` see gaps rather than
    plausible-looking temperatures.

    Args:
        values: Float array of TC engineering-unit readings.

    Returns:
        Mask array, same shape as ``values``, dtype int8. Zero where the
        reading is plausible.
    """
    import numpy as np  # noqa: PLC0415

    mask = np.zeros(values.shape, dtype=np.int8)
    for sentinel, ordinal in _SENTINEL_FLOAT_TO_ORDINAL.items():
        mask = np.where(values == sentinel, np.int8(ordinal), mask)
    return mask


_FAKE_SENTINELS: dict[str, float] = {
    "sensor_open": -9999.0,
    "temp_out_of_range_low": -8888.0,
    "temp_out_of_range_high": -7777.0,
}

_SENTINEL_FLOAT_TO_STATUS: dict[float, str] = {v: k for k, v in _FAKE_SENTINELS.items()}

# SensorStatus declaration order: OK(0), SENSOR_OPEN(1),
# TEMP_OUT_OF_RANGE_LOW(2), TEMP_OUT_OF_RANGE_HIGH(3).  Mirror that here so
# the vectorised mask values match SensorStatus.value-position lookups in
# block_to_long_rows.
_SENTINEL_FLOAT_TO_ORDINAL: dict[float, int] = {
    -9999.0: 1,
    -8888.0: 2,
    -7777.0: 3,
}
