"""Backend Protocol + real / fake implementations.

The :class:`DtolBackend` Protocol (in :mod:`dtollib.backend.base`) is
the seam between the typed session layer
(:class:`~dtollib.tasks.DtolSession`, :class:`~dtollib.manager.DtolManager`)
and the SDK-binding layer (:mod:`dtollib.capi`).

Two concrete implementations:

- :class:`~dtollib.backend.dataacq.DataAcqBackend` — wraps the real
  DataAcq SDK via :class:`~dtollib.capi.OpenLayersApi`.  Windows-only
  at runtime.
- :class:`~dtollib.backend.fake.FakeDtolBackend` — pure-Python
  in-memory fake used by every unit test.  Cross-platform.  Enforces
  the same ordering invariants as the real SDK so unit tests catch
  the same bugs hardware would.
"""

from __future__ import annotations

from dtollib.backend.base import DtolBackend
from dtollib.backend.dataacq import DataAcqBackend
from dtollib.backend.fake import FakeDtolBackend

__all__ = ["DataAcqBackend", "DtolBackend", "FakeDtolBackend"]
