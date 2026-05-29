"""``DtolSession`` — async lifecycle wrapper around one HDASS.

Lifecycle: prepare → commit → start → poll → stop/abort → close.
Single-value reads go through :meth:`poll`; single-value writes through
:meth:`write`. Continuous acquisition is owned by
:func:`dtollib.streaming.record`, which manages the buffer pool and the
notification bridge — the session does not expose a bare block-read.

Design reference: docs/design.md §9.1, §9.2; docs/implementation-plan.md §4.4.
"""

from __future__ import annotations

import contextlib
import logging
import math
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Self

import anyio
import anyio.to_thread  # pyright: ignore[reportMissingImports]

from dtollib.capi.constants import (
    OLSS_AD,
    OLSS_CT,
    OLSS_DA,
    OLSS_DIN,
    OLSS_DOUT,
    OLSS_QUAD,
    OLSS_TACH,
)
from dtollib.capi.conversion import detect_thermocouple_sentinel
from dtollib.errors import (
    DtolCapabilityError,
    DtolError,
    DtolTaskStateError,
    DtolTimeoutError,
    DtolValidationError,
    ErrorContext,
)
from dtollib.tasks._output_gate import ao_volts_to_code, gate_ao_samples
from dtollib.tasks.builder import TaskBuilder
from dtollib.tasks.models import (
    BufferState,
    DaqBlock,
    DaqReading,
    DataFlow,
    SensorStatus,
    SubsystemState,
    SubsystemType,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from dtollib.backend._buffer_pool import BufferPool
    from dtollib.backend.base import DtolBackend
    from dtollib.capi.conversion import BlockConversion
    from dtollib.system.capabilities import CapabilitySet
    from dtollib.tasks.spec import TaskSpec


__all__ = ["DtolSession"]


_LOG = logging.getLogger("dtollib.tasks.session")


# ---- Application-side thermocouple linearisation (DT9805/DT9806) -----------
#
# These boards do not linearise thermocouples in firmware; the wrapper reads
# the differential thermo-emf plus a cold-junction sensor and applies NIST
# ITS-90 polynomials.  Constants below are bench-verified — see
# docs/decisions.md and scripts/bench_probe_tc_diff.py.

# Cold-junction sensor sits near +0.25 V; read at unity gain so it stays on
# the ±10 V scale (gain 100 would saturate it).
_CJC_GAIN = 1.0
# DT9805/06 CJC output is 10 mV/°C (UM9800 §"Connecting Thermocouple Inputs").
_CJC_VOLTS_PER_DEGC = 0.010
# An open differential input is pulled to the +2.5 V reference and pegs the
# ADC at positive full scale, i.e. ~ +V_RAIL / gain referred to the input.
_BOARD_RAIL_VOLTS = 10.0  # DT9805/06 A/D is ±10 V bipolar (UM9800).
_OPEN_RAIL_FRACTION = 0.95

# Done-queue poll cadence for the synchronous read_block path (seconds). Short
# enough to keep latency low on slow tasks without busy-spinning the CPU.
_READ_POLL_INTERVAL_S = 0.001


def _noop_notify(msg_id: int, wparam: int, lparam: int) -> int:
    """No-op notification callback for the synchronous read path.

    The DT9805/06 require the notification window + the second ``olDaConfig``
    (``arm``) for the SDK to rotate buffers through the Ready/Done queues —
    even when the consumer polls ``olDaGetBuffer`` rather than reacting to
    buffer-done events (docs/decisions.md; enforced by ``FakeDtolBackend``).
    ``read_block`` / ``read_inprocess`` therefore register this no-op so
    ``arm`` succeeds, then ignore the events entirely.
    """
    del msg_id, wparam, lparam
    return 0


_SUBSYSTEM_TYPE_TO_OLSS: dict[SubsystemType, int] = {
    SubsystemType.ANALOG_INPUT: OLSS_AD,
    SubsystemType.ANALOG_OUTPUT: OLSS_DA,
    SubsystemType.DIGITAL_INPUT: OLSS_DIN,
    SubsystemType.DIGITAL_OUTPUT: OLSS_DOUT,
    SubsystemType.COUNTER_TIMER: OLSS_CT,
    SubsystemType.QUADRATURE: OLSS_QUAD,
    SubsystemType.TACHOMETER: OLSS_TACH,
}

# Counter-family subsystem kinds — routed through the builder's
# ``configure_counter`` path and read via ``read_events`` /
# ``measure_frequency`` rather than ``poll`` / ``record``.
_COUNTER_SUBSYSTEMS: frozenset[SubsystemType] = frozenset(
    {
        SubsystemType.COUNTER_TIMER,
        SubsystemType.QUADRATURE,
        SubsystemType.TACHOMETER,
    }
)


# The lookup raises on a missing key so a TaskSpec referencing an
# unsupported subsystem fails loudly instead of silently routing to AI.


def _to_olss(subsys_type: SubsystemType) -> int:
    try:
        return _SUBSYSTEM_TYPE_TO_OLSS[subsys_type]
    except KeyError as exc:
        raise DtolCapabilityError(
            f"Subsystem type {subsys_type.value!r} is not supported; "
            "see docs/design.md §26 for coverage.",
            context=ErrorContext(operation="DtolSession._to_olss"),
        ) from exc


class DtolSession:
    """Async lifecycle wrapper around one configured DT-Open Layers subsystem.

    Drives single-value reads (:meth:`poll`) and writes (:meth:`write`)
    end-to-end. Continuous acquisition uses the same session but is driven
    by :func:`dtollib.streaming.record`, which owns the buffer pool and
    notification bridge.

    Attributes:
        spec: Bound :class:`~dtollib.tasks.TaskSpec`.
        backend: Bound :class:`~dtollib.backend.DtolBackend`.
        timeout: Default per-call timeout in seconds.
    """

    def __init__(
        self,
        spec: TaskSpec,
        backend: DtolBackend,
        *,
        timeout: float = 10.0,
    ) -> None:
        self.spec = spec
        self.backend = backend
        self.timeout = timeout

        self._lock = anyio.Lock()
        self._hdrvr: int | None = None
        self._hdass: int | None = None
        self._capabilities: CapabilitySet | None = None
        self._prepared = False
        self._committed = False
        self._closed = False

        # DOUT shadow register — port_channel → last-written byte. A digital
        # port write is whole-byte at the SDK level; partial per-line writes
        # merge into this shadow so untouched lines are preserved. Seeded from
        # each DigitalOutputPort.safe_value at commit (docs/design.md §18.1).
        self._dout_shadow: dict[int, int] = {}

        # Lazily-primed buffer pool for the synchronous block-read path
        # (read_block / read_inprocess). Distinct from record(), which owns
        # its own pool + notification bridge; the two paths are mutually
        # exclusive on one session.
        self._read_pool: BufferPool | None = None
        self._read_samples_per_buffer: int | None = None
        self._read_conversion: BlockConversion | None = None
        self._read_block_index: int = 0
        self._read_first_sample: int = 0
        self._read_started_at: datetime | None = None
        self._read_started_mono_ns: int = 0
        self._read_notify_handle: object | None = None

    # ---- Handles ---------------------------------------------------------

    @property
    def hdass(self) -> int:
        """Reserved HDASS — set after :meth:`prepare`."""
        if self._hdass is None:
            raise DtolTaskStateError(
                "DtolSession.hdass accessed before prepare()",
                context=ErrorContext(
                    operation="DtolSession.hdass",
                    task_name=self.spec.name,
                ),
            )
        return self._hdass

    @property
    def raw_hdass(self) -> int:
        """Escape hatch — raw HDASS for direct ``olDa*`` calls."""
        return self.hdass

    @property
    def raw_hdrv(self) -> int:
        """Escape hatch — raw HDRVR for direct ``olDa*`` calls."""
        if self._hdrvr is None:
            raise DtolTaskStateError(
                "DtolSession.raw_hdrv accessed before prepare()",
                context=ErrorContext(
                    operation="DtolSession.raw_hdrv",
                    task_name=self.spec.name,
                ),
            )
        return self._hdrvr

    @property
    def capabilities(self) -> CapabilitySet:
        """Live capability snapshot.  Available after :meth:`prepare`."""
        if self._capabilities is None:
            raise DtolTaskStateError(
                "DtolSession.capabilities accessed before prepare()",
                context=ErrorContext(
                    operation="DtolSession.capabilities",
                    task_name=self.spec.name,
                ),
            )
        return self._capabilities

    # ---- Lifecycle -------------------------------------------------------

    async def prepare(self) -> None:
        """Allocate HDASS, validate against capabilities, configure channels.

        Idempotent.  Stops short of ``olDaConfig`` so the continuous
        path can insert notification + Ready-queue setup between
        :meth:`prepare` and :meth:`commit`.  For single-value mode there
        is nothing to interleave; :meth:`configure` is the convenience
        that runs ``prepare`` then ``commit``.
        """
        async with self._lock:
            if self._prepared:  # double-check under lock.
                return

            board_name = self._resolve_board_name()
            subsys_type = self.spec.subsystem_type or self.spec.infer_subsystem_type()
            olss = _to_olss(subsys_type)

            hdrvr = await anyio.to_thread.run_sync(self.backend.initialize, board_name)
            self._hdrvr = hdrvr
            try:
                hdass = await anyio.to_thread.run_sync(
                    self.backend.get_dass, hdrvr, olss, self.spec.element
                )
                self._hdass = hdass
            except BaseException:
                # Cleanup the HDRVR ref-count if subsystem reservation fails.
                await anyio.to_thread.run_sync(self.backend.terminate, hdrvr)
                self._hdrvr = None
                raise

            capabilities = await anyio.to_thread.run_sync(self.backend.query_capabilities, hdass)
            self._capabilities = capabilities
            self._validate_against_capabilities(capabilities)

            self._prepared = True

    async def commit(self) -> None:
        """Run the configured-state commit.

        Dispatches on ``spec.data_flow``:

        - ``SINGLE_VALUE`` → builder runs the per-channel configure + commit.
        - ``CONTINUOUS`` / ``FINITE`` → builder runs the pre-commit
          continuous configuration; the recorder (``record()``) drives the
          register → queue → ``commit()`` ordering after wiring its bridge.

        Idempotent.
        """
        if not self._prepared:
            raise DtolTaskStateError(
                "DtolSession.commit called before prepare()",
                context=ErrorContext(
                    operation="DtolSession.commit",
                    task_name=self.spec.name,
                ),
            )
        async with self._lock:
            if self._committed:
                return
            builder = TaskBuilder(self.backend)
            hdass = self._require_hdass("DtolSession.commit")
            capabilities = self.capabilities
            subsys_type = self.spec.subsystem_type or self.spec.infer_subsystem_type()
            if subsys_type in _COUNTER_SUBSYSTEMS:
                # Counter/timer / quadrature / tachometer — read on demand
                # after start; no channel/gain list or sample clock.
                await anyio.to_thread.run_sync(
                    builder.configure_counter,
                    hdass,
                    self.spec,
                    capabilities,
                )
            elif self.spec.data_flow == DataFlow.SINGLE_VALUE:
                await anyio.to_thread.run_sync(
                    builder.configure_single_value,
                    hdass,
                    self.spec,
                    capabilities,
                )
                self._seed_dout_shadow()
            else:
                # Pre-commit configure for continuous/finite — the recorder
                # finishes the §12.3.2 register → queue → commit ordering.
                await anyio.to_thread.run_sync(
                    builder.configure_continuous,
                    hdass,
                    self.spec,
                    capabilities,
                )
            self._committed = True

    async def configure(self) -> None:
        """Convenience: :meth:`prepare` followed by :meth:`commit`."""
        await self.prepare()
        await self.commit()

    async def start(self) -> None:
        """``olDaStart`` — transitions subsystem to RUNNING."""
        if not self._committed:
            await self.commit()
        async with self._lock:
            hdass = self._require_hdass("DtolSession.start")
            await anyio.to_thread.run_sync(self.backend.start, hdass)

    async def stop(self) -> None:
        """``olDaStop`` — orderly stop.  Blocks until current buffer fills."""
        async with self._lock:
            if self._hdass is None:
                return
            await anyio.to_thread.run_sync(self.backend.stop, self._hdass)

    async def abort(self) -> None:
        """``olDaAbort`` — immediate halt."""
        async with self._lock:
            if self._hdass is None:
                return
            await anyio.to_thread.run_sync(self.backend.abort, self._hdass)

    async def close(self, *, graceful: bool = False) -> None:
        """Tear the session down.

        Default ``graceful=False`` uses :meth:`abort`; ``graceful=True``
        uses :meth:`stop`.  Releases HDASS, decrements HDRVR ref-count.
        Idempotent.

        Args:
            graceful: When ``True``, prefer :meth:`stop` (waits for the
                current buffer).  ``False`` (default) uses :meth:`abort`
                — the safe choice from ``__aexit__`` when an outer
                exception is propagating and ``stop`` could deadlock
                waiting for an SDK trigger that will never fire.
        """
        if self._closed:
            return
        # Shield teardown from cancellation. A cancelled or timed-out
        # session (e.g. ``move_on_after`` wrapping ``record()``) must still
        # release the HDASS and terminate the HDRVR; otherwise the awaited
        # release_dass/terminate are cancelled mid-flight and the subsystem
        # stays reserved ("Subsystem in use", ECODE 20) until the OS reclaims
        # the process handle. Bench-confirmed on DT9806, 2026-05-28.
        with anyio.CancelScope(shield=True):
            # Tear down the synchronous read pool (read_block / read_inprocess)
            # before releasing the subsystem: stop the subsystem, unregister the
            # no-op notification, then flush + free its HBUFs.
            if self._read_pool is not None:
                if self._hdass is not None:
                    with contextlib.suppress(Exception):
                        await anyio.to_thread.run_sync(self.backend.abort, self._hdass)
                    if self._read_notify_handle is not None:
                        with contextlib.suppress(Exception):
                            await anyio.to_thread.run_sync(
                                self.backend.unregister_notification,
                                self._hdass,
                                self._read_notify_handle,
                            )
                self._read_notify_handle = None
                with contextlib.suppress(Exception):
                    await anyio.to_thread.run_sync(self._read_pool.flush)
                with contextlib.suppress(Exception):
                    await anyio.to_thread.run_sync(self._read_pool.free_all)
                self._read_pool = None
            try:
                if self._hdass is not None:
                    if self.backend.is_running(self._hdass):
                        if graceful:
                            await self.stop()
                        else:
                            await self.abort()
                    await anyio.to_thread.run_sync(self.backend.release_dass, self._hdass)
                    self._hdass = None
                if self._hdrvr is not None:
                    await anyio.to_thread.run_sync(self.backend.terminate, self._hdrvr)
                    self._hdrvr = None
            finally:
                self._closed = True
                self._prepared = False
                self._committed = False

    # ---- State queries ---------------------------------------------------

    @property
    def state(self) -> SubsystemState:
        """Reported SDK :class:`SubsystemState`.

        Cheap read; called inside the lock-free hot path of
        :meth:`is_running` and from error messages.
        """
        if self._hdass is None:
            return SubsystemState.INITIALIZED
        return self.backend.get_state(self._hdass)

    def is_running(self) -> bool:
        """Cheap running-state probe."""
        if self._hdass is None:
            return False
        return self.backend.is_running(self._hdass)

    @property
    def closed(self) -> bool:
        """Whether :meth:`close` has completed for this session."""
        return self._closed

    # ---- Single-value reads -------------------------------------

    async def poll(self, *, timeout: float | None = None) -> DaqReading:  # noqa: ASYNC109
        """One-shot scalar read across every channel of the task.

        Behaviour (docs/implementation-plan.md §3.7):

        1. Captures ``requested_at`` + monotonic ns.
        2. Branches on ``OLSSC_SUP_SIMULTANEOUS_SH``: one
           ``olDaGetSingleValues``/``Floats`` call across all
           channels, or per-channel loop.
        3. Branches on ``OLSSC_RETURNS_FLOATS``: skip
           code-to-volts if true.
        4. For TC channels, detects sentinel floats and populates
           ``sensor_status`` + NaN-fills ``values``.
        5. Computes ``t_utc`` as the midpoint of
           ``requested_at`` / ``received_at``.

        Args:
            timeout: Per-call timeout in seconds.  ``None`` =
                use :attr:`timeout`.

        Returns:
            One :class:`DaqReading`.

        Raises:
            DtolTaskStateError: Task is not in a state that admits
                ``poll`` (continuous mid-run, mid-stop, mid-abort, ...).
        """
        await self.configure()
        # ``start()`` is conventional even though single-value SDK reads
        # may not require it on every device; calling ``olDaStart``
        # turns the subsystem into RUNNING and standardises the
        # state-machine assertions in tests.
        if self.state in {SubsystemState.STOPPING, SubsystemState.ABORTING}:
            raise DtolTaskStateError(
                f"DtolSession.poll: invalid state {self.state.value}; task is mid-shutdown",
                context=ErrorContext(
                    operation="DtolSession.poll",
                    task_name=self.spec.name,
                ),
            )

        # poll() is the single-value read path.  Continuous/finite tasks
        # stream through record() — see docs/design.md §9.2.
        if self.spec.data_flow != DataFlow.SINGLE_VALUE:
            raise DtolTaskStateError(
                f"DtolSession.poll: data_flow={self.spec.data_flow.value} is not "
                "valid for single-value reads; use `record(session)` instead.",
                context=ErrorContext(
                    operation="DtolSession.poll",
                    task_name=self.spec.name,
                ),
            )

        del timeout  # Single-value reads honour the session-level default only.

        async with self._lock:
            hdass = self._require_hdass("DtolSession.poll")
            requested_at = datetime.now(UTC)
            t_mono_request = time.monotonic_ns()

            await anyio.to_thread.run_sync(self.backend.start, hdass)
            values, sentinels = await self._read_all_channels()
            received_at = datetime.now(UTC)
            t_mono_received = time.monotonic_ns()

        # Derive midpoint between request and receipt (wall-clock).
        midpoint_us = (requested_at.timestamp() + received_at.timestamp()) / 2.0
        t_utc = datetime.fromtimestamp(midpoint_us, tz=UTC)
        midpoint_mono_ns = (t_mono_request + t_mono_received) // 2
        latency_s = (received_at - requested_at).total_seconds()

        units: dict[str, str | None] = {
            ch.display_name: _channel_unit(ch) for ch in self.spec.channels
        }

        return DaqReading(
            device=self.spec.name,
            task=self.spec.name,
            values=values,
            units=units,
            requested_at=requested_at,
            received_at=received_at,
            t_utc=t_utc,
            t_mono_ns=t_mono_request,
            t_midpoint_mono_ns=midpoint_mono_ns,
            latency_s=latency_s,
            sensor_status=sentinels,
            metadata=dict(self.spec.metadata),
        )

    # ---- Synchronous block reads (continuous / finite) ----------

    async def read_block(
        self,
        samples_per_channel: int,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> DaqBlock:
        """Read one buffer's worth of hardware-clocked data, synchronously.

        The polled alternative to :func:`dtollib.streaming.record` for
        ``CONTINUOUS`` / ``FINITE`` tasks. On first call it primes a buffer
        pool (allocate → queue → ``olDaStart``); each call then waits for the
        next completed buffer on the SDK Done queue and returns it as a
        :class:`DaqBlock`. No notification bridge is involved — this is a
        direct ``olDaGetBuffer`` poll.

        :func:`record` / :func:`~dtollib.streaming.record_polled` remain the
        recommended path; ``read_block`` suits simple scripts and tests that
        want one buffer at a time without an ``async for`` consumer.

        Args:
            samples_per_channel: Buffer depth in samples per channel. Fixed
                for the life of the pool — later calls must pass the same value.
            timeout: Seconds to wait for a completed buffer. ``None`` uses the
                session default (:attr:`timeout`).

        Returns:
            One :class:`DaqBlock`. ``block.samples_per_channel`` is the actual
            count (``<= samples_per_channel`` for a partial final buffer).

        Raises:
            DtolValidationError: ``samples_per_channel < 1``.
            DtolTaskStateError: ``data_flow`` is not continuous/finite, or
                ``buffers`` is unconfigured, or the pool was primed at a
                different depth.
            DtolTimeoutError: No buffer completed within ``timeout``.
        """
        if samples_per_channel < 1:
            raise DtolValidationError(
                f"read_block: samples_per_channel must be >= 1, got {samples_per_channel}",
                context=ErrorContext(operation="DtolSession.read_block", task_name=self.spec.name),
            )
        await self._ensure_read_primed(samples_per_channel)
        pool = self._read_pool
        if pool is None:  # pragma: no cover - _ensure_read_primed sets it
            raise DtolTaskStateError(
                "read_block: buffer pool not primed",
                context=ErrorContext(operation="DtolSession.read_block", task_name=self.spec.name),
            )
        n_channels = max(pool.n_channels, 1)
        wait_s = self.timeout if timeout is None else timeout
        deadline = anyio.current_time() + wait_s

        sample_count = 0
        while True:
            raw = await anyio.to_thread.run_sync(pool.get_done)
            if raw is not None:
                sample_count = min(int(raw.valid_samples) // n_channels, samples_per_channel)
                if sample_count > 0:
                    break
                # Empty buffer — recycle it and keep waiting.
                await anyio.to_thread.run_sync(pool.requeue, raw)
            if anyio.current_time() >= deadline:
                raise DtolTimeoutError(
                    f"read_block: no buffer completed within {wait_s:.3f}s",
                    context=ErrorContext(
                        operation="DtolSession.read_block", task_name=self.spec.name
                    ),
                )
            await anyio.sleep(_READ_POLL_INTERVAL_S)

        import numpy as np  # noqa: PLC0415

        read_started = datetime.now(UTC)
        mono_ns = time.monotonic_ns()
        view = await anyio.to_thread.run_sync(pool.payload_view, raw)
        flat = np.asarray(view)[: sample_count * n_channels].copy()
        data_int = flat.reshape(sample_count, n_channels).T
        block = self._make_read_block(
            data_int, sample_count, mono_ns=mono_ns, read_started=read_started
        )
        await anyio.to_thread.run_sync(pool.requeue, raw)
        return block

    async def read_inprocess(self) -> DaqBlock | None:
        """Drain the currently-filling buffer without waiting for completion.

        The low-latency partial-buffer read for ``CONTINUOUS`` / ``FINITE``
        tasks on subsystems that advertise ``OLSSC_SUP_INPROCESSFLUSH`` —
        useful on slow tasks (e.g. 200 Hz TC, 1 kHz strain) where waiting for
        a full buffer is unacceptable. Backed by ``olDmCopyFromBuffer`` on the
        in-process HBUF; primes the pool on first use like :meth:`read_block`.

        The SDK transfers data in device-specific segment sizes, so the
        returned ``block.samples_per_channel`` is whatever was available — not
        necessarily a full buffer.

        Returns:
            A :class:`DaqBlock` for the samples available, or ``None`` when the
            in-process buffer holds zero valid samples.

        Raises:
            DtolCapabilityError: The subsystem does not support in-process flush.
            DtolTaskStateError: ``data_flow`` is not continuous/finite, or
                ``buffers`` is unconfigured.
        """
        if not self.capabilities.supports_inprocess_flush:
            raise DtolCapabilityError(
                "read_inprocess: subsystem does not advertise OLSSC_SUP_INPROCESSFLUSH; "
                "use read_block() or record() instead.",
                context=ErrorContext(
                    operation="DtolSession.read_inprocess", task_name=self.spec.name
                ),
            )
        if self.spec.buffers is None:
            raise DtolTaskStateError(
                "read_inprocess: requires TaskSpec.buffers to be configured",
                context=ErrorContext(
                    operation="DtolSession.read_inprocess", task_name=self.spec.name
                ),
            )
        await self._ensure_read_primed(self.spec.buffers.samples_per_buffer)
        pool = self._read_pool
        if pool is None:  # pragma: no cover - _ensure_read_primed sets it
            return None
        n_channels = max(pool.n_channels, 1)
        # The buffer the SDK is currently filling is the FIFO head of the pool.
        candidates = [b for b in pool.buffers if b.state == BufferState.QUEUED]
        if not candidates:
            return None
        raw = candidates[0]
        request_samples = pool.plan.samples_per_buffer * n_channels

        import numpy as np  # noqa: PLC0415

        read_started = datetime.now(UTC)
        mono_ns = time.monotonic_ns()
        data_bytes = await anyio.to_thread.run_sync(
            self.backend.copy_inprocess_buffer,
            raw.hbuf,
            request_samples,
            pool.sample_dtype_bytes,
        )
        if not data_bytes:
            return None
        dtype = np.int16 if pool.sample_dtype_bytes == 2 else np.int32  # noqa: PLR2004
        codes = np.frombuffer(data_bytes, dtype=dtype)
        sample_count = len(codes) // n_channels
        if sample_count <= 0:
            return None
        data_int = codes[: sample_count * n_channels].reshape(sample_count, n_channels).T
        return self._make_read_block(
            data_int, sample_count, mono_ns=mono_ns, read_started=read_started
        )

    async def _ensure_read_primed(self, samples_per_buffer: int) -> None:
        """Prime the synchronous read pool once (allocate → queue → start)."""
        if self._read_pool is not None:
            if self._read_samples_per_buffer != samples_per_buffer:
                raise DtolTaskStateError(
                    f"read pool already primed at {self._read_samples_per_buffer} "
                    f"samples/channel; cannot change to {samples_per_buffer} mid-session",
                    context=ErrorContext(
                        operation="DtolSession._ensure_read_primed", task_name=self.spec.name
                    ),
                )
            return
        if self.spec.data_flow not in {DataFlow.CONTINUOUS, DataFlow.FINITE}:
            raise DtolTaskStateError(
                f"read_block/read_inprocess require data_flow in {{CONTINUOUS, FINITE}}; "
                f"got {self.spec.data_flow.value}. Use poll() for single-value reads.",
                context=ErrorContext(
                    operation="DtolSession._ensure_read_primed", task_name=self.spec.name
                ),
            )
        if self.spec.buffers is None:
            raise DtolTaskStateError(
                "read_block/read_inprocess require TaskSpec.buffers to be configured",
                context=ErrorContext(
                    operation="DtolSession._ensure_read_primed", task_name=self.spec.name
                ),
            )

        # Continuous configure (channel list / clock / wrap) runs outside the
        # lock — it defers olDaConfig, which we issue below as config #1.
        if not self._committed:
            await self.configure()

        from dataclasses import replace  # noqa: PLC0415

        from dtollib.backend._buffer_pool import BufferPool  # noqa: PLC0415
        from dtollib.streaming.block import build_conversion_plan  # noqa: PLC0415

        async with self._lock:
            # Re-check under the lock — another task may have primed the pool
            # while we awaited configure(). (mypy narrows _read_pool to None
            # from the early guard above and can't model the concurrent set.)
            if self._read_pool is not None:
                return  # type: ignore[unreachable]
            hdass = self._require_hdass("DtolSession._ensure_read_primed")
            # Bench-proven continuous startup ordering (docs/decisions.md),
            # mirrored by record(): commit (olDaConfig #1) → register
            # notification → queue → arm (olDaConfig #2) → start. The DT9805/06
            # need the window + arm for buffer rotation even though this polled
            # path never reacts to the events — so we register a no-op.
            await anyio.to_thread.run_sync(self.backend.commit, hdass)
            self._read_conversion = build_conversion_plan(self, hdass)
            plan = replace(self.spec.buffers, samples_per_buffer=samples_per_buffer)
            pool = BufferPool(self.backend, hdass, plan, n_channels=len(self.spec.channels))
            pool.allocate()
            self._read_notify_handle = self.backend.register_notification(hdass, _noop_notify)
            pool.queue_all()
            await anyio.to_thread.run_sync(self.backend.arm, hdass)
            await anyio.to_thread.run_sync(self.backend.start, hdass)
            self._read_pool = pool
            self._read_samples_per_buffer = samples_per_buffer
            self._read_started_at = datetime.now(UTC)
            self._read_started_mono_ns = time.monotonic_ns()

    def _make_read_block(
        self,
        data_int: Any,
        sample_count: int,
        *,
        mono_ns: int,
        read_started: datetime,
    ) -> DaqBlock:
        """Build a :class:`DaqBlock` from a raw code block (shared by reads).

        Mirrors the continuous bridge's block construction: applies the
        code→engineering-units conversion plan when present (volts, and TC
        linearisation with CJC correction on application-linearising boards),
        else passes raw codes through as float64.
        """
        import numpy as np  # noqa: PLC0415

        channel_names = tuple(ch.display_name for ch in self.spec.channels)
        units: dict[str, str | None] = {
            ch.display_name: _channel_unit(ch) for ch in self.spec.channels
        }
        rate = self.spec.timing.rate_hz if self.spec.timing else None
        period_ns = round(1e9 / rate) if rate else None

        cjc_data: Any | None = None
        sensor_status: dict[str, Any] = {}
        is_linearised = False
        plan = self._read_conversion
        if plan is not None and data_int.dtype.kind in {"i", "u"}:
            from dtollib.capi.conversion import linearise_block  # noqa: PLC0415

            converted, cjc_data, row_masks = linearise_block(data_int, plan)
            data = converted
            is_linearised = True
            for row, mask in row_masks.items():
                if row < len(channel_names):
                    sensor_status[channel_names[row]] = mask
        else:
            data = data_int.astype(np.float64, copy=False)

        raw_codes = (
            data_int.astype(np.int32, copy=False) if data_int.dtype.kind in {"i", "u"} else None
        )
        read_finished = datetime.now(UTC)
        delta_ns = mono_ns - self._read_started_mono_ns
        started_at = self._read_started_at or read_started
        block_index = self._read_block_index
        first_sample = self._read_first_sample
        self._read_block_index += 1
        self._read_first_sample += sample_count

        return DaqBlock(
            device=self.spec.name,
            task=self.spec.name,
            channels=channel_names,
            data=data,
            raw_codes=raw_codes,
            cjc_data=cjc_data,
            block_index=block_index,
            first_sample_index=first_sample,
            samples_per_channel=sample_count,
            sample_rate_hz=rate,
            block_period_ns=period_ns,
            task_started_at=started_at,
            t0=started_at + timedelta(microseconds=delta_ns / 1000.0),
            t_mono_ns=mono_ns,
            t_utc=read_finished,
            t_midpoint_mono_ns=(
                mono_ns + (sample_count * period_ns) // 2 if period_ns is not None else None
            ),
            read_started_at=read_started,
            read_finished_at=read_finished,
            elapsed_s=(read_finished - read_started).total_seconds(),
            units=units,
            is_linearised=is_linearised,
            sensor_status=sensor_status,
        )

    # ---- Counter/timer reads ------------------------------------

    async def read_events(self, *, timeout: float | None = None) -> DaqReading:  # noqa: ASYNC109
        """Read the current counter value(s) across the task's channels.

        Valid for counter/timer + quadrature tasks (event counting,
        edge-to-edge interval, accumulated quadrature position).  Drives
        ``olDaReadEvents`` per channel and packages the counts into a
        :class:`DaqReading` so counter rows join sibling-library samples on
        ``(device, t_mono_ns)``.

        Returns:
            One :class:`DaqReading`; ``values`` maps each channel display
            name to its integer count.
        """
        return await self._counter_reading(
            self.backend.read_events,
            unit="counts",
            operation="DtolSession.read_events",
        )

    async def measure_frequency(self, *, timeout: float | None = None) -> DaqReading:  # noqa: ASYNC109
        """Measure input frequency (Hz) across the task's channels.

        Valid for counter-frequency + tachometer tasks.  Drives
        ``olDaMeasureFrequency`` per channel.

        Returns:
            One :class:`DaqReading`; ``values`` maps each channel display
            name to its measured frequency in hertz.
        """
        return await self._counter_reading(
            self.backend.measure_frequency,
            unit="Hz",
            operation="DtolSession.measure_frequency",
        )

    async def _counter_reading(
        self,
        reader: Any,
        *,
        unit: str,
        operation: str,
    ) -> DaqReading:
        """Shared counter read path for :meth:`read_events` / :meth:`measure_frequency`."""
        subsys_type = self.spec.subsystem_type or self.spec.infer_subsystem_type()
        if subsys_type not in _COUNTER_SUBSYSTEMS:
            raise DtolTaskStateError(
                f"{operation}: task subsystem {subsys_type.value} is not a counter "
                "subsystem; counter reads are valid only for counter/timer, "
                "quadrature, or tachometer tasks.",
                context=ErrorContext(operation=operation, task_name=self.spec.name),
            )

        await self.configure()

        async with self._lock:
            hdass = self._require_hdass(operation)
            requested_at = datetime.now(UTC)
            t_mono_request = time.monotonic_ns()

            await anyio.to_thread.run_sync(self.backend.start, hdass)
            values: dict[str, float | int | bool] = {}
            for channel in self.spec.channels:
                raw = await anyio.to_thread.run_sync(reader, hdass, channel.physical_channel)
                values[channel.display_name] = raw
            received_at = datetime.now(UTC)
            t_mono_received = time.monotonic_ns()

        midpoint_s = (requested_at.timestamp() + received_at.timestamp()) / 2.0
        t_utc = datetime.fromtimestamp(midpoint_s, tz=UTC)
        midpoint_mono_ns = (t_mono_request + t_mono_received) // 2
        latency_s = (received_at - requested_at).total_seconds()
        units: dict[str, str | None] = {
            ch.display_name: (ch.unit if ch.unit is not None else unit) for ch in self.spec.channels
        }

        return DaqReading(
            device=self.spec.name,
            task=self.spec.name,
            values=values,
            units=units,
            requested_at=requested_at,
            received_at=received_at,
            t_utc=t_utc,
            t_mono_ns=t_mono_request,
            t_midpoint_mono_ns=midpoint_mono_ns,
            latency_s=latency_s,
            metadata=dict(self.spec.metadata),
        )

    async def write(self, values: Mapping[str, float | bool], *, confirm: bool = False) -> None:
        """Single-value write to AO / DO channels with the §18 safety gate.

        Validation is **atomic and pre-SDK** (docs/design.md §18): every
        value is checked against its channel before *any* write reaches the
        backend, so a single bad value leaves the device untouched. The
        wrapper never silently clamps.

        Gate model (decided 2026-05-28; confirm-gate per design §18.1):

        - Unknown channel name → :class:`DtolValidationError`.
        - Value outside the device ``[min_val, max_val]`` → always
          :class:`DtolValidationError` (electrically impossible; ``confirm``
          does not override).
        - Value outside ``[safe_min, safe_max]`` (when set), **or** a channel
          with ``requires_confirm=True``, without ``confirm=True`` →
          :class:`DtolConfirmationRequiredError`.

        Args:
            values: Channel ``display_name`` → value. Floats for AO, bools
                for DO.
            confirm: Operator confirmation for safety-gated writes.

        Raises:
            DtolTaskStateError: ``data_flow`` is not ``SINGLE_VALUE`` (use
                :func:`~dtollib.streaming.play` for continuous AO), or the
                task is mid-shutdown.
            DtolValidationError: Unknown channel or out-of-device-range value.
            DtolConfirmationRequiredError: Safety gate tripped without confirm.
        """
        if self.spec.data_flow != DataFlow.SINGLE_VALUE:
            raise DtolTaskStateError(
                f"DtolSession.write: data_flow={self.spec.data_flow.value} is not "
                "valid for single-value writes; use play() for continuous AO.",
                context=ErrorContext(operation="DtolSession.write", task_name=self.spec.name),
            )

        await self.configure()

        # Resolve + validate EVERYTHING before any SDK call (atomic; never
        # clamp). Analog writes are one code per channel; digital writes are
        # accumulated per port into a single byte (whole-port writes plus
        # per-line bit merges over the shadow register).
        ao_writes, port_writes = self._plan_write(values, confirm=confirm)

        async with self._lock:
            hdass = self._require_hdass("DtolSession.write")
            caps = self.capabilities
            if (
                ao_writes
                and not port_writes
                and len(ao_writes) == len(self.spec.channels)
                and caps.supports_simultaneous_da
            ):
                codes = [code for _ch, code in ao_writes]
                await anyio.to_thread.run_sync(self.backend.put_single_values, hdass, codes, 1.0)
            else:
                for physical_channel, code in ao_writes:
                    await anyio.to_thread.run_sync(
                        self.backend.put_single_value, hdass, physical_channel, code, 1.0
                    )
                for port_channel, byte in port_writes.items():
                    await anyio.to_thread.run_sync(
                        self.backend.put_single_value, hdass, port_channel, byte, 1.0
                    )
                    self._dout_shadow[port_channel] = byte

    def _plan_write(
        self,
        values: Mapping[str, float | bool],
        *,
        confirm: bool,
    ) -> tuple[list[tuple[int, int]], dict[int, int]]:
        """Resolve a write request into AO codes and per-port DOUT bytes.

        Pure validation + encoding; no SDK calls. Applies the §18 safety gate
        per key (docs/design.md §18.1). Whole-port byte writes set the working
        byte; per-line writes then merge their bits over it (and over the
        shadow register, so untouched lines are preserved).

        Returns:
            ``(ao_writes, port_writes)`` — ``ao_writes`` is a list of
            ``(physical_channel, code)``; ``port_writes`` maps
            ``port_channel`` → the full byte to put.
        """
        from dtollib.channels.analog_output import AnalogOutputVoltage  # noqa: PLC0415
        from dtollib.channels.digital import (  # noqa: PLC0415
            DigitalLine,
            DigitalOutputPort,
        )

        # Build the key → target map: AO channel names, DOUT port names, and
        # per-line view keys.
        ao_by_name: dict[str, AnalogOutputVoltage] = {}
        port_by_name: dict[str, DigitalOutputPort] = {}
        line_by_key: dict[str, tuple[DigitalOutputPort, DigitalLine]] = {}
        for ch in self.spec.channels:
            if isinstance(ch, AnalogOutputVoltage):
                ao_by_name[ch.display_name] = ch
            elif isinstance(ch, DigitalOutputPort):
                port_by_name[ch.display_name] = ch
                for line, key in ((ln, ch.line_key(ln)) for ln in ch.lines):
                    line_by_key[key] = (ch, line)

        ao_writes: list[tuple[int, int]] = []
        # Working bytes per port, seeded lazily from the shadow register so a
        # partial write preserves untouched lines.
        port_working: dict[int, int] = {}
        # Process whole-port writes first so per-line writes refine on top.
        ordered = sorted(values.items(), key=lambda kv: kv[0] not in port_by_name)
        for name, value in ordered:
            if name in ao_by_name:
                ao_writes.append(self._plan_ao(ao_by_name[name], value, confirm=confirm))
            elif name in port_by_name:
                port = port_by_name[name]
                port_working[port.physical_channel] = self._plan_port_byte(
                    port, value, confirm=confirm
                )
            elif name in line_by_key:
                port, line = line_by_key[name]
                base = port_working.get(
                    port.physical_channel,
                    self._dout_shadow.get(port.physical_channel, 0),
                )
                port_working[port.physical_channel] = self._plan_line_bit(
                    port, line, value, base=base, confirm=confirm
                )
            else:
                raise DtolValidationError(
                    f"DtolSession.write: unknown channel {name!r}; "
                    f"writable keys: {sorted([*ao_by_name, *port_by_name, *line_by_key])}",
                    context=ErrorContext(operation="DtolSession.write", task_name=self.spec.name),
                )

        return ao_writes, port_working

    def _plan_ao(self, channel: Any, value: float | bool, *, confirm: bool) -> tuple[int, int]:
        """Gate + encode one analog-output write → ``(physical_channel, code)``."""
        ctx = ErrorContext(
            operation="DtolSession.write",
            task_name=self.spec.name,
            channel=channel.physical_channel,
            channel_name=channel.name,
        )
        v = float(value)
        gate_ao_samples(channel, lo=v, hi=v, confirm=confirm, ctx=ctx, op="DtolSession.write")
        return channel.physical_channel, ao_volts_to_code(v, channel.min_val, channel.max_val)

    def _plan_port_byte(self, port: Any, value: float | bool, *, confirm: bool) -> int:
        """Gate + range-check a whole-port byte write → the validated byte."""
        ctx = self._digital_ctx(port)
        self._gate_digital(port.requires_confirm, confirm=confirm, key=port.display_name, ctx=ctx)
        if isinstance(value, bool) or not isinstance(value, int):
            raise DtolValidationError(
                f"DtolSession.write: whole-port write to {port.display_name} expects an "
                f"int byte (0..2**width-1), got {value!r}; use the per-line keys for bools",
                context=ctx,
            )
        width = self._port_width(port)
        if not (0 <= value < (1 << width)):
            raise DtolValidationError(
                f"DtolSession.write: byte {value} is outside the {width}-bit port "
                f"{port.display_name} range [0, {(1 << width) - 1}]",
                context=ctx,
            )
        return value

    def _plan_line_bit(
        self, port: Any, line: Any, value: float | bool, *, base: int, confirm: bool
    ) -> int:
        """Gate + merge one per-line bool into ``base`` → the new port byte."""
        ctx = self._digital_ctx(port)
        requires_confirm = (
            line.requires_confirm if line.requires_confirm is not None else port.requires_confirm
        )
        self._gate_digital(requires_confirm, confirm=confirm, key=port.line_key(line), ctx=ctx)
        bit = int(line.bit)
        if bool(value):
            return base | (1 << bit)
        return base & ~(1 << bit)

    def _digital_ctx(self, port: Any) -> ErrorContext:
        return ErrorContext(
            operation="DtolSession.write",
            task_name=self.spec.name,
            channel=port.physical_channel,
            channel_name=port.name,
        )

    def _gate_digital(
        self, requires_confirm: bool, *, confirm: bool, key: str, ctx: ErrorContext
    ) -> None:
        """Raise :class:`DtolConfirmationRequiredError` if the gate trips."""
        from dtollib.errors import DtolConfirmationRequiredError  # noqa: PLC0415

        if requires_confirm and not confirm:
            raise DtolConfirmationRequiredError(
                f"DtolSession.write: {key} requires confirm=True",
                context=ctx,
            )

    def _port_width(self, port: Any) -> int:
        """Port width in bits — spec ``width`` if set, else ``capabilities.resolution``."""
        width = port.width if port.width is not None else self.capabilities.resolution
        if width <= 0:
            raise DtolValidationError(
                f"DtolSession.write: cannot determine width of port {port.display_name}; "
                "set DigitalOutputPort.width or ensure the subsystem reports a resolution",
                context=self._digital_ctx(port),
            )
        return width

    def _seed_dout_shadow(self) -> None:
        """Initialise the DOUT shadow register from each port's ``safe_value``."""
        from dtollib.channels.digital import DigitalOutputPort  # noqa: PLC0415

        for ch in self.spec.channels:
            if isinstance(ch, DigitalOutputPort):
                self._dout_shadow.setdefault(ch.physical_channel, ch.safe_value or 0)

    @property
    def queued_buffer_dones(self) -> int:
        """``olDaGetQueueSize(OL_QUE_DONE)`` — done-queue depth (synchronous)."""
        from dtollib.capi.constants import OL_QUE_DONE  # noqa: PLC0415

        # Direct synchronous backend call — query_size is non-blocking
        # and the session lock is unnecessary for a read-only probe.
        return self.backend.get_queue_size(self.hdass, OL_QUE_DONE)

    # ---- Async context manager -------------------------------------------

    async def __aenter__(self) -> Self:
        """Configure on entry.  Caller still calls :meth:`poll` explicitly."""
        await self.configure()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Best-effort close.  Uses ``abort`` to avoid deadlocking on a hung trigger."""
        del exc_type, exc, tb
        await self.close(graceful=False)

    # ---- Internals -------------------------------------------------------

    def _resolve_board_name(self) -> str:
        """Resolve the board name from ``spec.board`` or first-discovered."""
        if self.spec.board is not None:
            return self.spec.board
        boards = self.backend.enum_boards()
        if not boards:
            raise DtolValidationError(
                "DtolSession.prepare: no DT-Open Layers boards detected and "
                "TaskSpec.board is unset.",
                context=ErrorContext(
                    operation="DtolSession._resolve_board_name",
                    task_name=self.spec.name,
                ),
            )
        return boards[0].name

    @property
    def board_name(self) -> str:
        """Resolved board name for coordination layers."""
        return self._resolve_board_name()

    def _validate_against_capabilities(self, caps: CapabilitySet) -> None:
        """Check the spec against the live capability snapshot."""
        # Lazy import — analog_input depends on tasks.models, which imports
        # this module's package via tasks/__init__.py.
        from dtollib.channels.analog_input import ThermocoupleInput  # noqa: PLC0415

        # Single-value mode requires OLSSC_SUP_SINGLEVALUE.
        if self.spec.data_flow == DataFlow.SINGLE_VALUE and not caps.supports_singlevalue:
            raise DtolCapabilityError(
                f"Task {self.spec.name!r}: subsystem does not support single-value reads "
                "(OLSSC_SUP_SINGLEVALUE = false)",
                context=ErrorContext(
                    operation="DtolSession._validate_against_capabilities",
                    task_name=self.spec.name,
                ),
            )

        # Thermocouple channels: either the subsystem linearises in firmware
        # (returns_floats) or it exposes a TC front-end + CJC channel and we
        # linearise in software (supports_thermocouples).  See docs/decisions.md.
        for channel in self.spec.channels:
            if not isinstance(channel, ThermocoupleInput):
                continue
            if not (caps.returns_floats or caps.supports_thermocouples):
                raise DtolCapabilityError(
                    f"Task {self.spec.name!r}: ThermocoupleInput on channel "
                    f"{channel.physical_channel} requires a subsystem that either "
                    "linearises thermocouples in firmware (OLSSC_RETURNS_FLOATS) "
                    "or exposes a thermocouple front-end with a CJC channel "
                    "(OLSSC_SUP_THERMOCOUPLES); this subsystem reports neither.",
                    context=ErrorContext(
                        operation="DtolSession._validate_against_capabilities",
                        task_name=self.spec.name,
                        channel=channel.physical_channel,
                        channel_name=channel.name,
                    ),
                )
            if not caps.returns_floats:
                # Application-side path needs an implemented NIST polynomial.
                self._require_tc_polynomial(channel)

    def _require_tc_polynomial(self, channel: object) -> None:
        """Fail fast at configure-time if no ITS-90 polynomial exists for the type.

        The application-side path linearises in software via
        :func:`dtollib.utils.convert_volts_to_temperature`, which currently
        implements Types K and J. Probing it once here turns an unimplemented
        type into a clear configure-time error instead of a read-time surprise.
        """
        from dtollib.channels.analog_input import ThermocoupleInput  # noqa: PLC0415
        from dtollib.utils import convert_volts_to_temperature  # noqa: PLC0415

        if not isinstance(channel, ThermocoupleInput):
            return
        try:
            convert_volts_to_temperature(
                channel.thermocouple_type.value, 0.0, cjc_temperature_c=0.0
            )
        except DtolError as exc:
            raise DtolCapabilityError(
                f"Task {self.spec.name!r}: application-side linearisation for "
                f"Type-{channel.thermocouple_type.value} thermocouples is not "
                "implemented yet (only K and J ship today); see "
                f"dtollib.utils — channel {channel.physical_channel}.",
                context=ErrorContext(
                    operation="DtolSession._require_tc_polynomial",
                    task_name=self.spec.name,
                    channel=channel.physical_channel,
                    channel_name=channel.name,
                ),
            ) from exc

    async def _code_to_volts(self, hdass: int, code: int, gain: float) -> float:
        """Convert one raw ADC code to input volts at ``gain``.

        Wraps the backend's :meth:`code_to_volts` oracle off-thread (the real
        backend's conversion is pure/cached, but the fake takes a lock).
        Shared by the single-value voltage path and the application-side
        thermocouple path so both read engineering units the same way.
        """
        return await anyio.to_thread.run_sync(self.backend.code_to_volts, hdass, int(code), gain)

    async def _read_all_channels(  # noqa: PLR0912
        self,
    ) -> tuple[Mapping[str, float | int | bool], Mapping[str, SensorStatus]]:
        """Read every spec channel; populate sensor-status overlay."""
        from dtollib.channels.analog_input import (  # noqa: PLC0415
            AnalogInputBase,
            ThermocoupleInput,
        )

        hdass = self._require_hdass("DtolSession._read_all_channels")
        capabilities = self.capabilities

        # Application-side thermocouple path: a raw-code subsystem
        # (returns_floats False) with TC channels needs differential emf +
        # CJC + NIST ITS-90.  Handled separately because it mixes per-channel
        # gains (CJC at unity, TC at high gain) that the simultaneous reads
        # below cannot express.  See docs/decisions.md.
        if not capabilities.returns_floats and any(
            isinstance(c, ThermocoupleInput) for c in self.spec.channels
        ):
            return await self._read_all_channels_app_side_tc()

        # Branch on simultaneous sample-and-hold capability — drives a
        # single SDK call across all channels vs a per-channel loop.
        simultaneous = capabilities.supports_simultaneous_sh

        raw: list[float | int]
        if simultaneous and capabilities.returns_floats:
            floats = await anyio.to_thread.run_sync(self.backend.get_single_floats, hdass, 1.0)
            raw = list(floats)
        elif simultaneous:
            ints = await anyio.to_thread.run_sync(self.backend.get_single_values, hdass, 1.0)
            raw = list(ints)
        else:
            raw = []
            for channel in self.spec.channels:
                gain = channel.gain if isinstance(channel, AnalogInputBase) else 1.0
                if capabilities.returns_floats:
                    v_f = await anyio.to_thread.run_sync(
                        self.backend.get_single_float,
                        hdass,
                        channel.physical_channel,
                        gain,
                    )
                    raw.append(v_f)
                else:
                    v_i = await anyio.to_thread.run_sync(
                        self.backend.get_single_value,
                        hdass,
                        channel.physical_channel,
                        gain,
                    )
                    raw.append(v_i)

        # When the simultaneous read returns ``num_channels`` samples but
        # the task only configured a subset, index by ``physical_channel``.
        values: dict[str, float | int | bool] = {}
        sensors: dict[str, SensorStatus] = {}
        from dtollib.channels.digital import DigitalInputPort  # noqa: PLC0415

        for idx, channel in enumerate(self.spec.channels):
            raw_value = raw[channel.physical_channel] if simultaneous else raw[idx]

            sensor_status: SensorStatus = SensorStatus.OK
            display_value: float | int | bool

            if isinstance(channel, DigitalInputPort):
                # Digital read is a whole port byte; surface the raw int under
                # the port name plus one bool per declared line view.
                byte = int(raw_value)
                values[channel.display_name] = byte
                for line in channel.lines:
                    values[channel.line_key(line)] = bool((byte >> line.bit) & 1)
                continue

            if isinstance(channel, ThermocoupleInput):
                # TC sentinel detection — docs/design.md §13.1.
                sentinel = detect_thermocouple_sentinel(float(raw_value))
                if sentinel is not None:
                    sensor_status = SensorStatus(sentinel)
                    display_value = math.nan
                else:
                    display_value = float(raw_value)
            elif capabilities.returns_floats:
                display_value = float(raw_value)
            elif isinstance(channel, AnalogInputBase):
                # Int subsystem (DT9805/06): convert the raw ADC code to
                # engineering-unit volts via the SDK code_to_volts oracle, at
                # the gain the code was read at — 1.0 for the simultaneous
                # batch read, the channel's gain for the per-channel read — so
                # AnalogInputVoltage reads match the float and thermocouple
                # paths.  code_to_volts is a pure, cached conversion (no SDK
                # round-trip on the real backend), so the per-channel call is
                # cheap.  See docs/decisions.md.
                read_gain = 1.0 if simultaneous else channel.gain
                display_value = await self._code_to_volts(hdass, int(raw_value), read_gain)
            else:
                # Non-analog int channel (e.g. a raw port/counter code that
                # reaches this path): surface the integer unconverted.
                display_value = int(raw_value)

            values[channel.display_name] = display_value
            if sensor_status != SensorStatus.OK:
                sensors[channel.display_name] = sensor_status

        return values, sensors

    async def _read_all_channels_app_side_tc(
        self,
    ) -> tuple[Mapping[str, float | int | bool], Mapping[str, SensorStatus]]:
        """Read path for raw-code boards (DT9805/06): differential emf + CJC + ITS-90.

        Reads each channel's raw differential code at its configured gain and
        converts to input volts. Thermocouple channels are then linearised with
        their cold-junction sensor — read once per distinct CJC channel at unity
        gain (it sits near +0.25 V and would saturate at high gain). Open inputs
        (pegged at +full scale) become ``SENSOR_OPEN``; readings outside the
        channel envelope become ``TEMP_OUT_OF_RANGE_{LOW,HIGH}``. Non-TC channels
        on the same subsystem surface as plain volts. See docs/decisions.md.
        """
        from dtollib.channels.analog_input import (  # noqa: PLC0415
            AnalogInputBase,
            ThermocoupleInput,
        )

        hdass = self._require_hdass("DtolSession._read_all_channels_app_side_tc")
        cjc_cache: dict[int, float] = {}

        async def read_volts(channel_index: int, gain: float) -> float:
            code = await anyio.to_thread.run_sync(
                self.backend.get_single_value, hdass, channel_index, gain
            )
            return await self._code_to_volts(hdass, code, gain)

        async def cjc_degc(cjc_channel: int) -> float:
            if cjc_channel not in cjc_cache:
                volts = await read_volts(cjc_channel, _CJC_GAIN)
                cjc_cache[cjc_channel] = volts / _CJC_VOLTS_PER_DEGC
            return cjc_cache[cjc_channel]

        values: dict[str, float | int | bool] = {}
        sensors: dict[str, SensorStatus] = {}
        for channel in self.spec.channels:
            gain = channel.gain if isinstance(channel, AnalogInputBase) else 1.0
            volts = await read_volts(channel.physical_channel, gain)
            if isinstance(channel, ThermocoupleInput):
                status, value = self._linearise_tc(
                    channel, volts, await cjc_degc(channel.cjc_channel)
                )
            else:
                status, value = SensorStatus.OK, volts
            values[channel.display_name] = value
            if status != SensorStatus.OK:
                sensors[channel.display_name] = status
        return values, sensors

    def _linearise_tc(
        self, channel: Any, volts: float, cjc_degc: float
    ) -> tuple[SensorStatus, float]:
        """Linearise one thermocouple emf to ``(status, temperature °C)``.

        ``channel`` is a :class:`~dtollib.channels.analog_input.ThermocoupleInput`
        (typed ``Any`` to avoid the lazy-import dance the rest of this module
        uses). The caller guarantees the type.
        """
        from dtollib.utils import convert_volts_to_temperature  # noqa: PLC0415

        # Open circuit: the input is pulled to the +2.5 V reference and pegs the
        # ADC at +full scale, i.e. ~ +V_RAIL / gain referred to the input.
        if volts >= _OPEN_RAIL_FRACTION * (_BOARD_RAIL_VOLTS / channel.gain):
            return SensorStatus.SENSOR_OPEN, math.nan
        temp = convert_volts_to_temperature(
            channel.thermocouple_type.value, volts, cjc_temperature_c=cjc_degc
        )
        if temp < channel.min_val_degc:
            return SensorStatus.TEMP_OUT_OF_RANGE_LOW, math.nan
        if temp > channel.max_val_degc:
            return SensorStatus.TEMP_OUT_OF_RANGE_HIGH, math.nan
        return SensorStatus.OK, temp

    def _require_hdass(self, operation: str) -> int:
        if self._hdass is None:
            raise DtolTaskStateError(
                f"{operation}: HDASS unavailable before prepare()",
                context=ErrorContext(operation=operation, task_name=self.spec.name),
            )
        return self._hdass


def _channel_unit(channel: Any) -> str | None:
    """Best-effort unit string for a channel spec."""
    from dtollib.channels.analog_input import ThermocoupleInput  # noqa: PLC0415

    if channel.unit is not None:
        return str(channel.unit)
    if isinstance(channel, ThermocoupleInput):
        return "degC"
    # AnalogInputVoltage default.
    return "V"
