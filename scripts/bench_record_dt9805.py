"""Bench DoD — WS-A0: continuous AI ``record()`` on the live DT9805.

Proves the production path (NOT a probe): the ``olDaSetWndHandle`` + message-pump
bridge now delivers ``OLDA_WM_BUFFER_DONE`` through ``record()`` so ``DaqBlock``s
actually arrive on real hardware.  Before WS-A0 this hung with buffers stuck
INPROCESS (the silent ship-blocker).

Reads whatever is on AI ch0/ch1 — no external stimulus required.

Run:
    uv run --no-sync python scripts/bench_record_dt9805.py
"""

from __future__ import annotations

import time

import anyio

from dtollib import (
    AnalogInputVoltage,
    BufferPlan,
    DataFlow,
    TaskSpec,
    Timing,
    open_device,
    record,
)

RATE_HZ = 1000.0
SAMPLES_PER_BUFFER = 100
N_CHANNELS = 2
RUN_SECONDS = 5.0


def _spec() -> TaskSpec:
    return TaskSpec(
        name="bench_record",
        board="DT9805(00)",
        channels=[
            AnalogInputVoltage(physical_channel=0, name="ch0"),
            AnalogInputVoltage(physical_channel=1, name="ch1"),
        ],
        data_flow=DataFlow.CONTINUOUS,
        timing=Timing(rate_hz=RATE_HZ),
        buffers=BufferPlan(buffers=4, samples_per_buffer=SAMPLES_PER_BUFFER),
    )


async def main() -> None:
    blocks = 0
    samples = 0
    first_at: float | None = None
    deadline = time.monotonic() + RUN_SECONDS
    print(
        f"record() on DT9805(00): {RATE_HZ:.0f} Hz x{N_CHANNELS} ch, "
        f"{SAMPLES_PER_BUFFER} samp/buf, {RUN_SECONDS:.0f} s"
    )
    async with (
        await open_device(_spec(), autostart=False) as session,
        record(session) as recording,
    ):
        summary = recording.summary
        with anyio.move_on_after(RUN_SECONDS + 5.0):
            async for block in recording.stream:
                if first_at is None:
                    first_at = time.monotonic()
                    print(f"  first block after {first_at - (deadline - RUN_SECONDS):.3f} s")
                blocks += 1
                samples += int(block.data.shape[1])
                if blocks <= 3 or blocks % 10 == 0:
                    row0 = block.data[0, :4].tolist()
                    print(f"  block {block.block_index}: shape={block.data.shape} ch0[:4]={row0}")
                if time.monotonic() >= deadline:
                    break
    expected = RATE_HZ * RUN_SECONDS
    print(f"--- {blocks} blocks, {samples} samples (~{expected:.0f} expected at {RATE_HZ:.0f} Hz)")
    print(
        f"--- summary: payloads_emitted={summary.payloads_emitted} "
        f"overruns={getattr(summary, 'overruns', '?')}"
    )
    if blocks == 0:
        print("FAIL: no blocks — buffer-done events did not flow (WS-A0 regression).")
    else:
        print("PASS: continuous AI delivered blocks through the production record() path.")


if __name__ == "__main__":
    anyio.run(main)
