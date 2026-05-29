"""In-memory fake backend — :class:`FakeDtolBackend`.

Cross-platform, no SDK, no hardware.  Every unit test in
``tests/unit/`` exercises the same code paths as the real backend by
swapping in a :class:`FakeDtolBackend` instance.

The fake is **not a stub** — it enforces the same ordering and
capability rules as the real SDK so unit tests catch the same bugs
hardware would.  The fake's invariant table is the canonical
statement of contract; it grows monotonically as capabilities land.

Discovery / lifecycle / capability invariants:

- ``initialize(name)`` ref-counts HDRVRs; first call opens the
  device, last :meth:`terminate` closes it; intermediate calls just
  bump / decrement the refcount.
- ``get_dass(hdrvr, type, element)`` returns a stable handle until
  :meth:`release_dass` is called.  Re-acquiring after release is
  permitted (the SDK behaves the same way).
- :meth:`enum_subsystems` returns only the subsystems the fake board
  was constructed with.  Capability queries against an HDASS reflect
  the constructed-time CapabilitySet exactly.
- :meth:`get_version` returns the strings stored on the fake; default
  ``"fake-7.8.5"`` for both DLLs.

Construction helpers in :mod:`dtollib.testing` pre-populate the fake
with realistic DT9805 / DT9806 capability sets so downstream tests
stay short.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dtollib.errors import (
    DtolBackendError,
    DtolCapabilityError,
    DtolResourceError,
    DtolTaskStateError,
    ErrorContext,
)
from dtollib.system.models import BoardInfo, SubsystemInfo
from dtollib.tasks.models import BufferState, IOType, SdkEventKind, SubsystemState

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import numpy.typing as npt

    from dtollib.channels.analog_input import ThermocoupleType
    from dtollib.channels.base import ChannelSpec
    from dtollib.system.capabilities import CapabilitySet

__all__ = ["FakeBoard", "FakeDtolBackend", "FakeSubsystem"]


_READY_QUEUE_ID = 0
_INPROCESS_QUEUE_ID = 1
_DONE_QUEUE_ID = 2
_INT16_SAMPLE_BYTES = 2


def _empty_subsystems() -> list[FakeSubsystem]:
    return []


@dataclass(slots=True)
class FakeSubsystem:
    """One scripted subsystem on a :class:`FakeBoard`.

    Mutable: tests script per-subsystem behaviour by tweaking the
    fields after construction.  Capability set is required; the rest
    are derived from it where sensible.
    """

    type: int
    """Raw ``OLSS_*`` integer from :mod:`dtollib.capi.constants`."""

    element: int
    """Element index within the subsystem type on the parent board."""

    capabilities: CapabilitySet
    """Reported :class:`CapabilitySet`."""


@dataclass(slots=True)
class FakeBoard:
    """One scripted board.

    Mutable; constructed via :class:`FakeDtolBackend` arguments or via
    direct manipulation of :attr:`FakeDtolBackend.boards`.
    """

    name: str
    """Board name as returned by ``olDaEnumBoards``."""

    model: str = ""
    """Model identifier (e.g. ``"DT9805"``)."""

    driver_name: str = ""
    """Kernel-mode driver name from the registry."""

    instance: int = 0
    """Driver instance number."""

    subsystems: list[FakeSubsystem] = field(default_factory=_empty_subsystems)
    """Scripted subsystems on this board."""


class FakeDtolBackend:
    """In-memory backend satisfying :class:`~dtollib.backend.DtolBackend`.

    Construction takes a list of :class:`FakeBoard` instances.  An
    empty list represents "SDK installed but no boards plugged in" —
    the SDK contract for that state is "olDaEnumBoards returns
    success with zero callbacks", which translates to
    ``enum_boards() -> []`` here.

    Attributes:
        boards: List of scripted boards.  Mutable for tests.
        operations: Ordered log of ``(operation, payload)`` tuples
            for every method call.  Tests assert ordering by
            inspecting this list.
        scripted_failures: ``{operation_name: ECODE_int}`` — the next
            call to the named operation pops the entry and raises
            the matching typed exception.  Use :meth:`fail_next` to
            set up.
        oldaapi_version: Reported by :meth:`get_version`.  Defaults to
            ``"fake-7.8.5"``.
        olmem_version: Reported by :meth:`get_version`.
    """

    def __init__(  # noqa: PLR0915 - flat field initialisation, no branching.
        self,
        boards: list[FakeBoard] | None = None,
        *,
        oldaapi_version: str = "fake-7.8.5",
        olmem_version: str = "fake-7.8.5",
    ) -> None:
        self.boards: list[FakeBoard] = list(boards) if boards else []
        self.operations: list[tuple[str, object]] = []
        self.scripted_failures: dict[str, int] = {}
        self.oldaapi_version = oldaapi_version
        self.olmem_version = olmem_version

        self._lock = threading.RLock()

        # HDRVR refcounting mirrors DataAcqBackend exactly.
        self._next_hdrvr: int = 1
        self._open_devices: dict[str, tuple[int, int]] = {}  # name -> (hdrvr, refcount)
        self._hdrvr_to_name: dict[int, str] = {}

        # HDASS bookkeeping.  Each acquired HDASS maps back to its
        # parent board + subsystem so subsequent ``query_capabilities``
        # calls return the right :class:`CapabilitySet`.
        self._next_hdass: int = 0x10_0000  # high to make confusion with HDRVRs unlikely
        self._open_subsystems: dict[int, FakeSubsystem] = {}

        # Subsystem state machine.  Per-HDASS state tracking
        # so the fake rejects invalid transitions the same way the SDK
        # does.  Tests inspect ``state_of(hdass)`` to assert exact
        # SubsystemState transitions.
        self._states: dict[int, SubsystemState] = {}

        # MULTI_SENSOR ordering: the fake refuses any per-type
        # setter on a MULTI_SENSOR channel until ``set_multi_sensor_type``
        # has been called on that channel.  Set of (hdass, physical_channel)
        # tuples — once added, per-type setters are allowed.
        self._multi_sensor_typed: set[tuple[int, int]] = set()

        # Scripted reads.  ``scalar_values[(hdass, channel)]``
        # is the next value returned by ``get_single_value``/``get_single_float``.
        # ``thermocouple_sentinels[(hdass, channel)]`` causes the next TC
        # read to return the SDK-style sentinel float for that condition.
        self.scalar_values: dict[tuple[int, int], float | int] = {}
        self.thermocouple_sentinels: dict[tuple[int, int], str] = {}

        # TC type tracked per-channel (so tests can verify the
        # builder issued ``set_thermocouple_type``).
        self.thermocouple_types: dict[tuple[int, int], str] = {}

        # MULTI_SENSOR re-typing tracked per-channel (so tests can
        # verify the builder issued ``set_multi_sensor_type`` with the right
        # IOType), and the spec recorded per channel for configure-path asserts.
        self.multi_sensor_types: dict[tuple[int, int], IOType] = {}
        self.multi_sensor_specs: dict[tuple[int, int], ChannelSpec] = {}

        # Scriptable TEDS payloads keyed by (hdass, channel) for
        # hardware reads, and by virtual-TEDS file path for virtual reads.
        self.strain_gage_teds: dict[tuple[int, int], dict[str, object]] = {}
        self.bridge_sensor_teds: dict[tuple[int, int], dict[str, object]] = {}
        self.virtual_strain_gage_teds: dict[str, dict[str, object]] = {}
        self.virtual_bridge_sensor_teds: dict[str, dict[str, object]] = {}

        # ---- Continuous (AI streaming) -----------------------------------
        # Recorded data-flow mode per HDASS — used by ``commit`` to branch
        # between CONFIGURED_FOR_SINGLE_VALUE and CONFIGURED_FOR_CONTINUOUS.
        self._data_flow_mode: dict[int, int] = {}
        # Tracked channel list for continuous-mode HDASSes.
        self._channel_lists: dict[int, list[int]] = {}
        # Requested clock rate (rate-back via get_clock_frequency).
        self._clock_rate_hz: dict[int, float] = {}
        # Wrap mode + trigger kind + dma_usage — recorded for tests.
        self._wrap_mode: dict[int, int] = {}
        self._trigger_kind: dict[int, int] = {}
        # ``hdass`` → registered NOTIFY_PROC-style callable, or None when
        # un-registered.  Pinned here so GC cannot drop it mid-acquisition.
        self._notify_callbacks: dict[int, Callable[[int, int, int], int] | None] = {}
        # Buffer-pool state per HBUF.
        self._next_hbuf: int = 0x80_0000
        self._hbuf_state: dict[int, BufferState] = {}
        self._hbuf_owner: dict[int, int] = {}  # hbuf -> hdass
        self._hbuf_payload: dict[int, npt.NDArray[Any]] = {}
        self._hbuf_valid_samples: dict[int, int] = {}
        # HBUFs that have been ``copy_to_buffer``-filled and not yet consumed
        # by ``put_buffer`` — encodes the Fill-before-Queue invariant for D/A
        # (output) subsystems (mirrors the real SDK's "queue an empty DAC
        # buffer → silence/garbage" failure as a loud error).
        self._filled_hbufs: set[int] = set()
        # Per-HDASS queues (Ready / Inprocess / Done).
        self._ready_queue: dict[int, deque[int]] = {}
        self._inprocess: dict[int, int | None] = {}
        self._done_queue: dict[int, deque[int]] = {}
        # Whether ``commit`` (config #1) has run for this HDASS.
        self._configured: set[int] = set()
        # Whether ``arm`` (config #2) has run — continuous tasks must be
        # armed before ``start`` (enforces register/queue-before-arm).
        self._armed: set[int] = set()

        # ---- Output (AO / DO / DIO) --------------------------------------
        # Last value written to each output channel via ``put_single_value`` /
        # ``put_single_values`` — tests assert the wrapper wrote what it should.
        self.written_values: dict[tuple[int, int], int] = {}
        # Last simultaneous write per HDASS (channel-list order).
        self.written_value_lists: dict[int, list[int]] = {}
        # Muted state per HDASS (continuous AO).
        self._muted: dict[int, bool] = {}
        # Synchronous-digital-I/O usage + list entries (DIN/DOUT config).
        self._sync_dio_usage: dict[int, bool] = {}
        self._dio_list: dict[int, list[tuple[int, int]]] = {}

        # ---- Counter/timer + simultaneous start --------------------------
        # Counter/timer configuration recorded per HDASS.
        self._ct_mode: dict[int, int] = {}
        # HDASSes that have had set_ct_mode called — gate/pulse/measure/cascade
        # setters require it first (mirrors the MULTI_SENSOR ordering guard).
        self._ct_mode_set: set[int] = set()
        self._triggered_scan: dict[int, dict[str, object]] = {}
        # Scripted counter reads.  ``scripted_counts[(hdass, channel)]`` is the
        # next value returned by ``read_events``; ``scripted_frequencies`` for
        # ``measure_frequency``.  Tests set these to drive deterministic reads.
        self.scripted_counts: dict[tuple[int, int], int] = {}
        self.scripted_frequencies: dict[tuple[int, int], float] = {}
        # Simultaneous-start lists.  ``_ss_lists[hsslist]`` = member HDASSes;
        # ``_ss_prestarted`` tracks which lists have been pre-started.
        self._next_hsslist: int = 0x40_0000
        self._ss_lists: dict[int, list[int]] = {}
        self._ss_prestarted: set[int] = set()

    # ---- Test scripting --------------------------------------------------

    def fail_next(self, operation: str, *, code: int) -> None:
        """Cause the next call to ``operation`` to raise.

        Args:
            operation: Operation name as logged in
                :attr:`operations` (e.g. ``"initialize"``).
            code: SDK-style ECODE.  Used to construct an
                :class:`~dtollib.errors.ErrorContext`; the raised
                exception is selected via
                :func:`~dtollib.capi.errors.classify` so tests
                exercise the same routing as the real backend.
        """
        self.scripted_failures[operation] = code

    def _consume_scripted(self, operation: str) -> None:
        """Pop and raise a scripted failure if one is queued."""
        code = self.scripted_failures.pop(operation, None)
        if code is None:
            return
        from dtollib.capi.errors import classify  # noqa: PLC0415

        cls = classify(code)
        raise cls(
            f"scripted failure: {operation} (ECODE={code})",
            context=ErrorContext(
                operation=operation,
                ecode=code,
                ecode_source="oldaapi",
            ),
        )

    def _log(self, operation: str, payload: object = None) -> None:
        self.operations.append((operation, payload))

    # ---- Version ---------------------------------------------------------

    def get_version(self) -> tuple[str, str]:
        """Satisfies :meth:`DtolBackend.get_version` — returns scripted strings."""
        with self._lock:
            self._log("get_version")
            self._consume_scripted("get_version")
            return (self.oldaapi_version, self.olmem_version)

    # ---- Board enumeration -----------------------------------------------

    def enum_boards(self) -> list[BoardInfo]:
        """Satisfies :meth:`DtolBackend.enum_boards` — returns scripted boards."""
        with self._lock:
            self._log("enum_boards")
            self._consume_scripted("enum_boards")
            return [
                BoardInfo(
                    name=b.name,
                    model=b.model or b.name,
                    driver_name=b.driver_name,
                    instance=b.instance,
                )
                for b in self.boards
            ]

    def _find_board(self, name: str) -> FakeBoard:
        for board in self.boards:
            if board.name == name:
                return board
        raise DtolBackendError(
            f"fake backend: unknown board {name!r}",
            context=ErrorContext(operation="enum_subsystems", board=name),
        )

    # ---- Device lifecycle ------------------------------------------------

    def initialize(self, board_name: str) -> int:
        """Satisfies :meth:`DtolBackend.initialize` — ref-counted fake HDRVR."""
        with self._lock:
            self._log("initialize", board_name)
            self._consume_scripted("initialize")

            # Validate the board exists; mirrors SDK behaviour.
            self._find_board(board_name)

            existing = self._open_devices.get(board_name)
            if existing is not None:
                hdrvr, refcount = existing
                self._open_devices[board_name] = (hdrvr, refcount + 1)
                return hdrvr

            hdrvr = self._next_hdrvr
            self._next_hdrvr += 1
            self._open_devices[board_name] = (hdrvr, 1)
            self._hdrvr_to_name[hdrvr] = board_name
            return hdrvr

    def terminate(self, hdrvr: int) -> None:
        """Satisfies :meth:`DtolBackend.terminate` — refcount-aware fake close."""
        with self._lock:
            self._log("terminate", hdrvr)
            self._consume_scripted("terminate")

            name = self._hdrvr_to_name.get(hdrvr)
            if name is None:
                raise DtolResourceError(
                    f"fake backend: unknown HDRVR {hdrvr}",
                    context=ErrorContext(operation="terminate"),
                )
            current_hdrvr, refcount = self._open_devices[name]
            if current_hdrvr != hdrvr:
                raise DtolResourceError(
                    f"fake backend: HDRVR {hdrvr} does not match the open handle for {name!r}",
                    context=ErrorContext(operation="terminate", board=name),
                )
            if refcount > 1:
                self._open_devices[name] = (hdrvr, refcount - 1)
                return
            del self._open_devices[name]
            del self._hdrvr_to_name[hdrvr]

    # ---- Subsystem reservation -------------------------------------------

    def enum_subsystems(self, board_name: str) -> list[SubsystemInfo]:
        """Satisfies :meth:`DtolBackend.enum_subsystems` — scripted subsystems."""
        with self._lock:
            self._log("enum_subsystems", board_name)
            self._consume_scripted("enum_subsystems")

            from dtollib.backend.dataacq import (  # noqa: PLC0415
                SUBSYS_TYPE_TO_ENUM,
            )

            board = self._find_board(board_name)
            return [
                SubsystemInfo(
                    type=SUBSYS_TYPE_TO_ENUM[sub.type],
                    element=sub.element,
                    num_channels=sub.capabilities.num_channels,
                    supports_singlevalue=sub.capabilities.supports_singlevalue,
                    supports_continuous=sub.capabilities.supports_continuous,
                    supports_simultaneous_sh=sub.capabilities.supports_simultaneous_sh,
                    supports_multisensor=sub.capabilities.supports_multisensor,
                    supports_dma=sub.capabilities.supports_dma,
                    returns_floats=sub.capabilities.returns_floats,
                    max_throughput_hz=sub.capabilities.max_throughput_hz,
                    cgl_depth=sub.capabilities.cgl_depth,
                )
                for sub in board.subsystems
            ]

    def get_dass(self, hdrvr: int, subsystem_type: int, element: int) -> int:
        """Satisfies :meth:`DtolBackend.get_dass` — returns synthetic HDASS."""
        with self._lock:
            self._log("get_dass", (hdrvr, subsystem_type, element))
            self._consume_scripted("get_dass")

            name = self._hdrvr_to_name.get(hdrvr)
            if name is None:
                raise DtolResourceError(
                    f"fake backend: get_dass on unknown HDRVR {hdrvr}",
                    context=ErrorContext(operation="get_dass"),
                )
            board = self._find_board(name)
            for sub in board.subsystems:
                if sub.type == subsystem_type and sub.element == element:
                    hdass = self._next_hdass
                    self._next_hdass += 1
                    self._open_subsystems[hdass] = sub
                    return hdass
            raise DtolResourceError(
                f"fake backend: no subsystem (type={subsystem_type}, "
                f"element={element}) on {name!r}",
                context=ErrorContext(
                    operation="get_dass",
                    board=name,
                    element=element,
                    extra={"subsys_type": subsystem_type},
                ),
            )

    def release_dass(self, hdass: int) -> None:
        """Satisfies :meth:`DtolBackend.release_dass` — forgets the HDASS."""
        with self._lock:
            self._log("release_dass", hdass)
            self._consume_scripted("release_dass")
            self._open_subsystems.pop(hdass, None)

    # ---- Capability query ------------------------------------------------

    def query_capabilities(self, hdass: int) -> CapabilitySet:
        """Satisfies :meth:`DtolBackend.query_capabilities` — returns the scripted set."""
        with self._lock:
            self._log("query_capabilities", hdass)
            self._consume_scripted("query_capabilities")
            sub = self._open_subsystems.get(hdass)
            if sub is None:
                raise DtolResourceError(
                    f"fake backend: query_capabilities on unknown HDASS {hdass}",
                    context=ErrorContext(operation="query_capabilities"),
                )
            return sub.capabilities

    # ---- State-machine accessor for tests -----------------------

    def state_of(self, hdass: int) -> SubsystemState:
        """Current scripted :class:`SubsystemState` for ``hdass``.

        Defaults to ``INITIALIZED`` for an open-but-untouched HDASS.
        Tests use this to assert exact transitions.
        """
        with self._lock:
            return self._states.get(hdass, SubsystemState.INITIALIZED)

    def script_state(self, hdass: int, state: SubsystemState) -> None:
        """Force a scripted state for tests."""
        with self._lock:
            self._require_open(hdass, op="script_state")
            self._set_state(hdass, state)

    def _require_open(self, hdass: int, *, op: str) -> FakeSubsystem:
        sub = self._open_subsystems.get(hdass)
        if sub is None:
            raise DtolResourceError(
                f"fake backend: {op} on unknown HDASS {hdass}",
                context=ErrorContext(operation=op),
            )
        return sub

    def _require_channel_in_range(self, sub: FakeSubsystem, channel: int, *, op: str) -> None:
        """Reject a channel index past ``num_channels`` with SDK-style ECODE 7.

        Mirrors the real "Invalid Channel" failure. Crucially, a digital
        subsystem exposes one *port* per channel (the DT9805/06 has a single
        8-bit port at channel 0); addressing a line as channel N>=num_channels
        is exactly the bug that the per-line spec model produced on hardware.
        """
        n = sub.capabilities.num_channels
        if n and (channel < 0 or channel >= n):
            raise DtolBackendError(
                f"fake backend: {op} channel {channel} is out of range "
                f"[0, {n - 1}] (ECODE 7, Invalid Channel)",
                context=ErrorContext(operation=op, channel=channel, ecode=7),
            )

    def _set_state(self, hdass: int, state: SubsystemState) -> None:
        self._states[hdass] = state

    # ---- Configuration setters ----------------------------------

    def set_data_flow(self, hdass: int, mode: int) -> None:
        """Track data-flow mode via fake state.  Records the call."""
        with self._lock:
            self._log("set_data_flow", (hdass, mode))
            self._consume_scripted("set_data_flow")
            self._require_open(hdass, op="set_data_flow")
            self._data_flow_mode[hdass] = mode

    def set_multi_sensor_type(
        self,
        hdass: int,
        physical_channel: int,
        io_type: IOType,
    ) -> None:
        """Re-type a MULTI_SENSOR channel.  Marks it eligible for per-type setters."""
        with self._lock:
            self._log("set_multi_sensor_type", (hdass, physical_channel, io_type))
            self._consume_scripted("set_multi_sensor_type")
            self._require_open(hdass, op="set_multi_sensor_type")
            self._multi_sensor_typed.add((hdass, physical_channel))
            self.multi_sensor_types[(hdass, physical_channel)] = io_type

    def add_channel(
        self,
        hdass: int,
        list_index: int,
        spec: ChannelSpec,
    ) -> None:
        """Add a channel to the channel/gain list, enforcing MULTI_SENSOR ordering."""
        from dtollib.channels.analog_input import (  # noqa: PLC0415
            ThermocoupleInput,
        )

        with self._lock:
            self._log("add_channel", (hdass, list_index, type(spec).__name__))
            self._consume_scripted("add_channel")
            sub = self._require_open(hdass, op="add_channel")

            # MULTI_SENSOR ordering invariant (docs/design.md §8.5a).
            if sub.capabilities.supports_multisensor:
                key = (hdass, spec.physical_channel)
                if key not in self._multi_sensor_typed:
                    raise DtolTaskStateError(
                        f"fake backend: add_channel on MULTI_SENSOR channel "
                        f"{spec.physical_channel} before set_multi_sensor_type; "
                        f"see docs/design.md §8.5a (silent wrong-data bug)",
                        context=ErrorContext(
                            operation="add_channel",
                            channel=spec.physical_channel,
                            channel_name=spec.name,
                        ),
                    )
                self.multi_sensor_specs[(hdass, spec.physical_channel)] = spec

            if isinstance(spec, ThermocoupleInput):
                self.thermocouple_types[(hdass, spec.physical_channel)] = (
                    spec.thermocouple_type.value
                )

    def set_stop_on_error(self, hdass: int, stop: bool) -> None:
        """Track the stop-on-error setting; no state change."""
        with self._lock:
            self._log("set_stop_on_error", (hdass, stop))
            self._consume_scripted("set_stop_on_error")
            self._require_open(hdass, op="set_stop_on_error")

    def commit(self, hdass: int) -> None:
        """``olDaConfig`` #1 — INITIALIZED → CONFIGURED_FOR_{SINGLE_VALUE|CONTINUOUS}.

        This is config #1: it runs after channel/clock/wrap setup and
        before the notification window + Ready queue are wired. It does
        NOT require a registered notification or queued buffers — those are
        preconditions of :meth:`arm` (config #2), matching the bench-proven
        ordering (docs/decisions.md).
        """
        from dtollib.capi.constants import OL_DF_SINGLEVALUE  # noqa: PLC0415

        with self._lock:
            self._log("commit", hdass)
            self._consume_scripted("commit")
            self._require_open(hdass, op="commit")
            current = self.state_of(hdass)
            if current not in {SubsystemState.INITIALIZED, SubsystemState.IO_COMPLETE}:
                raise DtolTaskStateError(
                    f"fake backend: commit invalid from state {current.value}",
                    context=ErrorContext(operation="commit"),
                )
            mode = self._data_flow_mode.get(hdass, OL_DF_SINGLEVALUE)
            if mode == OL_DF_SINGLEVALUE:
                self._set_state(hdass, SubsystemState.CONFIGURED_FOR_SINGLE_VALUE)
            else:
                self._set_state(hdass, SubsystemState.CONFIGURED_FOR_CONTINUOUS)
            self._armed.discard(hdass)
            self._configured.add(hdass)

    def arm(self, hdass: int) -> None:
        """``olDaConfig`` #2 — continuous mode only; arm for ``start``.

        Enforces the bench-proven ordering: ``arm`` runs after the
        notification window is wired (:meth:`register_notification`) and
        the Ready queue is seeded. It raises if either precondition is
        unmet — register-BEFORE-arm and queue-BEFORE-arm — and refuses on a
        single-value subsystem (which has no second config).
        """
        with self._lock:
            self._log("arm", hdass)
            self._consume_scripted("arm")
            self._require_open(hdass, op="arm")
            if self.state_of(hdass) != SubsystemState.CONFIGURED_FOR_CONTINUOUS:
                raise DtolTaskStateError(
                    "fake backend: arm requires a committed continuous task "
                    f"(state is {self.state_of(hdass).value})",
                    context=ErrorContext(operation="arm"),
                )
            if self._notify_callbacks.get(hdass) is None:
                raise DtolTaskStateError(
                    "fake backend: arm BEFORE register_notification "
                    "(violates register-before-arm ordering)",
                    context=ErrorContext(operation="arm"),
                )
            if not self._ready_queue.get(hdass):
                raise DtolTaskStateError(
                    "fake backend: arm BEFORE queueing buffers "
                    "(violates queue-before-arm ordering)",
                    context=ErrorContext(operation="arm"),
                )
            self._armed.add(hdass)

    def set_thermocouple_type(
        self,
        hdass: int,
        channel: int,
        tc_type: ThermocoupleType,
    ) -> None:
        """Record TC type per channel for test inspection."""
        with self._lock:
            self._log("set_thermocouple_type", (hdass, channel, tc_type))
            self._consume_scripted("set_thermocouple_type")
            self._require_open(hdass, op="set_thermocouple_type")
            self.thermocouple_types[(hdass, channel)] = tc_type.value

    def set_return_cjc_in_stream(self, hdass: int, enable: bool) -> None:
        """Record the interleaved-CJC setting for assertion by tests."""
        with self._lock:
            self._log("set_return_cjc_in_stream", (hdass, enable))
            self._consume_scripted("set_return_cjc_in_stream")
            self._require_open(hdass, op="set_return_cjc_in_stream")

    # ---- Volts → engineering conversions (§8.B6) ----------------

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
        """Deterministic quarter-bridge strain approximation for tests.

        ε ≈ -4·Vr / GF with Vr = (Vstrained - Vunstrained) / Vexcitation.
        Not physically exact (lead/Poisson/shunt ignored) — it only needs to
        be deterministic so wiring tests can assert a stable value.
        """
        del config, gage_resistance, lead_resistance, poisson_ratio, shunt_correction
        self._log("volts_to_strain", (v_unstrained, v_strained, v_excitation, gage_factor))
        self._consume_scripted("volts_to_strain")
        if v_excitation == 0.0 or gage_factor == 0.0:
            return 0.0
        vr = (v_strained - v_unstrained) / v_excitation
        return -4.0 * vr / gage_factor

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
        """Deterministic bridge-sensor approximation for tests.

        value ≈ (Vr / rated_output) × full-scale, with Vr in mV/V; here
        simplified to ``Vr·1000 / rated_output_mv_per_v``.
        """
        del temperature_coefficient, gage_resistance, lead_resistance, shunt_correction
        self._log("volts_to_bridge_based_sensor", (v_unstrained, v_strained, v_excitation))
        self._consume_scripted("volts_to_bridge_based_sensor")
        if v_excitation == 0.0 or rated_output_mv_per_v == 0.0:
            return 0.0
        vr_mv_per_v = (v_strained - v_unstrained) / v_excitation * 1000.0
        return vr_mv_per_v / rated_output_mv_per_v

    # ---- TEDS readers (§8.B5) -----------------------------------

    def read_strain_gage_hardware_teds(self, hdass: int, channel: int) -> dict[str, object]:
        """Return the scripted strain-gage TEDS for ``(hdass, channel)``."""
        with self._lock:
            self._log("read_strain_gage_hardware_teds", (hdass, channel))
            self._consume_scripted("read_strain_gage_hardware_teds")
            self._require_open(hdass, op="read_strain_gage_hardware_teds")
            return self._teds_or_unsupported(
                self.strain_gage_teds.get((hdass, channel)),
                op="read_strain_gage_hardware_teds",
                channel=channel,
            )

    def read_strain_gage_virtual_teds(self, path: str) -> dict[str, object]:
        """Return the scripted strain-gage virtual TEDS for ``path``."""
        with self._lock:
            self._log("read_strain_gage_virtual_teds", path)
            self._consume_scripted("read_strain_gage_virtual_teds")
            return self._teds_or_unsupported(
                self.virtual_strain_gage_teds.get(path),
                op="read_strain_gage_virtual_teds",
            )

    def read_bridge_sensor_hardware_teds(self, hdass: int, channel: int) -> dict[str, object]:
        """Return the scripted bridge TEDS for ``(hdass, channel)``."""
        with self._lock:
            self._log("read_bridge_sensor_hardware_teds", (hdass, channel))
            self._consume_scripted("read_bridge_sensor_hardware_teds")
            self._require_open(hdass, op="read_bridge_sensor_hardware_teds")
            return self._teds_or_unsupported(
                self.bridge_sensor_teds.get((hdass, channel)),
                op="read_bridge_sensor_hardware_teds",
                channel=channel,
            )

    def read_bridge_sensor_virtual_teds(self, path: str) -> dict[str, object]:
        """Return the scripted bridge virtual TEDS for ``path``."""
        with self._lock:
            self._log("read_bridge_sensor_virtual_teds", path)
            self._consume_scripted("read_bridge_sensor_virtual_teds")
            return self._teds_or_unsupported(
                self.virtual_bridge_sensor_teds.get(path),
                op="read_bridge_sensor_virtual_teds",
            )

    @staticmethod
    def _teds_or_unsupported(
        payload: dict[str, object] | None,
        *,
        op: str,
        channel: int | None = None,
    ) -> dict[str, object]:
        """Return ``payload`` or raise ec=36 — mimics a board with no TEDS sensor."""
        if payload is None:
            raise DtolCapabilityError(
                f"fake backend: no scripted TEDS for {op}; the owned DT9805/06 "
                f"cannot read TEDS (ECODE 36)",
                context=ErrorContext(operation=op, channel=channel, ecode=36),
            )
        return dict(payload)

    # ---- Lifecycle ----------------------------------------------

    def start(self, hdass: int) -> None:
        """Transition CONFIGURED_* → RUNNING (idempotent on already-RUNNING)."""
        with self._lock:
            self._log("start", hdass)
            self._consume_scripted("start")
            self._require_open(hdass, op="start")
            current = self.state_of(hdass)
            if current == SubsystemState.RUNNING:
                # SDK is permissive on redundant starts; matches that behaviour.
                return
            if current not in {
                SubsystemState.CONFIGURED_FOR_SINGLE_VALUE,
                SubsystemState.CONFIGURED_FOR_CONTINUOUS,
                SubsystemState.PRESTARTED,
                SubsystemState.IO_COMPLETE,
            }:
                raise DtolTaskStateError(
                    f"fake backend: start invalid from state {current.value}; "
                    "commit() must run first",
                    context=ErrorContext(operation="start"),
                )
            # Continuous tasks must be armed (config #2) before start — this
            # is where a missing register/queue/arm surfaces loudly instead
            # of hanging with buffers stuck INPROCESS on hardware.
            if current == SubsystemState.CONFIGURED_FOR_CONTINUOUS and hdass not in self._armed:
                raise DtolTaskStateError(
                    "fake backend: start BEFORE arm (continuous tasks need the "
                    "second olDaConfig via arm() after register + queue)",
                    context=ErrorContext(operation="start"),
                )
            self._set_state(hdass, SubsystemState.RUNNING)

    def stop(self, hdass: int) -> None:
        """Transition RUNNING → STOPPING → IO_COMPLETE → INITIALIZED."""
        with self._lock:
            self._log("stop", hdass)
            self._consume_scripted("stop")
            self._require_open(hdass, op="stop")
            current = self.state_of(hdass)
            if current != SubsystemState.RUNNING:
                # SDK is permissive here; matching its behaviour for tests
                # would mean accepting redundant stops as no-ops.
                if current in {SubsystemState.INITIALIZED, SubsystemState.IO_COMPLETE}:
                    return
                raise DtolTaskStateError(
                    f"fake backend: stop invalid from state {current.value}",
                    context=ErrorContext(operation="stop"),
                )
            self._set_state(hdass, SubsystemState.IO_COMPLETE)

    def abort(self, hdass: int) -> None:
        """Transition RUNNING → ABORTING → INITIALIZED."""
        with self._lock:
            self._log("abort", hdass)
            self._consume_scripted("abort")
            self._require_open(hdass, op="abort")
            current = self.state_of(hdass)
            # Abort is idempotent / always valid; matches SDK behaviour.
            if current == SubsystemState.RUNNING:
                self._set_state(hdass, SubsystemState.INITIALIZED)

    def get_state(self, hdass: int) -> SubsystemState:
        """Return the tracked :class:`SubsystemState`."""
        with self._lock:
            self._require_open(hdass, op="get_state")
            return self.state_of(hdass)

    def is_running(self, hdass: int) -> bool:
        """Derived from :meth:`state_of`."""
        return self.state_of(hdass) == SubsystemState.RUNNING

    # ---- Single-value reads -------------------------------------

    def _sentinel_float(self, status: str) -> float:
        """SDK-style sentinel floats used by the fake.

        Specific values are arbitrary — what matters is that the
        :func:`~dtollib.capi.conversion.detect_thermocouple_sentinel`
        helper maps the same magic floats back to a SensorStatus.  Tests
        use the corresponding helper in :mod:`dtollib.testing` so the
        round-trip is exact.
        """
        return _TC_SENTINEL_FLOATS[status]

    def get_single_value(self, hdass: int, channel: int, gain: float) -> int:
        """Return scripted ``scalar_values[(hdass, channel)]`` cast to int."""
        with self._lock:
            self._log("get_single_value", (hdass, channel, gain))
            self._consume_scripted("get_single_value")
            sub = self._require_open(hdass, op="get_single_value")
            self._require_channel_in_range(sub, channel, op="get_single_value")
            value = self.scalar_values.get((hdass, channel), 0)
            return int(value)

    def get_single_float(self, hdass: int, channel: int, gain: float) -> float:
        """Return scripted scalar or TC sentinel for the channel."""
        with self._lock:
            self._log("get_single_float", (hdass, channel, gain))
            self._consume_scripted("get_single_float")
            sub = self._require_open(hdass, op="get_single_float")
            self._require_channel_in_range(sub, channel, op="get_single_float")
            sentinel = self.thermocouple_sentinels.get((hdass, channel))
            if sentinel is not None:
                return self._sentinel_float(sentinel)
            return float(self.scalar_values.get((hdass, channel), 0.0))

    def get_single_values(self, hdass: int, gain: float) -> list[int]:
        """Return per-channel scripted ints; missing channels default to 0."""
        with self._lock:
            self._log("get_single_values", (hdass, gain))
            self._consume_scripted("get_single_values")
            sub = self._require_open(hdass, op="get_single_values")
            n = sub.capabilities.num_channels
            return [int(self.scalar_values.get((hdass, ch), 0)) for ch in range(n)]

    def get_single_floats(self, hdass: int, gain: float) -> list[float]:
        """Return per-channel scripted floats with TC sentinel substitution."""
        with self._lock:
            self._log("get_single_floats", (hdass, gain))
            self._consume_scripted("get_single_floats")
            sub = self._require_open(hdass, op="get_single_floats")
            n = sub.capabilities.num_channels
            out: list[float] = []
            for ch in range(n):
                sentinel = self.thermocouple_sentinels.get((hdass, ch))
                if sentinel is not None:
                    out.append(self._sentinel_float(sentinel))
                else:
                    out.append(float(self.scalar_values.get((hdass, ch), 0.0)))
            return out

    def get_cjc_temperature(self, hdass: int, channel: int) -> float:
        """Return a plausible CJC temperature; tests override via ``scalar_values``."""
        with self._lock:
            self._log("get_cjc_temperature", (hdass, channel))
            self._consume_scripted("get_cjc_temperature")
            self._require_open(hdass, op="get_cjc_temperature")
            return 25.0  # room temperature default; tests can override via the dict.

    def code_to_volts(self, hdass: int, code: int, gain: float) -> float:
        """Offset-binary conversion mirroring the real DT9805/06 A/D.

        Matches :class:`~dtollib.backend.dataacq.DataAcqBackend.code_to_volts`
        on a ±10 V, 16-bit, offset-binary subsystem so the application-side
        thermocouple read path is exercised faithfully in unit tests: code
        ``32768`` → 0 V, ``0`` → −10 V, ``65535`` → +10 V, referred through
        ``gain``.
        """
        from dtollib.capi.conversion import code_to_input_volts  # noqa: PLC0415

        with self._lock:
            self._log("code_to_volts", (hdass, code, gain))
            self._consume_scripted("code_to_volts")
            self._require_open(hdass, op="code_to_volts")
            return code_to_input_volts(code, gain, vmin=-10.0, vmax=10.0, resolution_bits=16)

    def get_scaling(self, hdass: int) -> tuple[float, float, int, bool]:
        """Return the fake DT9805/06 scaling: ±10 V, 16-bit, offset-binary.

        Mirrors :meth:`code_to_volts` so the continuous block path builds a
        :class:`~dtollib.capi.conversion.BlockConversion` plan consistent with
        the fake's single-value scaling. ``twos_complement`` is ``False``
        (offset/straight binary, like the real A/D).
        """
        with self._lock:
            self._log("get_scaling", hdass)
            self._consume_scripted("get_scaling")
            self._require_open(hdass, op="get_scaling")
            return (-10.0, 10.0, 16, False)

    # ---- Continuous-mode configuration --------------------------

    def set_channel_list(self, hdass: int, channels: list[int]) -> None:
        """Record the channel list on the fake HDASS."""
        with self._lock:
            self._log("set_channel_list", (hdass, tuple(channels)))
            self._consume_scripted("set_channel_list")
            self._require_open(hdass, op="set_channel_list")
            self._channel_lists[hdass] = list(channels)

    def set_clock(
        self,
        hdass: int,
        *,
        rate_hz: float,
        clock_source: int,
        external_divider: int | None = None,
    ) -> None:
        """Record the clock configuration; quantise rate to 0.001 Hz precision."""
        with self._lock:
            self._log(
                "set_clock",
                (hdass, rate_hz, clock_source, external_divider),
            )
            self._consume_scripted("set_clock")
            self._require_open(hdass, op="set_clock")
            # Simulate the SDK quantising the rate — round to 0.001 Hz.  Tests
            # can override by writing into _clock_rate_hz directly if exact
            # round-trip is needed.
            self._clock_rate_hz[hdass] = round(rate_hz, 3)

    def get_clock_frequency(self, hdass: int) -> float:
        """Return the recorded clock rate (driver may quantise the request)."""
        with self._lock:
            self._log("get_clock_frequency", hdass)
            self._consume_scripted("get_clock_frequency")
            self._require_open(hdass, op="get_clock_frequency")
            return self._clock_rate_hz.get(hdass, 0.0)

    def set_trigger(
        self,
        hdass: int,
        *,
        kind: int,
        threshold_channel: int | None = None,
        threshold_level: float | None = None,
    ) -> None:
        """Record trigger configuration."""
        with self._lock:
            self._log(
                "set_trigger",
                (hdass, kind, threshold_channel, threshold_level),
            )
            self._consume_scripted("set_trigger")
            self._require_open(hdass, op="set_trigger")
            self._trigger_kind[hdass] = kind

    def set_wrap_mode(self, hdass: int, mode: int) -> None:
        """Record wrap mode."""
        with self._lock:
            self._log("set_wrap_mode", (hdass, mode))
            self._consume_scripted("set_wrap_mode")
            self._require_open(hdass, op="set_wrap_mode")
            self._wrap_mode[hdass] = mode

    def set_dma_usage(self, hdass: int, n_channels: int) -> None:
        """Record DMA usage."""
        with self._lock:
            self._log("set_dma_usage", (hdass, n_channels))
            self._consume_scripted("set_dma_usage")
            self._require_open(hdass, op="set_dma_usage")

    # ---- Notification + runtime ---------------------------------

    def register_notification(
        self,
        hdass: int,
        callback: Callable[[int, int, int], int],
    ) -> object:
        """Install a synthetic notification callback.

        Mirrors ``olDaSetWndHandle``: registration happens after config #1
        (``commit``) but before ``arm`` (config #2). Rejects if the task is
        already armed or running — registering then would not be wired into
        the buffer-rotation state machine.
        """
        with self._lock:
            self._log("register_notification", hdass)
            self._consume_scripted("register_notification")
            self._require_open(hdass, op="register_notification")
            if hdass in self._armed or self.state_of(hdass) == SubsystemState.RUNNING:
                raise DtolTaskStateError(
                    "fake backend: register_notification AFTER arm/start "
                    "(violates register-before-arm ordering)",
                    context=ErrorContext(operation="register_notification"),
                )
            self._notify_callbacks[hdass] = callback
            return callback

    def unregister_notification(self, hdass: int, handle: object) -> None:
        """Uninstall the notification callback.

        Enforces the §12.3.2 stop-BEFORE-unregister invariant.
        """
        del handle
        with self._lock:
            self._log("unregister_notification", hdass)
            self._consume_scripted("unregister_notification")
            self._require_open(hdass, op="unregister_notification")
            if self.state_of(hdass) == SubsystemState.RUNNING:
                raise DtolTaskStateError(
                    "fake backend: unregister_notification while RUNNING "
                    "(violates §12.3.2 stop-before-unregister invariant)",
                    context=ErrorContext(operation="unregister_notification"),
                )
            self._notify_callbacks[hdass] = None

    def get_queue_size(self, hdass: int, queue: int) -> int:
        """Return depth of the Ready / Inprocess / Done queue (0 / 1 / 2)."""
        with self._lock:
            self._log("get_queue_size", (hdass, queue))
            self._consume_scripted("get_queue_size")
            self._require_open(hdass, op="get_queue_size")
            if queue == _READY_QUEUE_ID:
                return len(self._ready_queue.get(hdass, deque()))
            if queue == _INPROCESS_QUEUE_ID:
                return 1 if self._inprocess.get(hdass) is not None else 0
            if queue == _DONE_QUEUE_ID:
                return len(self._done_queue.get(hdass, deque()))
            return 0

    # ---- Buffer-pool primitives ---------------------------------

    def alloc_buffer(
        self,
        n_samples: int,
        sample_dtype_bytes: int,
        *,
        zero_init: bool = True,
    ) -> int:
        """Allocate a synthetic HBUF backed by a numpy ndarray."""
        import numpy as np  # noqa: PLC0415

        with self._lock:
            self._log("alloc_buffer", (n_samples, sample_dtype_bytes, zero_init))
            self._consume_scripted("alloc_buffer")
            dtype = np.int16 if sample_dtype_bytes == _INT16_SAMPLE_BYTES else np.int32
            payload = np.zeros(n_samples, dtype=dtype)
            hbuf = self._next_hbuf
            self._next_hbuf += 1
            self._hbuf_state[hbuf] = BufferState.IDLE
            self._hbuf_payload[hbuf] = payload
            self._hbuf_valid_samples[hbuf] = 0
            return hbuf

    def free_buffer(self, hbuf: int) -> None:
        """Release a synthetic HBUF; rejects free while INPROCESS."""
        with self._lock:
            self._log("free_buffer", hbuf)
            self._consume_scripted("free_buffer")
            state = self._hbuf_state.get(hbuf)
            if state is None:
                raise DtolResourceError(
                    f"fake backend: free_buffer on unknown HBUF {hbuf}",
                    context=ErrorContext(operation="free_buffer"),
                )
            if state == BufferState.INPROCESS:
                raise DtolTaskStateError(
                    f"fake backend: free_buffer on INPROCESS HBUF {hbuf} "
                    "(violates §8.14 invariant)",
                    context=ErrorContext(operation="free_buffer"),
                )
            self._hbuf_state[hbuf] = BufferState.RELEASED
            self._hbuf_payload.pop(hbuf, None)

    def put_buffer(self, hdass: int, hbuf: int) -> None:
        """Push an HBUF onto the Ready queue for ``hdass``.

        On a D/A (output) subsystem this enforces Fill-before-Queue: the HBUF
        must have been ``copy_to_buffer``-filled since it was last queued.
        Queuing an unfilled DAC buffer would emit silence/garbage on real
        hardware, so the fake rejects it loudly.
        """
        from dtollib.capi.constants import OLSS_DA  # noqa: PLC0415

        with self._lock:
            self._log("put_buffer", (hdass, hbuf))
            self._consume_scripted("put_buffer")
            sub = self._require_open(hdass, op="put_buffer")
            if self._hbuf_state.get(hbuf) == BufferState.RELEASED:
                raise DtolTaskStateError(
                    f"fake backend: put_buffer on RELEASED HBUF {hbuf}",
                    context=ErrorContext(operation="put_buffer"),
                )
            if sub.type == OLSS_DA and hbuf not in self._filled_hbufs:
                raise DtolTaskStateError(
                    f"fake backend: put_buffer on unfilled D/A HBUF {hbuf} "
                    "(violates Fill-before-Queue — call copy_to_buffer first)",
                    context=ErrorContext(operation="put_buffer"),
                )
            # The fill is consumed by queuing; the buffer must be refilled
            # before it can be queued again.
            self._filled_hbufs.discard(hbuf)
            self._hbuf_state[hbuf] = BufferState.QUEUED
            self._hbuf_owner[hbuf] = hdass
            self._ready_queue.setdefault(hdass, deque()).append(hbuf)

    def get_buffer(self, hdass: int) -> int | None:
        """Pop the next HBUF from the Done queue (None if empty)."""
        with self._lock:
            self._log("get_buffer", hdass)
            self._consume_scripted("get_buffer")
            self._require_open(hdass, op="get_buffer")
            done = self._done_queue.setdefault(hdass, deque())
            if not done:
                return None
            hbuf = done.popleft()
            self._hbuf_state[hbuf] = BufferState.COMPLETED
            return hbuf

    def flush_buffers(self, hdass: int) -> None:
        """Empty the Ready and Done queues."""
        with self._lock:
            self._log("flush_buffers", hdass)
            self._consume_scripted("flush_buffers")
            self._require_open(hdass, op="flush_buffers")
            self._ready_queue[hdass] = deque()
            self._done_queue[hdass] = deque()

    def read_buffer_payload(self, hbuf: int) -> Any:
        """Return the synthetic payload ndarray view for the HBUF."""
        with self._lock:
            self._log("read_buffer_payload", hbuf)
            self._consume_scripted("read_buffer_payload")
            state = self._hbuf_state.get(hbuf)
            if state == BufferState.RELEASED or state is None:
                raise DtolTaskStateError(
                    f"fake backend: read_buffer_payload on invalid HBUF {hbuf}",
                    context=ErrorContext(operation="read_buffer_payload"),
                )
            return self._hbuf_payload.get(hbuf)

    def get_buffer_valid_samples(self, hbuf: int) -> int:
        """Return the recorded valid-sample count for the HBUF."""
        with self._lock:
            self._log("get_buffer_valid_samples", hbuf)
            self._consume_scripted("get_buffer_valid_samples")
            return self._hbuf_valid_samples.get(hbuf, 0)

    def copy_inprocess_buffer(
        self,
        hbuf: int,
        n_samples: int,
        sample_dtype_bytes: int,
    ) -> bytes:
        """Copy the in-process HBUF's valid samples without waiting.

        Models ``olDmCopyFromBuffer``: returns at most ``n_samples`` of the
        HBUF's recorded valid samples as raw bytes (empty when the buffer
        holds nothing yet). ``sample_dtype_bytes`` is accepted for signature
        parity; the synthetic payload already carries its own dtype.
        """
        import numpy as np  # noqa: PLC0415

        del sample_dtype_bytes  # payload dtype is intrinsic to the ndarray
        with self._lock:
            self._log("copy_inprocess_buffer", (hbuf, n_samples))
            self._consume_scripted("copy_inprocess_buffer")
            payload = self._hbuf_payload.get(hbuf)
            if payload is None:
                return b""
            valid = self._hbuf_valid_samples.get(hbuf, int(np.asarray(payload).size))
            take = max(0, min(int(valid), int(n_samples)))
            if take == 0:
                return b""
            flat = np.ascontiguousarray(np.asarray(payload).ravel()[:take])
            return bytes(flat.tobytes())

    # ---- Output writes ------------------------------------------

    def _require_output_subsystem(self, hdass: int, *, op: str) -> FakeSubsystem:
        """Reject writes against a non-output subsystem, as the SDK would."""
        from dtollib.capi.constants import OLSS_DA, OLSS_DOUT  # noqa: PLC0415

        sub = self._require_open(hdass, op=op)
        if sub.type not in {OLSS_DA, OLSS_DOUT}:
            raise DtolTaskStateError(
                f"fake backend: {op} on non-output subsystem (OLSS type {sub.type}); "
                "writes are only valid on D/A and digital-output subsystems",
                context=ErrorContext(operation=op),
            )
        return sub

    def _require_writable_state(self, hdass: int, *, op: str) -> None:
        """Writes require a committed (or running) subsystem."""
        current = self.state_of(hdass)
        if current not in {
            SubsystemState.CONFIGURED_FOR_SINGLE_VALUE,
            SubsystemState.CONFIGURED_FOR_CONTINUOUS,
            SubsystemState.RUNNING,
        }:
            raise DtolTaskStateError(
                f"fake backend: {op} invalid from state {current.value}; commit() must run first",
                context=ErrorContext(operation=op),
            )

    def put_single_value(self, hdass: int, channel: int, value: int, gain: float) -> None:
        """One-shot raw-code write; records the value for test inspection."""
        with self._lock:
            self._log("put_single_value", (hdass, channel, value, gain))
            self._consume_scripted("put_single_value")
            sub = self._require_output_subsystem(hdass, op="put_single_value")
            self._require_channel_in_range(sub, channel, op="put_single_value")
            self._require_writable_state(hdass, op="put_single_value")
            self.written_values[(hdass, channel)] = value

    def put_single_values(self, hdass: int, values: list[int], gain: float) -> None:
        """Simultaneous raw-code write; requires simultaneous-D/A support."""
        with self._lock:
            self._log("put_single_values", (hdass, list(values), gain))
            self._consume_scripted("put_single_values")
            sub = self._require_output_subsystem(hdass, op="put_single_values")
            self._require_writable_state(hdass, op="put_single_values")
            if not sub.capabilities.supports_simultaneous_da:
                raise DtolTaskStateError(
                    "fake backend: put_single_values on a subsystem without "
                    "simultaneous-D/A support; use per-channel put_single_value",
                    context=ErrorContext(operation="put_single_values"),
                )
            self.written_value_lists[hdass] = list(values)
            for channel, value in enumerate(values):
                self.written_values[(hdass, channel)] = value

    def set_synchronous_digital_io_usage(self, hdass: int, use: bool) -> None:
        """Record synchronous digital-I/O usage for the subsystem."""
        with self._lock:
            self._log("set_synchronous_digital_io_usage", (hdass, use))
            self._consume_scripted("set_synchronous_digital_io_usage")
            self._require_open(hdass, op="set_synchronous_digital_io_usage")
            self._sync_dio_usage[hdass] = use

    def set_digital_io_list_entry(self, hdass: int, entry: int, value: int) -> None:
        """Record a digital-I/O list entry for the subsystem."""
        with self._lock:
            self._log("set_digital_io_list_entry", (hdass, entry, value))
            self._consume_scripted("set_digital_io_list_entry")
            self._require_open(hdass, op="set_digital_io_list_entry")
            self._dio_list.setdefault(hdass, []).append((entry, value))

    def mute(self, hdass: int) -> None:
        """Mark the D/A output muted."""
        with self._lock:
            self._log("mute", hdass)
            self._consume_scripted("mute")
            self._require_open(hdass, op="mute")
            self._muted[hdass] = True

    def unmute(self, hdass: int) -> None:
        """Release a muted D/A output."""
        with self._lock:
            self._log("unmute", hdass)
            self._consume_scripted("unmute")
            self._require_open(hdass, op="unmute")
            self._muted[hdass] = False

    def is_muted(self, hdass: int) -> bool:
        """Test accessor — current muted state (default False)."""
        with self._lock:
            return self._muted.get(hdass, False)

    def copy_to_buffer(self, hbuf: int, data: bytes, n_samples: int) -> None:
        """Fill a synthetic HBUF from a host byte payload (waveform pre-fill)."""
        import numpy as np  # noqa: PLC0415

        with self._lock:
            self._log("copy_to_buffer", (hbuf, n_samples))
            self._consume_scripted("copy_to_buffer")
            payload = self._hbuf_payload.get(hbuf)
            if payload is None or self._hbuf_state.get(hbuf) == BufferState.RELEASED:
                raise DtolResourceError(
                    f"fake backend: copy_to_buffer on invalid HBUF {hbuf}",
                    context=ErrorContext(operation="copy_to_buffer"),
                )
            incoming = np.frombuffer(data, dtype=payload.dtype, count=n_samples)
            payload[:n_samples] = incoming
            self._hbuf_valid_samples[hbuf] = n_samples
            self._filled_hbufs.add(hbuf)

    def copy_buffer(self, hbuf: int, n_samples: int, sample_dtype_bytes: int) -> bytes:
        """Copy a synthetic HBUF's samples back out to a host byte payload."""
        with self._lock:
            self._log("copy_buffer", (hbuf, n_samples))
            self._consume_scripted("copy_buffer")
            payload = self._hbuf_payload.get(hbuf)
            if payload is None or self._hbuf_state.get(hbuf) == BufferState.RELEASED:
                raise DtolResourceError(
                    f"fake backend: copy_buffer on invalid HBUF {hbuf}",
                    context=ErrorContext(operation="copy_buffer"),
                )
            return bytes(payload[:n_samples].tobytes())

    # ---- Counter/timer + simultaneous start ---------------------

    def _require_counter_subsystem(self, hdass: int, *, op: str) -> FakeSubsystem:
        """Reject C/T-family calls against a non-counter subsystem.

        Counter/timer, quadrature, and tachometer share this guard — all
        three are dedicated subsystem types (``OLSS_CT`` / ``OLSS_QUAD`` /
        ``OLSS_TACH``); the SDK rejects ``olDaSetCTMode`` etc. on an A/D or
        D/A HDASS.
        """
        from dtollib.capi.constants import OLSS_CT, OLSS_QUAD, OLSS_TACH  # noqa: PLC0415

        sub = self._require_open(hdass, op=op)
        if sub.type not in {OLSS_CT, OLSS_QUAD, OLSS_TACH}:
            raise DtolTaskStateError(
                f"fake backend: {op} on non-counter subsystem (OLSS type {sub.type}); "
                "valid only on counter/timer, quadrature, or tachometer subsystems",
                context=ErrorContext(operation=op),
            )
        return sub

    def _require_ct_mode_set(self, hdass: int, *, op: str) -> None:
        """Enforce set_ct_mode-before-other-CT-setters ordering."""
        if hdass not in self._ct_mode_set:
            raise DtolTaskStateError(
                f"fake backend: {op} before set_ct_mode "
                "(C/T mode must be set first — it re-types the counter)",
                context=ErrorContext(operation=op),
            )

    def set_ct_mode(self, hdass: int, mode: int) -> None:
        """Record the C/T mode; marks the HDASS eligible for other CT setters."""
        with self._lock:
            self._log("set_ct_mode", (hdass, mode))
            self._consume_scripted("set_ct_mode")
            self._require_counter_subsystem(hdass, op="set_ct_mode")
            self._ct_mode[hdass] = mode
            self._ct_mode_set.add(hdass)

    def set_ct_clock(self, hdass: int, *, rate_hz: float, clock_source: int) -> None:
        """Record the counter clock configuration."""
        with self._lock:
            self._log("set_ct_clock", (hdass, rate_hz, clock_source))
            self._consume_scripted("set_ct_clock")
            self._require_counter_subsystem(hdass, op="set_ct_clock")
            self._require_ct_mode_set(hdass, op="set_ct_clock")

    def set_gate_type(self, hdass: int, gate: int) -> None:
        """Record the gate type."""
        with self._lock:
            self._log("set_gate_type", (hdass, gate))
            self._consume_scripted("set_gate_type")
            self._require_counter_subsystem(hdass, op="set_gate_type")
            self._require_ct_mode_set(hdass, op="set_gate_type")

    def set_pulse(self, hdass: int, *, pulse_type: int, duty_or_width: float) -> None:
        """Record the output pulse configuration."""
        with self._lock:
            self._log("set_pulse", (hdass, pulse_type, duty_or_width))
            self._consume_scripted("set_pulse")
            self._require_counter_subsystem(hdass, op="set_pulse")
            self._require_ct_mode_set(hdass, op="set_pulse")

    def set_measure_edges(self, hdass: int, *, start_edge: int, stop_edge: int) -> None:
        """Record the measurement edges."""
        with self._lock:
            self._log("set_measure_edges", (hdass, start_edge, stop_edge))
            self._consume_scripted("set_measure_edges")
            self._require_counter_subsystem(hdass, op="set_measure_edges")
            self._require_ct_mode_set(hdass, op="set_measure_edges")

    def set_cascade_mode(self, hdass: int, cascade: bool) -> None:
        """Record the cascade-mode flag."""
        with self._lock:
            self._log("set_cascade_mode", (hdass, cascade))
            self._consume_scripted("set_cascade_mode")
            self._require_counter_subsystem(hdass, op="set_cascade_mode")
            self._require_ct_mode_set(hdass, op="set_cascade_mode")

    def read_events(self, hdass: int, channel: int) -> int:
        """Return the scripted counter value; requires a RUNNING counter."""
        with self._lock:
            self._log("read_events", (hdass, channel))
            self._consume_scripted("read_events")
            self._require_counter_subsystem(hdass, op="read_events")
            if self.state_of(hdass) != SubsystemState.RUNNING:
                raise DtolTaskStateError(
                    f"fake backend: read_events requires a RUNNING counter "
                    f"(state {self.state_of(hdass).value})",
                    context=ErrorContext(operation="read_events"),
                )
            return int(self.scripted_counts.get((hdass, channel), 0))

    def measure_frequency(self, hdass: int, channel: int) -> float:
        """Return the scripted frequency; requires a RUNNING counter."""
        with self._lock:
            self._log("measure_frequency", (hdass, channel))
            self._consume_scripted("measure_frequency")
            self._require_counter_subsystem(hdass, op="measure_frequency")
            if self.state_of(hdass) != SubsystemState.RUNNING:
                raise DtolTaskStateError(
                    f"fake backend: measure_frequency requires a RUNNING counter "
                    f"(state {self.state_of(hdass).value})",
                    context=ErrorContext(operation="measure_frequency"),
                )
            return float(self.scripted_frequencies.get((hdass, channel), 0.0))

    def set_triggered_scan(
        self,
        hdass: int,
        *,
        multiscan_count: int,
        retrigger_mode: int,
        frequency_hz: float | None = None,
        source: int | None = None,
    ) -> None:
        """Record the triggered-scan retrigger configuration."""
        with self._lock:
            self._log(
                "set_triggered_scan",
                (hdass, multiscan_count, retrigger_mode, frequency_hz, source),
            )
            self._consume_scripted("set_triggered_scan")
            self._require_open(hdass, op="set_triggered_scan")
            self._triggered_scan[hdass] = {
                "multiscan_count": multiscan_count,
                "retrigger_mode": retrigger_mode,
                "frequency_hz": frequency_hz,
                "source": source,
            }

    def get_ss_list(self, hdrvr: int) -> int:
        """Open a new simultaneous-start list bound to ``hdrvr``."""
        with self._lock:
            self._log("get_ss_list", hdrvr)
            self._consume_scripted("get_ss_list")
            if self._hdrvr_to_name.get(hdrvr) is None:
                raise DtolResourceError(
                    f"fake backend: get_ss_list on unknown HDRVR {hdrvr}",
                    context=ErrorContext(operation="get_ss_list"),
                )
            hsslist = self._next_hsslist
            self._next_hsslist += 1
            self._ss_lists[hsslist] = []
            return hsslist

    def put_dass_to_ss_list(self, hsslist: int, hdass: int) -> None:
        """Add ``hdass`` to the list; rejects after pre-start (ordering invariant)."""
        with self._lock:
            self._log("put_dass_to_ss_list", (hsslist, hdass))
            self._consume_scripted("put_dass_to_ss_list")
            members = self._require_ss_list(hsslist, op="put_dass_to_ss_list")
            if hsslist in self._ss_prestarted:
                raise DtolTaskStateError(
                    "fake backend: put_dass_to_ss_list after simultaneous_pre_start "
                    "(members must be added before arming the list)",
                    context=ErrorContext(operation="put_dass_to_ss_list"),
                )
            self._require_open(hdass, op="put_dass_to_ss_list")
            members.append(hdass)

    def simultaneous_pre_start(self, hsslist: int) -> None:
        """Arm every subsystem in the list → PRESTARTED."""
        with self._lock:
            self._log("simultaneous_pre_start", hsslist)
            self._consume_scripted("simultaneous_pre_start")
            members = self._require_ss_list(hsslist, op="simultaneous_pre_start")
            if hsslist in self._ss_prestarted:
                raise DtolTaskStateError(
                    "fake backend: simultaneous_pre_start called twice on the same list",
                    context=ErrorContext(operation="simultaneous_pre_start"),
                )
            self._ss_prestarted.add(hsslist)
            for hdass in members:
                self._set_state(hdass, SubsystemState.PRESTARTED)

    def simultaneous_start(self, hsslist: int) -> None:
        """Start every subsystem in the list → RUNNING; requires pre-start first."""
        with self._lock:
            self._log("simultaneous_start", hsslist)
            self._consume_scripted("simultaneous_start")
            members = self._require_ss_list(hsslist, op="simultaneous_start")
            if hsslist not in self._ss_prestarted:
                raise DtolTaskStateError(
                    "fake backend: simultaneous_start before simultaneous_pre_start "
                    "(violates the SS-list ordering invariant)",
                    context=ErrorContext(operation="simultaneous_start"),
                )
            for hdass in members:
                self._set_state(hdass, SubsystemState.RUNNING)

    def release_ss_list(self, hsslist: int) -> None:
        """Release the list handle; using it afterwards raises."""
        with self._lock:
            self._log("release_ss_list", hsslist)
            self._consume_scripted("release_ss_list")
            self._require_ss_list(hsslist, op="release_ss_list")
            del self._ss_lists[hsslist]
            self._ss_prestarted.discard(hsslist)

    def _require_ss_list(self, hsslist: int, *, op: str) -> list[int]:
        members = self._ss_lists.get(hsslist)
        if members is None:
            raise DtolResourceError(
                f"fake backend: {op} on unknown/released HSSLIST {hsslist}",
                context=ErrorContext(operation=op),
            )
        return members

    # ---- Test scripting accessors -------------------------------

    def script_count(self, hdass: int, channel: int, count: int) -> None:
        """Set the value the next :meth:`read_events` returns for the channel."""
        with self._lock:
            self.scripted_counts[(hdass, channel)] = count

    def script_frequency(self, hdass: int, channel: int, frequency_hz: float) -> None:
        """Set the value the next :meth:`measure_frequency` returns."""
        with self._lock:
            self.scripted_frequencies[(hdass, channel)] = frequency_hz

    def ct_mode_of(self, hdass: int) -> int | None:
        """Test accessor — the last C/T mode set on ``hdass`` (or None)."""
        with self._lock:
            return self._ct_mode.get(hdass)

    # ---- Synthetic event emission (test-only) -------------------

    def fire_event(
        self,
        hdass: int,
        kind: SdkEventKind,
        *,
        wparam: int = 0,
        lparam: int = 0,
    ) -> None:
        """Synchronously fire an SDK notification event on the registered callback.

        Used by continuous callback-bridge tests to inject events without a
        real SDK driver thread. Raises :class:`DtolTaskStateError` if no
        callback is registered.
        """
        from dtollib.capi.constants import (  # noqa: PLC0415
            OLDA_WM_BUFFER_DONE,
            OLDA_WM_BUFFER_REUSED,
            OLDA_WM_EVENT_DONE,
            OLDA_WM_IO_COMPLETE,
            OLDA_WM_MEASURE_DONE,
            OLDA_WM_OVERRUN_ERROR,
            OLDA_WM_PRETRIGGER_BUFFER_DONE,
            OLDA_WM_QUEUE_DONE,
            OLDA_WM_QUEUE_STOPPED,
            OLDA_WM_TRIGGER_ERROR,
            OLDA_WM_UNDERRUN_ERROR,
        )

        kind_to_msg_id: dict[SdkEventKind, int] = {
            SdkEventKind.BUFFER_DONE: OLDA_WM_BUFFER_DONE,
            SdkEventKind.PRETRIGGER_BUFFER_DONE: OLDA_WM_PRETRIGGER_BUFFER_DONE,
            SdkEventKind.BUFFER_REUSED: OLDA_WM_BUFFER_REUSED,
            SdkEventKind.QUEUE_DONE: OLDA_WM_QUEUE_DONE,
            SdkEventKind.QUEUE_STOPPED: OLDA_WM_QUEUE_STOPPED,
            SdkEventKind.IO_COMPLETE: OLDA_WM_IO_COMPLETE,
            SdkEventKind.EVENT_DONE: OLDA_WM_EVENT_DONE,
            SdkEventKind.MEASURE_DONE: OLDA_WM_MEASURE_DONE,
            SdkEventKind.TRIGGER_ERROR: OLDA_WM_TRIGGER_ERROR,
            SdkEventKind.OVERRUN_ERROR: OLDA_WM_OVERRUN_ERROR,
            SdkEventKind.UNDERRUN_ERROR: OLDA_WM_UNDERRUN_ERROR,
        }
        with self._lock:
            callback = self._notify_callbacks.get(hdass)
            if callback is None:
                raise DtolTaskStateError(
                    f"fake backend: fire_event with no notification registered on {hdass}",
                    context=ErrorContext(operation="fire_event"),
                )
            msg_id = kind_to_msg_id[kind]
        # Run the callback OUTSIDE the lock so realistic event-handler
        # code that re-enters the backend does not deadlock.
        callback(msg_id, wparam, lparam)

    def fire_buffer_done(
        self,
        hdass: int,
        *,
        fill: npt.NDArray[Any] | None = None,
    ) -> int | None:
        """Promote the next Ready HBUF to Done and fire ``BUFFER_DONE``.

        Returns the HBUF that was moved (None if Ready queue empty).
        Optionally writes a synthetic payload into the HBUF before firing.
        """
        with self._lock:
            ready = self._ready_queue.setdefault(hdass, deque())
            if not ready:
                return None
            hbuf = ready.popleft()
            if fill is not None:
                self._hbuf_payload[hbuf] = fill
                self._hbuf_valid_samples[hbuf] = int(fill.size)
            else:
                payload = self._hbuf_payload.get(hbuf)
                if payload is not None:
                    self._hbuf_valid_samples[hbuf] = int(payload.size)
            self._hbuf_state[hbuf] = BufferState.COMPLETED
            self._done_queue.setdefault(hdass, deque()).append(hbuf)
        # Fire outside the lock — same reason as fire_event.
        self.fire_event(hdass, SdkEventKind.BUFFER_DONE)
        return hbuf

    def set_inprocess_payload(
        self,
        hdass: int,
        fill: npt.NDArray[Any],
        *,
        valid_samples: int | None = None,
    ) -> int | None:
        """Test-only: give the head Ready HBUF a partial in-process payload.

        Marks the buffer the SDK would currently be filling (the head of the
        Ready queue) as Inprocess and writes ``fill`` into it WITHOUT moving
        it to the Done queue, so :meth:`DtolSession.read_inprocess` can drain
        it mid-fill. Returns the affected HBUF (None if the Ready queue is
        empty).
        """
        with self._lock:
            ready = self._ready_queue.get(hdass)
            hbuf = ready[0] if ready else self._inprocess.get(hdass)
            if hbuf is None:
                return None
            self._inprocess[hdass] = hbuf
            self._hbuf_payload[hbuf] = fill
            self._hbuf_valid_samples[hbuf] = (
                int(fill.size) if valid_samples is None else int(valid_samples)
            )
            return hbuf

    def hbuf_state(self, hbuf: int) -> BufferState | None:
        """Test-only state accessor."""
        with self._lock:
            return self._hbuf_state.get(hbuf)

    def force_hbuf_state(self, hbuf: int, state: BufferState) -> None:
        """Test-only state mutator for invariant coverage."""
        with self._lock:
            self._hbuf_state[hbuf] = state


# Used by fire_event to time-stamp synthesised events.
_FAKE_MONOTONIC_BASE_NS: int = time.monotonic_ns()


# Sentinel-float values used by the fake to model the SDK's TC
# sensor-status reporting.  Pure synthetic — the real SDK has its own
# documented magic floats; the fake's job is to round-trip the
# *kind* of sentinel, not the exact bit pattern.
_TC_SENTINEL_FLOATS: Mapping[str, float] = {
    "sensor_open": -9999.0,
    "temp_out_of_range_low": -8888.0,
    "temp_out_of_range_high": -7777.0,
}
