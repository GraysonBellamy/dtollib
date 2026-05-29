"""Two-DLL discovery + loader for the DataAcq SDK.

The SDK ships as two cooperating DLLs:

- ``oldaapi*.dll`` — acquisition + configuration API.
- ``olmem*.dll`` — buffer management API.

They are loaded independently, with independent resolution chains
(explicit path → env-var override → default Windows install path → bare
DLL name). This module returns both handles on a single immutable
:class:`OpenLayersDlls` dataclass.

Failure modes:

- Non-Windows platform → :class:`~dtollib.errors.DtolDependencyError`
  with a clear platform message.
- 32-bit Python attempting a 64-bit DLL (or vice versa) →
  :class:`~dtollib.errors.DtolDependencyError` *before* the underlying
  ``ctypes.WinDLL`` raises its own opaque OS error.
- DLL not on any resolution-chain path →
  :class:`~dtollib.errors.DtolDependencyError` enumerating every path
  attempted, so the user can fix their install.

Loader behaviour is intentionally noisy on success (an INFO log line
naming the resolved DLL path and bitness) so ``dtol-diag`` and bug
reports surface the exact binary that loaded.

Design reference: docs/design.md §11.1.
"""

from __future__ import annotations

import logging
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from dtollib.errors import DtolDependencyError, ErrorContext

if TYPE_CHECKING:
    from ctypes import CDLL  # ``WinDLL`` is a subclass of ``CDLL``

__all__ = [
    "OpenLayersDlls",
    "default_oldaapi_paths",
    "default_olmem_paths",
    "load_openlayers",
    "python_bitness",
]


_logger = logging.getLogger("dtollib.capi.loader")


# Environment-variable names — kept in sync with
# :class:`dtollib.config.DtolConfig` and documented in
# docs/installation.md.
_ENV_OLDAAPI: Final[str] = "DTOLLIB_OLDAAPI_DLL"
_ENV_OLMEM: Final[str] = "DTOLLIB_OLMEM_DLL"

# Bitness sentinel values.  Named to keep ``ruff PLR2004`` honest and
# documented at one site rather than scattered.
_BITS_32: Final[int] = 32
_BITS_64: Final[int] = 64


def python_bitness() -> int:
    """Return Python interpreter pointer-size in bits (32 or 64)."""
    return struct.calcsize("P") * 8


def _system_root() -> Path:
    r"""Return ``%SystemRoot%`` (``C:\\Windows`` on a default install).

    Falls back to ``C:\\Windows`` if the env var is unset — only matters
    when this module is imported on Windows in a stripped-down shell.
    """
    # ``SystemRoot`` is the canonical name even though the variable
    # is treated case-insensitively by Windows; SIM112 prefers
    # uppercase, but matching Microsoft's own documentation
    # (``%SystemRoot%``) is more grep-friendly.
    raw = (
        os.environ.get("SystemRoot")  # noqa: SIM112 — see comment above
        or os.environ.get("WINDIR")
        or r"C:\Windows"
    )
    return Path(raw)


def default_oldaapi_paths(*, bitness: int | None = None) -> tuple[Path, ...]:
    """Default search paths for ``oldaapi*.dll`` on the current interpreter.

    Args:
        bitness: 32 or 64. Defaults to the current Python interpreter's
            pointer size.

    Returns:
        Ordered tuple of paths to try. The first existing path wins.
    """
    bits = bitness if bitness is not None else python_bitness()
    root = _system_root()
    if bits == _BITS_64:
        # 64-bit Python on 64-bit Windows: System32 holds the 64-bit DLLs.
        return (root / "System32" / "oldaapi64.dll",)
    # 32-bit Python on 64-bit Windows is redirected to SysWOW64 by the
    # file-system redirector; resolve the path explicitly to avoid
    # surprises if the loader is called from a 32-bit interpreter.
    return (root / "SysWOW64" / "oldaapi32.dll",)


def default_olmem_paths(*, bitness: int | None = None) -> tuple[Path, ...]:
    """Default search paths for ``olmem*.dll`` on the current interpreter."""
    bits = bitness if bitness is not None else python_bitness()
    root = _system_root()
    if bits == _BITS_64:
        return (root / "System32" / "olmem64.dll",)
    return (root / "SysWOW64" / "olmem32.dll",)


@dataclass(frozen=True, slots=True)
class OpenLayersDlls:
    """Loaded handles + provenance for the DataAcq SDK two-DLL pair.

    Construction goes through :func:`load_openlayers` — the dataclass is
    immutable so the loader's decisions don't drift over a session.

    Attributes:
        oldaapi: Handle to ``oldaapi*.dll``. Annotated as
            ``ctypes.CDLL`` (the parent class of ``WinDLL``) so this
            module type-checks on Linux / macOS, where ``WinDLL`` is
            not defined; at runtime on Windows the value is a
            ``ctypes.WinDLL``.
        olmem: Handle to ``olmem*.dll`` (same typing note).
        oldaapi_path: Resolved filesystem path of ``oldaapi*.dll``.
        olmem_path: Resolved filesystem path of ``olmem*.dll``.
        bitness: Pointer size of the loaded DLLs in bits (32 or 64).
            Always equal to :func:`python_bitness` for a successful
            load.
    """

    oldaapi: CDLL
    olmem: CDLL
    oldaapi_path: Path
    olmem_path: Path
    bitness: int


def load_openlayers(
    *,
    oldaapi_path: str | Path | None = None,
    olmem_path: str | Path | None = None,
) -> OpenLayersDlls:
    r"""Load the DataAcq SDK two-DLL pair.

    Resolution order (per design.md §11.1) — applied independently to
    each DLL:

    1. Explicit ``oldaapi_path`` / ``olmem_path`` argument.
    2. ``DTOLLIB_OLDAAPI_DLL`` / ``DTOLLIB_OLMEM_DLL`` env var.
    3. Default Windows install path for the current Python bitness
       (``System32\\oldaapi64.dll`` etc.).
    4. Bare DLL name — relies on the standard Windows DLL search order
       and is the last fallback before raising.

    Args:
        oldaapi_path: Explicit path to ``oldaapi*.dll``. Overrides all
            other lookups.
        olmem_path: Explicit path to ``olmem*.dll``. Overrides all
            other lookups.

    Returns:
        :class:`OpenLayersDlls` with both handles and their resolved
        paths.

    Raises:
        DtolDependencyError: Non-Windows platform, bitness mismatch,
            or DLL not found on any resolution-chain path. The error
            message names every path attempted so the user can fix
            their install.
    """
    if sys.platform != "win32":
        raise DtolDependencyError(
            "DataAcq SDK is Windows-only; "
            f"current platform is {sys.platform!r}. "
            "Install dtollib for type-checking on Linux/macOS, but the "
            "real backend cannot load there.",
            context=ErrorContext(operation="load_openlayers"),
        )

    bits = python_bitness()
    candidates_oldaapi = _build_candidates(
        explicit=oldaapi_path,
        env_var=_ENV_OLDAAPI,
        defaults=default_oldaapi_paths(bitness=bits),
        bare_name="oldaapi64.dll" if bits == _BITS_64 else "oldaapi32.dll",
    )
    candidates_olmem = _build_candidates(
        explicit=olmem_path,
        env_var=_ENV_OLMEM,
        defaults=default_olmem_paths(bitness=bits),
        bare_name="olmem64.dll" if bits == _BITS_64 else "olmem32.dll",
    )

    # Bitness pre-check: if a candidate's filename contains "32" but we
    # are running 64-bit (or vice versa), raise a precise error *before*
    # WinDLL emits the opaque OSError that bitness mismatch produces.
    for tag, candidates in (("oldaapi", candidates_oldaapi), ("olmem", candidates_olmem)):
        for path in candidates:
            _check_bitness_marker(tag, path, bits)

    oldaapi, oldaapi_resolved = _load_one("oldaapi", candidates_oldaapi)
    olmem, olmem_resolved = _load_one("olmem", candidates_olmem)

    _logger.info(
        "loaded DataAcq SDK: oldaapi=%s olmem=%s bitness=%d",
        oldaapi_resolved,
        olmem_resolved,
        bits,
    )

    return OpenLayersDlls(
        oldaapi=oldaapi,
        olmem=olmem,
        oldaapi_path=oldaapi_resolved,
        olmem_path=olmem_resolved,
        bitness=bits,
    )


def _build_candidates(
    *,
    explicit: str | Path | None,
    env_var: str,
    defaults: tuple[Path, ...],
    bare_name: str,
) -> list[Path]:
    """Compose the resolution chain for one DLL.

    An *explicit* path is authoritative: if the caller passes one, no
    other paths are tried.  Falling back to defaults would silently
    ignore the user's override and is a footgun in development /
    testing.

    The env-var override is the next-most-authoritative source: if
    ``DTOLLIB_OLDAAPI_DLL`` is set, the chain consists of just that
    path.  Defaults + bare-name lookups apply only when neither
    explicit-arg nor env-var is provided.
    """
    if explicit is not None:
        return [Path(explicit)]
    env_value = os.environ.get(env_var)
    if env_value:
        return [Path(env_value)]
    chain: list[Path] = list(defaults)
    chain.append(Path(bare_name))
    return chain


def _load_one(tag: str, candidates: list[Path]) -> tuple[CDLL, Path]:
    """Try each candidate in order; return the first successful load.

    Raises ``DtolDependencyError`` enumerating every attempt on total
    failure. Bare-name candidates (no parent) are handed to
    ``WinDLL`` by string so the OS DLL search order applies.
    """
    # Local import keeps non-Windows imports of this module clean.
    import ctypes  # noqa: PLC0415

    attempts: list[tuple[Path, str]] = []
    for candidate in candidates:
        is_bare = candidate.parent == Path()
        if not is_bare and not candidate.exists():
            attempts.append((candidate, "file not found"))
            continue
        try:
            handle = ctypes.WinDLL(str(candidate) if not is_bare else candidate.name)
        except OSError as exc:
            attempts.append((candidate, f"WinDLL: {exc}"))
            continue
        resolved = candidate if not is_bare else Path(candidate.name)
        return handle, resolved

    detail_lines = [f"  {path}: {reason}" for path, reason in attempts]
    detail = "\n".join(detail_lines) if detail_lines else "  (no candidates)"
    raise DtolDependencyError(
        f"failed to load {tag}*.dll; attempted:\n{detail}",
        context=ErrorContext(operation="load_openlayers", extra={"dll": tag}),
    )


_BITNESS_MARKERS: Final[dict[int, tuple[str, ...]]] = {
    _BITS_32: ("oldaapi32", "olmem32"),
    _BITS_64: ("oldaapi64", "olmem64"),
}


def _check_bitness_marker(tag: str, path: Path, bits: int) -> None:
    """Raise if ``path`` clearly names the wrong-bitness DLL.

    We only inspect candidates whose filename embeds an explicit
    bitness marker (``oldaapi32.dll`` / ``oldaapi64.dll``). Bare names
    like ``oldaapi.dll`` (no marker) are passed through to ``WinDLL``;
    if they load wrong-bitness, the resulting ``OSError`` is wrapped by
    :func:`_load_one`.
    """
    name = path.name.lower()
    other_bits = _BITS_32 if bits == _BITS_64 else _BITS_64
    other_markers = _BITNESS_MARKERS[other_bits]
    if any(marker in name for marker in other_markers):
        raise DtolDependencyError(
            f"bitness mismatch: Python is {bits}-bit but candidate {tag} "
            f"path {path} names a {other_bits}-bit DLL. "
            f"Install the matching DT-Open Layers SDK or run a "
            f"{other_bits}-bit Python interpreter.",
            context=ErrorContext(
                operation="load_openlayers",
                extra={"dll": tag, "candidate": str(path), "python_bitness": bits},
            ),
        )
