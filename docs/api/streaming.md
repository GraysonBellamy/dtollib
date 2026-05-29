# Continuous acquisition & playback

`record()` / `record_polled()` stream a continuous input task as `DaqBlock`s;
`play()` drives a continuous analog-output task from a waveform source. See
[Continuous acquisition](../continuous.md) and
[Waveform output](../waveform-output.md) for the narrative.

## Recorders, playback, and policies

::: dtollib.streaming

## Block and reading payloads

`DaqBlock`, `DaqReading`, and `DaqSample` are documented under
[Task specs → models](tasks.md). `block_to_long_rows` reshapes a block into
tidy per-sample rows for sinks.
