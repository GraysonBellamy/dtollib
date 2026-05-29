# Testing helpers

`FakeDtolBackend` is a faithful, hardware-free stand-in for the real backend —
it enforces the same ordering and capability rules the SDK does. The
`dtollib.testing` factories build pre-canned backends and capability sets for
the DT9805 / DT9806 and multi-sensor modules. See the
[Testing guide](../testing.md).

## Fixtures

::: dtollib.testing

## Fake backend

::: dtollib.backend.fake
