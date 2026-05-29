# `dtollib.backend`

Session orchestration above the `capi` layer — HDRVR refcount, capability
cache, and the notification bridge. The real backend never touches `ctypes`
directly; that is the `capi` layer's job.

## dataacq

::: dtollib.backend.dataacq
