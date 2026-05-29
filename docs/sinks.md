# Sinks — durable logging

A **sink** is a durable consumer of acquisition data. Every sink is an async
context manager: open it, write records, and it flushes and releases its
backing resource on exit. Sinks are never invoked automatically — you wire a
recorder stream to a sink in your consume loop (or via the
[`pipe`](#piping-streams-to-sinks) drivers).

Design reference: [design.md §15](design.md). The loss-proof raw-counts path has
its [own guide](raw-logging.md).

## The three Protocols

Sinks are typed by the shape they accept:

| Protocol | Accepts | Sinks |
|----------|---------|-------|
| `ReadingSink` | `Sequence[DaqReading]` (via `write_many`) | tabular sinks in reading mode |
| `BlockSink` | one `DaqBlock` per call (via `write`) | CSV / JSONL / SQLite / Parquet / Postgres |
| `RawBlockSink` | raw HBUF payloads (via `write_raw`) | [`RawCountsSink`](raw-logging.md) |

The tabular sinks (CSV, JSONL, SQLite, Parquet, Postgres) accept readings by
default and **also** accept blocks when constructed with `accept_blocks=True`
(CSV/JSONL) or natively (SQLite/Parquet/Postgres).

## Choosing a sink

| Sink | Extra | Best for |
|------|-------|----------|
| `CsvSink` | — | Quick human-readable export, spreadsheet import. |
| `JsonlSink` | — | Line-delimited JSON for log pipelines / `jq`. |
| `SqliteSink` | — | Single-file queryable store; WAL journaling, per-table schema lock. |
| `ParquetSink` | `dtollib[parquet]` | Columnar analytics; `zstd` compression, shape locked on first write. |
| `PostgresSink` | `dtollib[postgres]` | Centralised multi-run database. |
| `InMemorySink` | — | Tests and short-lived inspection. |
| `RawCountsSink` | — | Loss-proof archival of the raw codes — see [Raw logging](raw-logging.md). |

## Example — stream blocks to SQLite

```python
import anyio

from dtollib import BufferPlan, DataFlow, AnalogInputVoltage, TaskSpec, Timing, open_device, record
from dtollib.sinks import SqliteSink


async def main() -> None:
    spec = TaskSpec(
        name="run",
        channels=[AnalogInputVoltage(physical_channel=0, name="ch0")],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=1000.0),
        buffers=BufferPlan(buffers=4, samples_per_buffer=1000),
    )
    async with (
        await open_device(spec, autostart=False) as session,
        SqliteSink("run.db") as sink,
        record(session) as recording,
    ):
        async for block in recording.stream:
            await sink.write(block)


anyio.run(main)
```

Swap `SqliteSink("run.db")` for `CsvSink("run.csv", accept_blocks=True)`,
`ParquetSink("run.parquet")`, or `JsonlSink("run.jsonl", accept_blocks=True)` —
the loop is identical.

## Schema lock

The tabular sinks lock their column set on the **first** write. Later records
project onto the locked schema; unknown columns are dropped with a one-shot
WARN rather than silently corrupting the table or raising mid-run. Keep your
channel set stable for the life of one sink file.

## Constructor knobs

```python
CsvSink(path, *, accept_blocks=False)
JsonlSink(path, *, accept_blocks=False)
SqliteSink(path, *, table_readings="readings", table_blocks="blocks",
           journal_mode="WAL", synchronous="NORMAL", busy_timeout_ms=5000)
ParquetSink(path, *, compression="zstd", use_dictionary=True, row_group_size=None)
PostgresSink(PostgresConfig(...))
RawCountsSink(path, *, file_metadata=None)
```

See the [Sinks API reference](api/sinks.md) for every field.

## Piping streams to sinks

Two helper drivers in `dtollib.sinks` drain a stream into a sink for you:

- `pipe(stream, sink, *, batch_size=64, flush_interval_s=1.0)` — row-oriented,
  batches `DaqReading`s into `write_many` calls.
- `pipe_blocks(stream, sink, *, flush_interval_s=None)` — block-native, one
  `write_block` per `DaqBlock`.

```python
from dtollib.sinks import SqliteSink, pipe_blocks

async with (
    await open_device(spec, autostart=False) as session,
    SqliteSink("run.db") as sink,
    record(session) as recording,
):
    written = await pipe_blocks(recording.stream, sink)
```

For loss-proof archival, prefer the explicit consume loop with a
`RawCountsSink` under `OverflowPolicy.BLOCK` — see [Raw logging](raw-logging.md).
