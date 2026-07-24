# Raw logging (`.dt-raw`) and replay

`RawCountsSink` is the durable, loss-proof logger unique to `dtollib`. It
writes the **raw int16/int32 codes** the SDK handed us — not the
volt-converted floats — to a self-describing `.dt-raw` file, so a capture is
faithfully preserved exactly as the hardware produced it and can be
re-converted (or re-linearised) later with no loss.

Why a custom format instead of TDMS: no third-party dependency, dead-simple
to read in NumPy / MATLAB / any tool that can `np.fromfile`, and it preserves
precisely what the SDK gave us.

Design reference: [design.md §15.2](design.md#152-rawcountssink-the-tdms-equivalent).

## When to use it

- **Long unattended soaks** — fed at the head of the consume loop under
  `OverflowPolicy.BLOCK`, the sink's synchronous `write_raw` back-pressures
  the SDK buffer queue, so the durable file stays complete even when
  downstream processing falls behind.
- **TC / strain captures you may re-process** — store raw codes now,
  linearise later when calibration improves, without re-running the rig.
- **Archival** — the JSON file header records the channels, rate, units,
  and encoding needed to reconstruct engineering units offline.

## Writing a `.dt-raw` file

```python
import anyio
from dtollib import (
    AnalogInputVoltage,
    BufferPlan,
    DataFlow,
    OverflowPolicy,
    TaskSpec,
    Timing,
    open_device,
    record,
)
from dtollib.sinks import RawCountsSink


async def main() -> None:
    spec = TaskSpec(
        name="soak",
        channels=[AnalogInputVoltage(physical_channel=0, name="ch0")],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=1000.0),
        buffers=BufferPlan(buffers=4, samples_per_buffer=1000),
    )
    async with (
        await open_device(spec, autostart=False) as session,
        RawCountsSink("soak.dt-raw") as sink,
        # BLOCK overflow makes the consume loop back-pressure the SDK queue
        # rather than drop blocks — required for a loss-proof raw capture.
        record(session, overflow=OverflowPolicy.BLOCK) as recording,
    ):
        async for block in recording.stream:
            await sink.write_raw(block)  # durable copy of the raw codes
            ...  # plus any live view / processing


anyio.run(main)
```

`record()` has no `raw_sink` parameter — the sink is fed from the consume
loop. Write each block to the sink **first**, before any work that could
fall behind, so the durable file stays ahead of slow downstream processing.
This is exactly what `dtol-capture` does internally.

From the CLI:

```bash
dtol-capture --channels 0,1 --rate 1000 --duration 3600 --out soak.dt-raw
```

## File format (`.dt-raw` v2)

```
file_header_len : uint32 (little-endian)
file_header_json: bytes            # channels, rate, units, encoding, dtype
(chunk_record)*

chunk_record = chunk_header_len : uint32
             + chunk_header_json : bytes   # block_index, samples, timestamps, error flags
             + chunk_payload     : bytes   # raw int16/int32 codes, C-order
```

One chunk record per `DaqBlock`. A crash mid-run loses at most the chunk
being written.

## Replaying

`dtollib.tools.replay_raw` reads a `.dt-raw` file back into `DaqBlock`s,
re-applying the conversion captured in the header:

```python
from dtollib.tools.replay_raw import read_file_header, iter_blocks

header = read_file_header("soak.dt-raw")
print(header.channels, header.sample_rate_hz)

for block in iter_blocks("soak.dt-raw"):
    print(block.block_index, block.data.shape)
```

- `read_file_header(path) -> RawFileHeader` — the file-level metadata.
- `iter_blocks(path) -> Iterator[DaqBlock]` — stream blocks back in order
  with `raw_codes` populated. Each block's `data` is the stored codes as
  `float64` (not yet in engineering units); re-apply the channel ranges
  from the header to recover volts/temperature. Chunk `flags` (`partial`,
  `overrun_marker`) are preserved via `block.error`.

## Reading raw codes directly (NumPy / MATLAB)

The payloads are plain little-endian integers in C order, so any tool can
read them after skipping the JSON headers — `read_file_header` reports the
`dtype` and channel order needed to reshape each chunk to
`(n_channels, samples_per_channel)`.
