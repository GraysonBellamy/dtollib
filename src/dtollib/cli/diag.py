"""``dtol-diag`` — diagnose DataAcq SDK / DLL / driver install issues.

Three sections, runnable individually or together:

- ``dtol-diag sdk`` — DLL resolution + version report.
- ``dtol-diag boards`` — board enumeration via the loaded SDK.
- ``dtol-diag`` (no subcommand) — both, plus a final pass/fail
  summary.

Output is human-readable by default; ``--json`` produces a single
machine-readable object suitable for piping into ``jq`` /
``--data-stdin`` -friendly tooling.

Exit codes:

- ``0`` — all checks passed.
- ``1`` — one or more checks failed; details on stderr.
- ``2`` — invocation problem (unknown subcommand, bad flag).

Design reference: docs/design.md §21.3.
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import struct
import sys
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from dtollib.errors import DtolError
from dtollib.version import __version__

if TYPE_CHECKING:
    from collections.abc import Sequence


__all__ = ["main"]


_logger = logging.getLogger("dtollib.cli.diag")


def _empty_detail() -> dict[str, Any]:
    return {}


@dataclass(slots=True)
class DiagResult:
    """One section's outcome."""

    section: str
    ok: bool
    summary: str
    detail: dict[str, Any] = field(default_factory=_empty_detail)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by the ``dtol-diag`` console script.

    Args:
        argv: Optional argument vector for testing.  Defaults to
            :data:`sys.argv` ``[1:]``.

    Returns:
        Process exit code per the module docstring.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    results: list[DiagResult] = []

    section = getattr(args, "section", None)
    if section in (None, "sdk"):
        results.append(_check_sdk())
    if section in (None, "boards"):
        results.append(_check_boards())

    all_ok = all(r.ok for r in results)

    if args.json:
        payload = {
            "dtollib_version": __version__,
            "python_version": platform.python_version(),
            "python_bitness": struct.calcsize("P") * 8,
            "platform": sys.platform,
            "ok": all_ok,
            "sections": [asdict(r) for r in results],
        }
        print(json.dumps(payload, indent=2))
    else:
        _render_text(results, all_ok)

    return 0 if all_ok else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dtol-diag",
        description="Diagnose DataAcq SDK / DLL / driver install issues.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a single JSON object on stdout instead of formatted text.",
    )
    sub = parser.add_subparsers(dest="section")
    sub.add_parser("sdk", help="Only check the SDK DLL load + version.")
    sub.add_parser("boards", help="Only enumerate boards.")
    return parser


def _check_sdk() -> DiagResult:
    """DLL resolution + version report."""
    from dtollib.capi.loader import load_openlayers  # noqa: PLC0415

    detail: dict[str, Any] = {
        "python_bitness": struct.calcsize("P") * 8,
        "platform": sys.platform,
    }

    try:
        dlls = load_openlayers()
    except DtolError as exc:
        detail["error"] = str(exc)
        return DiagResult(
            section="sdk",
            ok=False,
            summary="failed to load DataAcq SDK",
            detail=detail,
        )

    detail["oldaapi_path"] = str(dlls.oldaapi_path)
    detail["olmem_path"] = str(dlls.olmem_path)
    detail["bitness"] = dlls.bitness

    # Versions: we instantiate the OpenLayersApi lazily so a successful
    # load that fails on the version query is reported with its own
    # error rather than as a blanket "SDK load failed".
    try:
        from dtollib.capi.api import OpenLayersApi  # noqa: PLC0415

        api = OpenLayersApi(dlls)
        detail["oldaapi_version"] = api.get_oldaapi_version()
        detail["olmem_version"] = api.get_olmem_version()
    except DtolError as exc:
        detail["version_error"] = str(exc)
        return DiagResult(
            section="sdk",
            ok=False,
            summary="loaded SDK but version query failed",
            detail=detail,
        )

    return DiagResult(
        section="sdk",
        ok=True,
        summary=(f"loaded oldaapi {detail['oldaapi_version']} + olmem {detail['olmem_version']}"),
        detail=detail,
    )


def _check_boards() -> DiagResult:
    """Board enumeration via the loaded SDK."""
    detail: dict[str, Any] = {}
    try:
        from dtollib.backend.dataacq import DataAcqBackend  # noqa: PLC0415

        backend = DataAcqBackend()
        boards = backend.enum_boards()
    except DtolError as exc:
        detail["error"] = str(exc)
        return DiagResult(
            section="boards",
            ok=False,
            summary="board enumeration failed",
            detail=detail,
        )

    detail["count"] = len(boards)
    detail["boards"] = [
        {
            "name": b.name,
            "model": b.model,
            "driver_name": b.driver_name,
            "instance": b.instance,
        }
        for b in boards
    ]

    if not boards:
        return DiagResult(
            section="boards",
            ok=True,
            summary=(
                "SDK loaded but no boards enumerated; check Open Layers "
                "Control Panel for a populated device list"
            ),
            detail=detail,
        )

    return DiagResult(
        section="boards",
        ok=True,
        summary=f"{len(boards)} board(s) enumerated",
        detail=detail,
    )


def _render_text(results: list[DiagResult], all_ok: bool) -> None:
    """Render results to stdout (success) / stderr (failure) as plain text."""
    width = 60
    print(f"dtol-diag (dtollib {__version__})")
    print(
        f"platform={sys.platform} python={platform.python_version()} "
        f"bitness={struct.calcsize('P') * 8}"
    )
    print("-" * width)

    for result in results:
        status = "OK" if result.ok else "FAIL"
        print(f"[{status}] {result.section}: {result.summary}")
        for key, value in result.detail.items():
            if key in {"boards", "error", "version_error"}:
                continue
            print(f"  {key}: {value}")
        if "boards" in result.detail:
            for board in result.detail["boards"]:
                print(
                    f"  - {board['name']} model={board['model']} "
                    f"driver={board['driver_name']} instance={board['instance']}"
                )
        if "error" in result.detail:
            print(f"  error: {result.detail['error']}", file=sys.stderr)
        if "version_error" in result.detail:
            print(f"  version_error: {result.detail['version_error']}", file=sys.stderr)

    print("-" * width)
    print(f"overall: {'PASS' if all_ok else 'FAIL'}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
