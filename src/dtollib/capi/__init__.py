"""Low-level ctypes binding to the Data Translation DataAcq SDK.

This package is the **binding-internal** seam. Users live in the typed
Python layer (:class:`~dtollib.tasks.TaskSpec`,
:class:`~dtollib.tasks.DtolSession`, ...); :mod:`dtollib.capi` is the
documented escape hatch, not the path of first resort.

Three-layer structure (docs/design.md §10.3):

1. :mod:`dtollib.capi.prototypes` — raw ctypes signatures
   (``argtypes`` / ``restype``). No state, no error wrapping.
2. :mod:`dtollib.capi.api` :: :class:`OpenLayersApi` — output-pointer
   extraction + ECODE → typed-exception classification.
3. :mod:`dtollib.backend.dataacq` :: ``DataAcqBackend`` — session-level
   orchestration (capability cache, notification wrappers, buffer
   pool). Never touches ctypes directly.

The discovery / lifecycle / capability surface binds the 15 functions
listed in docs/design.md §26. Configuration setters, data-flow setters,
buffer management, and the notification bridge handle single-value and
continuous operation.
"""

from __future__ import annotations

from dtollib.capi.loader import OpenLayersDlls, load_openlayers

__all__ = ["OpenLayersDlls", "load_openlayers"]
