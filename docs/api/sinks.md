# Sinks

Durable consumers of `DaqBlock` / `DaqReading` data. Every sink is an async
context manager satisfying the sink Protocol. See the [Sinks guide](../sinks.md)
for choosing among them and the [Raw logging guide](../raw-logging.md) for the
loss-proof `.dt-raw` path.

## Sink Protocol and helpers

::: dtollib.sinks.base

## CSV

::: dtollib.sinks.csv

## JSONL

::: dtollib.sinks.jsonl

## Parquet

::: dtollib.sinks.parquet

## SQLite

::: dtollib.sinks.sqlite

## Postgres

::: dtollib.sinks.postgres

## In-memory

::: dtollib.sinks.memory

## Raw counts (`.dt-raw`)

::: dtollib.sinks.raw_counts
