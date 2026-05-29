# Contributing to dtollib

Thank you for considering a contribution. The two architectural
references are:

- [design.md](docs/design.md) — what `dtollib` is, every public type, every
  invariant, and the rationale behind each.
- [implementation-plan.md](docs/implementation-plan.md) — how it gets built,
  in what order, and what done looks like at each phase.

Every PR should be traceable to one of those documents.

## Dev setup

```bash
uv sync --all-extras --group dev
uv run pytest
uv run pre-commit install
```

## Tests

```bash
uv run pytest                          # default — unit tests only
uv run pytest -m "binding"             # Phase 1+ — real DLL, Windows + SDK
uv run pytest -m "hardware"            # Phase 1+ — read-only on real DT hardware
```

### Hardware test gates

| Marker                  | Env var to enable                              | Effect |
|-------------------------|------------------------------------------------|--------|
| `hardware`              | (no gate — read-only)                          | Skipped by default; opt in via `-m hardware` |
| `hardware_stateful`     | `DTOLLIB_ENABLE_STATEFUL_TESTS=1`              | Mutates task/device state |
| `hardware_output`       | `DTOLLIB_ENABLE_OUTPUT_TESTS=1`                | Writes analog / digital / counter output |
| `hardware_destructive`  | `DTOLLIB_ENABLE_DESTRUCTIVE_TESTS=1`           | Calibration / unsafe operations |

## CI lanes

| Lane             | Phase | Notes                                                                  |
|------------------|------:|------------------------------------------------------------------------|
| `ci.yml`         |     0 | lint + typecheck + test matrix (Win + Ubuntu + macOS × py3.13 + py3.14) |
| `docs.yml`       |     0 | Zensical docs build; deploy on `main` push                              |
| `release.yml`    |     0 | tag-triggered PyPI publish via OIDC trusted publisher                   |
| `hardware.yml`   |     1 | `workflow_dispatch`-only on a self-hosted Windows runner with a real DT9805/DT9806 attached. **Lands in Phase 1** — Phase 0 deliberately omits the file to keep PR-status checks readable. |

## Binding verification gate (Phase 1+)

Before any new `olDa*` / `olDm*` function is added to
`capi/prototypes.py`:

1. Locate the function's prototype in the installed `OLDAAPI.H` /
   `OLMEM.H` headers.
2. Cross-check `argtypes` element-by-element. Watch for `LPARAM` vs
   `LONG` traps — these are silent data-corruption bugs.
3. Confirm `restype` is `ECODE` (or whatever the header says — counter
   functions return `BOOL` in places).
4. Record the SDK version checked against in
   [`docs/decisions.md`](docs/decisions.md) as a one-line entry:
   `2026-MM-DD — olDaFoo: argtypes verified against OLDAAPI.H rev 7.8.x`.
5. Add the function to the `scripts/gen_openlayers.py` diff allowlist.
6. Add a unit test in `tests/binding/test_signatures.py` that exercises
   the signature shape against the stub DLL.

This gate is what prevents silent divergence between the SDK and the
hand-rolled `capi/` binding. See docs/implementation-plan.md §1.4.

## Public surface discipline

`dtollib.__all__` is the public API. Every PR that adds, removes, or
renames a top-level export updates **both** `dtollib/__init__.py`
**and** `tests/unit/test_public_api.py` in the same commit. The test
exists to catch accidental surface drift in code review.

## Code style

- `mypy --strict` and `pyright --strict` both pass on every commit.
- Public dataclasses are always `frozen=True, slots=True, kw_only=True`.
- Test naming: `tests/unit/test_<topic>.py`; one assertion concept per
  test; parametrise where it cuts duplication.
- Coverage gate: 90 % statement, 80 % branch on `src/dtollib/`
  excluding `cli/*`.

## Reporting issues

GitHub issues at
<https://github.com/GraysonBellamy/dtollib/issues>. For SDK-level bugs,
include `dtol-diag` output (Phase 1+) and the SDK version.
