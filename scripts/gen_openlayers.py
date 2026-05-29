r"""Header-diff tool — sanity-check ``capi/`` against installed SDK headers.

Maintainer-only helper.  Given the path to an installed DataAcq SDK
include directory (typically
``C:\\Program Files (x86)\\Data Translation\\Win32\\SDK\\Include\\``),
this script parses out function prototypes and ``#define``\\s from
``OLDAAPI.H`` / ``OLMEM.H`` / ``OLDADEFS.H`` / ``OLERRORS.H`` and
diffs them against the hand-curated bindings in
:mod:`dtollib.capi.prototypes` and :mod:`dtollib.capi.constants`.

The parser is intentionally simple — regex over ``#define`` and
function declarations.  It is not a full C parser; novel macro tricks
in a future SDK release may produce spurious diffs that the maintainer
adjudicates by reading the headers directly.

Output modes:

- ``--check`` (default) — exit 0 if no diff, 1 otherwise.  CI-friendly.
- ``--report`` — print the diff; exit 0.
- ``--report --markdown`` — emit a markdown table suitable for
  pasting into ``docs/decisions.md``.

This tool is **not** wired into CI — Open Question 3 in
``docs/design.md`` §31 leaves header redistribution unresolved.  Run
it on the maintainer Windows machine after every SDK update; record
the findings in ``docs/decisions.md``.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


_DEFINE_RE = re.compile(
    r"^\s*#define\s+(?P<name>[A-Z_][A-Z0-9_]+)\s+"
    r"\(?\s*(?P<value>(?:0x[0-9A-Fa-f]+|-?\d+|[A-Z_][A-Z0-9_]*))\s*\)?",
    re.MULTILINE,
)

# Every exported prototype is declared ``<RETTYPE> WINAPI ol...(...)``.
# The return type varies (``ECODE`` for status-returning calls, ``LPSTR``
# for the GetErrorString helpers; ``OLSTATUS`` is only a typedef alias of
# ``ECODE``).  Anchor on the ``WINAPI`` calling-convention marker and
# capture whatever single return-type token precedes it, so the diff is
# robust to the spelling and never tied to one return type.
_PROTO_RE = re.compile(
    r"[A-Za-z_]\w*\s+WINAPI\s+(?P<name>ol[A-Za-z]+)\s*\((?P<args>[^)]*)\)",
    re.MULTILINE,
)


@dataclass(slots=True)
class HeaderContents:
    """Raw extract of one header file."""

    defines: dict[str, str] = field(default_factory=dict)
    prototypes: dict[str, str] = field(default_factory=dict)


def _stdout(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _stderr(text: str) -> None:
    sys.stderr.write(f"{text}\n")


def parse_header(path: Path) -> HeaderContents:
    """Parse one C header into ``#define`` and ``OLSTATUS`` prototypes."""
    text = path.read_text(encoding="utf-8", errors="replace")
    defines: dict[str, str] = {}
    for m in _DEFINE_RE.finditer(text):
        defines[m.group("name")] = m.group("value")
    prototypes: dict[str, str] = {}
    for m in _PROTO_RE.finditer(text):
        prototypes[m.group("name")] = m.group("args").strip()
    return HeaderContents(defines=defines, prototypes=prototypes)


def parse_headers(include_dir: Path) -> dict[str, HeaderContents]:
    """Parse every recognised DT-Open Layers header in ``include_dir``."""
    out: dict[str, HeaderContents] = {}
    for name in ("OLDAAPI.H", "OLMEM.H", "OLDADEFS.H", "OLERRORS.H", "OLWIN.H"):
        candidate = include_dir / name
        if candidate.exists():
            out[name] = parse_header(candidate)
        else:
            # Headers are case-sensitive on Linux, case-insensitive on
            # Windows.  Glob-search to recover from lowercase.
            for variant in include_dir.glob(f"[Oo][Ll]*{name.lower()[2:]}"):
                out[name] = parse_header(variant)
                break
    return out


def _load_binding_constants() -> dict[str, int]:
    """Return the integer constants currently declared in capi.constants."""
    from dtollib.capi import constants as capi_constants  # noqa: PLC0415

    out: dict[str, int] = {}
    for name in dir(capi_constants):
        if name.startswith("_"):
            continue
        value = getattr(capi_constants, name)
        if isinstance(value, int):
            out[name] = value
    return out


def _load_binding_prototypes() -> list[str]:
    """Return the function names currently declared in capi.prototypes."""
    from dtollib.capi.prototypes import (  # noqa: PLC0415
        BUFFER_OLMEM_FUNCTIONS,
        CONTINUOUS_OLDAAPI_FUNCTIONS,
        CORE_OLMEM_FUNCTIONS,
        COUNTER_OLDAAPI_FUNCTIONS,
        DISCOVERY_OLDAAPI_FUNCTIONS,
        MULTI_SENSOR_OLDAAPI_FUNCTIONS,
        OPTIONAL_OLDAAPI_FUNCTIONS,
        OUTPUT_OLDAAPI_FUNCTIONS,
        SINGLE_VALUE_OLDAAPI_FUNCTIONS,
        TEDS_OLDAAPI_FUNCTIONS,
        WAVEFORM_OLMEM_FUNCTIONS,
    )

    return [
        *DISCOVERY_OLDAAPI_FUNCTIONS,
        *CORE_OLMEM_FUNCTIONS,
        *SINGLE_VALUE_OLDAAPI_FUNCTIONS,
        *CONTINUOUS_OLDAAPI_FUNCTIONS,
        *BUFFER_OLMEM_FUNCTIONS,
        *OUTPUT_OLDAAPI_FUNCTIONS,
        *WAVEFORM_OLMEM_FUNCTIONS,
        *COUNTER_OLDAAPI_FUNCTIONS,
        *MULTI_SENSOR_OLDAAPI_FUNCTIONS,
        *TEDS_OLDAAPI_FUNCTIONS,
        *OPTIONAL_OLDAAPI_FUNCTIONS,
    ]


def diff(
    headers: dict[str, HeaderContents],
    *,
    binding_constants: dict[str, int],
    binding_prototypes: list[str],
) -> tuple[list[str], list[str]]:
    """Diff binding against parsed headers.

    Returns:
        ``(constant_findings, prototype_findings)`` — each a list of
        human-readable strings.  Empty lists mean no diff.
    """
    header_defines: dict[str, str] = {}
    header_protos: set[str] = set()
    for hc in headers.values():
        header_defines.update(hc.defines)
        header_protos.update(hc.prototypes.keys())

    const_findings: list[str] = []
    for name, bound_value in binding_constants.items():
        if name in header_defines:
            header_value_str = header_defines[name]
            try:
                header_value = int(header_value_str, 0)
            except ValueError:
                # Symbolic alias (#define X Y) — record but don't fail.
                const_findings.append(
                    f"NOTE  {name}: header value is symbolic ({header_value_str!r}); "
                    f"binding has {bound_value}"
                )
                continue
            if header_value != bound_value:
                const_findings.append(
                    f"DIFF  {name}: binding={bound_value:#x} header={header_value:#x}"
                )
        # else: binding declares a name that's not in the headers we
        # parsed — could be a constant from a header we didn't scan,
        # or a binding-internal name.  We do not flag this; it's
        # benign in practice.

    proto_findings = [
        f"MISSING  {proto}: not found in any scanned header"
        for proto in binding_prototypes
        if proto not in header_protos
    ]

    return const_findings, proto_findings


def render_markdown(const_findings: list[str], proto_findings: list[str]) -> str:
    """Render findings as a markdown table for ``docs/decisions.md``."""
    lines = ["| Kind | Finding |", "|------|---------|"]
    for f in const_findings + proto_findings:
        kind, _, rest = f.partition(" ")
        lines.append(f"| {kind} | {rest.strip()} |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the header diff CLI and return a process exit code."""
    parser = argparse.ArgumentParser(
        prog="gen_openlayers",
        description="Diff dtollib capi bindings against installed SDK headers.",
    )
    parser.add_argument(
        "include_dir",
        type=Path,
        help="Path to the DataAcq SDK Include directory.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero on diff (default behaviour).",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print the diff; always exit zero.",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Render the diff as a markdown table.",
    )
    args = parser.parse_args(argv)

    if not args.include_dir.is_dir():
        _stderr(f"error: not a directory: {args.include_dir}")
        return 2

    headers = parse_headers(args.include_dir)
    if not headers:
        _stderr(f"error: no DT-Open Layers headers found in {args.include_dir}")
        return 2

    binding_constants = _load_binding_constants()
    binding_prototypes = _load_binding_prototypes()
    const_findings, proto_findings = diff(
        headers,
        binding_constants=binding_constants,
        binding_prototypes=binding_prototypes,
    )

    if args.markdown:
        _stdout(render_markdown(const_findings, proto_findings))
    else:
        if const_findings:
            _stdout("Constant findings:")
            for f in const_findings:
                _stdout(f"  {f}")
        if proto_findings:
            _stdout("Prototype findings:")
            for f in proto_findings:
                _stdout(f"  {f}")
        if not (const_findings or proto_findings):
            _stdout("no diff: binding matches scanned headers")

    if args.report:
        return 0
    # NOTE-level findings (symbolic ``#define`` aliases the regex can't
    # evaluate) are informational and never fail --check; only DIFF /
    # MISSING findings are fatal.
    fatal = [f for f in (*const_findings, *proto_findings) if not f.startswith("NOTE")]
    return 0 if not fatal else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
