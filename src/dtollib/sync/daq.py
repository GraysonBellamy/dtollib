"""``Dtol`` — sync facade entry-point (``Dtol.open_device``).

Mirrors :func:`nidaqlib.sync.daq.NIDaq.open_device` shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dtollib.sync.session import SyncDtolSession

if TYPE_CHECKING:
    from dtollib.backend.base import DtolBackend
    from dtollib.tasks.spec import TaskSpec


__all__ = ["Dtol"]


class Dtol:
    """Static namespace for sync entry points.

    Idiomatic usage::

        with Dtol.open_device(spec) as session:
            reading = session.poll()
            print(reading.values)
    """

    @staticmethod
    def open_device(
        spec: TaskSpec,
        *,
        backend: DtolBackend | None = None,
        timeout: float = 10.0,
        autostart: bool = True,
    ) -> SyncDtolSession:
        """Sync analogue of :func:`dtollib.open_device`.

        Returns a :class:`SyncDtolSession` — usable as a normal
        context manager.
        """
        return SyncDtolSession(spec, backend=backend, timeout=timeout, autostart=autostart)
