"""System-level discovery + capability query.

Public surface:

- :func:`find_devices` — enumerate every installed DT-Open Layers board.
- :func:`find_subsystems` — enumerate subsystems on a given board.
- :class:`CapabilitySet` — typed view over an HDASS's reported caps.
- :class:`BoardInfo`, :class:`SubsystemInfo`, :class:`DeviceInfo` —
  immutable result dataclasses.

Discovery / lifecycle / capability surface; :class:`CapabilitySet` extends as new
capability flags become relevant (e.g. ``OLSSC_SUP_PAUSE`` for continuous streaming).

Design reference: docs/design.md §20.
"""

from __future__ import annotations

from dtollib.system.capabilities import CapabilitySet, query_capabilities
from dtollib.system.discovery import find_devices, find_subsystems
from dtollib.system.models import BoardInfo, DeviceInfo, SubsystemInfo

__all__ = [
    "BoardInfo",
    "CapabilitySet",
    "DeviceInfo",
    "SubsystemInfo",
    "find_devices",
    "find_subsystems",
    "query_capabilities",
]
