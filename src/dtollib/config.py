"""Process-wide configuration for :mod:`dtollib`.

Plain frozen dataclass, no validation library — keeps the core install free
of optional deps. Env-var coercion lives in :func:`config_from_env`.

Design reference: ``docs/design.md`` §19.1.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Final, Self

DEFAULT_ENV_PREFIX: Final[str] = "DTOLLIB_"


@dataclass(frozen=True, slots=True, kw_only=True)
class DtolConfig:
    """Process-wide default settings.

    Anything that varies *per task* (channel ranges, trigger source,
    buffer plan) belongs on :class:`~dtollib.tasks.TaskSpec`, not here.

    Attributes:
        default_timeout_s: Fallback SDK read/write timeout, in seconds.
            Used when the call site does not supply one explicitly.
        default_sample_rate_hz: Fallback ``Timing.rate_hz`` when the
            :class:`~dtollib.tasks.Timing` field is unset.
        default_chunk_size: Samples per channel per emitted ``DaqBlock``
            for ``record()``.
        default_buffers: ``BufferPlan.buffers`` default — number of HBUFs
            in the Ready / Inprocess / Done cycle. Hard minimum is 3.
            QuickDAQ defaults to 4; we match.
        default_stream_buffer: AnyIO send-stream capacity for ``record()``,
            measured in :class:`~dtollib.tasks.DaqBlock` slots.
        eager_tasks: Opt-in to ``asyncio.eager_task_factory``. No-op on
            trio. See :func:`dtollib._runtime.install_eager_task_factory`.
        oldaapi_dll_path: Explicit override for the ``oldaapi*.dll`` path.
            ``None`` = use the loader's default resolution chain. Consumed
            by the loader during discovery / lifecycle setup.
        olmem_dll_path: Explicit override for the ``olmem*.dll`` path.
            Consumed by the loader the same way as ``oldaapi_dll_path``.
    """

    default_timeout_s: float = 10.0
    default_sample_rate_hz: float = 1000.0
    default_chunk_size: int = 1000
    default_buffers: int = 4
    default_stream_buffer: int = 16
    eager_tasks: bool = False
    oldaapi_dll_path: str | None = None
    olmem_dll_path: str | None = None

    def replace(self, **updates: Any) -> Self:
        """Return a copy of this config with ``updates`` applied."""
        return replace(self, **updates)


def config_from_env(prefix: str = DEFAULT_ENV_PREFIX) -> DtolConfig:
    """Best-effort env loader.

    Only reads well-known keys. Missing or unparseable values fall back to
    :class:`DtolConfig`'s defaults — this function never raises.

    Recognised keys (with ``prefix="DTOLLIB_"``):

    - ``DTOLLIB_DEFAULT_TIMEOUT_S`` — float seconds
    - ``DTOLLIB_DEFAULT_SAMPLE_RATE_HZ`` — float Hz
    - ``DTOLLIB_DEFAULT_CHUNK_SIZE`` — int samples
    - ``DTOLLIB_DEFAULT_BUFFERS`` — int (clamped at 3 by ``BufferPlan``;
      stored verbatim here)
    - ``DTOLLIB_DEFAULT_STREAM_BUFFER`` — int slots
    - ``DTOLLIB_EAGER_TASKS`` — ``"1"`` / ``"true"`` / ``"yes"``
    - ``DTOLLIB_OLDAAPI_DLL`` — explicit ``oldaapi*.dll`` path
    - ``DTOLLIB_OLMEM_DLL`` — explicit ``olmem*.dll`` path

    Args:
        prefix: Prefix to prepend to each env key. Defaults to
            ``"DTOLLIB_"``.

    Returns:
        A :class:`DtolConfig` populated from env where parseable.
    """
    base = DtolConfig()
    return DtolConfig(
        default_timeout_s=_float_env(f"{prefix}DEFAULT_TIMEOUT_S", base.default_timeout_s),
        default_sample_rate_hz=_float_env(
            f"{prefix}DEFAULT_SAMPLE_RATE_HZ", base.default_sample_rate_hz
        ),
        default_chunk_size=_int_env(f"{prefix}DEFAULT_CHUNK_SIZE", base.default_chunk_size),
        default_buffers=_int_env(f"{prefix}DEFAULT_BUFFERS", base.default_buffers),
        default_stream_buffer=_int_env(
            f"{prefix}DEFAULT_STREAM_BUFFER", base.default_stream_buffer
        ),
        eager_tasks=_bool_env(f"{prefix}EAGER_TASKS", base.eager_tasks),
        oldaapi_dll_path=_str_env(f"{prefix}OLDAAPI_DLL", base.oldaapi_dll_path),
        olmem_dll_path=_str_env(f"{prefix}OLMEM_DLL", base.olmem_dll_path),
    )


def _float_env(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _str_env(key: str, default: str | None) -> str | None:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return raw


_TRUE_STRS: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_FALSE_STRS: Final[frozenset[str]] = frozenset({"0", "false", "no", "off", ""})


def _bool_env(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE_STRS:
        return True
    if lowered in _FALSE_STRS:
        return False
    return default


__all__ = ["DEFAULT_ENV_PREFIX", "DtolConfig", "config_from_env"]
