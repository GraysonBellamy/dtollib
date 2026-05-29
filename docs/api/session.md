# Sessions

`DtolSession` is the central acquisition object: it owns one configured
subsystem and exposes `poll()` / `write()` / `read_events()` /
`measure_frequency()` / `start()` / `stop()` / `capabilities()`. Open one with
[`open_device`](#dtollib.factory.open_device); use it as an async context
manager so the subsystem is committed on entry and released on exit.

See the [Async quickstart](../quickstart-async.md) for the happy path and
[Safety](../safety.md) for the `write()` gate model.

## `open_device`

::: dtollib.factory

## `DtolSession`

::: dtollib.tasks.session
