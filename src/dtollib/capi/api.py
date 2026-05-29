"""Typed wrapper over the ctypes binding — :class:`OpenLayersApi`.

This is **Layer 2** of the C-boundary stack (docs/design.md §10.3):

- Layer 1 (:mod:`dtollib.capi.prototypes`) — raw ctypes signatures.
- **Layer 2 (this module)** — output-pointer extraction + ECODE →
  typed-exception classification.  One method per SDK function;
  every method routes through :func:`~dtollib.capi.errors.check`.
- Layer 3 (:mod:`dtollib.backend.dataacq`) — session-level
  orchestration, capability cache, notification wrappers.

:class:`OpenLayersApi` does **no** state caching, no buffer-pool, no
notification-wrapper management.  It is a pure call shape.  Anything
that needs to hold onto a value across calls lives in
:class:`~dtollib.backend.dataacq.DataAcqBackend`.

Discovery binds the 15 functions in
:data:`dtollib.capi.prototypes.DISCOVERY_OLDAAPI_FUNCTIONS` plus the 2
in :data:`dtollib.capi.prototypes.CORE_OLMEM_FUNCTIONS`.
Further capabilities extend the method surface; the AST-level test
``tests/unit/test_capi_api_check_invariant.py`` asserts the
:func:`~dtollib.capi.errors.check` invariant on every new method.
"""

from __future__ import annotations

import ctypes
from ctypes import byref, c_char_p, c_double, c_float, c_int, c_long, c_uint, c_ulong
from typing import TYPE_CHECKING, Final

from dtollib.capi.callbacks import (
    BOARD_ENUM_EX_PROC,
    BOARD_ENUM_PROC,
    CHAN_CAP_ENUM_PROC,
    SS_CAP_ENUM_PROC,
    SS_ENUM_PROC,
)
from dtollib.capi.constants import OL_CT_CASCADE, OL_CT_SINGLE
from dtollib.capi.errors import check
from dtollib.capi.prototypes import declare_oldaapi, declare_olmem
from dtollib.capi.types import (
    BRIDGE_SENSOR_TEDS,
    HBUF,
    HDASS,
    HDRVR,
    HSSLIST,
    HWND,
    STRAIN_GAGE_TEDS,
)
from dtollib.errors import DtolCapabilityError, ErrorContext

if TYPE_CHECKING:
    from collections.abc import Iterable

    from dtollib.capi.loader import OpenLayersDlls


__all__ = ["BoardEnumRow", "OpenLayersApi"]


# Buffer sizes for SDK string outputs — large enough for any
# documented response; sized to keep stack usage modest.
_VERSION_BUFFER_SIZE: Final[int] = 64
_BOARD_MODEL_BUFFER_SIZE: Final[int] = 64
_BOARD_DRIVER_BUFFER_SIZE: Final[int] = 64


class BoardEnumRow:
    """One row emitted by :meth:`OpenLayersApi.enum_boards_ex`.

    Plain attribute container (not a frozen dataclass) so the
    enumeration callback can construct rows quickly without spelling
    out keyword arguments.  Higher layers re-shape these into the
    public :class:`~dtollib.system.BoardInfo` frozen dataclass.

    Attributes:
        name: Board name (``LPSTR lpszBoardName``).
        driver: Driver name (``LPSTR lpszDriverName``).
        instance: Board instance integer.
        registry_path: Registry path string.
    """

    __slots__ = ("driver", "instance", "name", "registry_path")

    def __init__(
        self,
        *,
        name: str,
        driver: str = "",
        instance: int = 0,
        registry_path: str = "",
    ) -> None:
        self.name = name
        self.driver = driver
        self.instance = instance
        self.registry_path = registry_path


class OpenLayersApi:
    """Typed wrapper over the DataAcq SDK ctypes binding.

    Construction binds the discovery prototype set on both DLLs.  Each
    public method on this class corresponds to one SDK function and
    follows the same pattern:

    1. Allocate output-pointer storage (e.g. ``hdass = HDASS()``).
    2. Call the prototype with :func:`byref` as needed.
    3. Hand the returned ``status`` to :func:`check`.
    4. Return the extracted output (plain Python types — int, str,
       sequence, etc.).

    The :func:`check` step is mandatory on every public method that
    returns a status: ``tests/unit/test_capi_api_check_invariant.py``
    AST-walks this class and fails if a method body lacks a
    ``check(...)`` call.

    No state caching: capability values, version strings, etc. are
    queried on every call.  The session layer
    (:class:`~dtollib.backend.dataacq.DataAcqBackend`) caches results
    when caching is appropriate.
    """

    def __init__(self, dlls: OpenLayersDlls) -> None:
        """Bind discovery prototypes on ``dlls`` and capture the handle.

        Args:
            dlls: Loaded handle pair from
                :func:`~dtollib.capi.loader.load_openlayers`.
        """
        self._dlls = dlls
        declare_oldaapi(dlls.oldaapi)
        declare_olmem(dlls.olmem)

    @property
    def dlls(self) -> OpenLayersDlls:
        """Underlying loaded DLL handle pair (escape hatch)."""
        return self._dlls

    # ---- Version helpers --------------------------------------------------

    def get_oldaapi_version(self) -> str:
        """Return the ``oldaapi*.dll`` version string."""
        buf = ctypes.create_string_buffer(_VERSION_BUFFER_SIZE)
        status = self._dlls.oldaapi.olDaGetVersion(buf, _VERSION_BUFFER_SIZE)
        check(self._dlls, status, op="olDaGetVersion", source="oldaapi")
        return buf.value.decode("ascii", errors="replace")

    def get_olmem_version(self) -> str:
        """Return the ``olmem*.dll`` version string."""
        buf = ctypes.create_string_buffer(_VERSION_BUFFER_SIZE)
        status = self._dlls.olmem.olDmGetVersion(buf, _VERSION_BUFFER_SIZE)
        check(self._dlls, status, op="olDmGetVersion", source="olmem")
        return buf.value.decode("ascii", errors="replace")

    # ---- Board enumeration ------------------------------------------------

    def enum_boards(self) -> list[str]:
        """Enumerate board names via ``olDaEnumBoards``.

        Returns:
            List of board name strings (e.g. ``["DT9805(00)"]``).
        """
        names: list[str] = []

        @BOARD_ENUM_PROC  # type: ignore[untyped-decorator]
        def _on_board(name: bytes, _lparam: int) -> int:  # pragma: no cover — driver invokes
            if name:
                names.append(name.decode("ascii", errors="replace"))
            return 1  # TRUE — continue enumeration

        status = self._dlls.oldaapi.olDaEnumBoards(_on_board, 0)
        check(self._dlls, status, op="olDaEnumBoards", source="oldaapi")
        return names

    def enum_boards_ex(self) -> list[BoardEnumRow]:
        """Enumerate boards with registry info via ``olDaEnumBoardsEx``.

        Returns:
            One :class:`BoardEnumRow` per enumerated board.
        """
        rows: list[BoardEnumRow] = []

        @BOARD_ENUM_EX_PROC  # type: ignore[untyped-decorator]
        def _on_board(  # pragma: no cover — driver invokes
            name: bytes,
            driver: bytes,
            instance: int,
            registry: bytes,
            _lparam: int,
        ) -> int:
            if name:
                rows.append(
                    BoardEnumRow(
                        name=name.decode("ascii", errors="replace"),
                        driver=(driver or b"").decode("ascii", errors="replace"),
                        instance=int(instance),
                        registry_path=(registry or b"").decode("ascii", errors="replace"),
                    )
                )
            return 1

        status = self._dlls.oldaapi.olDaEnumBoardsEx(_on_board, 0)
        check(self._dlls, status, op="olDaEnumBoardsEx", source="oldaapi")
        return rows

    def get_board_info(self, name: str) -> tuple[str, str]:
        """Query ``model`` and ``driver`` for a board by name.

        Args:
            name: Board name as returned by :meth:`enum_boards`.

        Returns:
            Two-tuple ``(model, driver_name)``.
        """
        model_buf = ctypes.create_string_buffer(_BOARD_MODEL_BUFFER_SIZE)
        driver_buf = ctypes.create_string_buffer(_BOARD_DRIVER_BUFFER_SIZE)
        status = self._dlls.oldaapi.olDaGetBoardInfo(
            name.encode("ascii"),
            model_buf,
            _BOARD_MODEL_BUFFER_SIZE,
            driver_buf,
            _BOARD_DRIVER_BUFFER_SIZE,
        )
        check(
            self._dlls,
            status,
            op="olDaGetBoardInfo",
            source="oldaapi",
            board=name,
        )
        return (
            model_buf.value.decode("ascii", errors="replace"),
            driver_buf.value.decode("ascii", errors="replace"),
        )

    # ---- Device lifecycle -------------------------------------------------

    def initialize(self, name: str) -> int:
        """Open the named board; return its HDRVR as an integer.

        Args:
            name: Board name to open (e.g. ``"DT9805(00)"``).

        Returns:
            HDRVR handle as a Python int.  Pass back into other
            ``OpenLayersApi`` methods that take an HDRVR.
        """
        handle = HDRVR()
        status = self._dlls.oldaapi.olDaInitialize(name.encode("ascii"), byref(handle))
        check(self._dlls, status, op="olDaInitialize", source="oldaapi", board=name)
        return int(handle.value or 0)

    def terminate(self, hdrvr: int) -> None:
        """Close the device handle previously returned by :meth:`initialize`."""
        status = self._dlls.oldaapi.olDaTerminate(HDRVR(hdrvr))
        check(self._dlls, status, op="olDaTerminate", source="oldaapi")

    # ---- Subsystem enumeration -------------------------------------------

    def enum_subsystems(self, hdrvr: int) -> list[int]:
        """Enumerate HDASS handles on a device via ``olDaEnumSubSystems``.

        Args:
            hdrvr: Device handle from :meth:`initialize`.

        Returns:
            List of HDASS handles (as Python ints) for every subsystem
            on the device.  Caller queries each HDASS for its
            ``OLSS_*`` type via ``olDaGetSSCaps``.
        """
        handles: list[int] = []

        @SS_ENUM_PROC  # type: ignore[untyped-decorator]
        def _on_subsys(hdass: int | None, _lparam: int) -> int:  # pragma: no cover
            if hdass is not None:
                handles.append(int(hdass))
            return 1

        status = self._dlls.oldaapi.olDaEnumSubSystems(HDRVR(hdrvr), _on_subsys, 0)
        check(self._dlls, status, op="olDaEnumSubSystems", source="oldaapi")
        return handles

    # ---- Capability queries ----------------------------------------------

    def get_dev_caps(self, hdrvr: int, subsys_type: int, cap_id: int) -> int:
        """Query a device-level integer capability."""
        out = c_ulong(0)
        status = self._dlls.oldaapi.olDaGetDevCaps(HDRVR(hdrvr), subsys_type, cap_id, byref(out))
        check(
            self._dlls,
            status,
            op="olDaGetDevCaps",
            source="oldaapi",
            extra={"subsys_type": subsys_type, "cap_id": cap_id},
        )
        return int(out.value)

    def get_dass(self, hdrvr: int, subsys_type: int, element: int) -> int:
        """Acquire a subsystem handle via ``olDaGetDASS``."""
        handle = HDASS()
        status = self._dlls.oldaapi.olDaGetDASS(HDRVR(hdrvr), subsys_type, element, byref(handle))
        check(
            self._dlls,
            status,
            op="olDaGetDASS",
            source="oldaapi",
            element=element,
            extra={"subsys_type": subsys_type},
        )
        return int(handle.value or 0)

    def release_dass(self, hdass: int) -> None:
        """Release a subsystem handle via ``olDaReleaseDASS``."""
        status = self._dlls.oldaapi.olDaReleaseDASS(HDASS(hdass))
        check(self._dlls, status, op="olDaReleaseDASS", source="oldaapi")

    def get_ss_caps(self, hdass: int, cap_id: int) -> int:
        """Query a subsystem integer capability via ``olDaGetSSCaps``."""
        out = c_ulong(0)
        status = self._dlls.oldaapi.olDaGetSSCaps(HDASS(hdass), cap_id, byref(out))
        check(
            self._dlls,
            status,
            op="olDaGetSSCaps",
            source="oldaapi",
            extra={"cap_id": cap_id},
        )
        return int(out.value)

    def get_ss_caps_ex(self, hdass: int, cap_id: int) -> float:
        """Query a subsystem floating-point capability via ``olDaGetSSCapsEx``."""
        out = c_double(0.0)
        status = self._dlls.oldaapi.olDaGetSSCapsEx(HDASS(hdass), cap_id, byref(out))
        check(
            self._dlls,
            status,
            op="olDaGetSSCapsEx",
            source="oldaapi",
            extra={"cap_id": cap_id},
        )
        return float(out.value)

    def enum_ss_caps(self, hdass: int, cap_id: int) -> list[tuple[float, float]]:
        """Enumerate discrete subsystem-capability values via ``olDaEnumSSCaps``.

        Used for capability IDs like ``OL_ENUM_RANGES`` /
        ``OL_ENUM_GAINS`` where the SDK exposes a list of supported
        values rather than a single integer.  The ``CAPSPROC`` callback
        receives ``(uiEnumCap, dParam1, dParam2, lParam)`` — the first
        arg is the cap ID being listed, and the value(s) ride in the two
        doubles.  For single-value caps (gains, filters, resolutions)
        only ``dParam1`` is meaningful; for ``OL_ENUM_RANGES`` the pair
        is ``(max, min)`` matching :meth:`get_range`.

        Returns:
            List of ``(dParam1, dParam2)`` tuples, one per enumerated
            value, in SDK enumeration order.
        """
        values: list[tuple[float, float]] = []

        @SS_CAP_ENUM_PROC  # type: ignore[untyped-decorator]
        def _on_value(
            _cap: int, param1: float, param2: float, _lparam: int
        ) -> int:  # pragma: no cover
            values.append((float(param1), float(param2)))
            return 1

        status = self._dlls.oldaapi.olDaEnumSSCaps(HDASS(hdass), cap_id, _on_value, 0)
        check(
            self._dlls,
            status,
            op="olDaEnumSSCaps",
            source="oldaapi",
            extra={"cap_id": cap_id},
        )
        return values

    def enum_channel_caps(self, hdass: int, channel: int, cap_id: int) -> list[int]:
        """Enumerate per-channel capability values via ``olDaEnumChannelCaps``.

        The ``CHANNELCAPSPROC`` callback receives
        ``(uiEnumCap, uParam, dParam, lParam)`` — the enumerated value
        is the integer ``uParam`` (e.g. a supported multi-sensor type).
        """
        values: list[int] = []

        @CHAN_CAP_ENUM_PROC  # type: ignore[untyped-decorator]
        def _on_value(
            _cap: int, uparam: int, _dparam: float, _lparam: int
        ) -> int:  # pragma: no cover
            values.append(int(uparam))
            return 1

        status = self._dlls.oldaapi.olDaEnumChannelCaps(HDASS(hdass), channel, cap_id, _on_value, 0)
        check(
            self._dlls,
            status,
            op="olDaEnumChannelCaps",
            source="oldaapi",
            channel=channel,
            extra={"cap_id": cap_id},
        )
        return values

    # ---- Subsystem-level configuration --------------------------

    def set_data_flow(self, hdass: int, mode: int) -> None:
        """``olDaSetDataFlow`` — set the subsystem data-flow mode."""
        status = self._dlls.oldaapi.olDaSetDataFlow(HDASS(hdass), mode)
        check(
            self._dlls,
            status,
            op="olDaSetDataFlow",
            source="oldaapi",
            extra={"mode": mode},
        )

    def set_stop_on_error(self, hdass: int, stop: bool) -> None:
        """``olDaSetStopOnError`` — orthogonal to recorder ``ErrorPolicy``.

        Not exported from every SDK build (e.g. V7.0.0.7 omits it).
        When absent, the subsystem keeps the SDK-side default; the
        recorder's ``ErrorPolicy`` still drives stop-on-overrun behaviour
        at the dtollib layer, so the SDK setting is informational only.
        """
        fn = getattr(self._dlls.oldaapi, "olDaSetStopOnError", None)
        if fn is None:
            check(self._dlls, 0, op="olDaSetStopOnError(absent)", source="oldaapi")
            return
        status = fn(HDASS(hdass), 1 if stop else 0)
        check(
            self._dlls,
            status,
            op="olDaSetStopOnError",
            source="oldaapi",
            extra={"stop": stop},
        )

    def set_channel_type(self, hdass: int, channel_type: int) -> None:
        """``olDaSetChannelType`` — single-ended / differential / pseudo-diff."""
        status = self._dlls.oldaapi.olDaSetChannelType(HDASS(hdass), channel_type)
        check(
            self._dlls,
            status,
            op="olDaSetChannelType",
            source="oldaapi",
            extra={"channel_type": channel_type},
        )

    def set_channel_range(
        self,
        hdass: int,
        channel: int,
        min_val: float,
        max_val: float,
    ) -> None:
        """``olDaSetChannelRange`` — per-channel voltage range."""
        status = self._dlls.oldaapi.olDaSetChannelRange(HDASS(hdass), min_val, max_val, channel)
        check(
            self._dlls,
            status,
            op="olDaSetChannelRange",
            source="oldaapi",
            channel=channel,
            extra={"min_val": min_val, "max_val": max_val},
        )

    def set_range(self, hdass: int, max_val: float, min_val: float) -> None:
        """``olDaSetRange`` — subsystem-wide range (no per-channel override)."""
        status = self._dlls.oldaapi.olDaSetRange(HDASS(hdass), max_val, min_val)
        check(
            self._dlls,
            status,
            op="olDaSetRange",
            source="oldaapi",
            extra={"min_val": min_val, "max_val": max_val},
        )

    def set_gain_list_entry(
        self,
        hdass: int,
        list_index: int,
        channel: int,
        gain: float,
    ) -> None:
        """``olDaSetGainListEntry`` — add channel + gain at list position."""
        status = self._dlls.oldaapi.olDaSetGainListEntry(HDASS(hdass), list_index, channel, gain)
        check(
            self._dlls,
            status,
            op="olDaSetGainListEntry",
            source="oldaapi",
            channel=channel,
            extra={"list_index": list_index, "gain": gain},
        )

    def set_multi_sensor_type(
        self,
        hdass: int,
        channel: int,
        sensor_type: int,
    ) -> None:
        """``olDaSetMultiSensorType`` — re-type a MULTI_SENSOR channel.

        MUST precede any per-type setter on the channel — docs/design.md
        §8.5a.  Silent wrong-data bug otherwise.

        Callers should gate this on ``capabilities.supports_multisensor``;
        subsystems that don't support it will return ``OLNOTSUPPORTED``
        (ECODE 36) and propagate as a typed exception.
        """
        status = self._dlls.oldaapi.olDaSetMultiSensorType(HDASS(hdass), channel, sensor_type)
        check(
            self._dlls,
            status,
            op="olDaSetMultiSensorType",
            source="oldaapi",
            channel=channel,
            extra={"sensor_type": sensor_type},
        )

    def set_thermocouple_type(
        self,
        hdass: int,
        channel: int,
        tc_type: int,
    ) -> None:
        """``olDaSetThermocoupleType`` — TC letter designation."""
        status = self._dlls.oldaapi.olDaSetThermocoupleType(HDASS(hdass), channel, tc_type)
        check(
            self._dlls,
            status,
            op="olDaSetThermocoupleType",
            source="oldaapi",
            channel=channel,
            extra={"tc_type": tc_type},
        )

    def set_return_cjc_in_stream(self, hdass: int, enable: bool) -> None:
        """``olDaSetReturnCjcTemperatureInStream`` — interleave CJC into the continuous stream."""
        status = self._dlls.oldaapi.olDaSetReturnCjcTemperatureInStream(
            HDASS(hdass), 1 if enable else 0
        )
        check(
            self._dlls,
            status,
            op="olDaSetReturnCjcTemperatureInStream",
            source="oldaapi",
            extra={"enable": enable},
        )

    def config(self, hdass: int) -> None:
        """``olDaConfig`` — commit the configured state."""
        status = self._dlls.oldaapi.olDaConfig(HDASS(hdass))
        check(self._dlls, status, op="olDaConfig", source="oldaapi")

    # ---- State / lifecycle --------------------------------------

    def get_ss_state(self, hdass: int) -> int:
        """``olDaGetSSState`` — raw subsystem-state code.

        Not exported from every SDK build (e.g. V7.0.0.7 omits it).
        Returns ``-1`` when absent; ``DataAcqBackend.get_state`` then
        derives the state from ``olDaIsRunning`` plus locally-tracked
        transitions.
        """
        fn = getattr(self._dlls.oldaapi, "olDaGetSSState", None)
        if fn is None:
            check(self._dlls, 0, op="olDaGetSSState(absent)", source="oldaapi")
            return -1
        out = c_ulong(0)
        status = fn(HDASS(hdass), byref(out))
        check(self._dlls, status, op="olDaGetSSState", source="oldaapi")
        return int(out.value)

    def start(self, hdass: int) -> None:
        """``olDaStart`` — begin acquisition."""
        status = self._dlls.oldaapi.olDaStart(HDASS(hdass))
        check(self._dlls, status, op="olDaStart", source="oldaapi")

    def stop(self, hdass: int) -> None:
        """``olDaStop`` — orderly stop; blocks until current buffer fills."""
        status = self._dlls.oldaapi.olDaStop(HDASS(hdass))
        check(self._dlls, status, op="olDaStop", source="oldaapi")

    def abort(self, hdass: int) -> None:
        """``olDaAbort`` — immediate halt."""
        status = self._dlls.oldaapi.olDaAbort(HDASS(hdass))
        check(self._dlls, status, op="olDaAbort", source="oldaapi")

    def is_running(self, hdass: int) -> bool:
        """``olDaIsRunning`` — non-blocking running-state probe."""
        out = c_int(0)
        status = self._dlls.oldaapi.olDaIsRunning(HDASS(hdass), byref(out))
        check(self._dlls, status, op="olDaIsRunning", source="oldaapi")
        return bool(out.value)

    # ---- Single-value reads -------------------------------------

    def get_single_value(self, hdass: int, channel: int, gain: float) -> int:
        """``olDaGetSingleValue`` — raw-code read of one channel."""
        out = c_ulong(0)
        status = self._dlls.oldaapi.olDaGetSingleValue(HDASS(hdass), byref(out), channel, gain)
        check(
            self._dlls,
            status,
            op="olDaGetSingleValue",
            source="oldaapi",
            channel=channel,
            extra={"gain": gain},
        )
        return int(out.value)

    def get_single_float(self, hdass: int, channel: int, gain: float) -> float:
        """``olDaGetSingleFloat`` — engineering-unit read of one channel."""
        out = c_float(0.0)
        status = self._dlls.oldaapi.olDaGetSingleFloat(HDASS(hdass), byref(out), channel, gain)
        check(
            self._dlls,
            status,
            op="olDaGetSingleFloat",
            source="oldaapi",
            channel=channel,
            extra={"gain": gain},
        )
        return float(out.value)

    def get_single_value_ex(self, hdass: int, channel: int, gain: float) -> int:
        """``olDaGetSingleValueEx`` — autoranging raw-code read."""
        out = c_ulong(0)
        status = self._dlls.oldaapi.olDaGetSingleValueEx(HDASS(hdass), byref(out), channel, gain)
        check(
            self._dlls,
            status,
            op="olDaGetSingleValueEx",
            source="oldaapi",
            channel=channel,
            extra={"gain": gain},
        )
        return int(out.value)

    def get_single_values(self, hdass: int, n_channels: int, gain: float) -> list[int]:
        """``olDaGetSingleValues`` — simultaneous raw-code read of every channel."""
        out = (c_ulong * n_channels)()
        status = self._dlls.oldaapi.olDaGetSingleValues(HDASS(hdass), out, gain)
        check(
            self._dlls,
            status,
            op="olDaGetSingleValues",
            source="oldaapi",
            extra={"n_channels": n_channels, "gain": gain},
        )
        return [int(v) for v in out]

    def get_single_floats(self, hdass: int, n_channels: int, gain: float) -> list[float]:
        """``olDaGetSingleFloats`` — simultaneous engineering-unit read."""
        out = (c_float * n_channels)()
        status = self._dlls.oldaapi.olDaGetSingleFloats(HDASS(hdass), out, gain)
        check(
            self._dlls,
            status,
            op="olDaGetSingleFloats",
            source="oldaapi",
            extra={"n_channels": n_channels, "gain": gain},
        )
        return [float(v) for v in out]

    def get_cjc_temperature(self, hdass: int, channel: int) -> float:
        """``olDaGetCjcTemperature`` — cold-junction temperature, °C."""
        out = c_float(0.0)
        status = self._dlls.oldaapi.olDaGetCjcTemperature(HDASS(hdass), byref(out), channel)
        check(
            self._dlls,
            status,
            op="olDaGetCjcTemperature",
            source="oldaapi",
            channel=channel,
        )
        return float(out.value)

    def code_to_volts(self, hdass: int, code: int, gain: float) -> float:
        """``olDaCodeToVolts`` — oracle for the vectorised converter."""
        out = c_double(0.0)
        status = self._dlls.oldaapi.olDaCodeToVolts(HDASS(hdass), code, byref(out), gain)
        check(
            self._dlls,
            status,
            op="olDaCodeToVolts",
            source="oldaapi",
            extra={"code": code, "gain": gain},
        )
        return float(out.value)

    def volts_to_code(self, hdass: int, volts: float, gain: float) -> int:
        """``olDaVoltsToCode`` — oracle for AO write paths."""
        out = c_ulong(0)
        status = self._dlls.oldaapi.olDaVoltsToCode(HDASS(hdass), volts, byref(out), gain)
        check(
            self._dlls,
            status,
            op="olDaVoltsToCode",
            source="oldaapi",
            extra={"volts": volts, "gain": gain},
        )
        return int(out.value)

    def get_encoding(self, hdass: int) -> int:
        """``olDaGetEncoding`` — one of the ``OL_ENC_*`` constants."""
        out = c_uint(0)
        status = self._dlls.oldaapi.olDaGetEncoding(HDASS(hdass), byref(out))
        check(self._dlls, status, op="olDaGetEncoding", source="oldaapi")
        return int(out.value)

    def get_resolution(self, hdass: int) -> int:
        """``olDaGetResolution`` — ADC resolution in bits."""
        out = c_uint(0)
        status = self._dlls.oldaapi.olDaGetResolution(HDASS(hdass), byref(out))
        check(self._dlls, status, op="olDaGetResolution", source="oldaapi")
        return int(out.value)

    def get_range(self, hdass: int) -> tuple[float, float]:
        """``olDaGetRange`` — configured ``(max_volts, min_volts)`` of the subsystem."""
        out_max = c_double(0.0)
        out_min = c_double(0.0)
        status = self._dlls.oldaapi.olDaGetRange(HDASS(hdass), byref(out_max), byref(out_min))
        check(self._dlls, status, op="olDaGetRange", source="oldaapi")
        return float(out_max.value), float(out_min.value)

    # ---- Continuous-mode configuration --------------------------

    def set_channel_list_size(self, hdass: int, size: int) -> None:
        """``olDaSetChannelListSize`` — set the channel-list length."""
        status = self._dlls.oldaapi.olDaSetChannelListSize(HDASS(hdass), size)
        check(
            self._dlls,
            status,
            op="olDaSetChannelListSize",
            source="oldaapi",
            extra={"size": size},
        )

    def set_channel_list_entry(self, hdass: int, list_index: int, channel: int) -> None:
        """``olDaSetChannelListEntry`` — bind a physical channel at a list index."""
        status = self._dlls.oldaapi.olDaSetChannelListEntry(HDASS(hdass), list_index, channel)
        check(
            self._dlls,
            status,
            op="olDaSetChannelListEntry",
            source="oldaapi",
            channel=channel,
            extra={"list_index": list_index},
        )

    def set_channel_list_entry_inhibit(
        self,
        hdass: int,
        list_index: int,
        inhibit: bool,
    ) -> None:
        """``olDaSetChannelListEntryInhibit`` — skip an entry while still scanned."""
        status = self._dlls.oldaapi.olDaSetChannelListEntryInhibit(
            HDASS(hdass), list_index, 1 if inhibit else 0
        )
        check(
            self._dlls,
            status,
            op="olDaSetChannelListEntryInhibit",
            source="oldaapi",
            extra={"list_index": list_index, "inhibit": inhibit},
        )

    def set_clock_source(self, hdass: int, source: int) -> None:
        """``olDaSetClockSource`` — internal vs external clock selector."""
        status = self._dlls.oldaapi.olDaSetClockSource(HDASS(hdass), source)
        check(
            self._dlls,
            status,
            op="olDaSetClockSource",
            source="oldaapi",
            extra={"clock_source": source},
        )

    def set_clock_frequency(self, hdass: int, frequency_hz: float) -> None:
        """``olDaSetClockFrequency`` — internal-clock rate setpoint."""
        status = self._dlls.oldaapi.olDaSetClockFrequency(HDASS(hdass), frequency_hz)
        check(
            self._dlls,
            status,
            op="olDaSetClockFrequency",
            source="oldaapi",
            extra={"frequency_hz": frequency_hz},
        )

    def get_clock_frequency(self, hdass: int) -> float:
        """``olDaGetClockFrequency`` — readback (SDK may quantise the request)."""
        out = c_double(0.0)
        status = self._dlls.oldaapi.olDaGetClockFrequency(HDASS(hdass), byref(out))
        check(self._dlls, status, op="olDaGetClockFrequency", source="oldaapi")
        return float(out.value)

    def set_external_clock_divider(self, hdass: int, divider: int) -> None:
        """``olDaSetExternalClockDivider`` — external-clock prescaler."""
        status = self._dlls.oldaapi.olDaSetExternalClockDivider(HDASS(hdass), divider)
        check(
            self._dlls,
            status,
            op="olDaSetExternalClockDivider",
            source="oldaapi",
            extra={"divider": divider},
        )

    def set_trigger(self, hdass: int, trigger_kind: int) -> None:
        """``olDaSetTrigger`` — start-trigger selector."""
        status = self._dlls.oldaapi.olDaSetTrigger(HDASS(hdass), trigger_kind)
        check(
            self._dlls,
            status,
            op="olDaSetTrigger",
            source="oldaapi",
            extra={"trigger_kind": trigger_kind},
        )

    def set_trigger_threshold_channel(self, hdass: int, channel: int) -> None:
        """``olDaSetTriggerThresholdChannel`` — analog-threshold monitor channel."""
        status = self._dlls.oldaapi.olDaSetTriggerThresholdChannel(HDASS(hdass), channel)
        check(
            self._dlls,
            status,
            op="olDaSetTriggerThresholdChannel",
            source="oldaapi",
            channel=channel,
        )

    def set_trigger_threshold_level(self, hdass: int, level: float) -> None:
        """``olDaSetTriggerThresholdLevel`` — analog-threshold voltage."""
        status = self._dlls.oldaapi.olDaSetTriggerThresholdLevel(HDASS(hdass), level)
        check(
            self._dlls,
            status,
            op="olDaSetTriggerThresholdLevel",
            source="oldaapi",
            extra={"level": level},
        )

    def set_wrap_mode(self, hdass: int, mode: int) -> None:
        """``olDaSetWrapMode`` — NONE / SINGLE / MULTIPLE."""
        status = self._dlls.oldaapi.olDaSetWrapMode(HDASS(hdass), mode)
        check(
            self._dlls,
            status,
            op="olDaSetWrapMode",
            source="oldaapi",
            extra={"wrap_mode": mode},
        )

    def set_dma_usage(self, hdass: int, n_channels: int) -> None:
        """``olDaSetDmaUsage`` — number of DMA channels to claim."""
        status = self._dlls.oldaapi.olDaSetDmaUsage(HDASS(hdass), n_channels)
        check(
            self._dlls,
            status,
            op="olDaSetDmaUsage",
            source="oldaapi",
            extra={"n_channels": n_channels},
        )

    # ---- Notification + runtime ---------------------------------

    def set_wnd_handle(self, hdass: int, hwnd: int, context: int = 0) -> None:
        """``olDaSetWndHandle`` — route buffer-done messages to ``hwnd``.

        The SDK posts ``OLDA_WM_*`` messages (buffer-done, overrun, etc.) to
        the given window. ``hwnd`` is owned by the message-pump thread in
        :class:`~dtollib.backend._message_window.MessageWindow`; pass ``0`` to
        detach. This is the only buffer-done mechanism that works on the
        DT9805/06 (the notification-procedure callback never fires —
        docs/decisions.md).
        """
        status = self._dlls.oldaapi.olDaSetWndHandle(HDASS(hdass), HWND(hwnd), context)
        check(
            self._dlls,
            status,
            op="olDaSetWndHandle",
            source="oldaapi",
            extra={"context": context},
        )

    def get_queue_size(self, hdass: int, queue: int) -> int:
        """``olDaGetQueueSize`` — Ready / Inprocess / Done queue depth."""
        out = c_ulong(0)
        status = self._dlls.oldaapi.olDaGetQueueSize(HDASS(hdass), queue, byref(out))
        check(
            self._dlls,
            status,
            op="olDaGetQueueSize",
            source="oldaapi",
            extra={"queue": queue},
        )
        return int(out.value)

    # ---- Buffer enqueue / dequeue --------------------------------

    def put_buffer(self, hdass: int, hbuf: int) -> None:
        """``olDaPutBuffer`` — push an HBUF onto the Ready queue."""
        status = self._dlls.oldaapi.olDaPutBuffer(HDASS(hdass), HBUF(hbuf))
        check(self._dlls, status, op="olDaPutBuffer", source="oldaapi")

    def get_buffer(self, hdass: int) -> int | None:
        """``olDaGetBuffer`` — pop an HBUF from the Done queue (or None)."""
        out = HBUF()
        status = self._dlls.oldaapi.olDaGetBuffer(HDASS(hdass), byref(out))
        check(self._dlls, status, op="olDaGetBuffer", source="oldaapi")
        handle = int(out.value or 0)
        return handle or None

    def flush_buffers(self, hdass: int) -> None:
        """``olDaFlushBuffers`` — clear Ready + Done queues."""
        status = self._dlls.oldaapi.olDaFlushBuffers(HDASS(hdass))
        check(self._dlls, status, op="olDaFlushBuffers", source="oldaapi")

    def copy_from_buffer(
        self,
        hbuf: int,
        n_samples: int,
        sample_dtype_bytes: int,
    ) -> bytes:
        """``olDmCopyFromBuffer`` — drain the Inprocess HBUF without waiting.

        Returns the actually-copied bytes; the SDK may transfer fewer samples
        than requested (device segment alignment).
        """
        size_bytes = n_samples * sample_dtype_bytes
        dst = ctypes.create_string_buffer(size_bytes)
        actual = c_ulong(0)
        status = self._dlls.olmem.olDmCopyFromBuffer(
            HBUF(hbuf),
            ctypes.cast(dst, c_char_p),
            n_samples,
            byref(actual),
        )
        check(
            self._dlls,
            status,
            op="olDmCopyFromBuffer",
            source="olmem",
            extra={"requested_samples": n_samples, "actual_samples": int(actual.value)},
        )
        return bytes(dst.raw[: int(actual.value) * sample_dtype_bytes])

    # ---- Olmem buffer allocation + introspection -----------------

    def alloc_buffer(
        self,
        n_samples: int,
        sample_dtype_bytes: int,
        *,
        zero_init: bool = True,
    ) -> int:
        """``olDmCallocBuffer`` / ``olDmMallocBuffer`` — allocate an HBUF."""
        out = HBUF()
        fn = self._dlls.olmem.olDmCallocBuffer if zero_init else self._dlls.olmem.olDmMallocBuffer
        status = fn(0, 0, n_samples, sample_dtype_bytes, byref(out))
        op = "olDmCallocBuffer" if zero_init else "olDmMallocBuffer"
        check(
            self._dlls,
            status,
            op=op,
            source="olmem",
            extra={"n_samples": n_samples, "sample_dtype_bytes": sample_dtype_bytes},
        )
        return int(out.value or 0)

    def realloc_buffer(self, hbuf: int, n_samples: int) -> None:
        """``olDmReAllocBuffer`` — resize an existing HBUF."""
        status = self._dlls.olmem.olDmReAllocBuffer(HBUF(hbuf), n_samples)
        check(
            self._dlls,
            status,
            op="olDmReAllocBuffer",
            source="olmem",
            extra={"n_samples": n_samples},
        )

    def free_buffer(self, hbuf: int) -> None:
        """``olDmFreeBuffer`` — release an HBUF."""
        status = self._dlls.olmem.olDmFreeBuffer(HBUF(hbuf))
        check(self._dlls, status, op="olDmFreeBuffer", source="olmem")

    def get_buffer_ptr(self, hbuf: int) -> int:
        """``olDmGetBufferPtr`` — raw data pointer (as int) into the HBUF."""
        out = c_char_p()
        status = self._dlls.olmem.olDmGetBufferPtr(HBUF(hbuf), byref(out))
        check(self._dlls, status, op="olDmGetBufferPtr", source="olmem")
        return int(ctypes.cast(out, ctypes.c_void_p).value or 0)

    def get_buffer_size(self, hbuf: int) -> int:
        """``olDmGetBufferSize`` — capacity in samples."""
        out = c_ulong(0)
        status = self._dlls.olmem.olDmGetBufferSize(HBUF(hbuf), byref(out))
        check(self._dlls, status, op="olDmGetBufferSize", source="olmem")
        return int(out.value)

    def get_buffer_max_samples(self, hbuf: int) -> int:
        """``olDmGetMaxSamples`` — maximum sample count the HBUF can hold."""
        out = c_ulong(0)
        status = self._dlls.olmem.olDmGetMaxSamples(HBUF(hbuf), byref(out))
        check(self._dlls, status, op="olDmGetMaxSamples", source="olmem")
        return int(out.value)

    def get_buffer_valid_samples(self, hbuf: int) -> int:
        """``olDmGetValidSamples`` — samples actually filled by the SDK."""
        out = c_ulong(0)
        status = self._dlls.olmem.olDmGetValidSamples(HBUF(hbuf), byref(out))
        check(self._dlls, status, op="olDmGetValidSamples", source="olmem")
        return int(out.value)

    def get_buffer_data_width(self, hbuf: int) -> int:
        """``olDmGetDataWidth`` — bytes per sample (2 for int16, 4 for int32)."""
        out = c_uint(0)
        status = self._dlls.olmem.olDmGetDataWidth(HBUF(hbuf), byref(out))
        check(self._dlls, status, op="olDmGetDataWidth", source="olmem")
        return int(out.value)

    def get_buffer_data_bits(self, hbuf: int) -> int:
        """``olDmGetDataBits`` — ADC resolution in bits."""
        out = c_uint(0)
        status = self._dlls.olmem.olDmGetDataBits(HBUF(hbuf), byref(out))
        check(self._dlls, status, op="olDmGetDataBits", source="olmem")
        return int(out.value)

    # ---- Single-value output writes ------------------------------

    def put_single_value(self, hdass: int, channel: int, value: int, gain: float) -> None:
        """``olDaPutSingleValue`` — one-shot raw-code write to one channel.

        ``value`` is a device code, not volts; callers convert via
        :meth:`volts_to_code` (or the subsystem's float path) first.
        """
        status = self._dlls.oldaapi.olDaPutSingleValue(HDASS(hdass), c_long(value), channel, gain)
        check(
            self._dlls,
            status,
            op="olDaPutSingleValue",
            source="oldaapi",
            channel=channel,
            extra={"value": value, "gain": gain},
        )

    def put_single_values(self, hdass: int, values: list[int], gain: float) -> None:
        """``olDaPutSingleValues`` — simultaneous raw-code write across channels.

        ``values`` are device codes in channel-list order; only valid on
        subsystems advertising simultaneous D/A update.
        """
        arr = (c_long * len(values))(*values)
        status = self._dlls.oldaapi.olDaPutSingleValues(HDASS(hdass), arr, gain)
        check(
            self._dlls,
            status,
            op="olDaPutSingleValues",
            source="oldaapi",
            extra={"n_channels": len(values), "gain": gain},
        )

    # ---- Digital-I/O configuration -------------------------------

    def set_synchronous_digital_io_usage(self, hdass: int, use: bool) -> None:
        """``olDaSetSynchronousDigitalIOUsage`` — scan-synchronised digital I/O."""
        status = self._dlls.oldaapi.olDaSetSynchronousDigitalIOUsage(HDASS(hdass), 1 if use else 0)
        check(
            self._dlls,
            status,
            op="olDaSetSynchronousDigitalIOUsage",
            source="oldaapi",
            extra={"use": use},
        )

    def set_digital_io_list_entry(self, hdass: int, entry: int, value: int) -> None:
        """``olDaSetDigitalIOListEntry`` — bind a digital port at a list slot."""
        status = self._dlls.oldaapi.olDaSetDigitalIOListEntry(HDASS(hdass), entry, value)
        check(
            self._dlls,
            status,
            op="olDaSetDigitalIOListEntry",
            source="oldaapi",
            extra={"entry": entry, "value": value},
        )

    # ---- Continuous-AO mute control ------------------------------

    def mute(self, hdass: int) -> None:
        """``olDaMute`` — hold the D/A output at its current value.

        Raises :class:`DtolCapabilityError` if this DLL build does not
        export ``olDaMute`` (e.g. V7.0.0.7).
        """
        if not hasattr(self._dlls.oldaapi, "olDaMute"):
            raise DtolCapabilityError(
                "olDaMute is not exported by this DataAcq DLL build; "
                "continuous-AO mute is unavailable.",
                context=ErrorContext(operation="olDaMute"),
            )
        status = self._dlls.oldaapi.olDaMute(HDASS(hdass))
        check(self._dlls, status, op="olDaMute", source="oldaapi")

    def unmute(self, hdass: int) -> None:
        """``olDaUnMute`` — release a muted D/A output.

        Raises :class:`DtolCapabilityError` if this DLL build does not
        export ``olDaUnMute``.
        """
        if not hasattr(self._dlls.oldaapi, "olDaUnMute"):
            raise DtolCapabilityError(
                "olDaUnMute is not exported by this DataAcq DLL build; "
                "continuous-AO mute is unavailable.",
                context=ErrorContext(operation="olDaUnMute"),
            )
        status = self._dlls.oldaapi.olDaUnMute(HDASS(hdass))
        check(self._dlls, status, op="olDaUnMute", source="oldaapi")

    # ---- Host→buffer copy (continuous-AO waveform fill) ----------

    def copy_to_buffer(self, hbuf: int, data: bytes, n_samples: int) -> None:
        """``olDmCopyToBuffer`` — fill an HBUF from a host byte buffer.

        ``data`` is the little-endian sample payload; ``n_samples`` is the
        number of samples (not bytes) the SDK should copy in.
        """
        buf = ctypes.create_string_buffer(data, len(data))
        status = self._dlls.olmem.olDmCopyToBuffer(
            HBUF(hbuf), ctypes.cast(buf, ctypes.c_void_p), n_samples
        )
        check(
            self._dlls,
            status,
            op="olDmCopyToBuffer",
            source="olmem",
            extra={"n_samples": n_samples},
        )

    def copy_buffer(self, hbuf: int, n_samples: int, sample_dtype_bytes: int) -> bytes:
        """``olDmCopyBuffer`` — copy an HBUF's valid samples into a host buffer."""
        size_bytes = n_samples * sample_dtype_bytes
        dst = ctypes.create_string_buffer(size_bytes)
        status = self._dlls.olmem.olDmCopyBuffer(HBUF(hbuf), ctypes.cast(dst, ctypes.c_void_p))
        check(
            self._dlls,
            status,
            op="olDmCopyBuffer",
            source="olmem",
            extra={"n_samples": n_samples},
        )
        return bytes(dst.raw[:size_bytes])

    # ---- Counter/timer configuration -----------------------------

    def set_ct_mode(self, hdass: int, mode: int) -> None:
        """``olDaSetCTMode`` — counter/timer operation mode (``OL_CTMODE_*``)."""
        status = self._dlls.oldaapi.olDaSetCTMode(HDASS(hdass), mode)
        check(self._dlls, status, op="olDaSetCTMode", source="oldaapi", extra={"mode": mode})

    def set_ct_clock_source(self, hdass: int, source: int) -> None:
        """Counter clock source (``OL_CLK_*``).

        The DLL exports no ``olDaSetCTClockSource``; the C/T subsystem
        shares the generic ``olDaSetClockSource`` (bench-confirmed
        2026-05-28, SDK V7.0.0.7).
        """
        status = self._dlls.oldaapi.olDaSetClockSource(HDASS(hdass), source)
        check(
            self._dlls,
            status,
            op="olDaSetClockSource",
            source="oldaapi",
            extra={"clock_source": source},
        )

    def set_ct_clock_frequency(self, hdass: int, frequency_hz: float) -> None:
        """Counter clock rate setpoint.

        Shares the generic ``olDaSetClockFrequency`` (no
        ``olDaSetCTClockFrequency`` export exists).
        """
        status = self._dlls.oldaapi.olDaSetClockFrequency(HDASS(hdass), frequency_hz)
        check(
            self._dlls,
            status,
            op="olDaSetClockFrequency",
            source="oldaapi",
            extra={"frequency_hz": frequency_hz},
        )

    def set_gate_type(self, hdass: int, gate: int) -> None:
        """``olDaSetGateType`` — gate-enable logic (``OL_GATE_*``)."""
        status = self._dlls.oldaapi.olDaSetGateType(HDASS(hdass), gate)
        check(self._dlls, status, op="olDaSetGateType", source="oldaapi", extra={"gate": gate})

    def set_pulse_type(self, hdass: int, polarity: int) -> None:
        """``olDaSetPulseType`` — pulse output polarity (``OL_PULSETYPE_*``)."""
        status = self._dlls.oldaapi.olDaSetPulseType(HDASS(hdass), polarity)
        check(
            self._dlls,
            status,
            op="olDaSetPulseType",
            source="oldaapi",
            extra={"polarity": polarity},
        )

    def set_pulse_width(self, hdass: int, duty_or_width: float) -> None:
        """``olDaSetPulseWidth`` — duty cycle (rate gen) or pulse width (one-shot)."""
        status = self._dlls.oldaapi.olDaSetPulseWidth(HDASS(hdass), duty_or_width)
        check(
            self._dlls,
            status,
            op="olDaSetPulseWidth",
            source="oldaapi",
            extra={"duty_or_width": duty_or_width},
        )

    def set_measure_start_edge(self, hdass: int, edge: int) -> None:
        """``olDaSetMeasureStartEdge`` — edge that starts edge-to-edge timing."""
        status = self._dlls.oldaapi.olDaSetMeasureStartEdge(HDASS(hdass), edge)
        check(
            self._dlls,
            status,
            op="olDaSetMeasureStartEdge",
            source="oldaapi",
            extra={"edge": edge},
        )

    def set_measure_stop_edge(self, hdass: int, edge: int) -> None:
        """``olDaSetMeasureStopEdge`` — edge that stops edge-to-edge timing."""
        status = self._dlls.oldaapi.olDaSetMeasureStopEdge(HDASS(hdass), edge)
        check(
            self._dlls,
            status,
            op="olDaSetMeasureStopEdge",
            source="oldaapi",
            extra={"edge": edge},
        )

    def set_cascade_mode(self, hdass: int, cascade: bool) -> None:
        """``olDaSetCascadeMode`` — cascade two counters into one 32-bit counter.

        Takes a UINT selector (``OL_CT_CASCADE`` / ``OL_CT_SINGLE``), not a
        BOOL — bench-confirmed 2026-05-28 (both accepted with ec 0; a raw
        ``1``/``0`` is the wrong family).
        """
        selector = OL_CT_CASCADE if cascade else OL_CT_SINGLE
        status = self._dlls.oldaapi.olDaSetCascadeMode(HDASS(hdass), selector)
        check(
            self._dlls,
            status,
            op="olDaSetCascadeMode",
            source="oldaapi",
            extra={"cascade": cascade, "selector": selector},
        )

    # ---- Counter/timer read --------------------------------------

    def read_events(self, hdass: int, channel: int) -> int:
        """``olDaReadEvents`` — current counter value for ``channel``."""
        out = c_ulong(0)
        status = self._dlls.oldaapi.olDaReadEvents(HDASS(hdass), channel, byref(out))
        check(self._dlls, status, op="olDaReadEvents", source="oldaapi", channel=channel)
        return int(out.value)

    def measure_frequency(self, hdass: int, channel: int) -> float:
        """``olDaMeasureFrequency`` — measured input frequency (Hz) for ``channel``."""
        out = c_double(0.0)
        status = self._dlls.oldaapi.olDaMeasureFrequency(HDASS(hdass), channel, byref(out))
        check(self._dlls, status, op="olDaMeasureFrequency", source="oldaapi", channel=channel)
        return float(out.value)

    # ---- Triggered-scan retrigger --------------------------------

    def set_triggered_scan_usage(self, hdass: int, enable: bool) -> None:
        """``olDaSetTriggeredScanUsage`` — enable/disable triggered scan mode."""
        status = self._dlls.oldaapi.olDaSetTriggeredScanUsage(HDASS(hdass), 1 if enable else 0)
        check(
            self._dlls,
            status,
            op="olDaSetTriggeredScanUsage",
            source="oldaapi",
            extra={"enable": enable},
        )

    def set_multiscan_count(self, hdass: int, count: int) -> None:
        """``olDaSetMultiscanCount`` — channel-list scans per trigger."""
        status = self._dlls.oldaapi.olDaSetMultiscanCount(HDASS(hdass), count)
        check(
            self._dlls,
            status,
            op="olDaSetMultiscanCount",
            source="oldaapi",
            extra={"count": count},
        )

    def set_retrigger_mode(self, hdass: int, mode: int) -> None:
        """``olDaSetRetriggerMode`` — retrigger mode (``OL_RETRIG_*``)."""
        status = self._dlls.oldaapi.olDaSetRetriggerMode(HDASS(hdass), mode)
        check(
            self._dlls,
            status,
            op="olDaSetRetriggerMode",
            source="oldaapi",
            extra={"mode": mode},
        )

    def set_retrigger(self, hdass: int, source: int) -> None:
        """``olDaSetRetrigger`` — retrigger source for EXTRA mode (``OL_TRG_*``)."""
        status = self._dlls.oldaapi.olDaSetRetrigger(HDASS(hdass), source)
        check(
            self._dlls,
            status,
            op="olDaSetRetrigger",
            source="oldaapi",
            extra={"retrigger_source": source},
        )

    def set_retrigger_frequency(self, hdass: int, frequency_hz: float) -> None:
        """``olDaSetRetriggerFrequency`` — internal retrigger rate for INTERNAL mode."""
        status = self._dlls.oldaapi.olDaSetRetriggerFrequency(HDASS(hdass), frequency_hz)
        check(
            self._dlls,
            status,
            op="olDaSetRetriggerFrequency",
            source="oldaapi",
            extra={"frequency_hz": frequency_hz},
        )

    # ---- Simultaneous start (HSSLIST) ----------------------------

    def get_ss_list(self, hdrvr: int) -> int:
        """``olDaGetSSList`` — obtain a simultaneous-start list handle for ``hdrvr``."""
        out = HSSLIST()
        status = self._dlls.oldaapi.olDaGetSSList(HDRVR(hdrvr), byref(out))
        check(self._dlls, status, op="olDaGetSSList", source="oldaapi")
        return int(out.value or 0)

    def put_dass_to_ss_list(self, hsslist: int, hdass: int) -> None:
        """``olDaPutDassToSSList`` — add a subsystem to the simultaneous-start list."""
        status = self._dlls.oldaapi.olDaPutDassToSSList(HSSLIST(hsslist), HDASS(hdass))
        check(self._dlls, status, op="olDaPutDassToSSList", source="oldaapi")

    def simultaneous_pre_start(self, hsslist: int) -> None:
        """``olDaSimultaneousPrestart`` — arm every subsystem in the list."""
        status = self._dlls.oldaapi.olDaSimultaneousPrestart(HSSLIST(hsslist))
        check(self._dlls, status, op="olDaSimultaneousPrestart", source="oldaapi")

    def simultaneous_start(self, hsslist: int) -> None:
        """``olDaSimultaneousStart`` — start every subsystem in the list at once."""
        status = self._dlls.oldaapi.olDaSimultaneousStart(HSSLIST(hsslist))
        check(self._dlls, status, op="olDaSimultaneousStart", source="oldaapi")

    def release_ss_list(self, hsslist: int) -> None:
        """``olDaReleaseSSList`` — release the simultaneous-start list handle."""
        status = self._dlls.oldaapi.olDaReleaseSSList(HSSLIST(hsslist))
        check(self._dlls, status, op="olDaReleaseSSList", source="oldaapi")

    # ---- Multi-sensor configuration -----------------------------
    #
    # Every method here targets an intelligent multi-sensor module; on the
    # owned DT9805/DT9806 the SDK returns ECODE 36 (``OLNOTSUPPORTED``),
    # which ``check`` raises as :class:`~dtollib.errors.DtolCapabilityError`.
    # Callers gate on ``capabilities.supports_multisensor`` (the builder's
    # ``_require_io_type_supported``) so this is reached only on hardware
    # that can honour it.

    def set_rtd_type(self, hdass: int, channel: int, rtd_type: int) -> None:
        """``olDaSetRtdType`` — RTD curve (``OL_RTD_TYPE_*`` selector)."""
        status = self._dlls.oldaapi.olDaSetRtdType(HDASS(hdass), channel, rtd_type)
        check(
            self._dlls,
            status,
            op="olDaSetRtdType",
            source="oldaapi",
            channel=channel,
            extra={"rtd_type": rtd_type},
        )

    def set_rtd_r0(self, hdass: int, channel: int, r0_ohms: float) -> None:
        """``olDaSetRtdR0`` — RTD resistance at 0 °C."""
        status = self._dlls.oldaapi.olDaSetRtdR0(HDASS(hdass), channel, r0_ohms)
        check(
            self._dlls,
            status,
            op="olDaSetRtdR0",
            source="oldaapi",
            channel=channel,
            extra={"r0_ohms": r0_ohms},
        )

    def set_rtd_a(self, hdass: int, channel: int, a: float) -> None:
        """``olDaSetRtdA`` — Callendar-Van Dusen coefficient A."""
        status = self._dlls.oldaapi.olDaSetRtdA(HDASS(hdass), channel, a)
        check(
            self._dlls,
            status,
            op="olDaSetRtdA",
            source="oldaapi",
            channel=channel,
            extra={"a": a},
        )

    def set_rtd_b(self, hdass: int, channel: int, b: float) -> None:
        """``olDaSetRtdB`` — Callendar-Van Dusen coefficient B."""
        status = self._dlls.oldaapi.olDaSetRtdB(HDASS(hdass), channel, b)
        check(
            self._dlls,
            status,
            op="olDaSetRtdB",
            source="oldaapi",
            channel=channel,
            extra={"b": b},
        )

    def set_rtd_c(self, hdass: int, channel: int, c: float) -> None:
        """``olDaSetRtdC`` — Callendar-Van Dusen coefficient C."""
        status = self._dlls.oldaapi.olDaSetRtdC(HDASS(hdass), channel, c)
        check(
            self._dlls,
            status,
            op="olDaSetRtdC",
            source="oldaapi",
            channel=channel,
            extra={"c": c},
        )

    def set_thermistor_a(self, hdass: int, channel: int, a: float) -> None:
        """``olDaSetThermistorA`` — Steinhart-Hart coefficient A."""
        status = self._dlls.oldaapi.olDaSetThermistorA(HDASS(hdass), channel, a)
        check(
            self._dlls,
            status,
            op="olDaSetThermistorA",
            source="oldaapi",
            channel=channel,
            extra={"a": a},
        )

    def set_thermistor_b(self, hdass: int, channel: int, b: float) -> None:
        """``olDaSetThermistorB`` — Steinhart-Hart coefficient B."""
        status = self._dlls.oldaapi.olDaSetThermistorB(HDASS(hdass), channel, b)
        check(
            self._dlls,
            status,
            op="olDaSetThermistorB",
            source="oldaapi",
            channel=channel,
            extra={"b": b},
        )

    def set_thermistor_c(self, hdass: int, channel: int, c: float) -> None:
        """``olDaSetThermistorC`` — Steinhart-Hart coefficient C."""
        status = self._dlls.oldaapi.olDaSetThermistorC(HDASS(hdass), channel, c)
        check(
            self._dlls,
            status,
            op="olDaSetThermistorC",
            source="oldaapi",
            channel=channel,
            extra={"c": c},
        )

    def set_coupling_type(self, hdass: int, channel: int, coupling: int) -> None:
        """``olDaSetCouplingType`` — DC (0) / AC (1) coupling."""
        status = self._dlls.oldaapi.olDaSetCouplingType(HDASS(hdass), channel, coupling)
        check(
            self._dlls,
            status,
            op="olDaSetCouplingType",
            source="oldaapi",
            channel=channel,
            extra={"coupling": coupling},
        )

    def set_excitation_current_source(self, hdass: int, channel: int, source: int) -> None:
        """``olDaSetExcitationCurrentSource`` — INTERNAL/EXTERNAL/DISABLED."""
        status = self._dlls.oldaapi.olDaSetExcitationCurrentSource(HDASS(hdass), channel, source)
        check(
            self._dlls,
            status,
            op="olDaSetExcitationCurrentSource",
            source="oldaapi",
            channel=channel,
            extra={"source": source},
        )

    def set_excitation_current_value(self, hdass: int, channel: int, amps: float) -> None:
        """``olDaSetExcitationCurrentValue`` — drive current in amps."""
        status = self._dlls.oldaapi.olDaSetExcitationCurrentValue(HDASS(hdass), channel, amps)
        check(
            self._dlls,
            status,
            op="olDaSetExcitationCurrentValue",
            source="oldaapi",
            channel=channel,
            extra={"amps": amps},
        )

    def set_strain_excitation_voltage_source(self, hdass: int, source: int) -> None:
        """``olDaSetStrainExcitationVoltageSource`` — subsystem-wide (no channel)."""
        status = self._dlls.oldaapi.olDaSetStrainExcitationVoltageSource(HDASS(hdass), source)
        check(
            self._dlls,
            status,
            op="olDaSetStrainExcitationVoltageSource",
            source="oldaapi",
            extra={"source": source},
        )

    def set_strain_excitation_voltage(self, hdass: int, volts: float) -> None:
        """``olDaSetStrainExcitationVoltage`` — subsystem-wide (no channel)."""
        status = self._dlls.oldaapi.olDaSetStrainExcitationVoltage(HDASS(hdass), volts)
        check(
            self._dlls,
            status,
            op="olDaSetStrainExcitationVoltage",
            source="oldaapi",
            extra={"volts": volts},
        )

    def set_strain_bridge_configuration(self, hdass: int, channel: int, config: int) -> None:
        """``olDaSetStrainBridgeConfiguration`` — strain-gage wiring."""
        status = self._dlls.oldaapi.olDaSetStrainBridgeConfiguration(HDASS(hdass), channel, config)
        check(
            self._dlls,
            status,
            op="olDaSetStrainBridgeConfiguration",
            source="oldaapi",
            channel=channel,
            extra={"config": config},
        )

    def set_strain_shunt_resistor(self, hdass: int, channel: int, enabled: bool) -> None:
        """``olDaSetStrainShuntResistor`` — engage the shunt-cal resistor."""
        status = self._dlls.oldaapi.olDaSetStrainShuntResistor(
            HDASS(hdass), channel, 1 if enabled else 0
        )
        check(
            self._dlls,
            status,
            op="olDaSetStrainShuntResistor",
            source="oldaapi",
            channel=channel,
            extra={"enabled": enabled},
        )

    def set_bridge_configuration(self, hdass: int, channel: int, config: int) -> None:
        """``olDaSetBridgeConfiguration`` — generic bridge wiring."""
        status = self._dlls.oldaapi.olDaSetBridgeConfiguration(HDASS(hdass), channel, config)
        check(
            self._dlls,
            status,
            op="olDaSetBridgeConfiguration",
            source="oldaapi",
            channel=channel,
            extra={"config": config},
        )

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
        """``olDaVoltsToStrain`` — bridge volts → strain (ε). Pure, no HDASS."""
        out = c_double(0.0)
        status = self._dlls.oldaapi.olDaVoltsToStrain(
            config,
            v_unstrained,
            v_strained,
            v_excitation,
            gage_factor,
            gage_resistance,
            lead_resistance,
            poisson_ratio,
            shunt_correction,
            byref(out),
        )
        check(self._dlls, status, op="olDaVoltsToStrain", source="oldaapi")
        return float(out.value)

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
        """``olDaVoltsToBridgeBasedSensor`` — bridge volts → engineering. Pure."""
        out = c_double(0.0)
        status = self._dlls.oldaapi.olDaVoltsToBridgeBasedSensor(
            v_unstrained,
            v_strained,
            v_excitation,
            temperature_coefficient,
            gage_resistance,
            lead_resistance,
            rated_output_mv_per_v,
            shunt_correction,
            byref(out),
        )
        check(self._dlls, status, op="olDaVoltsToBridgeBasedSensor", source="oldaapi")
        return float(out.value)

    def read_strain_gage_hardware_teds(self, hdass: int, channel: int) -> dict[str, object]:
        """``olDaReadStrainGageHardwareTeds`` — read on-sensor TEDS into a dict."""
        out = STRAIN_GAGE_TEDS()
        status = self._dlls.oldaapi.olDaReadStrainGageHardwareTeds(
            HDASS(hdass), channel, byref(out)
        )
        check(
            self._dlls,
            status,
            op="olDaReadStrainGageHardwareTeds",
            source="oldaapi",
            channel=channel,
        )
        return _teds_to_dict(out)

    def read_strain_gage_virtual_teds(self, path: str) -> dict[str, object]:
        """``olDaReadStrainGageVirtualTeds`` — read a virtual-TEDS file into a dict."""
        out = STRAIN_GAGE_TEDS()
        status = self._dlls.oldaapi.olDaReadStrainGageVirtualTeds(path.encode("utf-8"), byref(out))
        check(self._dlls, status, op="olDaReadStrainGageVirtualTeds", source="oldaapi")
        return _teds_to_dict(out)

    def read_bridge_sensor_hardware_teds(self, hdass: int, channel: int) -> dict[str, object]:
        """``olDaReadBridgeSensorHardwareTeds`` — read on-sensor TEDS into a dict."""
        out = BRIDGE_SENSOR_TEDS()
        status = self._dlls.oldaapi.olDaReadBridgeSensorHardwareTeds(
            HDASS(hdass), channel, byref(out)
        )
        check(
            self._dlls,
            status,
            op="olDaReadBridgeSensorHardwareTeds",
            source="oldaapi",
            channel=channel,
        )
        return _teds_to_dict(out)

    def read_bridge_sensor_virtual_teds(self, path: str) -> dict[str, object]:
        """``olDaReadBridgeSensorVirtualTeds`` — read a virtual-TEDS file into a dict."""
        out = BRIDGE_SENSOR_TEDS()
        status = self._dlls.oldaapi.olDaReadBridgeSensorVirtualTeds(
            path.encode("utf-8"), byref(out)
        )
        check(self._dlls, status, op="olDaReadBridgeSensorVirtualTeds", source="oldaapi")
        return _teds_to_dict(out)


def _teds_to_dict(struct: ctypes.Structure) -> dict[str, object]:
    """Flatten a TEDS ctypes struct into a plain dict (bytes → str)."""
    result: dict[str, object] = {}
    for field in struct._fields_:
        field_name = field[0]
        value = getattr(struct, field_name)
        result[field_name] = value.decode("ascii", "replace") if isinstance(value, bytes) else value
    return result


def single_value_method_names() -> Iterable[str]:
    """Names of single-value public ``OpenLayersApi`` methods.

    Consumed by ``tests/unit/test_capi_api_check_invariant.py`` so the
    AST-level invariant runs over the single-value surface too.
    """
    return (
        "set_data_flow",
        "set_stop_on_error",
        "set_channel_type",
        "set_channel_range",
        "set_range",
        "set_gain_list_entry",
        "set_multi_sensor_type",
        "set_thermocouple_type",
        "set_return_cjc_in_stream",
        "config",
        "get_ss_state",
        "start",
        "stop",
        "abort",
        "is_running",
        "get_single_value",
        "get_single_float",
        "get_single_value_ex",
        "get_single_values",
        "get_single_floats",
        "get_cjc_temperature",
        "code_to_volts",
        "volts_to_code",
    )


def discovery_method_names() -> Iterable[str]:
    """Names of discovery / lifecycle / capability public ``OpenLayersApi`` methods.

    Consumed by ``tests/unit/test_capi_api_check_invariant.py`` so the
    AST-level invariant runs over a known method set.
    """
    return (
        "get_oldaapi_version",
        "get_olmem_version",
        "enum_boards",
        "enum_boards_ex",
        "get_board_info",
        "initialize",
        "terminate",
        "enum_subsystems",
        "get_dev_caps",
        "get_dass",
        "release_dass",
        "get_ss_caps",
        "get_ss_caps_ex",
        "enum_ss_caps",
        "enum_channel_caps",
    )


def continuous_method_names() -> Iterable[str]:
    """Names of continuous-mode public ``OpenLayersApi`` methods.

    Consumed by ``tests/unit/test_capi_api_check_invariant.py`` so the
    AST-level ``check(...)`` invariant runs over the continuous-mode surface too.
    """
    return (
        # Continuous-mode configuration
        "set_channel_list_size",
        "set_channel_list_entry",
        "set_channel_list_entry_inhibit",
        "set_clock_source",
        "set_clock_frequency",
        "get_clock_frequency",
        "set_external_clock_divider",
        "set_trigger",
        "set_trigger_threshold_channel",
        "set_trigger_threshold_level",
        "set_wrap_mode",
        "set_dma_usage",
        # Notification + runtime
        "set_wnd_handle",
        "get_queue_size",
        # Buffer enqueue / dequeue
        "put_buffer",
        "get_buffer",
        "flush_buffers",
        "copy_from_buffer",
        # olmem buffer allocation + introspection
        "alloc_buffer",
        "realloc_buffer",
        "free_buffer",
        "get_buffer_ptr",
        "get_buffer_size",
        "get_buffer_max_samples",
        "get_buffer_valid_samples",
        "get_buffer_data_width",
        "get_buffer_data_bits",
    )


def output_method_names() -> Iterable[str]:
    """Names of output public ``OpenLayersApi`` methods.

    Consumed by ``tests/unit/test_capi_api_check_invariant.py`` so the
    AST-level ``check(...)`` invariant runs over the output surface.
    """
    return (
        "put_single_value",
        "put_single_values",
        "set_synchronous_digital_io_usage",
        "set_digital_io_list_entry",
        "mute",
        "unmute",
        "copy_to_buffer",
        "copy_buffer",
    )


def counter_method_names() -> Iterable[str]:
    """Names of counter/timer public ``OpenLayersApi`` methods.

    Consumed by ``tests/unit/test_capi_api_check_invariant.py`` so the
    AST-level ``check(...)`` invariant runs over the counter/timer +
    simultaneous-start surface.
    """
    return (
        # Counter/timer configuration
        "set_ct_mode",
        "set_ct_clock_source",
        "set_ct_clock_frequency",
        "set_gate_type",
        "set_pulse_type",
        "set_pulse_width",
        "set_measure_start_edge",
        "set_measure_stop_edge",
        "set_cascade_mode",
        # Counter/timer read
        "read_events",
        "measure_frequency",
        # Triggered-scan retrigger
        "set_triggered_scan_usage",
        "set_multiscan_count",
        "set_retrigger_mode",
        "set_retrigger",
        "set_retrigger_frequency",
        # Simultaneous start
        "get_ss_list",
        "put_dass_to_ss_list",
        "simultaneous_pre_start",
        "simultaneous_start",
        "release_ss_list",
    )


def multi_sensor_method_names() -> Iterable[str]:
    """Names of multi-sensor public ``OpenLayersApi`` methods.

    Consumed by ``tests/unit/test_capi_api_check_invariant.py`` so the
    AST-level ``check(...)`` invariant runs over the multi-sensor surface.
    """
    return (
        # RTD
        "set_rtd_type",
        "set_rtd_r0",
        "set_rtd_a",
        "set_rtd_b",
        "set_rtd_c",
        # Thermistor
        "set_thermistor_a",
        "set_thermistor_b",
        "set_thermistor_c",
        # Coupling + excitation current
        "set_coupling_type",
        "set_excitation_current_source",
        "set_excitation_current_value",
        # Strain + bridge
        "set_strain_excitation_voltage_source",
        "set_strain_excitation_voltage",
        "set_strain_bridge_configuration",
        "set_strain_shunt_resistor",
        "set_bridge_configuration",
        # Volts → engineering conversions
        "volts_to_strain",
        "volts_to_bridge_based_sensor",
        # TEDS readers
        "read_strain_gage_hardware_teds",
        "read_strain_gage_virtual_teds",
        "read_bridge_sensor_hardware_teds",
        "read_bridge_sensor_virtual_teds",
    )
