# Command-line tools

`dtollib` installs five console scripts. All are Windows-only against real
hardware, but `--backend fake` (where offered) lets you exercise them anywhere.
Every command supports `--json` for machine-readable output.

| Command | Purpose |
|---------|---------|
| [`dtol-diag`](#dtol-diag) | Diagnose the SDK / DLL / driver install. |
| [`dtol-discover`](#dtol-discover) | List boards and their subsystems. |
| [`dtol-info`](#dtol-info) | Full per-board capability dump. |
| [`dtol-read`](#dtol-read) | One-shot scalar read from one AI channel. |
| [`dtol-capture`](#dtol-capture) | Continuous capture to a file. |

## `dtol-diag`

Diagnoses DataAcq SDK / DLL / driver install issues — the first thing to run on
a new machine. Reports the loaded DLL paths, both SDK versions, and the
enumerated boards, with an overall `PASS` / `FAIL`. Exits non-zero on failure
and prints every candidate path the loader tried.

```pwsh
dtol-diag
dtol-diag --json     # full machine-readable report (attach to bug reports)
```

See [Installation](installation.md#verify-the-install) for sample output and
[Troubleshooting](troubleshooting.md) for decoding failures.

## `dtol-discover`

Enumerates boards and the subsystems on each.

```pwsh
dtol-discover
dtol-discover --board "DT9805(00)"   # limit to one board
dtol-discover --json
```

| Flag | Default | Notes |
|------|---------|-------|
| `--board NAME` | all | Limit output to a single board. |
| `--json` | off | Emit JSON instead of formatted text. |

## `dtol-info`

A full capability dump per board — every `OLSSC_*` flag, channel counts,
throughput, and ranges. Use it to confirm what a board can actually do before
writing a `TaskSpec` (the programmatic equivalent is
[`session.capabilities()`](discovery.md)).

```pwsh
dtol-info
dtol-info --board "DT9806(00)" --json
```

| Flag | Default | Notes |
|------|---------|-------|
| `--board NAME` | all | Limit output to a single board. |
| `--json` | off | Emit JSON instead of text. |
| `--backend` | `real` | `real` or `fake`. |

## `dtol-read`

A one-shot scalar read from a single analog-input channel — handy for a quick
sanity check at the bench.

```pwsh
dtol-read --channel 0 --range -10,10
dtol-read --board "DT9805(00)" --channel 2 --gain 8 --json
dtol-read --channel 0 --backend fake     # works with no hardware
```

| Flag | Default | Notes |
|------|---------|-------|
| `--board NAME` | first discovered | Board name. |
| `--channel N` | `0` | Physical channel index. |
| `--range MIN,MAX` | `-10,10` | Input voltage range (volts). |
| `--gain G` | `1.0` | Programmable-gain setting. |
| `--json` | off | Emit JSON instead of text. |
| `--backend` | `real` | `real` or `fake`. |

## `dtol-capture`

Continuous, hardware-clocked capture straight to a file — the CLI front end for
[`record()`](continuous.md) plus a [sink](sinks.md). It feeds each block to the
sink at the head of the consume loop so the file stays ahead of slow downstream
processing.

```pwsh
dtol-capture --channels 0,1 --rate 1000 --duration 3600 --out soak.dt-raw
```

| Flag | Default | Notes |
|------|---------|-------|
| `--channels` | `0` | Comma-separated physical channels. |
| `--rate` | `1000` | Sample rate (Hz). |
| `--duration` | `10` | Acquisition duration (seconds). |
| `--samples-per-buffer` | `1000` | Samples per HBUF. |
| `--buffers` | `4` | HBUFs in the pool (min 3). |
| `--board NAME` | first discovered | Board name. |
| `--out PATH` | **required** | Output file — the extension selects the sink. |
| `--backend` | `real` | `real` or `fake`. |

The output extension drives sink selection:

| Extension | Sink |
|-----------|------|
| `.dt-raw` | `RawCountsSink` — loss-proof raw codes ([Raw logging](raw-logging.md)). |
| `.csv` | `CsvSink` (block mode). |
| `.jsonl` | `JsonlSink` (block mode). |
