"""Real DataAcq SDK backend — :class:`DataAcqBackend`.

This class is **Layer 3** of the C-boundary stack (docs/design.md §10.3):

- Layer 1: :mod:`dtollib.capi.prototypes` — raw ctypes signatures.
- Layer 2: :class:`~dtollib.capi.OpenLayersApi` — output-pointer
  extraction + ECODE classification.
- **Layer 3 (this module)** — session-level orchestration:
  HDRVR ref-counting, capability cache, notification-wrapper pinning,
  buffer pool.

The class is constructed once per process and shared across every
:class:`~dtollib.tasks.DtolSession`.  See
:class:`~dtollib.manager.DtolManager` for the per-session sharing
discipline.
"""

from __future__ import annotations

import contextlib
import ctypes
import threading
from typing import TYPE_CHECKING, Any

from dtollib._logging import get_logger
from dtollib.backend._message_window import MessageWindow
from dtollib.capi import OpenLayersDlls, load_openlayers
from dtollib.capi.api import OpenLayersApi
from dtollib.capi.constants import (
    IOTYPE_ACCELEROMETER,
    IOTYPE_BRIDGE,
    IOTYPE_CURRENT,
    IOTYPE_RESISTANCE,
    IOTYPE_RTD,
    IOTYPE_STRAINGAGE,
    IOTYPE_THERMISTOR,
    IOTYPE_THERMOCOUPLE,
    IOTYPE_VOLTAGEIN,
    OL_BRIDGE_FULL,
    OL_BRIDGE_HALF,
    OL_BRIDGE_QUARTER,
    OL_COUPLING_AC,
    OL_COUPLING_DC,
    OL_DF_CONTINUOUS,
    OL_DF_CONTINUOUS_ABOUTTRIG,
    OL_DF_CONTINUOUS_PRETRIG,
    OL_DF_SINGLEVALUE,
    OL_ENC_2SCOMP,
    OL_EXCITATION_CURRENT_SRC_DISABLED,
    OL_EXCITATION_CURRENT_SRC_EXTERNAL,
    OL_EXCITATION_CURRENT_SRC_INTERNAL,
    OL_NOT_SUPPORTED,
    OL_RTD_TYPE_CUSTOM,
    OL_RTD_TYPE_PT3750,
    OL_RTD_TYPE_PT3850,
    OL_RTD_TYPE_PT3911,
    OL_RTD_TYPE_PT3916,
    OL_RTD_TYPE_PT3920,
    OL_RTD_TYPE_PT3928,
    OL_STRAIN_EXCITATION_VOLTAGE_SRC_EXTERNAL,
    OL_STRAIN_EXCITATION_VOLTAGE_SRC_INTERNAL,
    OL_STRAIN_FULL_BRIDGE_AXIAL,
    OL_STRAIN_FULL_BRIDGE_BENDING,
    OL_STRAIN_FULL_BRIDGE_BENDING_POISSON,
    OL_STRAIN_HALF_BRIDGE_BENDING,
    OL_STRAIN_HALF_BRIDGE_POISSON,
    OL_STRAIN_QUARTER_BRIDGE,
    OL_STRAIN_QUARTER_BRIDGE_TEMP_COMPENSATION,
    OLSS_AD,
    OLSS_CT,
    OLSS_DA,
    OLSS_DIN,
    OLSS_DOUT,
    OLSS_QUAD,
    OLSS_TACH,
)
from dtollib.capi.conversion import code_to_input_volts
from dtollib.errors import DtolError, DtolResourceError, ErrorContext
from dtollib.system.capabilities import CapabilitySet, query_capabilities
from dtollib.system.models import BoardInfo, SubsystemInfo
from dtollib.tasks.models import DataFlow, IOType, SubsystemState, SubsystemType

if TYPE_CHECKING:
    from collections.abc import Callable

    from dtollib.channels.analog_input import ThermocoupleType
    from dtollib.channels.base import ChannelSpec

__all__ = ["DataAcqBackend"]

_logger = get_logger("backend.dataacq")


SUBSYS_TYPE_TO_ENUM: dict[int, SubsystemType] = {
    OLSS_AD: SubsystemType.ANALOG_INPUT,
    OLSS_DA: SubsystemType.ANALOG_OUTPUT,
    OLSS_DIN: SubsystemType.DIGITAL_INPUT,
    OLSS_DOUT: SubsystemType.DIGITAL_OUTPUT,
    OLSS_CT: SubsystemType.COUNTER_TIMER,
    OLSS_QUAD: SubsystemType.QUADRATURE,
    OLSS_TACH: SubsystemType.TACHOMETER,
}


_INT16_SAMPLE_BYTES = 2


# Subsystem types we probe during ``enum_subsystems`` — we ignore
# ``OLSS_SRL`` (serial; legacy and unsupported by the libraries we
# care about) and any future types not in this list.
_PROBED_SUBSYSTEM_TYPES: tuple[int, ...] = (
    OLSS_AD,
    OLSS_DA,
    OLSS_DIN,
    OLSS_DOUT,
    OLSS_CT,
    OLSS_QUAD,
    OLSS_TACH,
)


class DataAcqBackend:
    """Real DataAcq SDK backend.

    Construction either accepts a pre-loaded
    :class:`~dtollib.capi.OpenLayersDlls` (dependency injection — used
    by tests) or calls :func:`~dtollib.capi.load_openlayers` itself.

    Thread safety: a single :class:`threading.RLock` guards every SDK
    call.  The lock is conservative — see docs/design.md §16.3.
    """

    def __init__(self, dlls: OpenLayersDlls | None = None) -> None:
        """Bind the SDK and prepare the session caches.

        Args:
            dlls: Pre-loaded SDK handle pair.  If ``None``,
                :func:`~dtollib.capi.load_openlayers` is called to
                resolve the default install paths.
        """
        self._dlls = dlls if dlls is not None else load_openlayers()
        self._api = OpenLayersApi(self._dlls)
        self._lock = threading.RLock()

        # HDRVR ref-counting: first ``initialize(name)`` opens the
        # device; subsequent calls reuse the existing handle.  Final
        # ``terminate(hdrvr)`` closes it.  Maps board name → (HDRVR, refcount).
        self._open_devices: dict[str, tuple[int, int]] = {}
        # Reverse lookup: HDRVR → board name (for terminate()).
        self._hdrvr_to_name: dict[int, str] = {}

        # Capability cache: one CapabilitySet per HDASS for the
        # lifetime of the held subsystem.
        self._capability_cache: dict[int, CapabilitySet] = {}

        # Input-scaling cache (vmin, vmax, resolution_bits, twos_complement)
        # per HDASS — populated lazily on the first ``code_to_volts`` after
        # commit, because ``olDaCodeToVolts`` is unusable on the DT9805/06
        # (ECODE=9) so we convert from the configured encoding ourselves.
        self._scaling_cache: dict[int, tuple[float, float, int, bool]] = {}

        # Last data-flow mode set per HDASS.  Single-value subsystems have no
        # ``olDaStart`` step (the SDK rejects it with ECODE=27 "Dataflow
        # mismatch"); ``start`` skips the call for them.
        self._dataflow_mode: dict[int, int] = {}

        # Notification-wrapper pinning to prevent GC collecting the
        # callback wrapper while the SDK still holds a pointer to it.
        self._notification_wrappers: dict[int, Any] = {}

    # ---- Version ---------------------------------------------------------

    def get_version(self) -> tuple[str, str]:
        """Return ``(oldaapi_version, olmem_version)``."""
        with self._lock:
            return (self._api.get_oldaapi_version(), self._api.get_olmem_version())

    # ---- Board enumeration -----------------------------------------------

    def enum_boards(self) -> list[BoardInfo]:
        """Enumerate every installed DT-Open Layers board.

        Uses ``olDaEnumBoardsEx`` for the registry-aware enumeration so
        the driver name and instance are populated.  Falls back to
        ``olDaEnumBoards`` + ``olDaGetBoardInfo`` if the Ex variant
        fails (older drivers).
        """
        with self._lock:
            try:
                rows = self._api.enum_boards_ex()
            except Exception:
                names = self._api.enum_boards()
                boards: list[BoardInfo] = []
                for name in names:
                    model, driver = self._api.get_board_info(name)
                    boards.append(
                        BoardInfo(
                            name=name,
                            model=model,
                            driver_name=driver,
                            instance=0,
                        )
                    )
                return boards

            boards = []
            for row in rows:
                model = ""
                try:
                    model, _driver_unused = self._api.get_board_info(row.name)
                except Exception:
                    model = ""
                boards.append(
                    BoardInfo(
                        name=row.name,
                        model=model or row.name,
                        driver_name=row.driver,
                        instance=row.instance,
                    )
                )
            return boards

    # ---- Device lifecycle ------------------------------------------------

    def initialize(self, board_name: str) -> int:
        """Open ``board_name``; return its HDRVR.  Ref-counted across sessions."""
        with self._lock:
            existing = self._open_devices.get(board_name)
            if existing is not None:
                hdrvr, refcount = existing
                self._open_devices[board_name] = (hdrvr, refcount + 1)
                return hdrvr

            hdrvr = self._api.initialize(board_name)
            self._open_devices[board_name] = (hdrvr, 1)
            self._hdrvr_to_name[hdrvr] = board_name
            return hdrvr

    def terminate(self, hdrvr: int) -> None:
        """Drop a reference to ``hdrvr``; close on final release."""
        with self._lock:
            name = self._hdrvr_to_name.get(hdrvr)
            if name is None:
                # Already terminated or never opened through this backend.
                # Closing an unknown HDRVR through the SDK would be a
                # bug; raise so the caller can investigate.
                self._api.terminate(hdrvr)
                return

            current_hdrvr, refcount = self._open_devices[name]
            if current_hdrvr != hdrvr:
                # Bookkeeping invariant: every entry in
                # ``_hdrvr_to_name`` matches the HDRVR stored in
                # ``_open_devices``.  A mismatch indicates the caller
                # is closing a stale or fabricated HDRVR.
                raise DtolResourceError(
                    f"HDRVR {hdrvr} does not match the open handle for {name!r}",
                    context=ErrorContext(operation="terminate", board=name),
                )
            if refcount > 1:
                self._open_devices[name] = (hdrvr, refcount - 1)
                return

            self._api.terminate(hdrvr)
            del self._open_devices[name]
            del self._hdrvr_to_name[hdrvr]

    # ---- Subsystem reservation -------------------------------------------

    def enum_subsystems(self, board_name: str) -> list[SubsystemInfo]:
        """Enumerate subsystems on ``board_name``.

        Implementation note: ``olDaEnumSubSystems`` enumerates raw
        HDASS handles, but the SDK's preferred discovery shape is
        "try to acquire each (subsys_type, element) pair until one
        fails".  We do the latter — it's portable across SDK
        revisions and surfaces the element index for each subsystem
        without an extra round-trip.
        """
        with self._lock:
            hdrvr = self.initialize(board_name)
            try:
                subs: list[SubsystemInfo] = []
                for subsys_type in _PROBED_SUBSYSTEM_TYPES:
                    typed = SUBSYS_TYPE_TO_ENUM[subsys_type]
                    for element in range(_MAX_ELEMENT_PROBE):
                        hdass: int
                        try:
                            hdass = self._api.get_dass(hdrvr, subsys_type, element)
                        except Exception:
                            break
                        try:
                            caps = query_capabilities(self._api, hdass)
                            subs.append(
                                SubsystemInfo(
                                    type=typed,
                                    element=element,
                                    num_channels=caps.num_channels,
                                    supports_singlevalue=caps.supports_singlevalue,
                                    supports_continuous=caps.supports_continuous,
                                    supports_simultaneous_sh=caps.supports_simultaneous_sh,
                                    supports_multisensor=caps.supports_multisensor,
                                    supports_dma=caps.supports_dma,
                                    returns_floats=caps.returns_floats,
                                    max_throughput_hz=caps.max_throughput_hz,
                                    cgl_depth=caps.cgl_depth,
                                )
                            )
                        finally:
                            self._api.release_dass(hdass)
                return subs
            finally:
                self.terminate(hdrvr)

    def get_dass(self, hdrvr: int, subsystem_type: int, element: int) -> int:
        """Reserve a subsystem; return its HDASS."""
        with self._lock:
            return self._api.get_dass(hdrvr, subsystem_type, element)

    def release_dass(self, hdass: int) -> None:
        """Release ``hdass`` and drop its capability + scaling cache entries."""
        with self._lock:
            self._capability_cache.pop(hdass, None)
            self._scaling_cache.pop(hdass, None)
            self._dataflow_mode.pop(hdass, None)
            self._api.release_dass(hdass)

    # ---- Capability query ------------------------------------------------

    def query_capabilities(self, hdass: int) -> CapabilitySet:
        """Build a :class:`CapabilitySet` for ``hdass`` (cached)."""
        with self._lock:
            cached = self._capability_cache.get(hdass)
            if cached is not None:
                return cached
            caps = query_capabilities(self._api, hdass)
            self._capability_cache[hdass] = caps
            return caps

    # ---- Configuration setters ----------------------------------

    def set_data_flow(self, hdass: int, mode: int) -> None:
        """Set data-flow mode via ``olDaSetDataFlow``."""
        with self._lock:
            self._api.set_data_flow(hdass, mode)
            self._dataflow_mode[hdass] = mode

    def set_multi_sensor_type(
        self,
        hdass: int,
        physical_channel: int,
        io_type: IOType,
    ) -> None:
        """Re-type a MULTI_SENSOR channel.  Caller orders this BEFORE per-type setters."""
        with self._lock:
            self._api.set_multi_sensor_type(
                hdass, physical_channel, _IO_TYPE_TO_OLSS_MULTI_SENSOR[io_type]
            )

    def add_channel(
        self,
        hdass: int,
        list_index: int,
        spec: ChannelSpec,
    ) -> None:
        """Add a channel to the channel/gain list with all per-type setters.

        Caller MUST have already called :meth:`set_multi_sensor_type`
        on this channel if it reports ``IOType.MULTI_SENSOR``
        (docs/design.md §8.5a).
        """
        # Lazy import — avoids circular import via channels → tasks.
        from dtollib.channels.analog_input import (  # noqa: PLC0415
            AnalogInputBase,
            AnalogInputVoltage,
            CurrentInput,
            ThermocoupleInput,
        )
        from dtollib.channels.analog_output import AnalogOutputVoltage  # noqa: PLC0415

        with self._lock:
            if isinstance(spec, AnalogInputBase):
                self._api.set_channel_type(hdass, _CHANNEL_TYPE_TO_OL(spec.channel_type))
            if isinstance(spec, AnalogInputVoltage | AnalogOutputVoltage | CurrentInput):
                self._set_voltage_range(hdass, spec.physical_channel, spec.min_val, spec.max_val)
            # Per-channel coupling (AC for IEPE, optional elsewhere). Tolerated
            # because the AD subsystem on the DT9805/06 rejects it (ec=36).
            if isinstance(spec, AnalogInputBase) and spec.coupling is not None:
                self._tolerate_unsupported(
                    lambda: self._api.set_coupling_type(
                        hdass,
                        spec.physical_channel,
                        _COUPLING_TO_OL[spec.coupling.value],  # type: ignore[union-attr]
                    )
                )
            self._configure_multi_sensor(hdass, spec)
            if isinstance(spec, ThermocoupleInput):
                caps = self._capability_cache.get(hdass)
                if caps is not None and caps.returns_floats:
                    # Firmware-linearising subsystem: tell the SDK the TC type
                    # so olDaGetSingleFloat returns temperature directly.
                    self._api.set_thermocouple_type(
                        hdass,
                        spec.physical_channel,
                        _TC_TYPE_TO_OL[spec.thermocouple_type.value],
                    )
                # Application-linearised path (DT9805/06): the SDK has no usable
                # TC setter — olDaSetThermocoupleType returns OLNOTSUPPORTED
                # (ec=36) — so we skip it and linearise in the read path from
                # the differential emf + CJC channel. See docs/decisions.md.
            # Gain-list entry applies to analog channels.  AI uses it to
            # select the per-channel range (DT9805/06 gain-select their fixed
            # ±10 V range).  A voltage DAC may have no programmable gain — the
            # DT9806 DA subsystem rejects olDaSetGainListEntry with
            # OLNOTSUPPORTED (ec=36, bench-confirmed 2026-05-28); tolerate that
            # for output channels, the single-value put writes the code
            # directly.  Digital lines have no gain list at all.
            if isinstance(spec, AnalogInputBase):
                self._api.set_gain_list_entry(hdass, list_index, spec.physical_channel, spec.gain)
            elif isinstance(spec, AnalogOutputVoltage):
                try:
                    self._api.set_gain_list_entry(
                        hdass, list_index, spec.physical_channel, spec.gain
                    )
                except DtolError as exc:
                    if exc.context.ecode != OL_NOT_SUPPORTED:
                        raise

    @staticmethod
    def _tolerate_unsupported(fn: Callable[[], None]) -> None:
        """Run ``fn``; swallow only ``OLNOTSUPPORTED`` (ECODE 36).

        Multi-sensor setters return ec=36 on subsystems that don't support
        the feature.  The :class:`~dtollib.tasks.TaskBuilder` capability gate
        (``_require_io_type_supported``) already blocks owned DT9805/06 from
        reaching here, so a residual ec=36 means a partial-support edge case
        — tolerate it rather than crash the whole configure.  Any other SDK
        error still propagates.
        """
        try:
            fn()
        except DtolError as exc:
            if exc.context.ecode != OL_NOT_SUPPORTED:
                raise

    def _configure_multi_sensor(self, hdass: int, spec: ChannelSpec) -> None:
        """Issue the per-type setters for a multi-sensor channel.

        Caller holds ``self._lock`` and has already run
        :meth:`set_multi_sensor_type` (the MULTI_SENSOR re-typing that MUST
        precede every per-type setter, docs/design.md §8.5a).  Each setter
        is wrapped in :meth:`_tolerate_unsupported`.
        """
        from dtollib.channels.analog_input import (  # noqa: PLC0415
            BridgeInput,
            IepeInput,
            ResistanceInput,
            RtdInput,
            StrainInput,
            ThermistorInput,
        )

        ch = spec.physical_channel
        if isinstance(spec, RtdInput):
            self._tolerate_unsupported(
                lambda: self._api.set_rtd_type(hdass, ch, _RTD_TYPE_TO_OL[spec.rtd_type.value])
            )
            self._tolerate_unsupported(lambda: self._api.set_rtd_r0(hdass, ch, spec.r0))
            rtd_a, rtd_b, rtd_c = spec.a, spec.b, spec.c
            if rtd_a is not None:
                self._tolerate_unsupported(lambda: self._api.set_rtd_a(hdass, ch, rtd_a))
            if rtd_b is not None:
                self._tolerate_unsupported(lambda: self._api.set_rtd_b(hdass, ch, rtd_b))
            if rtd_c is not None:
                self._tolerate_unsupported(lambda: self._api.set_rtd_c(hdass, ch, rtd_c))
            self._apply_excitation(
                hdass, ch, spec.excitation_source.value, spec.excitation_current_a
            )
        elif isinstance(spec, ThermistorInput):
            self._tolerate_unsupported(lambda: self._api.set_thermistor_a(hdass, ch, spec.a))
            self._tolerate_unsupported(lambda: self._api.set_thermistor_b(hdass, ch, spec.b))
            self._tolerate_unsupported(lambda: self._api.set_thermistor_c(hdass, ch, spec.c))
            self._apply_excitation(
                hdass, ch, spec.excitation_source.value, spec.excitation_current_a
            )
        elif isinstance(spec, ResistanceInput | IepeInput):
            # Both are configured purely by the excitation-current source/value
            # (IEPE's AC coupling is applied generically in add_channel).
            self._apply_excitation(
                hdass, ch, spec.excitation_source.value, spec.excitation_current_a
            )
        elif isinstance(spec, StrainInput):
            self._tolerate_unsupported(
                lambda: self._api.set_strain_bridge_configuration(
                    hdass, ch, _STRAIN_CONFIG_TO_OL[spec.configuration.value]
                )
            )
            self._apply_strain_excitation(
                hdass, spec.excitation_source.value, spec.excitation_voltage
            )
            self._tolerate_unsupported(
                lambda: self._api.set_strain_shunt_resistor(hdass, ch, spec.shunt_enabled)
            )
        elif isinstance(spec, BridgeInput):
            self._tolerate_unsupported(
                lambda: self._api.set_bridge_configuration(
                    hdass, ch, _BRIDGE_CONFIG_TO_OL[spec.configuration.value]
                )
            )
            self._apply_strain_excitation(
                hdass, spec.excitation_source.value, spec.excitation_voltage
            )
            self._tolerate_unsupported(
                lambda: self._api.set_strain_shunt_resistor(hdass, ch, spec.shunt_enabled)
            )

    def _apply_excitation(
        self,
        hdass: int,
        channel: int,
        source_value: str,
        current_a: float | None,
    ) -> None:
        """Set excitation-current source (+ value) for an RTD/thermistor/IEPE channel."""
        self._tolerate_unsupported(
            lambda: self._api.set_excitation_current_source(
                hdass, channel, _EXCITATION_SRC_TO_OL[source_value]
            )
        )
        if current_a is not None:
            self._tolerate_unsupported(
                lambda: self._api.set_excitation_current_value(hdass, channel, current_a)
            )

    def _apply_strain_excitation(self, hdass: int, source_value: str, volts: float) -> None:
        """Set the subsystem-wide strain/bridge excitation source + voltage."""
        self._tolerate_unsupported(
            lambda: self._api.set_strain_excitation_voltage_source(
                hdass, _STRAIN_EXCITATION_SRC_TO_OL[source_value]
            )
        )
        self._tolerate_unsupported(lambda: self._api.set_strain_excitation_voltage(hdass, volts))

    def _set_voltage_range(
        self,
        hdass: int,
        channel: int,
        min_val: float,
        max_val: float,
    ) -> None:
        """Configure a voltage channel's input range, adapting to the board.

        ``olDaSetChannelRange`` (per-channel programmable range) is the
        preferred call, but the DT9805/06 A/D has a single fixed range
        (±10 V native, gain-selected per channel) and rejects it with
        ``OLNOTSUPPORTED`` (ECODE 36). On that error fall back to the
        subsystem-wide ``olDaSetRange``; if the board can't honour the
        requested span either, leave the native range in place — the
        per-channel gain (set via ``set_gain_list_entry``) selects the
        effective range. Bench-confirmed on DT9806 SDK V7.0.0.7,
        2026-05-28; see docs/decisions.md.
        """
        try:
            self._api.set_channel_range(hdass, channel, min_val, max_val)
            return
        except DtolError as exc:
            if exc.context.ecode != OL_NOT_SUPPORTED:
                raise
        # Per-channel range unsupported — try the subsystem-wide range.
        try:
            self._api.set_range(hdass, max_val, min_val)
        except DtolError as exc:
            if exc.context.ecode != OL_NOT_SUPPORTED:
                raise
            _logger.debug(
                "hdass=%s ch=%s: neither per-channel nor subsystem range "
                "honoured for [%s, %s] V; using native range (gain-selected)",
                hdass,
                channel,
                min_val,
                max_val,
            )

    def set_stop_on_error(self, hdass: int, stop: bool) -> None:
        """``olDaSetStopOnError`` — SDK-level error policy."""
        with self._lock:
            self._api.set_stop_on_error(hdass, stop)

    def commit(self, hdass: int) -> None:
        """``olDaConfig`` — first config (after channel/clock/wrap setup).

        For single-value tasks this is the only config. For continuous
        tasks it is config #1; :meth:`arm` runs config #2 after the
        window handle is wired and buffers are queued (docs/decisions.md,
        "Bench-verified continuous-mode setup").
        """
        with self._lock:
            self._api.config(hdass)

    def arm(self, hdass: int) -> None:
        """``olDaConfig`` — second config, after ``olDaSetWndHandle`` + queue.

        Continuous mode only. The second ``olDaConfig`` wires the message
        window into the SDK's buffer-rotation state machine; without it the
        SDK never posts ``OLDA_WM_BUFFER_DONE`` on the DT9805/06.
        """
        with self._lock:
            self._api.config(hdass)

    def set_thermocouple_type(
        self,
        hdass: int,
        channel: int,
        tc_type: ThermocoupleType,
    ) -> None:
        """Set TC type via ``olDaSetThermocoupleType``."""
        with self._lock:
            self._api.set_thermocouple_type(hdass, channel, _TC_TYPE_TO_OL[tc_type.value])

    def set_return_cjc_in_stream(self, hdass: int, enable: bool) -> None:
        """``olDaSetReturnCjcTemperatureInStream`` — interleaved-CJC streaming."""
        with self._lock:
            self._api.set_return_cjc_in_stream(hdass, enable)

    # ---- Volts → engineering conversions (§8.B6) --------

    def volts_to_strain(
        self,
        config: int,
        v_unstrained: float,
        v_strained: float,
        v_excitation: float,
        gage_factor: float,
        gage_resistance: float,
        lead_resistance: float,
        poisson_ratio: float,
        shunt_correction: float,
    ) -> float:
        """``olDaVoltsToStrain`` passthrough (pure SDK conversion, no lock needed)."""
        return self._api.volts_to_strain(
            config,
            v_unstrained,
            v_strained,
            v_excitation,
            gage_factor,
            gage_resistance,
            lead_resistance,
            poisson_ratio,
            shunt_correction,
        )

    def volts_to_bridge_based_sensor(
        self,
        v_unstrained: float,
        v_strained: float,
        v_excitation: float,
        temperature_coefficient: float,
        gage_resistance: float,
        lead_resistance: float,
        rated_output_mv_per_v: float,
        shunt_correction: float,
    ) -> float:
        """``olDaVoltsToBridgeBasedSensor`` passthrough (pure SDK conversion)."""
        return self._api.volts_to_bridge_based_sensor(
            v_unstrained,
            v_strained,
            v_excitation,
            temperature_coefficient,
            gage_resistance,
            lead_resistance,
            rated_output_mv_per_v,
            shunt_correction,
        )

    # ---- TEDS readers (§8.B5) ---------------------------

    def read_strain_gage_hardware_teds(self, hdass: int, channel: int) -> dict[str, object]:
        """``olDaReadStrainGageHardwareTeds`` passthrough."""
        with self._lock:
            return self._api.read_strain_gage_hardware_teds(hdass, channel)

    def read_strain_gage_virtual_teds(self, path: str) -> dict[str, object]:
        """``olDaReadStrainGageVirtualTeds`` passthrough."""
        with self._lock:
            return self._api.read_strain_gage_virtual_teds(path)

    def read_bridge_sensor_hardware_teds(self, hdass: int, channel: int) -> dict[str, object]:
        """``olDaReadBridgeSensorHardwareTeds`` passthrough."""
        with self._lock:
            return self._api.read_bridge_sensor_hardware_teds(hdass, channel)

    def read_bridge_sensor_virtual_teds(self, path: str) -> dict[str, object]:
        """``olDaReadBridgeSensorVirtualTeds`` passthrough."""
        with self._lock:
            return self._api.read_bridge_sensor_virtual_teds(path)

    # ---- Lifecycle ----------------------------------------------

    def start(self, hdass: int) -> None:
        """``olDaStart`` — skipped for single-value subsystems.

        Single-value mode has no run state: after ``olDaConfig`` the
        subsystem is ready for ``olDaGetSingleValue`` and ``olDaStart``
        returns ECODE=27 ("Dataflow mismatch"). The session calls ``start``
        unconditionally as a convention, so we make it a no-op there.
        """
        with self._lock:
            if self._dataflow_mode.get(hdass) == OL_DF_SINGLEVALUE:
                return
            self._api.start(hdass)

    def stop(self, hdass: int) -> None:
        """``olDaStop`` (orderly)."""
        with self._lock:
            self._api.stop(hdass)

    def abort(self, hdass: int) -> None:
        """``olDaAbort`` (immediate)."""
        with self._lock:
            self._api.abort(hdass)

    def get_state(self, hdass: int) -> SubsystemState:
        """``olDaGetSSState`` → :class:`SubsystemState`."""
        with self._lock:
            raw = self._api.get_ss_state(hdass)
        return _OLSSC_STATE_TO_ENUM.get(raw, SubsystemState.INITIALIZED)

    def is_running(self, hdass: int) -> bool:
        """``olDaIsRunning``."""
        with self._lock:
            return self._api.is_running(hdass)

    # ---- Single-value reads -------------------------------------

    def get_single_value(self, hdass: int, channel: int, gain: float) -> int:
        """``olDaGetSingleValue`` — raw code."""
        with self._lock:
            return self._api.get_single_value(hdass, channel, gain)

    def get_single_float(self, hdass: int, channel: int, gain: float) -> float:
        """``olDaGetSingleFloat`` — engineering units."""
        with self._lock:
            return self._api.get_single_float(hdass, channel, gain)

    def get_single_values(self, hdass: int, gain: float) -> list[int]:
        """``olDaGetSingleValues`` — simultaneous SH; requires cached n_channels."""
        # Derive channel count from the capability cache — populated at session
        # construction.  Without it we'd have to query the SDK on every poll.
        caps = self._capability_cache.get(hdass)
        n = caps.num_channels if caps is not None else 0
        with self._lock:
            return self._api.get_single_values(hdass, n, gain)

    def get_single_floats(self, hdass: int, gain: float) -> list[float]:
        """``olDaGetSingleFloats`` — simultaneous SH + engineering units."""
        caps = self._capability_cache.get(hdass)
        n = caps.num_channels if caps is not None else 0
        with self._lock:
            return self._api.get_single_floats(hdass, n, gain)

    def get_cjc_temperature(self, hdass: int, channel: int) -> float:
        """``olDaGetCjcTemperature``."""
        with self._lock:
            return self._api.get_cjc_temperature(hdass, channel)

    def get_scaling(self, hdass: int) -> tuple[float, float, int, bool]:
        """Return ``(vmin, vmax, resolution_bits, twos_complement)`` for ``hdass``.

        Queries the subsystem range / resolution / encoding once (after
        ``olDaConfig``) and caches it. Exposed so the continuous block path
        (:func:`dtollib.streaming.record`) can build a
        :class:`~dtollib.capi.conversion.BlockConversion` plan with the same
        scaling the single-value ``code_to_volts`` path uses.
        """
        with self._lock:
            scaling = self._scaling_cache.get(hdass)
            if scaling is None:
                vmax, vmin = self._api.get_range(hdass)
                resolution = self._api.get_resolution(hdass)
                twos_complement = self._api.get_encoding(hdass) == OL_ENC_2SCOMP
                scaling = (vmin, vmax, resolution, twos_complement)
                self._scaling_cache[hdass] = scaling
        return scaling

    def code_to_volts(self, hdass: int, code: int, gain: float) -> float:
        """Convert a raw code to input volts from the configured scaling.

        ``olDaCodeToVolts`` returns ECODE=9 ("Invalid Encoding") on the
        DT9805/DT9806 A/D (bench-verified 2026-05-28; docs/decisions.md),
        so we query the subsystem encoding / resolution / range once
        (cached after commit) and convert ourselves via
        :func:`~dtollib.capi.conversion.code_to_input_volts`.
        """
        vmin, vmax, resolution, twos_complement = self.get_scaling(hdass)
        # Pure conversion — no SDK call, safe outside the lock.
        return code_to_input_volts(
            code,
            gain,
            vmin=vmin,
            vmax=vmax,
            resolution_bits=resolution,
            twos_complement=twos_complement,
        )

    # ---- Continuous-mode configuration --------------------------

    def set_channel_list(self, hdass: int, channels: list[int]) -> None:
        """Set list size + each entry via two SDK calls (drainer-thread safe)."""
        with self._lock:
            self._api.set_channel_list_size(hdass, len(channels))
            for list_index, channel in enumerate(channels):
                self._api.set_channel_list_entry(hdass, list_index, channel)

    def set_clock(
        self,
        hdass: int,
        *,
        rate_hz: float,
        clock_source: int,
        external_divider: int | None = None,
    ) -> None:
        """Configure clock source + frequency (+ divider when external)."""
        with self._lock:
            self._api.set_clock_source(hdass, clock_source)
            self._api.set_clock_frequency(hdass, rate_hz)
            if external_divider is not None:
                self._api.set_external_clock_divider(hdass, external_divider)

    def get_clock_frequency(self, hdass: int) -> float:
        """``olDaGetClockFrequency``."""
        with self._lock:
            return self._api.get_clock_frequency(hdass)

    def set_trigger(
        self,
        hdass: int,
        *,
        kind: int,
        threshold_channel: int | None = None,
        threshold_level: float | None = None,
    ) -> None:
        """Configure start-trigger + analog-threshold parameters when given."""
        with self._lock:
            self._api.set_trigger(hdass, kind)
            if threshold_channel is not None:
                self._api.set_trigger_threshold_channel(hdass, threshold_channel)
            if threshold_level is not None:
                self._api.set_trigger_threshold_level(hdass, threshold_level)

    def set_wrap_mode(self, hdass: int, mode: int) -> None:
        """``olDaSetWrapMode``."""
        with self._lock:
            self._api.set_wrap_mode(hdass, mode)

    def set_dma_usage(self, hdass: int, n_channels: int) -> None:
        """``olDaSetDmaUsage``."""
        with self._lock:
            self._api.set_dma_usage(hdass, n_channels)

    # ---- Notification + runtime ---------------------------------

    def register_notification(
        self,
        hdass: int,
        callback: Callable[[int, int, int], int],
    ) -> object:
        """Route SDK buffer-done events for ``hdass`` to ``callback``.

        Creates a hidden message-only window on a dedicated pump thread,
        calls ``olDaSetWndHandle`` to point the SDK at it, and pins the
        :class:`~dtollib.backend._message_window.MessageWindow` on
        ``self._notification_wrappers[id(hdass)]`` so neither the window,
        the pump thread, nor the WNDPROC is collected while the SDK holds
        the handle.

        ``callback(msg_id, wparam, lparam)`` runs on the pump thread for
        each ``OLDA_WM_*`` message — the window-handle mechanism is the
        only one that delivers events on the DT9805/06 (docs/decisions.md).

        Returns the :class:`MessageWindow` as the opaque handle for
        :meth:`unregister_notification`.
        """
        window = MessageWindow(callback)
        with self._lock:
            try:
                self._api.set_wnd_handle(hdass, window.hwnd, 0)
            except BaseException:
                window.close()
                raise
            self._notification_wrappers[id(hdass)] = window
            return window

    def unregister_notification(self, hdass: int, handle: object) -> None:
        """Detach the window handle, stop the pump, and drop the window."""
        del handle  # the strong ref is on us, not the caller
        with self._lock:
            with contextlib.suppress(Exception):
                self._api.set_wnd_handle(hdass, 0, 0)
            window = self._notification_wrappers.pop(id(hdass), None)
        # Join the pump thread outside the backend lock — close() blocks on
        # the thread, which must not contend with other backend calls.
        if isinstance(window, MessageWindow):
            window.close()

    def get_queue_size(self, hdass: int, queue: int) -> int:
        """``olDaGetQueueSize``."""
        with self._lock:
            return self._api.get_queue_size(hdass, queue)

    # ---- Buffer pool primitives ---------------------------------

    def alloc_buffer(
        self,
        n_samples: int,
        sample_dtype_bytes: int,
        *,
        zero_init: bool = True,
    ) -> int:
        """``olDmCallocBuffer`` / ``olDmMallocBuffer``."""
        with self._lock:
            return self._api.alloc_buffer(
                n_samples,
                sample_dtype_bytes,
                zero_init=zero_init,
            )

    def free_buffer(self, hbuf: int) -> None:
        """``olDmFreeBuffer``."""
        with self._lock:
            self._api.free_buffer(hbuf)

    def put_buffer(self, hdass: int, hbuf: int) -> None:
        """``olDaPutBuffer``."""
        with self._lock:
            self._api.put_buffer(hdass, hbuf)

    def get_buffer(self, hdass: int) -> int | None:
        """``olDaGetBuffer``."""
        with self._lock:
            return self._api.get_buffer(hdass)

    def flush_buffers(self, hdass: int) -> None:
        """``olDaFlushBuffers``."""
        with self._lock:
            self._api.flush_buffers(hdass)

    def read_buffer_payload(self, hbuf: int) -> Any:
        """Build an ndarray view over the HBUF payload — drainer-thread call.

        Returns a NumPy view; the caller copies before requeueing the HBUF.
        Dtype is inferred from ``olDmGetDataWidth`` (2 bytes → int16, 4
        bytes → int32).
        """
        import numpy as np  # noqa: PLC0415

        with self._lock:
            valid = self._api.get_buffer_valid_samples(hbuf)
            width_bytes = self._api.get_buffer_data_width(hbuf)
            ptr = self._api.get_buffer_ptr(hbuf)
        if not ptr or valid == 0:
            return np.zeros(0, dtype=np.int16)
        dtype = np.int16 if width_bytes == _INT16_SAMPLE_BYTES else np.int32
        nbytes = valid * width_bytes
        buf = (ctypes.c_char * nbytes).from_address(ptr)
        return np.frombuffer(buf, dtype=dtype, count=valid)

    def get_buffer_valid_samples(self, hbuf: int) -> int:
        """``olDmGetValidSamples``."""
        with self._lock:
            return self._api.get_buffer_valid_samples(hbuf)

    def copy_inprocess_buffer(
        self,
        hbuf: int,
        n_samples: int,
        sample_dtype_bytes: int,
    ) -> bytes:
        """``olDmCopyFromBuffer`` — copy the in-process HBUF without waiting."""
        with self._lock:
            return self._api.copy_from_buffer(hbuf, n_samples, sample_dtype_bytes)

    # ---- Output writes ------------------------------------------

    def put_single_value(self, hdass: int, channel: int, value: int, gain: float) -> None:
        """One-shot raw-code write via ``olDaPutSingleValue``."""
        with self._lock:
            self._api.put_single_value(hdass, channel, value, gain)

    def put_single_values(self, hdass: int, values: list[int], gain: float) -> None:
        """Simultaneous raw-code write via ``olDaPutSingleValues``."""
        with self._lock:
            self._api.put_single_values(hdass, values, gain)

    def set_synchronous_digital_io_usage(self, hdass: int, use: bool) -> None:
        """``olDaSetSynchronousDigitalIOUsage``."""
        with self._lock:
            self._api.set_synchronous_digital_io_usage(hdass, use)

    def set_digital_io_list_entry(self, hdass: int, entry: int, value: int) -> None:
        """``olDaSetDigitalIOListEntry``."""
        with self._lock:
            self._api.set_digital_io_list_entry(hdass, entry, value)

    def mute(self, hdass: int) -> None:
        """``olDaMute`` — hold the D/A output at its current value."""
        with self._lock:
            self._api.mute(hdass)

    def unmute(self, hdass: int) -> None:
        """``olDaUnMute`` — release a muted D/A output."""
        with self._lock:
            self._api.unmute(hdass)

    def copy_to_buffer(self, hbuf: int, data: bytes, n_samples: int) -> None:
        """``olDmCopyToBuffer`` — fill an HBUF from a host byte payload."""
        with self._lock:
            self._api.copy_to_buffer(hbuf, data, n_samples)

    def copy_buffer(self, hbuf: int, n_samples: int, sample_dtype_bytes: int) -> bytes:
        """``olDmCopyBuffer`` — copy an HBUF's valid samples to a host buffer."""
        with self._lock:
            return self._api.copy_buffer(hbuf, n_samples, sample_dtype_bytes)

    # ---- Counter/timer configuration ----------------------------

    def set_ct_mode(self, hdass: int, mode: int) -> None:
        """``olDaSetCTMode``."""
        with self._lock:
            self._api.set_ct_mode(hdass, mode)

    def set_ct_clock(self, hdass: int, *, rate_hz: float, clock_source: int) -> None:
        """``olDaSetCTClockSource`` + ``olDaSetCTClockFrequency``."""
        with self._lock:
            self._api.set_ct_clock_source(hdass, clock_source)
            self._api.set_ct_clock_frequency(hdass, rate_hz)

    def set_gate_type(self, hdass: int, gate: int) -> None:
        """``olDaSetGateType``."""
        with self._lock:
            self._api.set_gate_type(hdass, gate)

    def set_pulse(self, hdass: int, *, pulse_type: int, duty_or_width: float) -> None:
        """``olDaSetPulseType`` + ``olDaSetPulseWidth``."""
        with self._lock:
            self._api.set_pulse_type(hdass, pulse_type)
            self._api.set_pulse_width(hdass, duty_or_width)

    def set_measure_edges(self, hdass: int, *, start_edge: int, stop_edge: int) -> None:
        """``olDaSetMeasureStartEdge`` + ``olDaSetMeasureStopEdge``."""
        with self._lock:
            self._api.set_measure_start_edge(hdass, start_edge)
            self._api.set_measure_stop_edge(hdass, stop_edge)

    def set_cascade_mode(self, hdass: int, cascade: bool) -> None:
        """``olDaSetCascadeMode``."""
        with self._lock:
            self._api.set_cascade_mode(hdass, cascade)

    # ---- Counter/timer read -------------------------------------

    def read_events(self, hdass: int, channel: int) -> int:
        """``olDaReadEvents`` — current counter value."""
        with self._lock:
            return self._api.read_events(hdass, channel)

    def measure_frequency(self, hdass: int, channel: int) -> float:
        """``olDaMeasureFrequency`` — measured input frequency (Hz)."""
        with self._lock:
            return self._api.measure_frequency(hdass, channel)

    # ---- Triggered-scan retrigger -------------------------------

    def set_triggered_scan(
        self,
        hdass: int,
        *,
        multiscan_count: int,
        retrigger_mode: int,
        frequency_hz: float | None = None,
        source: int | None = None,
    ) -> None:
        """Enable triggered scan + configure the retrigger mode."""
        with self._lock:
            self._api.set_triggered_scan_usage(hdass, True)
            self._api.set_multiscan_count(hdass, multiscan_count)
            self._api.set_retrigger_mode(hdass, retrigger_mode)
            if frequency_hz is not None:
                self._api.set_retrigger_frequency(hdass, frequency_hz)
            if source is not None:
                self._api.set_retrigger(hdass, source)

    # ---- Simultaneous start (HSSLIST) ---------------------------

    def get_ss_list(self, hdrvr: int) -> int:
        """``olDaGetSSList``."""
        with self._lock:
            return self._api.get_ss_list(hdrvr)

    def put_dass_to_ss_list(self, hsslist: int, hdass: int) -> None:
        """``olDaPutDassToSSList``."""
        with self._lock:
            self._api.put_dass_to_ss_list(hsslist, hdass)

    def simultaneous_pre_start(self, hsslist: int) -> None:
        """``olDaSimultaneousPreStart``."""
        with self._lock:
            self._api.simultaneous_pre_start(hsslist)

    def simultaneous_start(self, hsslist: int) -> None:
        """``olDaSimultaneousStart``."""
        with self._lock:
            self._api.simultaneous_start(hsslist)

    def release_ss_list(self, hsslist: int) -> None:
        """``olDaReleaseSSList``."""
        with self._lock:
            self._api.release_ss_list(hsslist)

    # ---- Escape hatches --------------------------------------------------

    @property
    def api(self) -> OpenLayersApi:
        """Underlying :class:`OpenLayersApi` (escape hatch)."""
        return self._api

    @property
    def dlls(self) -> OpenLayersDlls:
        """Underlying :class:`OpenLayersDlls` (escape hatch)."""
        return self._dlls


# Upper bound on the per-subsystem-type element index we probe in
# :meth:`DataAcqBackend.enum_subsystems`.  No published DT-Open
# Layers device exposes more than a handful of subsystems of the same
# type; 16 is generous and avoids unbounded probing.
_MAX_ELEMENT_PROBE: int = 16


# ---------------------------------------------------------------------------
# Lookup tables — Python enum → SDK integer constant
# ---------------------------------------------------------------------------


# DataFlow → OL_DF_* (used by DtolSession.prepare to dispatch via SDK).
DATA_FLOW_TO_OL: dict[DataFlow, int] = {
    DataFlow.SINGLE_VALUE: OL_DF_SINGLEVALUE,
    DataFlow.CONTINUOUS: OL_DF_CONTINUOUS,
    # FINITE rides on CONTINUOUS + WrapMode.NONE; same underlying SDK mode.
    DataFlow.FINITE: OL_DF_CONTINUOUS,
    DataFlow.CONTINUOUS_PRETRIGGER: OL_DF_CONTINUOUS_PRETRIG,
    DataFlow.CONTINUOUS_ABOUT_TRIGGER: OL_DF_CONTINUOUS_ABOUTTRIG,
}


# ChannelType → SDK channel-type integer.  Values are the OL_CHNT_*
# constants from OLDADEFS.H (bench-verified V7.0.0.7, 2026-05-28):
# OL_CHNT_SINGLEENDED=100, OL_CHNT_DIFFERENTIAL=101.  The earlier 0/1/2
# values were wrong — olDaSetChannelType(101) is required for thermocouple
# reads (ECODE=8 "Invalid Channel Type" with the old value).  The keys are
# ``ChannelType.value`` strings so this dict can be populated at module load
# without importing the enum class (avoids the circular import:
# dtollib.channels.analog_input ↔ dtollib.backend.dataacq).  This SDK build
# has no pseudo-differential channel-type constant — see docs/decisions.md.
_CHANNEL_TYPE_VALUE_TO_OL: dict[str, int] = {
    "single_ended": 100,
    "differential": 101,
}


def _CHANNEL_TYPE_TO_OL(channel_type: Any) -> int:  # noqa: N802
    """Dispatch ``ChannelType`` → SDK integer.

    Accepts either the enum member or its ``.value`` string so the
    dispatch works without an import of :class:`ChannelType` at this
    module's load time.
    """
    key = getattr(channel_type, "value", channel_type)
    try:
        return _CHANNEL_TYPE_VALUE_TO_OL[str(key)]
    except KeyError as exc:
        raise DtolResourceError(
            f"ChannelType {key!r} is not supported by this SDK build "
            "(only single-ended and differential have OL_CHNT_* constants "
            "in OLDADEFS.H; pseudo-differential is wiring-only).",
            context=ErrorContext(operation="_CHANNEL_TYPE_TO_OL"),
        ) from exc


# IOType → SDK MULTI_SENSOR re-typing constant (third arg to
# ``olDaSetMultiSensorType``).  Values are the verified ``IO_TYPE`` enum
# ordinals from OLDADEFS.H:576 (= ``capi.constants.IOTYPE_*``), confirmed
# 2026-05-28.  The pre-2026-05-28 placeholders (0–8) were wrong; see
# docs/decisions.md.  The lookup keys on the typed enum so any future fix
# touches only this table.
_IO_TYPE_TO_OLSS_MULTI_SENSOR: dict[IOType, int] = {
    IOType.VOLTAGE_IN: IOTYPE_VOLTAGEIN,
    IOType.THERMOCOUPLE: IOTYPE_THERMOCOUPLE,
    IOType.RTD: IOTYPE_RTD,
    IOType.THERMISTOR: IOTYPE_THERMISTOR,
    IOType.RESISTANCE: IOTYPE_RESISTANCE,
    IOType.STRAIN_GAGE: IOTYPE_STRAINGAGE,
    IOType.BRIDGE: IOTYPE_BRIDGE,
    IOType.CURRENT: IOTYPE_CURRENT,
    IOType.ACCELEROMETER: IOTYPE_ACCELEROMETER,
}


# Multi-sensor channel-spec enum value → SDK constant.  Keyed on the StrEnum
# ``.value`` so the backend never imports the channel-spec classes.

_RTD_TYPE_TO_OL: dict[str, int] = {
    "pt3750": OL_RTD_TYPE_PT3750,
    "pt3850": OL_RTD_TYPE_PT3850,
    "pt3911": OL_RTD_TYPE_PT3911,
    "pt3916": OL_RTD_TYPE_PT3916,
    "pt3920": OL_RTD_TYPE_PT3920,
    "pt3928": OL_RTD_TYPE_PT3928,
    "custom": OL_RTD_TYPE_CUSTOM,
}

_EXCITATION_SRC_TO_OL: dict[str, int] = {
    "internal": OL_EXCITATION_CURRENT_SRC_INTERNAL,
    "external": OL_EXCITATION_CURRENT_SRC_EXTERNAL,
    "disabled": OL_EXCITATION_CURRENT_SRC_DISABLED,
}

_STRAIN_EXCITATION_SRC_TO_OL: dict[str, int] = {
    "internal": OL_STRAIN_EXCITATION_VOLTAGE_SRC_INTERNAL,
    "external": OL_STRAIN_EXCITATION_VOLTAGE_SRC_EXTERNAL,
}

_STRAIN_CONFIG_TO_OL: dict[str, int] = {
    "full_bridge_bending": OL_STRAIN_FULL_BRIDGE_BENDING,
    "full_bridge_bending_poisson": OL_STRAIN_FULL_BRIDGE_BENDING_POISSON,
    "full_bridge_axial": OL_STRAIN_FULL_BRIDGE_AXIAL,
    "half_bridge_poisson": OL_STRAIN_HALF_BRIDGE_POISSON,
    "half_bridge_bending": OL_STRAIN_HALF_BRIDGE_BENDING,
    "quarter_bridge": OL_STRAIN_QUARTER_BRIDGE,
    "quarter_bridge_temp_compensation": OL_STRAIN_QUARTER_BRIDGE_TEMP_COMPENSATION,
}

_BRIDGE_CONFIG_TO_OL: dict[str, int] = {
    "full": OL_BRIDGE_FULL,
    "half": OL_BRIDGE_HALF,
    "quarter": OL_BRIDGE_QUARTER,
}

_COUPLING_TO_OL: dict[str, int] = {
    "dc": OL_COUPLING_DC,
    "ac": OL_COUPLING_AC,
}


# ThermocoupleType.value → SDK TC constant.  Matches the SDK manual's
# documented ``OL_TC_*`` enumeration.
_TC_TYPE_TO_OL: dict[str, int] = {
    "J": 0,
    "K": 1,
    "T": 2,
    "E": 3,
    "R": 4,
    "S": 5,
    "B": 6,
    "N": 7,
}


# Raw subsystem-state integer → :class:`SubsystemState`.  The SDK's
# ``olDaGetSSState`` returns an ``OLSSSTATE_*`` constant; the table
# below mirrors the documented enum ordering.  Defaults to
# INITIALIZED for unknown codes — a safer fallback than guessing
# RUNNING.
_OLSSC_STATE_TO_ENUM: dict[int, SubsystemState] = {
    0: SubsystemState.INITIALIZED,
    1: SubsystemState.CONFIGURED_FOR_SINGLE_VALUE,
    2: SubsystemState.CONFIGURED_FOR_CONTINUOUS,
    3: SubsystemState.PRESTARTED,
    4: SubsystemState.RUNNING,
    5: SubsystemState.STOPPING,
    6: SubsystemState.ABORTING,
    7: SubsystemState.IO_COMPLETE,
}
