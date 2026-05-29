"""Data sinks for ``DaqReading`` / ``DaqBlock`` outputs.

- :class:`ReadingSink` / :class:`BlockSink` / :class:`RawBlockSink` — Protocols.
- :class:`InMemorySink` — collects records in lists for tests and notebooks.
- Row helpers (:func:`reading_to_row`, :func:`block_to_rows`) and pipe
  drivers (:func:`pipe`, :func:`pipe_blocks`).
- Durable sinks: :class:`CsvSink`, :class:`JsonlSink`, :class:`SqliteSink`,
  :class:`ParquetSink`, :class:`PostgresSink`, :class:`RawCountsSink`.

:class:`ParquetSink` and :class:`PostgresSink` need the ``parquet`` /
``postgres`` extras; importing the names always succeeds, but constructing
or opening them without the extra raises
:class:`~dtollib.errors.DtolSinkDependencyError`.
"""

from __future__ import annotations

from dtollib.sinks.base import (
    BlockSink,
    RawBlockSink,
    ReadingSink,
    block_to_rows,
    pipe,
    pipe_blocks,
    reading_to_row,
)
from dtollib.sinks.csv import CsvSink
from dtollib.sinks.jsonl import JsonlSink
from dtollib.sinks.memory import InMemorySink
from dtollib.sinks.parquet import ParquetSink
from dtollib.sinks.postgres import PostgresConfig, PostgresSink
from dtollib.sinks.raw_counts import RawCountsSink
from dtollib.sinks.sqlite import SqliteSink

__all__ = [
    "BlockSink",
    "CsvSink",
    "InMemorySink",
    "JsonlSink",
    "ParquetSink",
    "PostgresConfig",
    "PostgresSink",
    "RawBlockSink",
    "RawCountsSink",
    "ReadingSink",
    "SqliteSink",
    "block_to_rows",
    "pipe",
    "pipe_blocks",
    "reading_to_row",
]
