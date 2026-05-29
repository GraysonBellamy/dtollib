"""Immutable result dataclasses for board / subsystem / device discovery.

All public types here are frozen, slotted, kw-only dataclasses per the
ecosystem convention shared with the other ``*lib`` packages.

Design reference: docs/design.md §20.2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dtollib.tasks.models import SubsystemType

__all__ = ["BoardInfo", "DeviceInfo", "SubsystemInfo"]


@dataclass(frozen=True, slots=True, kw_only=True)
class BoardInfo:
    """One DT-Open Layers board as reported by ``olDaEnumBoardsEx``.

    Attributes:
        name: Board name passed to :func:`olDaInitialize`
            (e.g. ``"DT9805(00)"``).
        model: Model identifier from ``olDaGetBoardInfo``
            (e.g. ``"DT9805"``).  Falls back to ``name`` if the SDK
            does not populate a distinct model string.
        driver_name: Kernel-mode driver name from the registry
            (e.g. ``"OLDT9805"``).
        instance: Driver instance number; non-zero when multiple
            boards of the same model are installed.
    """

    name: str
    model: str
    driver_name: str
    instance: int


@dataclass(frozen=True, slots=True, kw_only=True)
class SubsystemInfo:
    """One subsystem on a board, as reported by capability queries.

    The capability-query surface populates the boolean-cap fields and
    the two most common integer fields (``num_channels``, ``cgl_depth``).
    Sensor-list capabilities, range lists, etc. are not yet populated.

    Attributes:
        type: Typed subsystem kind.
        element: Element index — ``0`` for the first AI subsystem, the
            second AI subsystem on the same board has ``element=1``,
            and so on.  Used as the third argument to
            :func:`olDaGetDASS`.
        num_channels: Number of physical channels on the subsystem
            (``OLSSC_NUMCHANNELS``).
        supports_singlevalue: ``OLSSC_SUP_SINGLEVALUE``.
        supports_continuous: ``OLSSC_SUP_CONTINUOUS``.
        supports_simultaneous_sh: ``OLSSC_SUP_SIMULTANEOUS_SH`` —
            simultaneous-sample-and-hold (single-call read of every
            channel at one timestamp).
        supports_multisensor: ``OLSSC_SUP_MULTISENSOR`` — channels
            can be re-typed at configure time (DT9805/DT9806).
        supports_dma: ``OLSSC_SUP_DMA``.
        returns_floats: ``OLSSC_RETURNS_FLOATS`` — subsystem returns
            engineering units, skip code-to-volts conversion.
        max_throughput_hz: ``OLSSCE_MAX_THROUGHPUT`` (float Hz).  None
            if the SDK does not report it for this subsystem type.
        cgl_depth: ``OLSSC_CGLDEPTH`` — maximum channel-list size.
    """

    type: SubsystemType
    element: int
    num_channels: int
    supports_singlevalue: bool
    supports_continuous: bool
    supports_simultaneous_sh: bool
    supports_multisensor: bool
    supports_dma: bool
    returns_floats: bool
    max_throughput_hz: float | None
    cgl_depth: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceInfo:
    """A single device endpoint = board + one subsystem.

    Mirrors the nidaqlib :class:`DeviceInfo` shape so cross-instrument
    experiment scripts that loop over ``find_devices()`` see the same
    field names.  Discovery populates ``board`` + ``subsystem_type`` +
    ``element``; single-value sessions construct a :class:`DeviceInfo`
    per open subsystem.

    Attributes:
        name: Human-readable device identifier; typically
            ``f"{board.name}/{subsystem_type.value}{element}"``.
        board: Owning :class:`BoardInfo`.
        subsystem_type: Typed subsystem kind.
        element: Element index of the subsystem on the board.
    """

    name: str
    board: BoardInfo
    subsystem_type: SubsystemType
    element: int
