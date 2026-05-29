"""``DtolBackend`` Protocol — the seam between session layer and SDK.

The Protocol covers discovery, lifecycle, capability query, the
configuration setters and single-value read/write methods, the
notification + buffer-pool surface used by continuous acquisition, and
counter/timer configuration.

A backend implementation that satisfies this Protocol can be plugged
into :class:`~dtollib.tasks.DtolSession` without modification — the
typed Python layer never knows whether it is talking to
:class:`~dtollib.backend.dataacq.DataAcqBackend` (real SDK) or
:class:`~dtollib.backend.fake.FakeDtolBackend` (in-memory fake used
in tests).

Design reference: docs/design.md §10.2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from dtollib.channels.analog_input import ThermocoupleType
    from dtollib.channels.base import ChannelSpec
    from dtollib.system.capabilities import CapabilitySet
    from dtollib.system.models import BoardInfo, SubsystemInfo
    from dtollib.tasks.models import IOType, SubsystemState


__all__ = ["DtolBackend"]


@runtime_checkable
class DtolBackend(Protocol):
    """Protocol satisfied by every dtollib backend.

    Covers the full SDK surface the session layer needs: configuration,
    lifecycle, single-value read/write, the continuous notification +
    buffer-pool primitives, and counter/timer.

    All methods are synchronous; the async :class:`DtolSession`
    layer wraps blocking SDK calls in :func:`anyio.to_thread.run_sync`.
    """

    # ---- Version / diagnostics --------------------------------------------

    def get_version(self) -> tuple[str, str]:
        """Return ``(oldaapi_version, olmem_version)`` strings.

        Used by ``dtol-diag``.  Implementations may cache the result
        for the session's lifetime — the SDK version does not change
        at runtime.
        """
        ...

    # ---- Board enumeration -------------------------------------------------

    def enum_boards(self) -> list[BoardInfo]:
        """Enumerate every installed DT-Open Layers board.

        Returns:
            One :class:`BoardInfo` per board.  Empty list if no boards
            are installed (not an error).
        """
        ...

    # ---- Device lifecycle (HDRVR ref-counted across sessions) -------------

    def initialize(self, board_name: str) -> int:
        """Open ``board_name``; return its HDRVR as a Python int.

        Implementations ref-count HDRVRs across sessions: the first
        :meth:`initialize` for a given board name calls
        ``olDaInitialize``, subsequent calls reuse the existing handle.
        :meth:`terminate` decrements the ref-count; the final
        :meth:`terminate` calls ``olDaTerminate``.
        """
        ...

    def terminate(self, hdrvr: int) -> None:
        """Drop a reference to ``hdrvr``; close the handle if refcount hits 0."""
        ...

    # ---- Subsystem reservation -------------------------------------------

    def enum_subsystems(self, board_name: str) -> list[SubsystemInfo]:
        """Enumerate subsystems on ``board_name``.

        Args:
            board_name: Board to enumerate.  The backend opens the
                board temporarily if it is not already open.

        Returns:
            One :class:`SubsystemInfo` per subsystem.
        """
        ...

    def get_dass(self, hdrvr: int, subsystem_type: int, element: int) -> int:
        """Reserve a subsystem; return its HDASS as a Python int."""
        ...

    def release_dass(self, hdass: int) -> None:
        """Release a subsystem handle previously returned by :meth:`get_dass`."""
        ...

    # ---- Capability query ------------------------------------------------

    def query_capabilities(self, hdass: int) -> CapabilitySet:
        """Build a :class:`CapabilitySet` for ``hdass`` from SDK queries.

        Result is cached per HDASS for the session's lifetime;
        capability flags do not change while a subsystem is held.
        """
        ...

    # ---- Configuration setters ----------------------------------

    def set_data_flow(self, hdass: int, mode: int) -> None:
        """Set the subsystem data-flow mode via ``olDaSetDataFlow``.

        Args:
            hdass: Subsystem handle.
            mode: One of the ``OL_DF_*`` constants from
                :mod:`dtollib.capi.constants`.
        """
        ...

    def set_multi_sensor_type(
        self,
        hdass: int,
        physical_channel: int,
        io_type: IOType,
    ) -> None:
        """Re-type a MULTI_SENSOR channel via ``olDaSetMultiSensorType``.

        Must be called BEFORE any per-type setter on a MULTI_SENSOR
        channel (docs/design.md §8.5a).  Skipping it is a silent
        wrong-data bug — the SDK happily reads voltage off a TC-intended
        channel.
        """
        ...

    def add_channel(
        self,
        hdass: int,
        list_index: int,
        spec: ChannelSpec,
    ) -> None:
        """Add a channel to the subsystem's channel/gain list.

        Drives ``olDaSetChannelType`` + ``olDaSetChannelRange`` +
        ``olDaSetGainListEntry`` (+ ``olDaSetThermocoupleType`` for TC
        channels) in the right order.  Caller is responsible for
        :meth:`set_multi_sensor_type` BEFORE calling this on a
        MULTI_SENSOR channel.
        """
        ...

    def set_stop_on_error(self, hdass: int, stop: bool) -> None:
        """SDK-level ``olDaSetStopOnError`` — orthogonal to ``ErrorPolicy``."""
        ...

    def commit(self, hdass: int) -> None:
        """First ``olDaConfig`` — apply the configured state.

        Single-value tasks need only this. Continuous tasks call it as
        config #1 (after channel/clock/wrap setup) and then :meth:`arm`
        as config #2 once the notification + Ready queue are wired.
        """
        ...

    def arm(self, hdass: int) -> None:
        """Second ``olDaConfig`` — continuous mode only.

        Run after :meth:`register_notification` and the Ready queue are in
        place; the second config wires the notification window into the
        SDK's buffer-rotation state machine. Without it the SDK never posts
        buffer-done events on the DT9805/06 (docs/decisions.md).
        """
        ...

    # ---- Lifecycle operations -----------------------------------

    def start(self, hdass: int) -> None:
        """Start the subsystem via ``olDaStart``."""
        ...

    def stop(self, hdass: int) -> None:
        """Orderly stop via ``olDaStop`` (blocks until current buffer fills)."""
        ...

    def abort(self, hdass: int) -> None:
        """Immediate abort via ``olDaAbort`` (current buffer may be partial)."""
        ...

    def get_state(self, hdass: int) -> SubsystemState:
        """Return the SDK's reported subsystem state via ``olDaGetSSState``."""
        ...

    def is_running(self, hdass: int) -> bool:
        """Cheap running-state query via ``olDaIsRunning``."""
        ...

    # ---- Single-value reads -------------------------------------

    def get_single_value(self, hdass: int, channel: int, gain: float) -> int:
        """One-shot raw-code read of ``channel`` via ``olDaGetSingleValue``."""
        ...

    def get_single_float(self, hdass: int, channel: int, gain: float) -> float:
        """One-shot engineering-unit read via ``olDaGetSingleFloat``.

        Only valid when the subsystem reports
        ``OLSSC_RETURNS_FLOATS = True`` (DT9805/DT9806 multi-sensor).
        """
        ...

    def get_single_values(self, hdass: int, gain: float) -> list[int]:
        """Simultaneous raw-code read of every channel via ``olDaGetSingleValues``.

        Only valid when the subsystem reports
        ``OLSSC_SUP_SIMULTANEOUS_SH = True``.
        """
        ...

    def get_single_floats(self, hdass: int, gain: float) -> list[float]:
        """Simultaneous engineering-unit read via ``olDaGetSingleFloats``.

        Requires both ``OLSSC_RETURNS_FLOATS`` and
        ``OLSSC_SUP_SIMULTANEOUS_SH``.
        """
        ...

    def get_cjc_temperature(self, hdass: int, channel: int) -> float:
        """Cold-junction temperature for ``channel`` via ``olDaGetCjcTemperature``."""
        ...

    def code_to_volts(self, hdass: int, code: int, gain: float) -> float:
        """Convert a raw code to volts via ``olDaCodeToVolts`` (oracle path)."""
        ...

    def get_scaling(self, hdass: int) -> tuple[float, float, int, bool]:
        """Return ``(vmin, vmax, resolution_bits, twos_complement)`` for ``hdass``.

        The subsystem range / resolution / encoding needed to scale raw codes
        to volts. Used by the continuous block path to build a
        :class:`~dtollib.capi.conversion.BlockConversion` plan that matches the
        single-value :meth:`code_to_volts` scaling.
        """
        ...

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
        """``olDaVoltsToStrain`` — bridge volts → strain (ε)."""
        ...

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
        """``olDaVoltsToBridgeBasedSensor`` — bridge volts → engineering value."""
        ...

    # ---- TEDS readers (§8.B5) ---------------------------

    def read_strain_gage_hardware_teds(self, hdass: int, channel: int) -> dict[str, object]:
        """Read on-sensor strain-gage TEDS via ``olDaReadStrainGageHardwareTeds``."""
        ...

    def read_strain_gage_virtual_teds(self, path: str) -> dict[str, object]:
        """Read a strain-gage virtual-TEDS file via ``olDaReadStrainGageVirtualTeds``."""
        ...

    def read_bridge_sensor_hardware_teds(self, hdass: int, channel: int) -> dict[str, object]:
        """Read on-sensor bridge TEDS via ``olDaReadBridgeSensorHardwareTeds``."""
        ...

    def read_bridge_sensor_virtual_teds(self, path: str) -> dict[str, object]:
        """Read a bridge virtual-TEDS file via ``olDaReadBridgeSensorVirtualTeds``."""
        ...

    # ---- Thermocouple configuration -----------------------------

    def set_thermocouple_type(
        self,
        hdass: int,
        channel: int,
        tc_type: ThermocoupleType,
    ) -> None:
        """Set ``channel``'s thermocouple type via ``olDaSetThermocoupleType``."""
        ...

    def set_return_cjc_in_stream(self, hdass: int, enable: bool) -> None:
        """``olDaSetReturnCjcTemperatureInStream`` — interleaved CJC in continuous mode."""
        ...

    # ---- Continuous-mode configuration --------------------------

    def set_channel_list(self, hdass: int, channels: list[int]) -> None:
        """Set the channel-list size + each entry in order.

        Driving call sequence: ``olDaSetChannelListSize`` then
        ``olDaSetChannelListEntry`` per position. Distinct from the
        ``add_channel`` shape because continuous mode uses a flat
        channel list, not the per-channel range / gain mutator sequence.
        """
        ...

    def set_clock(
        self,
        hdass: int,
        *,
        rate_hz: float,
        clock_source: int,
        external_divider: int | None = None,
    ) -> None:
        """Configure clock source + frequency via ``olDaSetClockSource`` etc."""
        ...

    def get_clock_frequency(self, hdass: int) -> float:
        """``olDaGetClockFrequency`` — actual (possibly quantised) rate."""
        ...

    def set_trigger(
        self,
        hdass: int,
        *,
        kind: int,
        threshold_channel: int | None = None,
        threshold_level: float | None = None,
    ) -> None:
        """Configure start-trigger via ``olDaSetTrigger`` + threshold setters."""
        ...

    def set_wrap_mode(self, hdass: int, mode: int) -> None:
        """``olDaSetWrapMode`` — NONE / SINGLE / MULTIPLE."""
        ...

    def set_dma_usage(self, hdass: int, n_channels: int) -> None:
        """``olDaSetDmaUsage`` — claim DMA channels for the subsystem."""
        ...

    # ---- Notification + runtime ---------------------------------

    def register_notification(
        self,
        hdass: int,
        callback: Callable[[int, int, int], int],
    ) -> object:
        """Route SDK buffer-done events for ``hdass`` to ``callback``.

        Returns an opaque handle the caller passes to
        :meth:`unregister_notification`. The backend keeps whatever
        machinery the mechanism needs alive internally (the real backend
        owns a hidden message window + pump thread) so nothing is collected
        mid-acquisition. ``callback(msg_id, wparam, lparam)`` is invoked for
        each ``OLDA_WM_*`` message and must do minimal work.
        """
        ...

    def unregister_notification(self, hdass: int, handle: object) -> None:
        """Stop routing events for ``hdass`` and release the machinery."""
        ...

    def get_queue_size(self, hdass: int, queue: int) -> int:
        """``olDaGetQueueSize`` — depth of Ready/Inprocess/Done queue."""
        ...

    # ---- Buffer pool primitives ---------------------------------

    def alloc_buffer(
        self,
        n_samples: int,
        sample_dtype_bytes: int,
        *,
        zero_init: bool = True,
    ) -> int:
        """Allocate an HBUF; return its handle as a Python int."""
        ...

    def free_buffer(self, hbuf: int) -> None:
        """``olDmFreeBuffer`` — release an HBUF."""
        ...

    def put_buffer(self, hdass: int, hbuf: int) -> None:
        """``olDaPutBuffer`` — push an HBUF onto the Ready queue."""
        ...

    def get_buffer(self, hdass: int) -> int | None:
        """``olDaGetBuffer`` — pop the next Done HBUF (None if empty)."""
        ...

    def flush_buffers(self, hdass: int) -> None:
        """``olDaFlushBuffers`` — drop Ready + Done queues."""
        ...

    def read_buffer_payload(self, hbuf: int) -> Any:
        """Copy the HBUF payload into an ndarray view (drainer-thread call)."""
        ...

    def get_buffer_valid_samples(self, hbuf: int) -> int:
        """``olDmGetValidSamples`` — samples actually populated by the SDK."""
        ...

    def copy_inprocess_buffer(
        self,
        hbuf: int,
        n_samples: int,
        sample_dtype_bytes: int,
    ) -> bytes:
        """``olDmCopyFromBuffer`` — copy the currently-filling HBUF in place.

        Drains up to ``n_samples`` from the in-process (still-filling) HBUF
        WITHOUT waiting for ``BUFFER_DONE``. Returns the bytes actually
        copied — the SDK may transfer fewer samples than requested (device
        segment alignment). Powers :meth:`DtolSession.read_inprocess`; only
        valid on subsystems advertising ``OLSSC_SUP_INPROCESSFLUSH``.
        """
        ...

    # ---- Single-value output writes -----------------------------

    def put_single_value(self, hdass: int, channel: int, value: int, gain: float) -> None:
        """One-shot raw-code write to ``channel`` via ``olDaPutSingleValue``.

        ``value`` is a device code (not volts) — the session layer
        converts engineering units to a code before calling this.
        """
        ...

    def put_single_values(self, hdass: int, values: list[int], gain: float) -> None:
        """Simultaneous raw-code write across the channel list.

        Drives ``olDaPutSingleValues``. Only valid on subsystems
        advertising simultaneous D/A update (``supports_simultaneous_da``).
        """
        ...

    # ---- Digital-I/O configuration ------------------------------

    def set_synchronous_digital_io_usage(self, hdass: int, use: bool) -> None:
        """``olDaSetSynchronousDigitalIOUsage`` — scan-synchronised digital I/O."""
        ...

    def set_digital_io_list_entry(self, hdass: int, entry: int, value: int) -> None:
        """``olDaSetDigitalIOListEntry`` — bind a digital port at a list slot."""
        ...

    # ---- Continuous-AO mute control -----------------------------

    def mute(self, hdass: int) -> None:
        """``olDaMute`` — hold the D/A output at its current value."""
        ...

    def unmute(self, hdass: int) -> None:
        """``olDaUnMute`` — release a muted D/A output."""
        ...

    # ---- Host→buffer copy (continuous-AO waveform fill) ---------

    def copy_to_buffer(self, hbuf: int, data: bytes, n_samples: int) -> None:
        """``olDmCopyToBuffer`` — fill an HBUF from a host byte payload."""
        ...

    def copy_buffer(self, hbuf: int, n_samples: int, sample_dtype_bytes: int) -> bytes:
        """``olDmCopyBuffer`` — copy an HBUF's valid samples to a host buffer."""
        ...

    # ---- Counter/timer configuration ----------------------------

    def set_ct_mode(self, hdass: int, mode: int) -> None:
        """``olDaSetCTMode`` — counter/timer operation mode (``OL_CTMODE_*``)."""
        ...

    def set_ct_clock(self, hdass: int, *, rate_hz: float, clock_source: int) -> None:
        """Configure the counter clock source + frequency.

        Drives ``olDaSetCTClockSource`` + ``olDaSetCTClockFrequency``.
        """
        ...

    def set_gate_type(self, hdass: int, gate: int) -> None:
        """``olDaSetGateType`` — gate-enable logic (``OL_GATE_*``)."""
        ...

    def set_pulse(self, hdass: int, *, pulse_type: int, duty_or_width: float) -> None:
        """Configure output pulse polarity + width.

        Drives ``olDaSetPulseType`` + ``olDaSetPulseWidth``. ``duty_or_width``
        is a duty cycle in ``(0, 1)`` for rate generation, or a pulse width
        in seconds for one-shot modes.
        """
        ...

    def set_measure_edges(self, hdass: int, *, start_edge: int, stop_edge: int) -> None:
        """Configure edge-to-edge measurement edges.

        Drives ``olDaSetMeasureStartEdge`` + ``olDaSetMeasureStopEdge``
        (``OL_EDGE_*`` selectors).
        """
        ...

    def set_cascade_mode(self, hdass: int, cascade: bool) -> None:
        """``olDaSetCascadeMode`` — cascade two counters into a 32-bit counter."""
        ...

    # ---- Counter/timer read -------------------------------------

    def read_events(self, hdass: int, channel: int) -> int:
        """``olDaReadEvents`` — current counter value for ``channel``."""
        ...

    def measure_frequency(self, hdass: int, channel: int) -> float:
        """``olDaMeasureFrequency`` — measured input frequency (Hz) for ``channel``."""
        ...

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
        """Configure triggered-scan retrigger.

        Drives ``olDaSetTriggeredScanUsage`` + ``olDaSetMultiscanCount`` +
        ``olDaSetRetriggerMode`` (+ ``olDaSetRetriggerFrequency`` for the
        INTERNAL mode, ``olDaSetRetrigger`` for EXTRA).
        """
        ...

    # ---- Simultaneous start (HSSLIST) ---------------------------

    def get_ss_list(self, hdrvr: int) -> int:
        """``olDaGetSSList`` — obtain a simultaneous-start list handle."""
        ...

    def put_dass_to_ss_list(self, hsslist: int, hdass: int) -> None:
        """``olDaPutDassToSSList`` — add a subsystem to the list."""
        ...

    def simultaneous_pre_start(self, hsslist: int) -> None:
        """``olDaSimultaneousPreStart`` — arm every subsystem in the list."""
        ...

    def simultaneous_start(self, hsslist: int) -> None:
        """``olDaSimultaneousStart`` — start every subsystem in the list at once."""
        ...

    def release_ss_list(self, hsslist: int) -> None:
        """``olDaReleaseSSList`` — release the list handle."""
        ...
