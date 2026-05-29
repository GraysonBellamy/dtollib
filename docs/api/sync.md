# Sync facade

A blocking wrapper around the async API for scripts, notebooks, and REPL use.
`Dtol.open_device(...)` returns a `SyncDtolSession` that dispatches every call
through an `anyio` blocking portal — no parallel implementation. See the
[Sync quickstart](../quickstart-sync.md).

::: dtollib.sync
