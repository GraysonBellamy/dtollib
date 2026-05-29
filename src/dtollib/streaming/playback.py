"""``play()`` — hardware-clocked continuous analog-output (waveform playback).

The output mirror of :func:`dtollib.streaming.record`. Where ``record()`` owns
an *input* buffer pool and the input callback bridge, ``play()`` owns an
*output* pool (filled, not drained) and the output callback bridge. It enforces
the §18 confirm gate on every sample before any waveform reaches the DAC.

Two source shapes select the two wrap modes:

- **One period (``np.ndarray``)** with ``WrapMode.SINGLE``: the array is laid
  across the buffer ring and the SDK loops it as one continuous waveform. No
  refill happens.
- **A stream (async iterator or ``() -> np.ndarray | None`` callable)** with
  ``WrapMode.MULTIPLE``: each ``BUFFER_DONE`` pulls the next chunk, fills the
  emptied buffer, and re-queues it. ``None`` ends finite playback cleanly.

Startup ordering mirrors ``record()`` (docs/decisions.md): ``commit``
(``olDaConfig`` #1) → register (inside the bridge) → ``seed_all`` → ``queue_all``
→ ``arm`` (``olDaConfig`` #2) → ``start``. Teardown is shielded inside the
bridge; the pool is freed here.

Design reference: docs/design.md §12.3.2, §18; docs/plan-hardware-functional.md
§WS-AO; docs/waveform-output.md.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any, cast

import anyio.to_thread as anyio_to_thread

from dtollib.backend._buffer_pool import BufferDirection, BufferPool
from dtollib.backend._output_callback_bridge import (
    OutputBridgeConfig,
    output_callback_bridge,
)
from dtollib.channels.analog_output import AnalogOutputVoltage
from dtollib.errors import (
    DtolCapabilityError,
    DtolTaskStateError,
    DtolValidationError,
    ErrorContext,
)
from dtollib.streaming._types import ErrorPolicy
from dtollib.tasks._output_gate import ao_volts_to_codes, gate_ao_samples
from dtollib.tasks.models import DataFlow, WrapMode

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable

    import numpy.typing as npt

    from dtollib.streaming._types import AcquisitionSummary
    from dtollib.tasks.session import DtolSession


__all__ = ["PlaybackSource", "play"]


# A one-shot period, a sync chunk producer, or an async chunk stream. ``None``
# from either producer marks the end of a finite stream.
type PlaybackSource = (
    npt.NDArray[Any] | Callable[[], npt.NDArray[Any] | None] | AsyncIterator[npt.NDArray[Any]]
)


_SAMPLE_DTYPE_BYTES = 2  # DT9806 D/A is 16-bit (offset-binary codes).
_CHANNEL_ROWS_NDIM = 2  # a normalised chunk is (n_channels, n_samples).


@asynccontextmanager
async def play(
    session: DtolSession,
    source: PlaybackSource,
    *,
    confirm: bool = False,
    error_policy: ErrorPolicy = ErrorPolicy.RAISE,
) -> AsyncGenerator[AcquisitionSummary]:
    """Drive continuous analog output from ``source``.

    Args:
        session: A session for an analog-output continuous task — every
            channel an :class:`~dtollib.channels.analog_output.AnalogOutputVoltage`,
            ``spec.data_flow == CONTINUOUS``, ``spec.buffers.wrap_mode`` one of
            ``SINGLE`` / ``MULTIPLE``. Open it with ``autostart=False`` (this
            facade drives the lifecycle).
        source: For ``WrapMode.SINGLE``, a single-period ``np.ndarray`` shaped
            ``(n_channels, ring_capacity)`` (or ``(ring_capacity,)`` for one
            channel), where ``ring_capacity == buffers * samples_per_buffer``.
            For ``WrapMode.MULTIPLE``, an async iterator or a ``() -> ndarray |
            None`` callable yielding ``(n_channels, samples_per_buffer)`` chunks
            (``None`` = end of finite playback).
        confirm: Operator confirmation for the §18 safety gate, exactly as
            :meth:`DtolSession.write`. Required if any sample leaves the safe
            band or any channel is ``requires_confirm``.
        error_policy: How SDK errors reaching the producer loop are surfaced
            (``RAISE`` cancels playback; ``RETURN`` / ``SKIP`` log + count).

    Yields:
        The mutable :class:`AcquisitionSummary` for the run.

    Raises:
        DtolCapabilityError: The subsystem's D/A is single-value only
            (``OLSSC_SUP_CONTINUOUS=0``) — e.g. the DT9806. Use
            :meth:`DtolSession.write` for single-value output.
        DtolTaskStateError: Wrong data-flow / wrap mode, or a non-AO task.
        DtolValidationError: Source shape mismatch, or a sample outside the
            device range (raised *before* any waveform reaches the DAC).
        DtolConfirmationRequiredError: Safety gate tripped without ``confirm``.
    """
    await session.configure()
    spec = session.spec
    channels = list(spec.channels)
    _validate_output_task(spec, channels)

    # Hardware gate: continuous AO needs a streaming DAC (FIFO + wrap modes).
    # The DT9806 D/A is single-value-only — it reports OLSSC_SUP_CONTINUOUS=0
    # and rejects every continuous setter with OLNOTSUPPORTED (ec=36,
    # bench-confirmed 2026-05-28). Fail loud here with a clear, typed error
    # rather than dying mid-startup at olDaConfig. play() stays correct for a
    # board whose D/A *does* report continuous support.
    if not session.capabilities.supports_continuous:
        raise DtolCapabilityError(
            f"play() needs a continuous (streaming) analog-output subsystem, but "
            f"the D/A on {spec.board!r} reports OLSSC_SUP_CONTINUOUS=0 — it is "
            "single-value only. Use DtolSession.write() for single-value output. "
            "(Bench-confirmed on the DT9806; see docs/decisions.md.)",
            context=ErrorContext(operation="play", task_name=spec.name),
        )

    wrap = spec.buffers.wrap_mode  # type: ignore[union-attr]  # validated above
    n_buffers = spec.buffers.buffers  # type: ignore[union-attr]
    samples_per_buffer = spec.buffers.samples_per_buffer  # type: ignore[union-attr]

    # Build + validate the seed chunks (and the refill puller) BEFORE any SDK
    # lifecycle call, so a bad waveform fails loudly pre-seed (§18 gate).
    seed_chunks, pull = await _prepare_source(
        source,
        channels=channels,
        wrap=wrap,
        n_buffers=n_buffers,
        samples_per_buffer=samples_per_buffer,
        confirm=confirm,
        task_name=spec.name,
    )

    backend = session.backend
    hdass = session.raw_hdass
    pool = BufferPool(
        backend,
        hdass,
        spec.buffers,  # type: ignore[arg-type]  # validated non-None above
        n_channels=len(channels),
        sample_dtype_bytes=_SAMPLE_DTYPE_BYTES,
        direction=BufferDirection.OUTPUT,
    )
    pool.allocate()

    config = OutputBridgeConfig(
        device=spec.name,
        task=spec.name,
        wrap_mode=wrap,
        error_policy=error_policy,
        # OLSSC_SUP_MUTE position is header-verified but bench read-back is
        # still pending (WS-B). The capability is wired through here; on a
        # subsystem that reports it false (incl. the fake) the bridge degrades
        # gracefully and skips the pre-stop mute.
        supports_mute=session.capabilities.supports_mute,
    )

    try:
        # Bench-proven continuous startup ordering (docs/decisions.md):
        #   commit (#1) → register (bridge entry) → seed → queue → arm (#2) → start.
        await anyio_to_thread.run_sync(backend.commit, hdass)
        async with output_callback_bridge(backend, hdass, pool, config, pull=pull) as summary:
            pool.seed_all(seed_chunks)
            pool.queue_all()
            await anyio_to_thread.run_sync(backend.arm, hdass)
            await anyio_to_thread.run_sync(backend.start, hdass)
            yield summary
    finally:
        # Bridge shutdown is shielded inside output_callback_bridge; the pool
        # is freed here, after drain-wait completed in the bridge __aexit__.
        with suppress(Exception):
            pool.flush()
        with suppress(Exception):
            pool.free_all()


def _validate_output_task(spec: Any, channels: list[Any]) -> None:
    """Reject anything that is not an AO continuous SINGLE/MULTIPLE task."""
    if spec.data_flow != DataFlow.CONTINUOUS:
        raise DtolTaskStateError(
            f"play() requires data_flow=CONTINUOUS; got {spec.data_flow.value}. "
            "Single-value AO writes go through DtolSession.write().",
            context=ErrorContext(operation="play", task_name=spec.name),
        )
    if spec.buffers is None:
        raise DtolTaskStateError(
            "play() requires TaskSpec.buffers to be configured",
            context=ErrorContext(operation="play", task_name=spec.name),
        )
    if spec.buffers.wrap_mode not in {WrapMode.SINGLE, WrapMode.MULTIPLE}:
        raise DtolTaskStateError(
            f"play() requires buffers.wrap_mode in {{SINGLE, MULTIPLE}}; "
            f"got {spec.buffers.wrap_mode.value}",
            context=ErrorContext(operation="play", task_name=spec.name),
        )
    if not channels or not all(isinstance(c, AnalogOutputVoltage) for c in channels):
        raise DtolTaskStateError(
            "play() drives analog output; every channel must be an "
            "AnalogOutputVoltage (continuous DO/CT playback is not supported)",
            context=ErrorContext(operation="play", task_name=spec.name),
        )


async def _prepare_source(
    source: PlaybackSource,
    *,
    channels: list[Any],
    wrap: WrapMode,
    n_buffers: int,
    samples_per_buffer: int,
    confirm: bool,
    task_name: str,
) -> tuple[list[bytes], Callable[[], Awaitable[bytes | None]] | None]:
    """Build the pre-seed chunks and (for MULTIPLE) the refill puller.

    Returns ``(seed_chunks, pull)`` — ``pull`` is ``None`` for SINGLE.
    """
    import numpy as np  # noqa: PLC0415

    n_channels = len(channels)

    if wrap is WrapMode.SINGLE:
        if not isinstance(source, np.ndarray):
            raise DtolValidationError(
                "play(): WrapMode.SINGLE requires a single-period np.ndarray source",
                context=ErrorContext(operation="play", task_name=task_name),
            )
        arr = _as_channel_rows(source, n_channels, task_name=task_name)
        ring_capacity = n_buffers * samples_per_buffer
        if arr.shape[1] != ring_capacity:
            raise DtolValidationError(
                f"play(): SINGLE source has {arr.shape[1]} samples/channel; the buffer ring "
                f"holds {ring_capacity} (buffers={n_buffers} x samples_per_buffer="
                f"{samples_per_buffer}). Resize the period to match.",
                context=ErrorContext(operation="play", task_name=task_name),
            )
        chunks = [
            _encode_chunk(
                arr[:, i * samples_per_buffer : (i + 1) * samples_per_buffer],
                channels,
                confirm=confirm,
                task_name=task_name,
            )
            for i in range(n_buffers)
        ]
        return chunks, None

    # WrapMode.MULTIPLE — stream the source through a shared puller.
    pull_raw = _make_raw_puller(source, task_name=task_name)

    async def pull_encoded() -> bytes | None:
        raw = await pull_raw()
        if raw is None:
            return None
        return _encode_chunk(
            _as_channel_rows(raw, n_channels, task_name=task_name, max_samples=samples_per_buffer),
            channels,
            confirm=confirm,
            task_name=task_name,
        )

    seed_chunks: list[bytes] = []
    for _ in range(n_buffers):
        chunk = await pull_encoded()
        if chunk is None:
            raise DtolValidationError(
                f"play(): MULTIPLE source yielded only {len(seed_chunks)} chunk(s); "
                f"at least {n_buffers} are required to seed the buffer ring",
                context=ErrorContext(operation="play", task_name=task_name),
            )
        seed_chunks.append(chunk)
    return seed_chunks, pull_encoded


def _make_raw_puller(
    source: PlaybackSource,
    *,
    task_name: str,
) -> Callable[[], Awaitable[npt.NDArray[Any] | None]]:
    """Adapt a callable or async-iterable source to ``async () -> ndarray | None``."""
    from collections.abc import AsyncIterator  # noqa: PLC0415

    if isinstance(source, AsyncIterator):
        iterator = cast("AsyncIterator[npt.NDArray[Any]]", source)  # type: ignore[redundant-cast]

        async def pull_aiter() -> npt.NDArray[Any] | None:
            try:
                return await iterator.__anext__()
            except StopAsyncIteration:
                return None

        return pull_aiter

    if callable(source):
        sync_source = source

        async def pull_callable() -> npt.NDArray[Any] | None:
            return sync_source()

        return pull_callable

    raise DtolValidationError(
        "play(): WrapMode.MULTIPLE source must be an async iterator or a "
        "`() -> ndarray | None` callable",
        context=ErrorContext(operation="play", task_name=task_name),
    )


def _as_channel_rows(
    data: Any,
    n_channels: int,
    *,
    task_name: str,
    max_samples: int | None = None,
) -> npt.NDArray[Any]:
    """Normalise ``data`` to a ``(n_channels, n_samples)`` float array."""
    import numpy as np  # noqa: PLC0415

    arr = np.asarray(data, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim != _CHANNEL_ROWS_NDIM or arr.shape[0] != n_channels:
        raise DtolValidationError(
            f"play(): source chunk shape {arr.shape} does not match the task's "
            f"{n_channels} channel(s); expected (n_channels, n_samples)",
            context=ErrorContext(operation="play", task_name=task_name),
        )
    if arr.shape[1] == 0:
        raise DtolValidationError(
            "play(): source chunk is empty",
            context=ErrorContext(operation="play", task_name=task_name),
        )
    if max_samples is not None and arr.shape[1] > max_samples:
        raise DtolValidationError(
            f"play(): source chunk has {arr.shape[1]} samples/channel; the buffer holds "
            f"at most {max_samples} (samples_per_buffer)",
            context=ErrorContext(operation="play", task_name=task_name),
        )
    return arr


def _encode_chunk(
    chunk: npt.NDArray[Any],
    channels: list[Any],
    *,
    confirm: bool,
    task_name: str,
) -> bytes:
    """§18-gate then encode a ``(n_channels, n_samples)`` volt chunk to codes.

    Returns the interleaved, code-domain (offset-binary ``uint16``) byte
    payload — sample-major (``[s0c0, s0c1, s1c0, ...]``), matching the layout
    the input drainer reshapes.
    """
    import numpy as np  # noqa: PLC0415

    n_samples = chunk.shape[1]
    n_channels = len(channels)
    codes = np.empty((n_samples, n_channels), dtype=np.uint16)
    for i, channel in enumerate(channels):
        column = chunk[i]
        ctx = ErrorContext(
            operation="play",
            task_name=task_name,
            channel=channel.physical_channel,
            channel_name=channel.name,
        )
        gate_ao_samples(
            channel,
            lo=float(column.min()),
            hi=float(column.max()),
            confirm=confirm,
            ctx=ctx,
            op="play",
        )
        codes[:, i] = ao_volts_to_codes(column, channel.min_val, channel.max_val)
    return codes.ravel().tobytes()
